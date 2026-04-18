"""구독 티어별 한도 정의 — 프론트엔드·백엔드 공통 소스 오브 트루스.

참고: _docs/20260417_business_review_subscription.md §2.1 요금제 비교표
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

# ── 티어 식별자 ────────────────────────────────────────────
TIER_FREE = "free"
TIER_PRO = "pro"
TIER_PREMIUM = "premium"
VALID_TIERS = (TIER_FREE, TIER_PRO, TIER_PREMIUM)

# ── 한도 상수 (None = 무제한) ────────────────────────────
WATCHLIST_LIMITS: Dict[str, Optional[int]] = {
    TIER_FREE: 5,
    TIER_PRO: 30,
    TIER_PREMIUM: None,
}

SUBSCRIPTION_LIMITS: Dict[str, Optional[int]] = {
    TIER_FREE: 3,
    TIER_PRO: 30,
    TIER_PREMIUM: None,
}

STAGE2_DAILY_LIMITS: Dict[str, Optional[int]] = {
    TIER_FREE: 1,
    TIER_PRO: 5,
    TIER_PREMIUM: None,
}

CHAT_DAILY_TURNS: Dict[str, Optional[int]] = {
    TIER_FREE: 0,     # 채팅 자체 차단
    TIER_PRO: 10,
    TIER_PREMIUM: None,
}

HISTORY_DAYS_LIMITS: Dict[str, Optional[int]] = {
    TIER_FREE: 7,
    TIER_PRO: 90,
    TIER_PREMIUM: None,
}

EDU_CHAT_DAILY_TURNS: Dict[str, Optional[int]] = {
    TIER_FREE: 5,     # 무료도 교육 채팅은 허용 (일 5턴)
    TIER_PRO: 20,
    TIER_PREMIUM: None,
}

THEME_VIEW_LIMITS: Dict[str, Optional[int]] = {
    # Free는 당일 테마 2건까지만 상세 열람 가능
    TIER_FREE: 2,
    TIER_PRO: None,
    TIER_PREMIUM: None,
}


@dataclass(frozen=True)
class TierInfo:
    """티어의 표기용 메타데이터 (UI 자연어·색상 매핑)."""
    key: str
    label_ko: str
    label_en: str
    price_krw_monthly: int
    badge_color: str  # CSS 클래스 suffix (badge-plan-{color})


TIER_INFO: Dict[str, TierInfo] = {
    TIER_FREE: TierInfo(TIER_FREE, "무료", "Free", 0, "free"),
    TIER_PRO: TierInfo(TIER_PRO, "프로", "Pro", 9_900, "pro"),
    TIER_PREMIUM: TierInfo(TIER_PREMIUM, "프리미엄", "Premium", 29_900, "premium"),
}


def normalize_tier(tier: Optional[str]) -> str:
    """잘못된 값이나 None은 free로 강등."""
    if tier not in VALID_TIERS:
        return TIER_FREE
    return tier  # type: ignore[return-value]


def get_watchlist_limit(tier: str) -> Optional[int]:
    return WATCHLIST_LIMITS.get(normalize_tier(tier), 5)


def get_subscription_limit(tier: str) -> Optional[int]:
    return SUBSCRIPTION_LIMITS.get(normalize_tier(tier), 3)


def get_stage2_daily_limit(tier: str) -> Optional[int]:
    return STAGE2_DAILY_LIMITS.get(normalize_tier(tier), 1)


def get_chat_daily_limit(tier: str) -> Optional[int]:
    return CHAT_DAILY_TURNS.get(normalize_tier(tier), 0)


def get_history_days_limit(tier: str) -> Optional[int]:
    return HISTORY_DAYS_LIMITS.get(normalize_tier(tier), 7)


def get_edu_chat_daily_limit(tier: str) -> Optional[int]:
    return EDU_CHAT_DAILY_TURNS.get(normalize_tier(tier), 5)


def is_unlimited(limit: Optional[int]) -> bool:
    return limit is None
