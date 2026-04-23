"""종목 기초정보 조회 API + 종목 페이지"""
from fastapi import APIRouter, Depends, HTTPException, Query

from analyzer.stock_data import fetch_fundamentals
from api.auth.dependencies import get_current_user_required
from api.auth.models import UserInDB
from api.templates_provider import templates
from api.deps import make_page_ctx
from shared.config import AppConfig
from shared.db import get_connection

router = APIRouter(prefix="/api/stocks", tags=["종목 기초정보"])

pages_router = APIRouter(prefix="/pages/stocks", tags=["종목 페이지"])

indices_router = APIRouter(prefix="/api/indices", tags=["벤치마크 지수"])


@router.get("/{ticker}/fundamentals")
def get_fundamentals(
    ticker: str,
    market: str = Query(default="", description="시장 코드 (KRX, NASDAQ 등)"),
):
    """종목 기초정보 온디맨드 조회 (yfinance 기반, 1시간 캐싱)"""
    data = fetch_fundamentals(ticker, market)
    if not data:
        raise HTTPException(
            status_code=404,
            detail=f"종목 '{ticker}' 데이터를 조회할 수 없습니다",
        )
    return data


@router.get("/{ticker}/ohlcv")
def get_stock_ohlcv(
    ticker: str,
    market: str = Query(default="", description="시장 코드 (KOSPI/KOSDAQ/NASDAQ/NYSE 등, 빈 값이면 무시)"),
    days: int = Query(default=200, ge=1, le=1000, description="최근 N일 (최대 1000)"),
):
    """종목 OHLCV 이력 조회 — stock_universe_ohlcv 기반.

    UI-1 미니 스파크라인·52주 게이지·거래량 추이 등에 사용.
    OHLCV가 아직 수집되지 않은 신규 종목은 404 대신 빈 배열 반환 (차트는 "데이터 없음" 표시).
    """
    tk = ticker.strip().upper()
    mk = (market or "").strip().upper()
    cfg = AppConfig()

    # market 옵션 따라 WHERE 분기
    if mk:
        sql = """
            SELECT trade_date::text, open::float, high::float, low::float,
                   close::float, volume, change_pct::float
            FROM stock_universe_ohlcv
            WHERE UPPER(ticker) = %s AND UPPER(market) = %s
              AND trade_date >= CURRENT_DATE - (%s::int)
            ORDER BY trade_date ASC
        """
        params = (tk, mk, int(days))
    else:
        sql = """
            SELECT trade_date::text, open::float, high::float, low::float,
                   close::float, volume, change_pct::float
            FROM stock_universe_ohlcv
            WHERE UPPER(ticker) = %s
              AND trade_date >= CURRENT_DATE - (%s::int)
            ORDER BY trade_date ASC
        """
        params = (tk, int(days))

    conn = get_connection(cfg.db)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    finally:
        conn.close()

    series = [
        {
            "date": r[0], "open": r[1], "high": r[2], "low": r[3],
            "close": r[4], "volume": r[5], "change_pct": r[6],
        }
        for r in rows
    ]

    # 52주 고/저 (있으면 함께 반환 — UI 게이지용)
    if series:
        closes = [p["close"] for p in series[-252:] if p["close"] is not None]
        high_52w = max(closes) if closes else None
        low_52w = min(closes) if closes else None
        latest = series[-1]
    else:
        high_52w = low_52w = None
        latest = None

    return {
        "ticker": tk,
        "market": mk or None,
        "days": days,
        "count": len(series),
        "high_52w": high_52w,
        "low_52w": low_52w,
        "latest": latest,
        "series": series,
    }


# ──────────────────────────────────────────────
# 벤치마크 지수 OHLCV (B2 market_indices_ohlcv)
# ──────────────────────────────────────────────
_ALLOWED_INDICES = ("KOSPI", "KOSDAQ", "SP500", "NDX100")


@indices_router.get("/{index_code}/ohlcv")
def get_index_ohlcv(
    index_code: str,
    days: int = Query(default=252, ge=1, le=1000, description="최근 N일 (최대 1000)"),
):
    """벤치마크 지수 OHLCV 이력.

    index_code: KOSPI / KOSDAQ / SP500 / NDX100
    UI-2 테마 상대성과·UI-3 대시보드 시장 추이 등에 사용.
    """
    code = index_code.strip().upper()
    if code not in _ALLOWED_INDICES:
        raise HTTPException(
            status_code=400,
            detail=f"index_code는 {_ALLOWED_INDICES} 중 하나여야 합니다 (받음: {code})",
        )

    cfg = AppConfig()
    sql = """
        SELECT trade_date::text, open::float, high::float, low::float,
               close::float, volume, change_pct::float
        FROM market_indices_ohlcv
        WHERE index_code = %s
          AND trade_date >= CURRENT_DATE - (%s::int)
        ORDER BY trade_date ASC
    """
    conn = get_connection(cfg.db)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (code, int(days)))
            rows = cur.fetchall()
    finally:
        conn.close()

    series = [
        {
            "date": r[0], "open": r[1], "high": r[2], "low": r[3],
            "close": r[4], "volume": r[5], "change_pct": r[6],
        }
        for r in rows
    ]
    latest = series[-1] if series else None
    return {
        "index_code": code,
        "days": days,
        "count": len(series),
        "latest": latest,
        "series": series,
    }


# ──────────────────────────────────────────────
# Stock Fundamentals Page (종목 기초정보 페이지)
# ──────────────────────────────────────────────
@pages_router.get("/{ticker}")
def stock_fundamentals_page(
    ticker: str,
    market: str = Query(default="", description="시장 코드"),
    ctx: dict = Depends(make_page_ctx("proposals")),
):
    """종목 기초정보 페이지 — 온디맨드 yfinance 조회"""
    return templates.TemplateResponse(request=ctx["request"], name="stock_fundamentals.html", context={
        **ctx,
        "ticker": ticker.upper(),
        "market": market.upper(),
    })
