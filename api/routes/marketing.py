"""마케팅/가격 페이지 라우트 — B1: pages.py에서 이전."""
from typing import Optional

from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

from shared.config import AuthConfig
from shared.tier_limits import (
    TIER_INFO,
    WATCHLIST_LIMITS,
    SUBSCRIPTION_LIMITS,
    STAGE2_DAILY_LIMITS,
    CHAT_DAILY_TURNS,
    HISTORY_DAYS_LIMITS,
)
from api.page_context import base_ctx as _base_ctx
from api.template_filters import register as _register_filters
from api.auth.dependencies import get_current_user, _get_auth_cfg
from api.auth.models import UserInDB

pages_router = APIRouter(tags=["마케팅 페이지"])
templates = Jinja2Templates(directory="api/templates")
_register_filters(templates.env)


@pages_router.get("/pages/landing")
def landing_page(request: Request, user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    """비로그인 공개 랜딩 페이지 — Hero + 핵심 기능 + 트랙레코드 미리보기 + 요금제 티저.

    인증 활성 환경에서 미로그인 사용자가 `/`에 접근하면 이 페이지로 리다이렉트된다.
    """
    ctx = _base_ctx(request, "landing", user, auth_cfg)
    return templates.TemplateResponse(request=request, name="landing.html", context=ctx)


@pages_router.get("/pages/pricing")
def pricing_page(request: Request, user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    """요금제 비교 페이지 — 정적 콘텐츠 + 티어 상수 렌더."""
    ctx = _base_ctx(request, "pricing", user, auth_cfg)

    def _display(val):
        return "무제한" if val is None else str(val)

    tier_cards = []
    for key in ("free", "pro", "premium"):
        info = TIER_INFO[key]
        tier_cards.append({
            "key": key,
            "label_ko": info.label_ko,
            "label_en": info.label_en,
            "price_krw_monthly": info.price_krw_monthly,
            "badge_color": info.badge_color,
            "watchlist_limit": _display(WATCHLIST_LIMITS.get(key)),
            "subscription_limit": _display(SUBSCRIPTION_LIMITS.get(key)),
            "stage2_daily_limit": _display(STAGE2_DAILY_LIMITS.get(key)),
            "chat_daily_limit": _display(CHAT_DAILY_TURNS.get(key)),
            "history_days_limit": _display(HISTORY_DAYS_LIMITS.get(key)),
        })

    return templates.TemplateResponse(request=request, name="pricing.html", context={
        **ctx,
        "tier_cards": tier_cards,
    })
