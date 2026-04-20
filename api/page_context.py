"""페이지 라우트용 공통 템플릿 컨텍스트 빌더 — pages.py에서 추출 (B1).

모든 HTML 페이지 라우트가 호출해 base.html에 필요한 공통 변수를 채운다.
"""
from typing import Optional

from fastapi import Request

from shared.config import AuthConfig
from shared.tier_limits import (
    TIER_INFO,
    get_watchlist_limit,
    get_subscription_limit,
    get_chat_daily_limit,
)
from api.auth.models import UserInDB


def base_ctx(
    request: Request,
    active_page: str,
    user: Optional[UserInDB],
    auth_cfg: AuthConfig,
    conn=None,
) -> dict:
    """모든 템플릿에 공통으로 전달할 컨텍스트.

    tier 정보와 사용량/한도는 업그레이드 CTA/사용량 배지 표시에 쓰인다.

    conn: 재사용할 DB 연결. None이면 사용량 조회 스킵 (비로그인 또는 dep 외 호출).
    """
    effective_tier = user.effective_tier() if user else "free"
    tier_info = TIER_INFO.get(effective_tier)

    ctx = {
        "request": request,
        "active_page": active_page,
        "current_user": user,
        "auth_enabled": auth_cfg.enabled,
        "unread_notifications": 0,
        "tier": effective_tier,
        "tier_label": tier_info.label_ko if tier_info else None,
        "tier_badge_color": tier_info.badge_color if tier_info else "free",
        "watchlist_limit": get_watchlist_limit(effective_tier),
        "subscription_limit": get_subscription_limit(effective_tier),
        "chat_daily_limit": get_chat_daily_limit(effective_tier),
        "watchlist_usage": 0,
        "subscription_usage": 0,
    }
    if user and auth_cfg.enabled and conn is not None:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 'noti'   AS k, COUNT(*) FROM user_notifications
                        WHERE user_id = %s AND is_read = FALSE
                    UNION ALL
                    SELECT 'watch'  AS k, COUNT(*) FROM user_watchlist
                        WHERE user_id = %s
                    UNION ALL
                    SELECT 'sub'    AS k, COUNT(*) FROM user_subscriptions
                        WHERE user_id = %s
                    """,
                    (user.id, user.id, user.id),
                )
                for key, cnt in cur.fetchall():
                    if key == "noti":
                        ctx["unread_notifications"] = cnt
                    elif key == "watch":
                        ctx["watchlist_usage"] = cnt
                    elif key == "sub":
                        ctx["subscription_usage"] = cnt
        except Exception as e:
            print(f"[page_context.base_ctx] 사용량 조회 실패 (user_id={user.id}): {e}")
    return ctx
