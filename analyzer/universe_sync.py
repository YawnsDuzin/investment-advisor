"""Stock Universe 동기화 — Phase 1a (KRX) + Phase 1b (US: S&P500 + Nasdaq100).

`stock_universe` 테이블을 갱신한다.

- KRX: pykrx 배치 API
- US:  yfinance batch download + 정적 시드 (`shared/seeds_data/us_universe.json`)
       시드는 `python -m tools.refresh_us_universe`로 갱신 (Wikipedia 1회 fetch)

모드:
- meta (주간):  종목 리스트·종목명·업종·시총·상장상태 — 느린 데이터
- price (일별): last_price·last_price_at — 빠른 데이터
- auto: 마지막 meta_synced_at이 7일 초과 시 meta 포함, 그 외 price만

CLI:
    python -m analyzer.universe_sync --mode price                         # KRX+US (활성화 기준)
    python -m analyzer.universe_sync --mode meta --market KOSPI
    python -m analyzer.universe_sync --mode price --market US             # US만
    python -m analyzer.universe_sync --mode auto

설계 참조: _docs/20260422172248_recommendation-engine-redesign.md §1.3
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from psycopg2.extras import execute_values

from shared.config import AppConfig, DatabaseConfig
from shared.db import get_connection, init_db
from shared.logger import get_logger
from shared.sector_mapping import market_cap_bucket, normalize_sector

try:
    from pykrx import stock as pykrx_stock
except ImportError:
    pykrx_stock = None

try:
    import yfinance as yf
except ImportError:
    yf = None


_log = get_logger("universe_sync")

KST = timezone(timedelta(hours=9))


# ── 우선주 / 비보통주 필터 ─────────────────────────
# 종목명 끝이 "우", "1우", "2우", "B", "(전환)" 등이면 우선주/특수주식으로 간주.
# 100% 정확하지는 않으나(예: 본주 이름에 "우"가 들어가는 경우 위양성 가능) Phase 1a 수준에서 안전한 근사.
_PREFERRED_SUFFIX = re.compile(r"(우|우[A-Z]?|[0-9]?우[A-Z]?|\(전환\)|\(2우[A-Z]?\)|\(1우[A-Z]?\))$")
# 명백한 비보통주 패턴 (전환사채/리츠/스팩 등은 일단 추천 대상에서 제외)
_NON_COMMON_PATTERNS = ("스팩", "리츠", "REIT")


def _is_likely_preferred_or_special(name: str, ticker: str) -> bool:
    """종목명·티커 패턴으로 우선주/특수주식 여부 추정."""
    if not name:
        return False
    # 6자리 종목코드의 마지막 자리가 0이 아니면 우선주 의심 (보통주 컨벤션)
    if ticker.isdigit() and len(ticker) == 6 and ticker[-1] != "0":
        # 단, 코스닥 신규 종목 중 일부 예외가 있으므로 이름 패턴도 함께 검사
        if _PREFERRED_SUFFIX.search(name):
            return True
    if _PREFERRED_SUFFIX.search(name):
        return True
    for kw in _NON_COMMON_PATTERNS:
        if kw in name:
            return True
    return False


# ── 마켓 코드 정규화 ───────────────────────────────
_MARKET_LABELS = ("KOSPI", "KOSDAQ")


def _today_yyyymmdd() -> str:
    return datetime.now(KST).strftime("%Y%m%d")


def _ensure_pykrx() -> None:
    if pykrx_stock is None:
        raise RuntimeError("pykrx가 설치되지 않았습니다. `pip install pykrx` 실행 후 재시도하세요.")


# ── 메타데이터 수집 ─────────────────────────────────

def _fetch_market_snapshot(date: str, market: str) -> dict[str, dict]:
    """특정 일자 + 시장의 종목별 메타 + 시총 통합 조회.

    Returns:
        {ticker: {"name": str, "market_cap_krw": int, "listed_shares": int, "close": float}}
    """
    out: dict[str, dict] = {}

    # 시총 + 상장주식수 (배치 1회)
    cap_df = pykrx_stock.get_market_cap(date, market=market)
    if cap_df is None or cap_df.empty:
        _log.warning(f"[{market}] {date} 시총 데이터 없음 — 휴장일/조회 실패 가능")
        return out

    # OHLCV (배치 1회) — 종가 추출용
    ohlcv_df = pykrx_stock.get_market_ohlcv(date, market=market)
    close_map: dict[str, float] = {}
    if ohlcv_df is not None and not ohlcv_df.empty and "종가" in ohlcv_df.columns:
        for tk, row in ohlcv_df.iterrows():
            try:
                close_map[str(tk)] = float(row["종가"])
            except (TypeError, ValueError):
                pass

    # 종목명: 개별 조회 (배치 API 부재 — 한 번 빌드 후 캐시)
    for tk, row in cap_df.iterrows():
        ticker = str(tk)
        name = pykrx_stock.get_market_ticker_name(ticker)
        if not name:
            continue
        try:
            mcap = int(row.get("시가총액", 0))
        except (TypeError, ValueError):
            mcap = 0
        try:
            shares = int(row.get("상장주식수", 0))
        except (TypeError, ValueError):
            shares = 0
        out[ticker] = {
            "name": str(name),
            "market_cap_krw": mcap,
            "listed_shares": shares,
            "close": close_map.get(ticker),
        }
    return out


def _fetch_sector_map(date: str, market: str) -> dict[str, str]:
    """KRX 업종 분류 일괄 조회 (pykrx >= 1.0.46).

    Returns:
        {ticker: "음식료품" | "금융업" | ...}. API 미지원 시 빈 dict.
    """
    fn = getattr(pykrx_stock, "get_market_sector_classifications", None)
    if fn is None:
        _log.warning("pykrx.get_market_sector_classifications 미지원 — sector_krx 비어 있음")
        return {}
    try:
        df = fn(date, market=market)
    except Exception as e:
        _log.warning(f"[{market}] 업종 분류 조회 실패: {e}")
        return {}
    if df is None or df.empty:
        return {}

    out: dict[str, str] = {}
    # pykrx 버전에 따라 컬럼명이 "업종명" 또는 "INDUSTRY_NAME" 등일 수 있음
    sector_col = None
    for col in df.columns:
        if "업종" in col or col.upper() in ("INDUSTRY_NAME", "SECTOR"):
            sector_col = col
            break
    if sector_col is None:
        return {}
    for tk, row in df.iterrows():
        sector = row.get(sector_col)
        if sector:
            out[str(tk)] = str(sector).strip()
    return out


def _last_business_day(today: datetime) -> str:
    """KRX는 토·일 휴장. 토요일이면 금요일, 일요일이면 금요일을 반환."""
    weekday = today.weekday()  # Mon=0, Sun=6
    if weekday == 5:  # Sat
        today = today - timedelta(days=1)
    elif weekday == 6:  # Sun
        today = today - timedelta(days=2)
    return today.strftime("%Y%m%d")


# ── DB 업서트 ─────────────────────────────────────

_UPSERT_META_SQL = """
INSERT INTO stock_universe (
    ticker, market, asset_name, sector_krx, sector_norm,
    market_cap_krw, market_cap_bucket, last_price, last_price_ccy, last_price_at,
    listed, has_preferred, data_source, meta_synced_at, price_synced_at
) VALUES %s
ON CONFLICT (ticker, market) DO UPDATE SET
    asset_name        = EXCLUDED.asset_name,
    sector_krx        = COALESCE(EXCLUDED.sector_krx, stock_universe.sector_krx),
    sector_norm       = COALESCE(EXCLUDED.sector_norm, stock_universe.sector_norm),
    market_cap_krw    = EXCLUDED.market_cap_krw,
    market_cap_bucket = EXCLUDED.market_cap_bucket,
    last_price        = COALESCE(EXCLUDED.last_price, stock_universe.last_price),
    last_price_ccy    = COALESCE(EXCLUDED.last_price_ccy, stock_universe.last_price_ccy),
    last_price_at     = COALESCE(EXCLUDED.last_price_at, stock_universe.last_price_at),
    listed            = EXCLUDED.listed,
    has_preferred     = EXCLUDED.has_preferred,
    data_source       = EXCLUDED.data_source,
    meta_synced_at    = EXCLUDED.meta_synced_at,
    price_synced_at   = COALESCE(EXCLUDED.price_synced_at, stock_universe.price_synced_at)
