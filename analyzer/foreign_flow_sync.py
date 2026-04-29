"""KRX 투자자별 수급 PIT 시계열 수집 (외국인 + 기관 + 개인).

매일 KST 06:40 systemd timer 가 호출. 1일 sync 또는 N일 백필 모두 지원.
v1 UI 는 외국인 컬럼만 노출, 기관/개인은 데이터 레이어에만 보존 (재백필 회피).

Spec: docs/superpowers/specs/2026-04-30-foreign-flow-screener-design.md
"""
from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from psycopg2.extras import execute_values

from shared.config import DatabaseConfig, ForeignFlowConfig
from shared.db import get_connection
from shared.logger import get_logger
from analyzer.stock_data import _check_pykrx, _safe_pykrx_call

try:
    from pykrx import stock as pykrx_stock
except ImportError:
    pykrx_stock = None


_log = get_logger("foreign_flow_sync")
KST = timezone(timedelta(hours=9))

_KR_MARKETS = ("KOSPI", "KOSDAQ")


def _today_kst() -> date:
    return datetime.now(KST).date()


def _pick_column(df, *candidates: str) -> Optional[str]:
    """DataFrame 에서 후보 이름 중 첫 매칭 컬럼을 반환. 없으면 None."""
    for c in candidates:
        if c in df.columns:
            return c
    # 부분 매칭 (예: "외국인합계" vs "외국인" only)
    # 주의: pykrx 가 다른 "외국인*" 컬럼을 동시 반환하면 첫 매칭이 정확치 않을 수 있음.
    # 현재 pykrx API 는 "외국인합계" 만 반환 → 안전.
    for c in df.columns:
        s = str(c)
        for cand in candidates:
            if cand in s:
                return c
    return None


