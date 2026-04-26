"""펀더멘털 PIT 시계열 수집 (B-Lite — pykrx KR + yfinance.info US).

매일 sync로 `stock_universe_fundamentals` 에 일별 row 누적. 결측 종목은 skip
(NULL row 기록하지 않음 — IS NOT NULL 필터로 latest 조회 단순화).

Spec: docs/superpowers/specs/2026-04-26-screener-investor-strategies-design.md §3
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from shared.logger import get_logger

try:
    from pykrx import stock as pykrx_stock
except ImportError:
    pykrx_stock = None

try:
    import yfinance as yf
except ImportError:
    yf = None


_log = get_logger("fundamentals_sync")


def _to_float(v) -> Optional[float]:
    """NaN/None/이상값 안전 변환."""
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def fetch_kr_fundamental(ticker: str, snapshot_date: date) -> Optional[dict]:
    """KRX 종목 단일 펀더 조회 (pykrx).

    Returns:
        {"per", "pbr", "eps", "bps", "dps", "dividend_yield", "data_source"}
        또는 None (조회 실패 / 빈 DataFrame).
    """
    if pykrx_stock is None:
        _log.warning("pykrx 미설치 — KR 펀더 sync 불가")
        return None

    yyyymmdd = snapshot_date.strftime("%Y%m%d")
    try:
        df = pykrx_stock.get_market_fundamental_by_date(yyyymmdd, yyyymmdd, ticker)
    except Exception as e:
        _log.debug(f"[{ticker}] pykrx 조회 실패: {e}")
        return None

    if df is None or df.empty:
        return None

    row = df.iloc[0]
    return {
        "per":            _to_float(row.get("PER")),
        "pbr":            _to_float(row.get("PBR")),
        "eps":            _to_float(row.get("EPS")),
        "bps":            _to_float(row.get("BPS")),
        "dps":            _to_float(row.get("DPS")),
        "dividend_yield": _to_float(row.get("DIV")),
        "data_source":    "pykrx",
    }
