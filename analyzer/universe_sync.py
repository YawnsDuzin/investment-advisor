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


def sync_prices_krx(db_cfg: DatabaseConfig, *, markets: tuple[str, ...] = _MARKET_LABELS) -> dict:
    """KRX 가격(일별) 동기화 — 종가만 갱신.

    이미 메타가 있는 종목 위주이며, 신규 종목이 있으면 INSERT하되 sector 등은 NULL로 남긴다 (다음 meta sync에서 채워짐).
    """
    _ensure_pykrx()
    started = datetime.now(KST)
    date = _last_business_day(started)
    _log.info(f"KRX 가격 동기화 시작 (date={date}, markets={markets})")

    rows: list[tuple] = []
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

    if not rows:
        _log.warning("가격 데이터가 비어 있습니다.")
        return {"updated": 0, "markets": per_market,
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

    duration = (datetime.now(KST) - started).total_seconds()
    _log.info(f"KRX 가격 동기화 완료: {len(rows)}건 / {duration:.1f}s")
    return {"updated": len(rows), "markets": per_market, "duration_sec": duration}


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


def sync_prices_us(db_cfg: DatabaseConfig, *, index_filter: str | None = None) -> dict:
    """US 가격(일별) 동기화 — yfinance batch download (group_by='ticker', threads=True).

    **update-only**: DB에 이미 메타가 있는 종목만 가격을 갱신한다.
    시드에 있지만 DB에 메타가 없는 신규 종목은 무시 (meta sync에서 등록되어야 함).
    이는 같은 티커가 잘못된 시장으로 중복 INSERT되는 것을 방지한다 — 시장 정보는
    yfinance(meta sync)가 권위 있는 출처.
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
        return {"updated": 0, "missing": missing,
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

    duration = (datetime.now(KST) - started).total_seconds()
    _log.info(f"US 가격 동기화 완료: {len(rows)}건 / 실패 {missing}건 / {duration:.1f}s")
    return {"updated": len(rows), "missing": missing, "duration_sec": duration}


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
        description="Stock Universe 동기화 (Phase 1a KRX + Phase 1b US: S&P500/Nasdaq100)"
    )
    p.add_argument("--mode", choices=("meta", "price", "auto"), default="auto",
                   help="meta: 주간 메타/시총/업종 | price: 일별 가격 | auto: stale 판별 후 자동 결정")
    p.add_argument("--market",
                   choices=("KOSPI", "KOSDAQ", "KRX", "US", "SP500", "NDX", "ALL"),
                   default="ALL",
                   help=("동기화할 시장 (기본: ALL=KRX+US). "
                         "KRX=KOSPI+KOSDAQ | US=SP500+NDX100 | SP500/NDX=US 부분 인덱스"))
    p.add_argument("--no-mark-unlisted", action="store_true",
                   help="meta 모드에서 상장폐지 추정 종목을 listed=FALSE로 마킹하지 않음 (디버깅용)")
    p.add_argument("--init-db", action="store_true",
                   help="실행 전 init_db() 호출 - 신규 환경에서 마이그레이션 적용")
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

    if not krx_markets and not us_filter:
        _log.warning(
            f"동기화 대상이 없습니다 (--market={args.market}, "
            f"krx_enabled={cfg.universe.krx_enabled}, us_enabled={cfg.universe.us_enabled})"
        )
        return 0

    result: dict = {"krx": None, "us": None}

    # KRX
    if krx_markets:
        if args.mode == "meta":
            result["krx"] = sync_meta_krx(cfg.db, markets=krx_markets,
                                          mark_unlisted=not args.no_mark_unlisted)
        elif args.mode == "price":
            result["krx"] = sync_prices_krx(cfg.db, markets=krx_markets)
        else:
            if _meta_is_stale(cfg.db, markets=("KOSPI", "KOSDAQ"),
                              max_age_days=cfg.universe.meta_stale_days):
                _log.info("[KRX] 메타 stale — meta 동기화")
                result["krx"] = {"mode": "meta", "data": sync_meta_krx(cfg.db, markets=krx_markets)}
            else:
                _log.info("[KRX] 메타 신선 — price만")
                result["krx"] = {"mode": "price", "data": sync_prices_krx(cfg.db, markets=krx_markets)}

    # US
    if us_filter:
        index_filter = None if us_filter == "ALL_US" else us_filter
        if args.mode == "meta":
            result["us"] = sync_meta_us(cfg.db, index_filter=index_filter)
        elif args.mode == "price":
            result["us"] = sync_prices_us(cfg.db, index_filter=index_filter)
        else:
            if _meta_is_stale(cfg.db, markets=("NASDAQ", "NYSE"),
                              max_age_days=cfg.universe.meta_stale_days):
                _log.info("[US] 메타 stale — meta 동기화 (yfinance, ~5분 예상)")
                result["us"] = {"mode": "meta", "data": sync_meta_us(cfg.db, index_filter=index_filter)}
            else:
                _log.info("[US] 메타 신선 — price만")
                result["us"] = {"mode": "price", "data": sync_prices_us(cfg.db, index_filter=index_filter)}

    _log.info(f"결과: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