def _to_int_or_none(v) -> Optional[int]:
    try:
        if v is None:
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def _to_float_or_none(v) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _pd_to_date(idx) -> Optional[date]:
    """pandas Timestamp / datetime / str → date. 실패 시 None."""
    try:
        if hasattr(idx, "date"):
            return idx.date()
        return datetime.strptime(str(idx)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def fetch_kr_investor_flow(
    ticker: str, start_date: date, end_date: date
) -> list[dict]:
    """단일 KRX 종목 N일 일괄 수집.

    pykrx 두 API 호출:
      - get_exhaustion_rates_of_foreign_investment(start, end, ticker) → ownership_pct
      - get_market_trading_value_by_date(start, end, ticker) → foreign/inst/retail net_buy

    영업일별 row 생성. 한 API 만 성공해도 다른 컬럼은 NULL 로 row 보존.
    한 API 도 성공 못 하면 빈 리스트.

    Args:
        ticker: KRX 6자리 종목코드 (비-숫자면 빈 리스트 반환).
        start_date, end_date: 수집 범위 (inclusive).

    Returns:
        [{ticker, snapshot_date, foreign_ownership_pct,
          foreign_net_buy_value, inst_net_buy_value, retail_net_buy_value,
          data_source}, ...]
        market 은 호출자가 채움 (이 함수에선 미설정).
    """
    if not _check_pykrx():
        return []

    raw = ticker.strip().upper()
    if not raw.isdigit():
        return []

    start_str = start_date.strftime("%Y%m%d")
    end_str = end_date.strftime("%Y%m%d")

    # 1) 외국인 보유율 시계열
    own_df = _safe_pykrx_call(
        pykrx_stock.get_exhaustion_rates_of_foreign_investment,
        start_str, end_str, raw,
    )

    # 2) 투자자별 거래대금 (외국인/기관/개인 같이 옴)
    tv_df = _safe_pykrx_call(
        pykrx_stock.get_market_trading_value_by_date,
        start_str, end_str, raw,
    )

    own_empty = own_df is None or own_df.empty
    tv_empty = tv_df is None or tv_df.empty
    if own_empty and tv_empty:
        return []

    # 컬럼 매핑
    own_col = None if own_empty else _pick_column(own_df, "지분율", "보유비중")
    f_col = None if tv_empty else _pick_column(tv_df, "외국인합계", "외국인")
    i_col = None if tv_empty else _pick_column(tv_df, "기관합계", "기관")
    r_col = None if tv_empty else _pick_column(tv_df, "개인")

    # snapshot_date set 합집합 — tz-naive date 로 정규화
    own_dates: set[date] = set()
    tv_dates: set[date] = set()
    if not own_empty and own_col:
        own_dates = {_pd_to_date(idx) for idx in own_df.index}
        own_dates.discard(None)
    if not tv_empty and f_col:
        tv_dates = {_pd_to_date(idx) for idx in tv_df.index}
        tv_dates.discard(None)
    all_dates = sorted(own_dates | tv_dates)

    if not all_dates:
        return []

    import pandas as pd

    # 인덱스를 date → tz-naive normalize 형태로 재맵핑 (조회 편의)
    own_index_map: dict[date, int] = {}
    if not own_empty and own_col:
        for pos, idx in enumerate(own_df.index):
            d = _pd_to_date(idx)
            if d is not None:
                own_index_map[d] = pos

    tv_index_map: dict[date, int] = {}
    if not tv_empty:
        for pos, idx in enumerate(tv_df.index):
            d = _pd_to_date(idx)
            if d is not None:
                tv_index_map[d] = pos

    rows: list[dict] = []
    for d in all_dates:
        own_val: Optional[float] = None
        if d in own_index_map and own_col:
            try:
                own_val = _to_float_or_none(own_df.iloc[own_index_map[d]][own_col])
            except Exception:
                own_val = None

        f_val = i_val = r_val = None
        if d in tv_index_map:
            try:
                row = tv_df.iloc[tv_index_map[d]]
                f_val = _to_int_or_none(row[f_col]) if f_col else None
                i_val = _to_int_or_none(row[i_col]) if i_col else None
                r_val = _to_int_or_none(row[r_col]) if r_col else None
            except Exception:
                pass

        # 모든 컬럼 None 이면 row 건너뜀 (의미 없음)
        if own_val is None and f_val is None and i_val is None and r_val is None:
            continue

        rows.append({
            "ticker": raw,
            "snapshot_date": d,
            "foreign_ownership_pct": own_val,
            "foreign_net_buy_value": f_val,
            "inst_net_buy_value": i_val,
            "retail_net_buy_value": r_val,
            "data_source": "pykrx",
        })
    return rows


# ─── UPSERT SQL ────────────────────────────────────────────────────────────────

_UPSERT_SQL = """
INSERT INTO stock_universe_foreign_flow (
    ticker, market, snapshot_date,
    foreign_ownership_pct, foreign_net_buy_value,
    inst_net_buy_value, retail_net_buy_value,
    data_source
) VALUES %s
ON CONFLICT (ticker, market, snapshot_date) DO UPDATE SET
    foreign_ownership_pct = EXCLUDED.foreign_ownership_pct,
    foreign_net_buy_value = EXCLUDED.foreign_net_buy_value,
    inst_net_buy_value    = EXCLUDED.inst_net_buy_value,
    retail_net_buy_value  = EXCLUDED.retail_net_buy_value,
    data_source           = EXCLUDED.data_source,
    fetched_at            = NOW()
"""


def upsert_investor_flow(cur, rows: list[dict]) -> None:
    """일괄 UPSERT. 빈 리스트는 no-op."""
    if not rows:
        return
    values = [
        (
            r["ticker"], r["market"], r["snapshot_date"],
            r.get("foreign_ownership_pct"),
            r.get("foreign_net_buy_value"),
            r.get("inst_net_buy_value"),
            r.get("retail_net_buy_value"),
            r.get("data_source") or "pykrx",
        )
        for r in rows
    ]
    execute_values(cur, _UPSERT_SQL, values, page_size=500)


def sync_market_investor_flow(
    cur,
    market: str,
    tickers: list[str],
    start_date: date,
    end_date: date,
    *,
    max_workers: int = 4,
    max_consecutive_failures: int = 0,
) -> int:
    """단일 시장 일괄 sync. 병렬 fetch → 한꺼번에 UPSERT.

    Returns: UPSERT 된 row 수 (= 성공 종목별 영업일 수 합).
    """
    started = time.time()
    market_up = market.upper()
    if market_up not in _KR_MARKETS:
        _log.warning(f"[{market}] KRX 외 시장 — skip")
        return 0

    all_rows: list[dict] = []
    consecutive_failures = 0
    aborted = False

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {
            pool.submit(fetch_kr_investor_flow, t, start_date, end_date): t
            for t in tickers
        }
        for fut in as_completed(future_map):
            t = future_map[fut]
            try:
                rows = fut.result() or []
            except Exception as e:
                _log.debug(f"[{t}] fetch 예외: {e}")
                rows = []
            if not rows:
                consecutive_failures += 1
                if max_consecutive_failures > 0 and consecutive_failures >= max_consecutive_failures:
                    _log.warning(
                        f"[{market_up}] 연속 {max_consecutive_failures}건 실패 — 조기 종료"
                    )
                    aborted = True
                    break
                continue
            consecutive_failures = 0
            for r in rows:
                r["market"] = market_up
                all_rows.append(r)

    upsert_investor_flow(cur, all_rows)
    duration = time.time() - started
    abort_marker = " (early-abort)" if aborted else ""
    success_tickers = len({r["ticker"] for r in all_rows})
    _log.info(
        f"[{market_up}] {start_date}~{end_date} 수급 sync — "
        f"{len(all_rows)} row / {success_tickers}/{len(tickers)} 종목{abort_marker} / {duration:.1f}s"
    )
    return len(all_rows)


def run_foreign_flow_sync(
    db_cfg: DatabaseConfig,
    *,
    cfg: Optional[ForeignFlowConfig] = None,
    snapshot_date: Optional[date] = None,
    markets: tuple[str, ...] = _KR_MARKETS,
    backfill_days: int = 0,
) -> dict:
    """엔트리포인트. `stock_universe` 활성 KRX 종목 일괄 sync.

    Args:
        snapshot_date: 종료일 기준. None 이면 오늘 (KST).
        backfill_days: 0=종료일 1일만, N>0=종료일 기준 과거 N일 일괄.
        markets: KOSPI/KOSDAQ 만 지원 (다른 시장은 자연 skip).

    Returns:
        {"start_date", "end_date", "by_market": {KOSPI: int, ...}, "total": int}
    """
    cfg = cfg or ForeignFlowConfig()
    if not cfg.sync_enabled:
        _log.info("FOREIGN_FLOW_SYNC_ENABLED=false — skip")
        return {"start_date": None, "end_date": None, "by_market": {}, "total": 0}

    end_d = snapshot_date or _today_kst()
    start_d = end_d - timedelta(days=backfill_days) if backfill_days > 0 else end_d

    conn = get_connection(db_cfg)
    by_market: dict[str, int] = {}
    total = 0
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ticker, market FROM stock_universe
                WHERE listed = TRUE AND has_preferred = FALSE
                  AND market = ANY(%s)
            """, (list(markets),))
            rows = cur.fetchall()
        grouped: dict[str, list[str]] = {}
        for ticker, market in rows:
            grouped.setdefault(market.upper(), []).append(ticker)

        for market, tickers in grouped.items():
            with conn.cursor() as cur:
                n = sync_market_investor_flow(
                    cur, market, tickers, start_d, end_d,
                    max_consecutive_failures=cfg.max_consecutive_failures,
                )
                conn.commit()
            by_market[market] = n
            total += n
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    _log.info(
        f"foreign_flow sync 완료 — 총 {total} row "
        f"(범위 {start_d}~{end_d}, by_market={by_market})"
    )
    return {
        "start_date": start_d,
        "end_date": end_d,
        "by_market": by_market,
        "total": total,
    }
