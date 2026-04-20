"""마케팅/가격 페이지 라우트 — B1: pages.py에서 이전."""
from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse

from shared.tier_limits import (
    TIER_INFO,
    WATCHLIST_LIMITS,
    SUBSCRIPTION_LIMITS,
    STAGE2_DAILY_LIMITS,
    CHAT_DAILY_TURNS,
    HISTORY_DAYS_LIMITS,
)
from api.templates_provider import templates
from api.deps import make_page_ctx

pages_router = APIRouter(tags=["마케팅 페이지"])


@pages_router.get("/pages/landing")
def landing_page(ctx: dict = Depends(make_page_ctx("landing"))):
    """비로그인 공개 랜딩 페이지 — Hero + 핵심 기능 + 트랙레코드 미리보기 + 요금제 티저.

    인증 활성 환경에서 미로그인 사용자가 `/`에 접근하면 이 페이지로 리다이렉트된다.
    """
    return templates.TemplateResponse(request=ctx["request"], name="landing.html", context=ctx)


@pages_router.get("/pages/pricing")
def pricing_page(ctx: dict = Depends(make_page_ctx("pricing"))):
    """요금제 비교 페이지 — 정적 콘텐츠 + 티어 상수 렌더."""
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

    return templates.TemplateResponse(request=ctx["request"], name="pricing.html", context={
        **ctx,
        "tier_cards": tier_cards,
    })
