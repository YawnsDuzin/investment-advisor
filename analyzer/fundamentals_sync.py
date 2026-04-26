"""펀더멘털 PIT 시계열 수집 (B-Lite — pykrx KR + yfinance.info US).

매일 sync로 `stock_universe_fundamentals` 에 일별 row 누적. 결측 종목은 skip
(NULL row 기록하지 않음 — IS NOT NULL 필터로 latest 조회 단순화).

Spec: docs/superpowers/specs/2026-04-26-screener-investor-strategies-design.md §3
"""
from __future__ import annotations

import math
from datetime import date
from typing import Optional

from psycopg2.extras import execute_values

from shared.logger import get_logger

try:
    from pykrx import stock as pykrx_stock
except ImportError:
    pykrx_stock = None

try:
    import yfinance as yf
except ImportError:
    yf = None

from analyzer.stock_data import _check_pykrx, _is_login_failure, _disable_pykrx

_log = get_logger("fundamentals_sync")


def _to_float(v) -> Optional[float]:
    """NaN/None/이상값 안전 변환."""
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def fetch_kr_fundamental(ticker: str, snapshot_date: date) -> Optional[dict]:
    """KRX 종목 단일 펀더 조회 (pykrx).

    배치 모드 안전성을 위해 analyzer.stock_data의 shared guard 사용 — 인증 실패 시
    세션 단위로 short-circuit되어 매 호출 round-trip 방지.

    Returns:
        {"per", "pbr", "eps", "bps", "dps", "dividend_yield", "data_source"}
        또는 None (조회 실패 / 빈 DataFrame).
    """
    if not _check_pykrx():
        return None

    yyyymmdd = snapshot_date.strftime("%Y%m%d")
    try:
        df = pykrx_stock.get_market_fundamental_by_date(yyyymmdd, yyyymmdd, ticker)
    except Exception as e:
        if _is_login_failure(e):
            _disable_pykrx(f"[{ticker}] 펀더 조회 중 인증 오류: {str(e)[:100]}")
        else:
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


def fetch_us_fundamental(ticker: str) -> Optional[dict]:
    """US 종목 단일 펀더 조회 (yfinance.info — '현재 스냅샷').

    매일 호출 시 그날 값을 누적하여 일별 PIT 구성. yfinance dividendYield는 ratio
    (0.0058 = 0.58%)로 반환되므로 표시 단위(%)로 정규화하여 저장.

    Returns:
        {"per", "pbr", "eps", "bps", "dps", "dividend_yield", "data_source"}
        또는 None (예외/빈 info).
    """
    if yf is None:
        _log.warning("yfinance 미설치 — US 펀더 sync 불가")
        return None
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception as e:
        _log.debug(f"[{ticker}] yfinance 조회 실패: {e}")
        return None
    if not info:
        return None

    div_yield_ratio = _to_float(info.get("dividendYield"))
    div_yield_pct = (div_yield_ratio * 100) if div_yield_ratio is not None else None

    out = {
        "per":            _to_float(info.get("trailingPE")),
        "pbr":            _to_float(info.get("priceToBook")),
        "eps":            _to_float(info.get("trailingEps")),
        "bps":            _to_float(info.get("bookValue")),
        "dps":            _to_float(info.get("dividendRate")),
        "dividend_yield": div_yield_pct,
        "data_source":    "yfinance_info",
    }
    # 모든 메트릭이 None이면 수집할 가치 없음 (사실상 빈 응답)
    if all(out[k] is None for k in ("per", "pbr", "eps", "bps", "dps", "dividend_yield")):
        return None
    return out


_UPSERT_SQL = """
INSERT INTO stock_universe_fundamentals (
    ticker, market, snapshot_date,
    per, pbr, eps, bps, dps, dividend_yield,
    data_source
) VALUES %s
ON CONFLICT (ticker, market, snapshot_date) DO UPDATE SET
    per            = EXCLUDED.per,
    pbr            = EXCLUDED.pbr,
    eps            = EXCLUDED.eps,
    bps            = EXCLUDED.bps,
    dps            = EXCLUDED.dps,
    dividend_yield = EXCLUDED.dividend_yield,
    data_source    = EXCLUDED.data_source,
    fetched_at     = NOW()
"""


_KR_MARKETS = {"KOSPI", "KOSDAQ", "KONEX"}
_US_MARKETS = {"NASDAQ", "NYSE", "AMEX"}


def sync_market_fundamentals(
    cur,
    market: str,
    tickers: list[str],
    snapshot_date: date,
) -> int:
    """단일 시장 일괄 sync. market에 따라 fetcher 자동 분기.

    Returns:
        UPSERT된 row 수 (결측 제외).
    """
    market_up = market.upper()
    if market_up in _KR_MARKETS:
        fetcher = lambda t: fetch_kr_fundamental(t, snapshot_date)
    elif market_up in _US_MARKETS:
        fetcher = fetch_us_fundamental
    else:
        _log.warning(f"[{market}] 지원하지 않는 시장 — skip")
        return 0

    rows: list[dict] = []
    for ticker in tickers:
        data = fetcher(ticker)
        if data is None:
            continue
        rows.append({
            **data,
            "ticker": ticker,
            "market": market_up,
            "snapshot_date": snapshot_date,
        })

    upsert_fundamentals(cur, rows)
    _log.info(f"[{market_up}] {snapshot_date} 펀더 sync — {len(rows)}/{len(tickers)} 종목")
    return len(rows)


def upsert_fundamentals(cur, rows: list[dict]) -> None:
    """일괄 UPSERT. 빈 리스트는 no-op.

    각 row는 fetch_*_fundamental 결과 + ticker/market/snapshot_date 합본 dict.
    동일 (ticker, market, snapshot_date)가 이미 존재하면 data_source 포함 전 필드 덮어씀
    (last-write-wins — 재실행 시 소스 변경 허용).

    Note:
        커밋은 호출자 책임. 이 함수는 commit/rollback을 호출하지 않는다.
    """
    if not rows:
        return
    values = [
        (
            r["ticker"], r["market"], r["snapshot_date"],
            r.get("per"), r.get("pbr"), r.get("eps"),
            r.get("bps"), r.get("dps"), r.get("dividend_yield"),
            r["data_source"],
        )
        for r in rows
    ]
    execute_values(cur, _UPSERT_SQL, values, page_size=500)
