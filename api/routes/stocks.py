"""종목 기초정보 조회 API + 종목 페이지"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.templating import Jinja2Templates

from analyzer.stock_data import fetch_fundamentals
from api.auth.dependencies import _get_auth_cfg, get_current_user
from api.auth.models import UserInDB
from api.page_context import base_ctx as _base_ctx
from api.template_filters import register as _register_filters
from shared.config import AuthConfig

router = APIRouter(prefix="/api/stocks", tags=["종목 기초정보"])

pages_router = APIRouter(prefix="/pages/stocks", tags=["종목 페이지"])

templates = Jinja2Templates(directory="api/templates")
_register_filters(templates.env)


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
    request: Request,
    ticker: str,
    market: str = Query(default="", description="시장 코드"),
    user: Optional[UserInDB] = Depends(get_current_user),
    auth_cfg: AuthConfig = Depends(_get_auth_cfg),
):
    """종목 기초정보 페이지 — 온디맨드 yfinance 조회"""
    ctx = _base_ctx(request, "proposals", user, auth_cfg)
    return templates.TemplateResponse(request=request, name="stock_fundamentals.html", context={
        **ctx,
        "ticker": ticker.upper(),
        "market": market.upper(),
    })