"""


_UPSERT_PRICE_SQL = """
INSERT INTO stock_universe (
    ticker, market, asset_name, last_price, last_price_ccy, last_price_at,
    price_synced_at, listed, data_source
) VALUES %s
ON CONFLICT (ticker, market) DO UPDATE SET
    last_price      = EXCLUDED.last_price,
    last_price_ccy  = EXCLUDED.last_price_ccy,
    last_price_at   = EXCLUDED.last_price_at,
    price_synced_at = EXCLUDED.price_synced_at
"""


# ── 동기화 진입점 ─────────────────────────────────

def sync_meta_krx(db_cfg: DatabaseConfig, *, markets: tuple[str, ...] = _MARKET_LABELS,
                  mark_unlisted: bool = True) -> dict:
    """KRX 메타데이터(주간) 동기화.

    Args:
        db_cfg: DB 설정
        markets: 동기화할 시장 코드 (기본: KOSPI + KOSDAQ)
        mark_unlisted: True면 이번 동기화에서 발견되지 않은 KRX 종목을 listed=FALSE로 마킹

    Returns:
        {"upserted": N, "preferred_skipped": N, "markets": {...}, "duration_sec": float}
    """
    _ensure_pykrx()
    started = datetime.now(KST)
    date = _last_business_day(started)
    _log.info(f"KRX 메타 동기화 시작 (date={date}, markets={markets})")

    rows: list[tuple] = []
    seen_tickers: set[tuple[str, str]] = set()
    preferred_skipped = 0
    per_market: dict[str, int] = {}

    for market in markets:
        snap = _fetch_market_snapshot(date, market)
        sectors = _fetch_sector_map(date, market)
        per_market[market] = len(snap)
        _log.info(f"[{market}] {len(snap)}종목 메타 수집 (업종 매핑 {len(sectors)}건)")

        for ticker, info in snap.items():
            name = info["name"]
            # 우선주/스팩/리츠 등은 has_preferred=True로 표시
            is_pref = _is_likely_preferred_or_special(name, ticker)
            if is_pref:
                preferred_skipped += 1
            sector_krx = sectors.get(ticker)
            sector_norm = normalize_sector(sector_krx=sector_krx, warn_on_miss=False)
            mcap = info["market_cap_krw"]
            bucket = market_cap_bucket(mcap)
            close = info.get("close")
            last_price_at = started if close is not None else None
            rows.append((
                ticker, market, name, sector_krx, sector_norm,
                mcap, bucket, close, "KRW", last_price_at,
                True, is_pref, "pykrx", started, started if close is not None else None,
            ))
            seen_tickers.add((ticker, market))

    if not rows:
        _log.warning("동기화 결과가 비어 있습니다 — 휴장일이거나 pykrx 인증 문제")
        return {"upserted": 0, "preferred_skipped": 0, "markets": per_market,
                "duration_sec": (datetime.now(KST) - started).total_seconds()}

    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            execute_values(cur, _UPSERT_META_SQL, rows, page_size=500)
            if mark_unlisted and seen_tickers:
                # 이번에 발견되지 않은 KRX 종목은 상장 폐지 가능성
                cur.execute("""
                    UPDATE stock_universe
                    SET listed = FALSE, delisted_at = COALESCE(delisted_at, %s::date)
                    WHERE market = ANY(%s)
                      AND (ticker, market) NOT IN %s
                      AND listed = TRUE
                """, (started.date(), list(markets), tuple(seen_tickers)))
                unlisted_count = cur.rowcount
                if unlisted_count:
                    _log.info(f"상장폐지 추정 {unlisted_count}종목 → listed=FALSE")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    duration = (datetime.now(KST) - started).total_seconds()
    _log.info(f"KRX 메타 동기화 완료: {len(rows)}건 upsert / {preferred_skipped}건 우선주 표시 / {duration:.1f}s")
    return {"upserted": len(rows), "preferred_skipped": preferred_skipped,
            "markets": per_market, "duration_sec": duration}


def sync_prices_krx(db_cfg: DatabaseConfig, *, markets: tuple[str, ...] = _MARKET_LABELS,
                    with_ohlcv: bool = False) -> dict:
    """KRX 가격(일별) 동기화 — 종가만 갱신.

    이미 메타가 있는 종목 위주이며, 신규 종목이 있으면 INSERT하되 sector 등은 NULL로 남긴다 (다음 meta sync에서 채워짐).

    with_ohlcv=True이면 동일 pykrx 응답에서 OHLCV도 추출하여 stock_universe_ohlcv에 UPSERT (OhlcvConfig.on_price_sync).
    """
    _ensure_pykrx()
    started = datetime.now(KST)
    date = _last_business_day(started)
    _log.info(f"KRX 가격 동기화 시작 (date={date}, markets={markets}, with_ohlcv={with_ohlcv})")

    rows: list[tuple] = []
    ohlcv_rows: list[tuple] = []
    per_market: dict[str, int] = {}

    for market in markets:
        ohlcv = pykrx_stock.get_market_ohlcv(date, market=market)
        if ohlcv is None or ohlcv.empty:
            _log.warning(f"[{market}] {date} OHLCV 비어 있음")
            per_market[market] = 0
            continue
        per_market[market] = len(ohlcv)
        for tk, row in ohlcv.iterrows():
            ticker = str(tk)
            try:
                close = float(row["종가"])
            except (TypeError, ValueError, KeyError):
                continue
            if close <= 0:
                continue
            # 가격 sync 시에는 asset_name 조회를 생략 → INSERT 신규 row가 발생할 경우 ticker로 임시 채움
            rows.append((
                ticker, market, ticker,
                close, "KRW", started, started, True, "pykrx",
            ))
        if with_ohlcv:
            ohlcv_rows.extend(_krx_ohlcv_rows_from_df(date, market, ohlcv))

    if not rows:
        _log.warning("가격 데이터가 비어 있습니다.")
        return {"updated": 0, "markets": per_market, "ohlcv_upserted": 0,
                "duration_sec": (datetime.now(KST) - started).total_seconds()}

    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            execute_values(cur, _UPSERT_PRICE_SQL, rows, page_size=500)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    ohlcv_upserted = 0
    if with_ohlcv and ohlcv_rows:
        ohlcv_upserted = _upsert_ohlcv_rows(db_cfg, ohlcv_rows)

    duration = (datetime.now(KST) - started).total_seconds()
    _log.info(
        f"KRX 가격 동기화 완료: {len(rows)}건 / OHLCV {ohlcv_upserted}건 / {duration:.1f}s"
    )
    return {"updated": len(rows), "markets": per_market,
            "ohlcv_upserted": ohlcv_upserted, "duration_sec": duration}


# ── US 시장 동기화 (Phase 1b) ─────────────────────

_US_MARKET_LABELS = ("NASDAQ", "NYSE")

# 시드 위치 (Wikipedia 1회 fetch 결과 — `python -m tools.refresh_us_universe`로 갱신)
_US_SEED_PATH = Path(__file__).resolve().parent.parent / "shared" / "seeds_data" / "us_universe.json"

# 환율 가정 (시총 버킷 산정용 — 정밀할 필요 없음, 다양성 제약 buckets 용도)
# 향후 fx_rate 테이블로 분리 예정.
_USD_TO_KRW = 1400


def _ensure_yfinance() -> None:
    if yf is None:
        raise RuntimeError("yfinance가 설치되지 않았습니다. `pip install yfinance` 실행 후 재시도하세요.")


def _load_us_seed() -> list[dict]:
    """시드 JSON 로드. 파일 없으면 친절한 에러."""
    if not _US_SEED_PATH.exists():
        raise FileNotFoundError(
            f"US 시드 파일이 없습니다: {_US_SEED_PATH}\n"
            "먼저 `python -m tools.refresh_us_universe`를 실행해 시드를 생성하세요."
        )
    data = json.loads(_US_SEED_PATH.read_text(encoding="utf-8"))
    return data["constituents"]


def _normalize_us_exchange(yf_exchange: str | None, indices: list[str]) -> str:
    """yfinance exchange 코드 → 'NASDAQ' / 'NYSE'."""
    if yf_exchange:
        e = yf_exchange.upper()
        if e in ("NMS", "NCM", "NGM", "NASDAQ", "NAS"):
            return "NASDAQ"
        if e in ("NYQ", "NYS", "NYSE"):
            return "NYSE"
        if e in ("PCX", "ASE", "AMEX", "ARCA", "BATS"):
            return "NYSE"  # ETF/AMEX 통합
    # 시드 fallback: NDX100만 들어있으면 NASDAQ, 그 외(SP500은 NYSE 다수) NYSE
    if indices == ["NDX100"]:
        return "NASDAQ"
    return "NYSE"


def _filter_seed(seed: list[dict], index_filter: str | None) -> list[dict]:
    """index_filter='SP500'/'NDX100'/None(전체)."""
    if not index_filter:
        return seed
    return [c for c in seed if index_filter in c.get("indices", [])]


def sync_meta_us(db_cfg: DatabaseConfig, *, index_filter: str | None = None,
                 max_workers: int = 5) -> dict:
    """US 메타데이터(주간) 동기화 — 시드 + yfinance 시총·섹터 보강.

    시드 데이터(asset_name/sector_gics/industry/indices)는 항상 사용.
    yfinance에서 marketCap, exchange만 추가 조회 (개별 호출 — 동시 max_workers).
    """
    _ensure_yfinance()
    from concurrent.futures import ThreadPoolExecutor, as_completed

    started = datetime.now(KST)
    seed = _filter_seed(_load_us_seed(), index_filter)
    _log.info(f"US 메타 동기화 시작 (대상 {len(seed)}종목, workers={max_workers})")

    def _fetch_one(entry: dict) -> tuple[dict, dict | None]:
        ticker = entry["ticker"]
        try:
            info = yf.Ticker(ticker).info
            return entry, info or None
        except Exception as e:
            _log.warning(f"[US:{ticker}] yfinance 조회 실패: {e}")
            return entry, None

    rows: list[tuple] = []
    failed = 0
    per_market: dict[str, int] = {"NASDAQ": 0, "NYSE": 0}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_fetch_one, entry) for entry in seed]
        for fut in as_completed(futures):
            entry, info = fut.result()
            ticker = entry["ticker"]
            if info is None:
                failed += 1
                continue
            mcap_usd = info.get("marketCap") or 0
            mcap_krw = int(mcap_usd) * _USD_TO_KRW if mcap_usd else None
            bucket = market_cap_bucket(mcap_krw)
            close = info.get("regularMarketPrice") or info.get("currentPrice") or info.get("previousClose")
            try:
                close = float(close) if close is not None else None
            except (TypeError, ValueError):
                close = None
            currency = info.get("currency") or "USD"
            yf_exchange = info.get("exchange")
            market = _normalize_us_exchange(yf_exchange, entry.get("indices", []))
            per_market[market] = per_market.get(market, 0) + 1

            sector_gics = info.get("sector") or entry.get("sector_gics")
            industry = info.get("industry") or entry.get("industry")
            sector_norm = normalize_sector(
                sector_gics=sector_gics,
                industry=industry,
                warn_on_miss=False,
            )
            asset_name = entry.get("asset_name") or info.get("shortName") or ticker
            aliases = {"indices": entry.get("indices", [])}

            rows.append((
                ticker, market, asset_name, None, sector_gics, sector_norm, industry,
                mcap_krw, bucket, close, currency, started,
                True, False, json.dumps(aliases), "yfinance", started,
                started if close is not None else None,
            ))

    if not rows:
        _log.warning("US 메타 동기화 결과가 비어 있습니다 — yfinance 전체 실패")
        return {"upserted": 0, "failed": failed, "markets": per_market,
                "duration_sec": (datetime.now(KST) - started).total_seconds()}

    sql = """
    INSERT INTO stock_universe (
        ticker, market, asset_name, asset_name_en, sector_gics, sector_norm, industry,
        market_cap_krw, market_cap_bucket, last_price, last_price_ccy, last_price_at,
        listed, has_preferred, aliases, data_source, meta_synced_at, price_synced_at
    ) VALUES %s
    ON CONFLICT (ticker, market) DO UPDATE SET
        asset_name        = EXCLUDED.asset_name,
        sector_gics       = COALESCE(EXCLUDED.sector_gics, stock_universe.sector_gics),
        sector_norm       = COALESCE(EXCLUDED.sector_norm, stock_universe.sector_norm),
        industry          = COALESCE(EXCLUDED.industry, stock_universe.industry),
        market_cap_krw    = EXCLUDED.market_cap_krw,
        market_cap_bucket = EXCLUDED.market_cap_bucket,
        last_price        = COALESCE(EXCLUDED.last_price, stock_universe.last_price),
        last_price_ccy    = COALESCE(EXCLUDED.last_price_ccy, stock_universe.last_price_ccy),
        last_price_at     = COALESCE(EXCLUDED.last_price_at, stock_universe.last_price_at),
        listed            = EXCLUDED.listed,
        aliases           = EXCLUDED.aliases,
        data_source       = EXCLUDED.data_source,
        meta_synced_at    = EXCLUDED.meta_synced_at,
        price_synced_at   = COALESCE(EXCLUDED.price_synced_at, stock_universe.price_synced_at)
    """

    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            execute_values(cur, sql, rows, page_size=200)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    duration = (datetime.now(KST) - started).total_seconds()
    _log.info(f"US 메타 동기화 완료: {len(rows)}건 upsert / {failed}건 실패 / {duration:.1f}s")
    return {"upserted": len(rows), "failed": failed, "markets": per_market,
            "duration_sec": duration}


def sync_prices_us(db_cfg: DatabaseConfig, *, index_filter: str | None = None,
                   with_ohlcv: bool = False) -> dict:
    """US 가격(일별) 동기화 — yfinance batch download (group_by='ticker', threads=True).

    **update-only**: DB에 이미 메타가 있는 종목만 가격을 갱신한다.
    시드에 있지만 DB에 메타가 없는 신규 종목은 무시 (meta sync에서 등록되어야 함).
    이는 같은 티커가 잘못된 시장으로 중복 INSERT되는 것을 방지한다 — 시장 정보는
    yfinance(meta sync)가 권위 있는 출처.

    with_ohlcv=True이면 동일 yf.download 응답에서 OHLCV를 추출하여 stock_universe_ohlcv에도 UPSERT.
    """
    _ensure_yfinance()
    started = datetime.now(KST)
    seed = _filter_seed(_load_us_seed(), index_filter)
    seed_tickers = {c["ticker"] for c in seed}

    # DB에 이미 있는 US 종목만 update 대상
    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ticker, market, asset_name FROM stock_universe "
                "WHERE market IN ('NASDAQ', 'NYSE') AND listed = TRUE"
            )
            existing = {row[0]: (row[1], row[2]) for row in cur.fetchall()}
    finally:
        conn.close()

    if index_filter:
        # 시드 필터가 걸려 있으면 해당 인덱스에 속한 종목만 갱신 (전체 US 갱신은 ALL_US 모드)
        existing = {tk: v for tk, v in existing.items() if tk in seed_tickers}

    if not existing:
        _log.warning("US 가격 동기화: DB에 메타가 등록된 US 종목이 없습니다 — meta sync 먼저 실행하세요.")
        return {"updated": 0, "missing": 0,
                "duration_sec": (datetime.now(KST) - started).total_seconds()}

    all_tickers = sorted(existing.keys())
    _log.info(f"US 가격 동기화 시작 (DB 등록 {len(all_tickers)}종목)")

    # yfinance batch download — period='5d'면 휴장/지연 대비 안전한 마지막 유효 종가 확보
    df = yf.download(
        tickers=all_tickers,
        period="5d",
        interval="1d",
        group_by="ticker",
        threads=True,
        progress=False,
        auto_adjust=False,
    )

    rows: list[tuple] = []
    missing = 0
    for ticker in all_tickers:
        market, asset_name = existing[ticker]
        try:
            sub = df[ticker]["Close"].dropna()
            if sub.empty:
                missing += 1
                continue
            close = float(sub.iloc[-1])
        except (KeyError, ValueError, TypeError, IndexError):
            missing += 1
            continue
        if close <= 0:
            missing += 1
            continue
        rows.append((
            ticker, market, asset_name,
            close, "USD", started, started, True, "yfinance",
        ))

    if not rows:
        _log.warning(f"US 가격 동기화: 모든 종목 실패 ({missing}건)")
        return {"updated": 0, "missing": missing, "ohlcv_upserted": 0,
                "duration_sec": (datetime.now(KST) - started).total_seconds()}

    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            execute_values(cur, _UPSERT_PRICE_SQL, rows, page_size=500)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    ohlcv_upserted = 0
    if with_ohlcv:
        # 동일 df에서 OHLCV 추출 — 5일치 반환되지만 trade_date PK로 중복 UPSERT 안전
        ticker_to_market = {tk: existing[tk][0] for tk in all_tickers}
        ohlcv_rows, _ = _us_ohlcv_rows_from_df(df, ticker_to_market)
        ohlcv_upserted = _upsert_ohlcv_rows(db_cfg, ohlcv_rows)

    duration = (datetime.now(KST) - started).total_seconds()
    _log.info(
        f"US 가격 동기화 완료: {len(rows)}건 / 실패 {missing}건 / OHLCV {ohlcv_upserted}건 / {duration:.1f}s"
    )
    return {"updated": len(rows), "missing": missing,
            "ohlcv_upserted": ohlcv_upserted, "duration_sec": duration}


# ── OHLCV 이력 테이블 (Phase 7) ─────────────────
# stock_universe_ohlcv: 종목별 일별 OHLCV를 rolling 보관. 설계: _docs/20260422235016_ohlcv-history-table-plan.md

_UPSERT_OHLCV_SQL = """
INSERT INTO stock_universe_ohlcv (
    ticker, market, trade_date, open, high, low, close, volume, data_source, adjusted
) VALUES %s
ON CONFLICT (ticker, market, trade_date) DO UPDATE SET
    open        = EXCLUDED.open,
    high        = EXCLUDED.high,
    low         = EXCLUDED.low,
    close       = EXCLUDED.close,
    volume      = EXCLUDED.volume,
    data_source = EXCLUDED.data_source,
    adjusted    = EXCLUDED.adjusted
