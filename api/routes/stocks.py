"""종목 기초정보 조회 API + 종목 페이지"""
from fastapi import APIRouter, Depends, HTTPException, Query

from analyzer.stock_data import fetch_fundamentals
from api.auth.dependencies import get_current_user_required
from api.auth.models import UserInDB
from api.templates_provider import templates
from api.deps import make_page_ctx

router = APIRouter(prefix="/api/stocks", tags=["종목 기초정보"])

pages_router = APIRouter(prefix="/pages/stocks", tags=["종목 페이지"])


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
