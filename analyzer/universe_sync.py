"""Stock Universe 동기화 — Phase 1a (KRX 전용).

`stock_universe` 테이블을 pykrx 배치 API로 갱신한다.

- meta 모드 (주간): 종목 리스트·종목명·업종·시총·상장상태 — 느린 데이터
- price 모드 (일별): last_price·last_price_at — 빠른 데이터
- auto 모드: 마지막 meta_synced_at이 7일 초과 시 meta 포함, 그 외 price만

CLI:
    python -m analyzer.universe_sync --mode price
    python -m analyzer.universe_sync --mode meta --market KOSPI
    python -m analyzer.universe_sync --mode auto

설계 참조: _docs/20260422172248_recommendation-engine-redesign.md §1.3
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta, timezone

from psycopg2.extras import execute_values

from shared.config import AppConfig, DatabaseConfig
from shared.db import get_connection, init_db
from shared.logger import get_logger
from shared.sector_mapping import market_cap_bucket, normalize_sector

try:
    from pykrx import stock as pykrx_stock
except ImportError:
    pykrx_stock = None


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


def _meta_is_stale(db_cfg: DatabaseConfig, max_age_days: int = 7) -> bool:
    """가장 최신 meta_synced_at이 max_age_days 초과면 True. 데이터 없으면 True."""
    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT MAX(meta_synced_at) FROM stock_universe
                WHERE market IN ('KOSPI', 'KOSDAQ')
            """)
            row = cur.fetchone()
            last = row[0] if row else None
    finally:
        conn.close()
    if last is None:
        return True
    age = (datetime.now(KST) - last).days
    return age > max_age_days


def sync_auto(db_cfg: DatabaseConfig) -> dict:
    """자동 모드: meta가 7일 초과 stale이면 meta+price, 아니면 price만."""
    if _meta_is_stale(db_cfg):
        _log.info("메타가 7일 이상 stale — meta + price 동기화 실행")
        meta = sync_meta_krx(db_cfg)
        return {"mode": "meta+price", "meta": meta, "price": None}
    else:
        _log.info("메타 신선 — price만 동기화")
        price = sync_prices_krx(db_cfg)
        return {"mode": "price", "meta": None, "price": price}


# ── CLI ─────────────────────────────────────────

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Stock Universe 동기화 (Phase 1a - KRX 전용)"
    )
    p.add_argument("--mode", choices=("meta", "price", "auto"), default="auto",
                   help="meta: 주간 메타/시총/업종 | price: 일별 가격 | auto: stale 판별 후 자동 결정")
    p.add_argument("--market", choices=("KOSPI", "KOSDAQ", "ALL"), default="ALL",
                   help="동기화할 시장 (기본: ALL)")
    p.add_argument("--no-mark-unlisted", action="store_true",
                   help="meta 모드에서 상장폐지 추정 종목을 listed=FALSE로 마킹하지 않음 (디버깅용)")
    p.add_argument("--init-db", action="store_true",
                   help="실행 전 init_db() 호출 - 신규 환경에서 마이그레이션 적용")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    cfg = AppConfig()

    if args.init_db:
        init_db(cfg.db)

    markets: tuple[str, ...]
    if args.market == "ALL":
        markets = _MARKET_LABELS
    else:
        markets = (args.market,)

    if args.mode == "meta":
        result = sync_meta_krx(cfg.db, markets=markets, mark_unlisted=not args.no_mark_unlisted)
    elif args.mode == "price":
        result = sync_prices_krx(cfg.db, markets=markets)
    else:
        result = sync_auto(cfg.db)

    _log.info(f"결과: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
