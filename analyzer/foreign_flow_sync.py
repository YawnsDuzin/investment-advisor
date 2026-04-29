"""KRX 투자자별 수급 PIT 시계열 수집 (외국인 + 기관 + 개인).

매일 KST 06:40 systemd timer 가 호출. 1일 sync 또는 N일 백필 모두 지원.
v1 UI 는 외국인 컬럼만 노출, 기관/개인은 데이터 레이어에만 보존 (재백필 회피).

Spec: docs/superpowers/specs/2026-04-30-foreign-flow-screener-design.md
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Optional

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