"""


def _upsert_ohlcv_rows(db_cfg: DatabaseConfig, rows: list[tuple]) -> int:
    """OHLCV row 배치 UPSERT. rows는 _UPSERT_OHLCV_SQL 컬럼 순서."""
    if not rows:
        return 0
    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            execute_values(cur, _UPSERT_OHLCV_SQL, rows, page_size=1000)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return len(rows)


def _parse_date_yyyymmdd(s: str) -> datetime:
    """YYYYMMDD 또는 YYYY-MM-DD → datetime (KST)."""
    s = s.strip().replace("-", "")
    return datetime.strptime(s, "%Y%m%d").replace(tzinfo=KST)


def _krx_trading_days(start: datetime, end: datetime) -> list[str]:
    """start~end (inclusive) KRX 거래일 리스트 (오래된 날짜 순, YYYYMMDD)."""
    _ensure_pykrx()
    start_s = start.strftime("%Y%m%d")
    end_s = end.strftime("%Y%m%d")
    # pykrx >= 1.0.44 전용 API 시도
    try:
        days = pykrx_stock.get_previous_business_days(fromdate=start_s, todate=end_s)
        return [d.strftime("%Y%m%d") for d in days]
    except (AttributeError, TypeError):
        pass
    # fallback: 평일만 — 공휴일은 pykrx가 빈 DataFrame을 반환하면 skip 처리
    out: list[str] = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return out


def _krx_ohlcv_rows_from_df(date_str: str, market: str, df) -> list[tuple]:
    """pykrx get_market_ohlcv(date, market=M) DataFrame → 행 튜플 리스트.

    컬럼: 시가/고가/저가/종가/거래량 (+ 등락률/거래대금 등 버전에 따라 다름)
    반환 튜플 순서: (ticker, market, trade_date, open, high, low, close, volume, data_source, adjusted)
    """
    if df is None or df.empty:
        return []
    # YYYYMMDD → DATE
    try:
        trade_date = datetime.strptime(date_str, "%Y%m%d").date()
    except ValueError:
        return []
    rows: list[tuple] = []
    for tk, r in df.iterrows():
        ticker = str(tk)
        try:
            close = float(r["종가"])
        except (TypeError, ValueError, KeyError):
            continue
        if close <= 0:
            continue
        def _f(col: str) -> float | None:
            try:
                v = float(r[col])
                return v if v > 0 else None
            except (TypeError, ValueError, KeyError):
                return None
        def _i(col: str) -> int | None:
            try:
                v = int(r[col])
                return v if v >= 0 else None
            except (TypeError, ValueError, KeyError):
                return None
        rows.append((
            ticker, market, trade_date,
            _f("시가"), _f("고가"), _f("저가"), close,
            _i("거래량"), "pykrx", False,
        ))
    return rows


def _fetch_krx_day_rows(date_str: str, markets: tuple[str, ...]) -> list[tuple]:
    """특정 1일 KRX 전체 시장 OHLCV 수집. 휴장일이면 빈 리스트."""
    _ensure_pykrx()
    all_rows: list[tuple] = []
    for mk in markets:
        try:
            df = pykrx_stock.get_market_ohlcv(date_str, market=mk)
        except Exception as e:
            _log.warning(f"[KRX OHLCV] {date_str} {mk} 조회 실패: {e}")
            continue
        rows = _krx_ohlcv_rows_from_df(date_str, mk, df)
        if rows:
            all_rows.extend(rows)
    return all_rows


def sync_ohlcv_krx_day(db_cfg: DatabaseConfig, *, date: str,
                       markets: tuple[str, ...] = _MARKET_LABELS) -> dict:
    """KRX 특정 1일 OHLCV UPSERT (장애 복구·단일 날짜 재수집).

    Args:
        date: YYYYMMDD
        markets: ('KOSPI', 'KOSDAQ')
    """
    started = datetime.now(KST)
    rows = _fetch_krx_day_rows(date, markets)
    upserted = _upsert_ohlcv_rows(db_cfg, rows)
    duration = (datetime.now(KST) - started).total_seconds()
    _log.info(f"[OHLCV/KRX] {date} {list(markets)}: {upserted}건 upsert / {duration:.1f}s")
    return {"date": date, "markets": list(markets), "upserted": upserted, "duration_sec": duration}


def sync_ohlcv_krx_range(db_cfg: DatabaseConfig, *, start_date: datetime, end_date: datetime,
                         markets: tuple[str, ...] = _MARKET_LABELS) -> dict:
    """KRX 날짜 범위 백필 — 거래일마다 get_market_ohlcv 호출.

    ~250 거래일 × 2 시장 = ~500 API 호출 (1년 기준). 세션 인증 1회 유지 시 ~10~15분 소요.
    오래된 날짜부터 순차 진행하여 change_pct 계산 순서 자연스럽게 맞춤.
    """
    _ensure_pykrx()
    started = datetime.now(KST)
    days = _krx_trading_days(start_date, end_date)
    if not days:
        _log.warning(f"[OHLCV/KRX] 거래일 없음 ({start_date.date()} ~ {end_date.date()})")
        return {"days": 0, "upserted": 0, "empty_days": 0,
                "duration_sec": (datetime.now(KST) - started).total_seconds()}

    _log.info(f"[OHLCV/KRX] 백필 시작: {len(days)}거래일 × {len(markets)}시장 "
              f"({days[0]} ~ {days[-1]})")
    total = 0
    empty = 0
    # 100일 단위로 중간 commit (긴 백필 중 세션 끊김 대비)
    batch_rows: list[tuple] = []
    for i, d in enumerate(days, 1):
        rows = _fetch_krx_day_rows(d, markets)
        if not rows:
            empty += 1
        else:
            batch_rows.extend(rows)
        # 50일마다 flush (메모리 · 중간 진행 가시성)
        if i % 50 == 0 or i == len(days):
            flushed = _upsert_ohlcv_rows(db_cfg, batch_rows)
            total += flushed
            _log.info(f"[OHLCV/KRX] {i}/{len(days)}일 처리 — 누적 {total}건 upsert")
            batch_rows = []

    duration = (datetime.now(KST) - started).total_seconds()
    _log.info(f"[OHLCV/KRX] 백필 완료: {len(days)}일 / {total}건 upsert / 휴장·빈결과 {empty}일 / {duration:.1f}s")
    return {"days": len(days), "upserted": total, "empty_days": empty, "duration_sec": duration}


def _fetch_us_ohlcv_df(tickers: list[str], period: str):
    """yfinance batch download wrapper — single-ticker vs multi-ticker 차이 흡수."""
    _ensure_yfinance()
    if not tickers:
        return None
    df = yf.download(
        tickers=tickers,
        period=period,
        interval="1d",
        group_by="ticker",
        threads=True,
        progress=False,
        auto_adjust=False,
    )
    return df


def _us_ohlcv_rows_from_df(df, ticker_to_market: dict[str, str]) -> tuple[list[tuple], set[str]]:
    """yfinance batch DataFrame → (OHLCV 행 리스트, 행 1개 이상 생성된 티커 집합).

    - `group_by='ticker'`인 경우 2-level column: df[ticker][OHLCV...]
    - 단일 ticker만 넘긴 경우 DataFrame의 columns가 1-level일 수 있음 → 우회 처리.

    성공 티커 집합은 호출자가 batch 실패 티커(= 요청했으나 행 0건)를 집합연산으로
    식별하여 개별 재시도에 사용한다.
    """
    rows: list[tuple] = []
    success: set[str] = set()
    if df is None:
        return rows, success

    def _as_float(x) -> float | None:
        try:
            f = float(x)
            return f if f == f else None  # NaN check
        except (TypeError, ValueError):
            return None

    def _as_int(x) -> int | None:
        try:
            f = float(x)
            if f != f:
                return None
            return int(f)
        except (TypeError, ValueError):
            return None

    def _emit(ticker: str, sub_df):
        if sub_df is None or getattr(sub_df, "empty", True):
            return
        market = ticker_to_market.get(ticker)
        if not market:
            return
        try:
            close_series = sub_df["Close"]
        except (KeyError, IndexError):
            return
        emitted = 0
        for dt, close_v in close_series.items():
            close = _as_float(close_v)
            if close is None or close <= 0:
                continue
            try:
                trade_date = dt.date() if hasattr(dt, "date") else datetime.strptime(str(dt)[:10], "%Y-%m-%d").date()
            except (ValueError, AttributeError):
                continue
            o = _as_float(sub_df["Open"].get(dt))
            h = _as_float(sub_df["High"].get(dt))
            lo = _as_float(sub_df["Low"].get(dt))
            v = _as_int(sub_df["Volume"].get(dt))
            rows.append((
                ticker, market, trade_date,
                o, h, lo, close, v,
                "yfinance", False,
            ))
            emitted += 1
        if emitted > 0:
            success.add(ticker)

    # 2-level columns (batch)
    if hasattr(df.columns, "levels") and len(df.columns.levels) >= 2:
        tickers_in_df = list(df.columns.levels[0])
        for tk in tickers_in_df:
            try:
                sub = df[tk]
            except KeyError:
                continue
            _emit(tk, sub)
    else:
        # 1-level columns (single ticker)
        if len(ticker_to_market) == 1:
            only_ticker = next(iter(ticker_to_market.keys()))
            _emit(only_ticker, df)
    return rows, success


def sync_ohlcv_us(db_cfg: DatabaseConfig, *, days: int, index_filter: str | None = None,
                  chunk_size: int = 100, retry_failed: bool = True,
                  max_retry_budget: int = 30, retry_sleep_sec: float = 2.0) -> dict:
    """US 일별 OHLCV 백필/증분 — yfinance batch download.

    Args:
        days: 과거 N일 (yfinance period=f"{days}d")
        index_filter: 'SP500'/'NDX100'/None(전체)
        chunk_size: yf.download 1회 호출당 ticker 수 (rate limit 완화)
        retry_failed: batch 내 행 0건 티커를 단건 재시도할지 여부
        max_retry_budget: 실패 티커가 이 수 초과 시 재시도 skip (systemic 장애 의심)
        retry_sleep_sec: 단건 재시도 사이 sleep (rate limit 회피)

    Returns dict:
        upserted / chunks / duration_sec / failed_tickers / retried_ok / still_failed
    """
    _ensure_yfinance()
    started = datetime.now(KST)

    # DB에서 등록된 US 종목만 대상 (meta sync 이후)
    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ticker, market FROM stock_universe "
                "WHERE market IN ('NASDAQ', 'NYSE') AND listed = TRUE"
            )
            registered: dict[str, str] = {row[0]: row[1] for row in cur.fetchall()}
    finally:
        conn.close()

    # 시드 필터 (SP500/NDX100) 적용
    if index_filter:
        seed = _filter_seed(_load_us_seed(), index_filter)
        seed_tickers = {c["ticker"] for c in seed}
        registered = {tk: mk for tk, mk in registered.items() if tk in seed_tickers}

    if not registered:
        _log.warning("[OHLCV/US] 대상 종목 없음 — meta sync 먼저 실행하세요.")
        return {"upserted": 0, "chunks": 0,
                "failed_tickers": [], "retried_ok": [], "still_failed": [],
                "duration_sec": (datetime.now(KST) - started).total_seconds()}

    tickers = sorted(registered.keys())
    period = f"{days}d"
    _log.info(f"[OHLCV/US] {len(tickers)}종목 / period={period} / chunk={chunk_size}")

    total_upserted = 0
    chunks = 0
    success_tickers: set[str] = set()
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i:i + chunk_size]
        chunks += 1
        try:
            df = _fetch_us_ohlcv_df(chunk, period=period)
        except Exception as e:
            # chunk 전체 실패 → 해당 티커들은 success_tickers에 추가되지 않아 아래에서 실패 감지됨
            _log.warning(f"[OHLCV/US] chunk {chunks} 조회 실패: {e}")
            continue
        ticker_to_market = {tk: registered[tk] for tk in chunk}
        rows, chunk_success = _us_ohlcv_rows_from_df(df, ticker_to_market)
        success_tickers |= chunk_success
        total_upserted += _upsert_ohlcv_rows(db_cfg, rows)
        _log.info(
            f"[OHLCV/US] chunk {chunks}: {len(chunk)}종목 요청 → {len(chunk_success)}종목 성공 / {len(rows)}행 upsert"
        )

    # 실패 티커 식별 (요청했으나 행 0건)
    failed_tickers: list[str] = sorted(set(registered.keys()) - success_tickers)
    retried_ok: list[str] = []
    still_failed: list[str] = []

    if failed_tickers:
        sample = failed_tickers[:10]
        _log.warning(
            f"[OHLCV/US] 실패 티커 {len(failed_tickers)}건 (sample={sample})"
        )
        if retry_failed:
            if len(failed_tickers) > max_retry_budget:
                _log.warning(
                    f"[OHLCV/US] 실패 {len(failed_tickers)}건이 재시도 한도({max_retry_budget}) 초과 — "
                    "systemic 장애 의심, 단건 재시도 skip. `--ticker` 로 수동 재실행하세요."
                )
                still_failed = list(failed_tickers)
            else:
                _log.info(f"[OHLCV/US] 실패 {len(failed_tickers)}건 단건 재시도 시작")
                for tk in failed_tickers:
                    try:
                        r = sync_ohlcv_ticker(db_cfg, ticker=tk, days=days)
                        if r.get("upserted", 0) > 0:
                            retried_ok.append(tk)
                        else:
                            still_failed.append(tk)
                    except Exception as e:
                        _log.warning(f"[OHLCV/US/retry] {tk} 재시도 예외: {type(e).__name__}: {e}")
                        still_failed.append(tk)
                    if retry_sleep_sec > 0:
                        time.sleep(retry_sleep_sec)
                _log.warning(
                    f"[OHLCV/US] 재시도 결과: 복구={len(retried_ok)}건, 여전히실패={len(still_failed)}건"
                )
                if still_failed:
                    _log.warning(f"[OHLCV/US] 여전히실패: {still_failed[:20]}")
        else:
            still_failed = list(failed_tickers)

    duration = (datetime.now(KST) - started).total_seconds()
    _log.info(
        f"[OHLCV/US] 완료: upsert={total_upserted}건 / {chunks}청크 / "
        f"실패={len(failed_tickers)}, 재시도복구={len(retried_ok)}, 잔여실패={len(still_failed)} / {duration:.1f}s"
    )
    return {
        "upserted": total_upserted,
        "chunks": chunks,
        "failed_tickers": failed_tickers,
        "retried_ok": retried_ok,
        "still_failed": still_failed,
        "duration_sec": duration,
    }


def sync_ohlcv_ticker(db_cfg: DatabaseConfig, *, ticker: str, days: int) -> dict:
    """단건 ticker 백필 — 신규 상장 종목 개별 백필 용도.

    stock_universe에서 market을 조회하여 적절한 데이터 소스(pykrx/yfinance) 선택.
    """
    started = datetime.now(KST)
    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT market FROM stock_universe WHERE ticker = %s LIMIT 1",
                (ticker,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        _log.error(f"[OHLCV/ticker] 종목 {ticker} 이 stock_universe에 없습니다. 먼저 meta sync를 실행하세요.")
        return {"ticker": ticker, "upserted": 0, "reason": "not_in_universe"}
    market = row[0]

    rows: list[tuple] = []
    if market in _MARKET_LABELS:
        _ensure_pykrx()
        today = datetime.now(KST)
        start = today - timedelta(days=days)
        # pykrx 단건: get_market_ohlcv_by_date(fromdate, todate, ticker)
        try:
            df = pykrx_stock.get_market_ohlcv_by_date(
                fromdate=start.strftime("%Y%m%d"),
                todate=today.strftime("%Y%m%d"),
                ticker=ticker,
            )
        except Exception as e:
            _log.error(f"[OHLCV/ticker] pykrx 조회 실패 {ticker}: {e}")
            return {"ticker": ticker, "upserted": 0, "reason": "pykrx_failed"}
        if df is None or df.empty:
            _log.warning(f"[OHLCV/ticker] pykrx 빈 결과 {ticker}")
            return {"ticker": ticker, "upserted": 0, "reason": "empty"}
        for dt, r in df.iterrows():
            try:
                trade_date = dt.date() if hasattr(dt, "date") else datetime.strptime(str(dt)[:10], "%Y-%m-%d").date()
                close = float(r["종가"])
            except (TypeError, ValueError, KeyError, AttributeError):
                continue
            if close <= 0:
                continue
            def _f(col: str) -> float | None:
                try:
                    v = float(r[col])
                    return v if v > 0 else None
                except (TypeError, ValueError, KeyError):
                    return None
            def _i(col: str) -> int | None:
                try:
                    v = int(r[col])
                    return v if v >= 0 else None
                except (TypeError, ValueError, KeyError):
                    return None
            rows.append((
                ticker, market, trade_date,
                _f("시가"), _f("고가"), _f("저가"), close,
                _i("거래량"), "pykrx", False,
            ))
    elif market in ("NASDAQ", "NYSE"):
        df = _fetch_us_ohlcv_df([ticker], period=f"{days}d")
        rows, _ = _us_ohlcv_rows_from_df(df, {ticker: market})
    else:
        _log.error(f"[OHLCV/ticker] 알 수 없는 market={market}")
        return {"ticker": ticker, "upserted": 0, "reason": "unknown_market"}

    upserted = _upsert_ohlcv_rows(db_cfg, rows)
    duration = (datetime.now(KST) - started).total_seconds()
    _log.info(f"[OHLCV/ticker] {ticker}({market}) days={days}: {upserted}건 / {duration:.1f}s")
    return {"ticker": ticker, "market": market, "upserted": upserted, "duration_sec": duration}


# change_pct 컬럼의 수용 한계 (v28: NUMERIC(10,4) → ±999999.9999%)
# 역분할·상폐·수정주가 미반영으로 한계 초과 row가 들어올 수 있어 UPDATE 시 가드로 사용.
_CHANGE_PCT_ABS_LIMIT = 999999.9999


def recompute_change_pct(db_cfg: DatabaseConfig) -> int:
    """change_pct가 NULL인 row들을 window 함수로 일괄 재계산.

    (ticker, market) 쌍이 NULL change_pct를 하나라도 가지고 있으면 해당 종목 전체를
    대상으로 LAG로 이전 종가 참조하여 (close-prev)/prev*100 계산 후 UPDATE.
    첫 거래일은 prev_close가 없어 change_pct는 NULL 유지 (정상).

    방어 로직:
      1. 오버플로우 가능 row(|계산값| >= _CHANGE_PCT_ABS_LIMIT)를 UPDATE 전에 식별하여
         WARNING 로그로 티커·날짜·close·prev_close 샘플 출력 (최대 20건).
         → 조작된 수정주가/상폐 직전 이상 체결 / 역분할 미반영 의심 케이스 조기 발견.
      2. UPDATE SQL에 동일 범위 가드(WHERE ABS(...) < _CHANGE_PCT_ABS_LIMIT) 추가 —
         한계 초과 row는 NULL 유지(이력 손실 아님, 재계산 시도는 다음 배치에서 반복).
      3. 최종 UPDATE는 try/except로 감싸 psycopg2 오버플로우 등 예외 발생 시
         rollback + ERROR 로그만 남기고 0 반환. **호출자로 예외 전파하지 않음** —
         백필/가격 동기화 파이프라인이 이 단계 실패로 종료되지 않도록.

    Returns: 업데이트된 row 수 (실패 시 0)
    """
    # 1) 오버플로우 가능 후보 사전 식별 (관측/모니터링 목적)
    probe_sql = """
    WITH affected AS (
        SELECT DISTINCT ticker, market
        FROM stock_universe_ohlcv
        WHERE change_pct IS NULL
    ),
    ranked AS (
        SELECT o.ticker, o.market, o.trade_date, o.close,
               LAG(o.close) OVER (PARTITION BY o.ticker, o.market ORDER BY o.trade_date) AS prev_close
        FROM stock_universe_ohlcv o
        JOIN affected a USING (ticker, market)
    )
    SELECT ticker, market, trade_date, close, prev_close,
           ((close - prev_close) / prev_close * 100)::numeric AS raw_pct
    FROM ranked
    WHERE prev_close IS NOT NULL
      AND prev_close > 0
      AND ABS((close - prev_close) / prev_close * 100) >= %s
    ORDER BY ABS((close - prev_close) / prev_close * 100) DESC
    LIMIT 20;
    """
    update_sql = """
    WITH affected AS (
        SELECT DISTINCT ticker, market
        FROM stock_universe_ohlcv
        WHERE change_pct IS NULL
    ),
    ranked AS (
        SELECT o.ticker, o.market, o.trade_date, o.close,
               LAG(o.close) OVER (PARTITION BY o.ticker, o.market ORDER BY o.trade_date) AS prev_close
        FROM stock_universe_ohlcv o
        JOIN affected a USING (ticker, market)
    )
    UPDATE stock_universe_ohlcv o
    SET change_pct = ROUND(((r.close - r.prev_close) / r.prev_close * 100)::numeric, 4)
    FROM ranked r
    WHERE o.ticker = r.ticker
      AND o.market = r.market
      AND o.trade_date = r.trade_date
      AND o.change_pct IS NULL
      AND r.prev_close IS NOT NULL
      AND r.prev_close > 0
      AND ABS((r.close - r.prev_close) / r.prev_close * 100) < %s;
    """

    updated = 0
    conn = get_connection(db_cfg)
    try:
        # 1a) 오버플로우 후보 스캔
        try:
            with conn.cursor() as cur:
                cur.execute(probe_sql, (_CHANGE_PCT_ABS_LIMIT,))
                overflow_rows = cur.fetchall()
            conn.commit()
            if overflow_rows:
                _log.warning(
                    f"[OHLCV] change_pct 계산값이 한계(|{_CHANGE_PCT_ABS_LIMIT}%|) 초과 — "
                    f"{len(overflow_rows)}건 (상위 20개 샘플 출력). "
                    f"역분할/수정주가 미반영/상폐 직전 이상 체결 의심 — 해당 row는 NULL 유지."
                )
                for tk, mk, dt, close, prev, raw in overflow_rows:
                    _log.warning(
                        f"  └ {tk}({mk}) {dt} close={close} prev_close={prev} raw_pct={raw:+.2f}%"
                    )
        except Exception as e:
            # probe 실패해도 본 UPDATE는 시도 (진단용이므로)
            conn.rollback()
            _log.warning(f"[OHLCV] change_pct 오버플로우 사전 스캔 실패 (UPDATE는 계속 시도): {e}")

        # 2) 가드 포함 UPDATE — 실패해도 예외 전파 안 함
        try:
            with conn.cursor() as cur:
                cur.execute(update_sql, (_CHANGE_PCT_ABS_LIMIT,))
                updated = cur.rowcount
            conn.commit()
            _log.info(f"[OHLCV] change_pct 재계산: {updated}건 업데이트")
        except Exception as e:
            conn.rollback()
            _log.error(
                f"[OHLCV] change_pct 재계산 실패 — 이 단계를 건너뛰고 계속 진행합니다. "
                f"원인: {type(e).__name__}: {e}"
            )
            updated = 0
    finally:
        conn.close()
    return updated


def cleanup_ohlcv(db_cfg: DatabaseConfig, *, retention_days: int,
                  delisted_retention_days: int = 0) -> dict:
    """retention 초과 OHLCV row 삭제. 대용량 환경을 위해 배치 LIMIT 반복 사용.

    Args:
        retention_days: 기본 보존 일수 (이 기간 초과 row 삭제)
        delisted_retention_days: >0 이면 listed=FALSE 종목에 더 짧은 retention 적용
    """
    started = datetime.now(KST)
    deleted_normal = 0
    deleted_delisted = 0

    # 일반 retention
    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            while True:
                cur.execute(
                    "DELETE FROM stock_universe_ohlcv "
                    "WHERE ctid IN ("
                    "  SELECT ctid FROM stock_universe_ohlcv "
                    "  WHERE trade_date < CURRENT_DATE - (%s::int) "
                    "  LIMIT 10000"
                    ")",
                    (retention_days,),
                )
                n = cur.rowcount
                deleted_normal += n
                conn.commit()
                if n < 10000:
                    break
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # 상폐 종목 축소 retention
    if delisted_retention_days and delisted_retention_days > 0 and delisted_retention_days < retention_days:
        conn = get_connection(db_cfg)
        try:
            with conn.cursor() as cur:
                while True:
                    cur.execute(
                        "DELETE FROM stock_universe_ohlcv o "
                        "WHERE o.ctid IN ("
                        "  SELECT o2.ctid FROM stock_universe_ohlcv o2 "
                        "  JOIN stock_universe u USING (ticker, market) "
                        "  WHERE u.listed = FALSE "
                        "    AND o2.trade_date < CURRENT_DATE - (%s::int) "
                        "  LIMIT 10000"
                        ")",
                        (delisted_retention_days,),
                    )
                    n = cur.rowcount
                    deleted_delisted += n
                    conn.commit()
                    if n < 10000:
                        break
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    duration = (datetime.now(KST) - started).total_seconds()
    _log.info(
        f"[OHLCV cleanup] 일반 {deleted_normal}건 + 상폐 {deleted_delisted}건 삭제 / {duration:.1f}s"
    )
    return {
        "deleted_normal": deleted_normal,
        "deleted_delisted": deleted_delisted,
        "duration_sec": duration,
    }


# ── 통합 auto 모드 ────────────────────────────────

def _meta_is_stale(db_cfg: DatabaseConfig, *, max_age_days: int = 7,
                   markets: tuple[str, ...] = ("KOSPI", "KOSDAQ")) -> bool:
    """가장 최신 meta_synced_at이 max_age_days 초과면 True. 데이터 없으면 True."""
    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MAX(meta_synced_at) FROM stock_universe WHERE market = ANY(%s)",
                (list(markets),),
            )
            row = cur.fetchone()
            last = row[0] if row else None
    finally:
        conn.close()
    if last is None:
        return True
    age = (datetime.now(KST) - last).days
    return age > max_age_days


def sync_auto(db_cfg: DatabaseConfig, *, krx_enabled: bool = True,
              us_enabled: bool = True) -> dict:
    """자동 모드: KRX/US 각각 stale 판별 후 meta 또는 price 실행."""
    result: dict = {"krx": None, "us": None}

    if krx_enabled:
        if _meta_is_stale(db_cfg, markets=("KOSPI", "KOSDAQ")):
            _log.info("[KRX] 메타 stale — meta 동기화 실행")
            result["krx"] = {"mode": "meta", "data": sync_meta_krx(db_cfg)}
        else:
            _log.info("[KRX] 메타 신선 — price만")
            result["krx"] = {"mode": "price", "data": sync_prices_krx(db_cfg)}

    if us_enabled:
        if _meta_is_stale(db_cfg, markets=("NASDAQ", "NYSE")):
            _log.info("[US] 메타 stale — meta 동기화 실행 (yfinance, ~5분 소요)")
            result["us"] = {"mode": "meta", "data": sync_meta_us(db_cfg)}
        else:
            _log.info("[US] 메타 신선 — price만")
            result["us"] = {"mode": "price", "data": sync_prices_us(db_cfg)}

    return result


# ── CLI ─────────────────────────────────────────

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Stock Universe 동기화 (Phase 1a KRX + Phase 1b US + Phase 7 OHLCV 이력)"
    )
    p.add_argument("--mode",
                   choices=("meta", "price", "auto", "ohlcv", "backfill", "cleanup"),
                   default="auto",
                   help=("meta: 주간 메타/시총/업종 | "
                         "price: 일별 가격 (+ OHLCV if OHLCV_ON_PRICE_SYNC=true) | "
                         "auto: stale 판별 후 자동 | "
                         "ohlcv: 특정 1일 OHLCV 재수집 (--date 필수) | "
                         "backfill: 과거 N일 OHLCV 일괄 수집 (--days 또는 --ticker) | "
                         "cleanup: retention 초과 OHLCV row 삭제"))
    p.add_argument("--market",
                   choices=("KOSPI", "KOSDAQ", "KRX", "US", "SP500", "NDX", "ALL"),
                   default="ALL",
                   help=("동기화할 시장 (기본: ALL=KRX+US). "
                         "KRX=KOSPI+KOSDAQ | US=SP500+NDX100 | SP500/NDX=US 부분 인덱스"))
    p.add_argument("--no-mark-unlisted", action="store_true",
                   help="meta 모드에서 상장폐지 추정 종목을 listed=FALSE로 마킹하지 않음 (디버깅용)")
    p.add_argument("--init-db", action="store_true",
                   help="실행 전 init_db() 호출 - 신규 환경에서 마이그레이션 적용")
    # OHLCV / backfill / cleanup 전용 인자
    p.add_argument("--days", type=int, default=None,
                   help="backfill/cleanup 모드 기준 일수 (미지정 시 OHLCV_BACKFILL_DAYS / OHLCV_RETENTION_DAYS)")
    p.add_argument("--date", type=str, default=None,
                   help="ohlcv 모드 전용 날짜 YYYY-MM-DD 또는 YYYYMMDD (미지정 시 오늘)")
    p.add_argument("--ticker", type=str, default=None,
                   help="backfill 모드에서 단건 종목만 백필 (신규 상장 대응)")
    p.add_argument("--no-change-pct", action="store_true",
                   help="OHLCV/backfill 후 change_pct 재계산 건너뜀 (디버깅용)")
    return p.parse_args(argv)


def _resolve_targets(market_arg: str, krx_cfg_enabled: bool, us_cfg_enabled: bool) -> tuple[
    tuple[str, ...], str | None
]:
    """--market 인자 + UniverseConfig 결합 → (krx_markets, us_index_filter).

    krx_markets: () 면 KRX 동기화 안 함
    us_index_filter: 'SP500'/'NDX100'/'ALL_US'/None — None이면 US 동기화 안 함
    """
    krx_markets: tuple[str, ...] = ()
    us_filter: str | None = None

    if market_arg == "ALL":
        if krx_cfg_enabled:
            krx_markets = _MARKET_LABELS
        if us_cfg_enabled:
            us_filter = "ALL_US"
    elif market_arg == "KRX":
        krx_markets = _MARKET_LABELS if krx_cfg_enabled else ()
    elif market_arg in ("KOSPI", "KOSDAQ"):
        krx_markets = (market_arg,) if krx_cfg_enabled else ()
    elif market_arg == "US":
        us_filter = "ALL_US" if us_cfg_enabled else None
    elif market_arg == "SP500":
        us_filter = "SP500" if us_cfg_enabled else None
    elif market_arg == "NDX":
        us_filter = "NDX100" if us_cfg_enabled else None

    return krx_markets, us_filter


def _run_mode_cleanup(cfg: AppConfig, args: argparse.Namespace) -> dict:
    """--mode cleanup: retention 초과 row 삭제."""
    retention = args.days if args.days is not None else cfg.ohlcv.retention_days
    delisted = cfg.ohlcv.delisted_retention_days
    return cleanup_ohlcv(cfg.db, retention_days=retention, delisted_retention_days=delisted)


def _run_mode_ohlcv_single(cfg: AppConfig, args: argparse.Namespace,
                           krx_markets: tuple[str, ...], us_filter: str | None) -> dict:
    """--mode ohlcv: 특정 1일 강제 재수집 (장애 복구)."""
    date_str = args.date
    if date_str:
        date_dt = _parse_date_yyyymmdd(date_str)
    else:
        date_dt = datetime.now(KST)
    date_yyyymmdd = date_dt.strftime("%Y%m%d")
    result: dict = {"date": date_yyyymmdd, "krx": None, "us": None}

    if krx_markets:
        result["krx"] = sync_ohlcv_krx_day(cfg.db, date=date_yyyymmdd, markets=krx_markets)

    if us_filter:
        # yfinance는 단일 날짜 API가 깔끔하지 않아 period='5d'로 조회 → PK가 해당 날짜 row만 덮어씀
        index_filter = None if us_filter == "ALL_US" else us_filter
        result["us"] = sync_ohlcv_us(cfg.db, days=5, index_filter=index_filter)

    if not args.no_change_pct:
        _safe_recompute_change_pct(cfg.db, stage="ohlcv-single")
    return result


def _run_mode_backfill(cfg: AppConfig, args: argparse.Namespace,
                       krx_markets: tuple[str, ...], us_filter: str | None) -> dict:
    """--mode backfill: 과거 N일 구간 수집."""
    days = args.days if args.days is not None else cfg.ohlcv.backfill_days
    result: dict = {"days": days, "krx": None, "us": None, "ticker": None}

    # 단건 ticker 백필
    if args.ticker:
        result["ticker"] = sync_ohlcv_ticker(cfg.db, ticker=args.ticker, days=days)
        if not args.no_change_pct:
            _safe_recompute_change_pct(cfg.db, stage="backfill-ticker")
        return result

    today = datetime.now(KST)
    start = today - timedelta(days=days)

    if krx_markets:
        result["krx"] = sync_ohlcv_krx_range(cfg.db, start_date=start, end_date=today,
                                             markets=krx_markets)

    if us_filter:
        index_filter = None if us_filter == "ALL_US" else us_filter
        result["us"] = sync_ohlcv_us(cfg.db, days=days, index_filter=index_filter)

    if not args.no_change_pct:
        _safe_recompute_change_pct(cfg.db, stage="backfill")
    return result


def _safe_recompute_change_pct(db_cfg: DatabaseConfig, *, stage: str) -> int:
    """recompute_change_pct 래퍼 — 내부에서 이미 대부분의 예외를 흡수하지만,
    추가 안전망으로 호출부에서도 BaseException 외의 모든 예외를 WARNING으로 전환하여
    상위 배치(backfill/ohlcv/price 모드)의 비정상 종료를 방지한다.

    Args:
        stage: 로그 식별자 (backfill / backfill-ticker / ohlcv-single / price-sync 등)
    Returns:
        업데이트된 row 수 (실패 시 0)
    """
    try:
        return recompute_change_pct(db_cfg)
    except Exception as e:
        _log.warning(
            f"[{stage}] change_pct 재계산 호출 중 예외 — 이 단계를 건너뜁니다. "
            f"원인: {type(e).__name__}: {e}"
        )
        return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    cfg = AppConfig()

    if args.init_db:
        init_db(cfg.db)

    krx_markets, us_filter = _resolve_targets(
        args.market,
        krx_cfg_enabled=cfg.universe.krx_enabled,
        us_cfg_enabled=cfg.universe.us_enabled,
    )

    # ── OHLCV 전용 모드 (대상 판별 불필요한 cleanup 먼저) ─────
    if args.mode == "cleanup":
        _log.info(f"결과: {_run_mode_cleanup(cfg, args)}")
        return 0

    if not krx_markets and not us_filter:
        _log.warning(
            f"동기화 대상이 없습니다 (--market={args.market}, "
            f"krx_enabled={cfg.universe.krx_enabled}, us_enabled={cfg.universe.us_enabled})"
        )
        return 0

    # ── 신규 OHLCV/backfill 모드 ──────────────────────
    if args.mode == "ohlcv":
        _log.info(f"결과: {_run_mode_ohlcv_single(cfg, args, krx_markets, us_filter)}")
        return 0

    if args.mode == "backfill":
        _log.info(f"결과: {_run_mode_backfill(cfg, args, krx_markets, us_filter)}")
        return 0

    # ── 기존 meta/price/auto 모드 ─────────────────────
    with_ohlcv = cfg.ohlcv.on_price_sync  # price 모드에서 OHLCV 묻어가기 여부

    result: dict = {"krx": None, "us": None}

    # KRX
    if krx_markets:
        if args.mode == "meta":
            result["krx"] = sync_meta_krx(cfg.db, markets=krx_markets,
                                          mark_unlisted=not args.no_mark_unlisted)
        elif args.mode == "price":
            result["krx"] = sync_prices_krx(cfg.db, markets=krx_markets, with_ohlcv=with_ohlcv)
        else:
            if _meta_is_stale(cfg.db, markets=("KOSPI", "KOSDAQ"),
                              max_age_days=cfg.universe.meta_stale_days):
                _log.info("[KRX] 메타 stale — meta 동기화")
                result["krx"] = {"mode": "meta", "data": sync_meta_krx(cfg.db, markets=krx_markets)}
            else:
                _log.info("[KRX] 메타 신선 — price만")
                result["krx"] = {"mode": "price",
                                 "data": sync_prices_krx(cfg.db, markets=krx_markets,
                                                         with_ohlcv=with_ohlcv)}

    # US
    if us_filter:
        index_filter = None if us_filter == "ALL_US" else us_filter
        if args.mode == "meta":
            result["us"] = sync_meta_us(cfg.db, index_filter=index_filter)
        elif args.mode == "price":
            result["us"] = sync_prices_us(cfg.db, index_filter=index_filter, with_ohlcv=with_ohlcv)
        else:
            if _meta_is_stale(cfg.db, markets=("NASDAQ", "NYSE"),
                              max_age_days=cfg.universe.meta_stale_days):
                _log.info("[US] 메타 stale — meta 동기화 (yfinance, ~5분 예상)")
                result["us"] = {"mode": "meta", "data": sync_meta_us(cfg.db, index_filter=index_filter)}
            else:
                _log.info("[US] 메타 신선 — price만")
                result["us"] = {"mode": "price",
                                "data": sync_prices_us(cfg.db, index_filter=index_filter,
                                                       with_ohlcv=with_ohlcv)}

    # price/auto 모드에서 OHLCV가 함께 수집되었으면 change_pct 재계산
    if with_ohlcv and args.mode in ("price", "auto") and not args.no_change_pct:
        _safe_recompute_change_pct(cfg.db, stage=f"price-sync/{args.mode}")

    _log.info(f"결과: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
