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

# ── 프리미엄 스크리너 (로드맵 UI-6) ────────────────────
# 스크리너 실행은 모든 티어 허용. 프리셋 저장·공유는 Pro 이상.
SCREENER_PRESETS_MAX: Dict[str, Optional[int]] = {
    # Sprint 1 design.md §4.2 — Premium 도 50 cap (DB·UI 보호).
    # 기존 None(무제한) 에서 50 으로 조정 — 운영 시점에 Premium 사용자 0 명이라 영향 없음.
    TIER_FREE: 0,     # Free: 저장 불가 (공개 프리셋 read-only 이용만)
    TIER_PRO: 10,
    TIER_PREMIUM: 50,
}

SCREENER_RESULT_ROW_LIMIT: Dict[str, Optional[int]] = {
    # 일회 실행 시 최대 반환 행 수
    TIER_FREE: 50,
    TIER_PRO: 200,
    TIER_PREMIUM: 500,
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


# ── Sprint 1 신규 한도 (NL→SQL / Vision / Red Team / 스크리너 커스텀) ──

NL_SEARCH_DAILY: Dict[str, Optional[int]] = {
    TIER_FREE: 5,
    TIER_PRO: 50,
    TIER_PREMIUM: 500,
}

CHART_VISION_DAILY: Dict[str, Optional[int]] = {
    # 익명 1회 체험은 별도 처리 (CHART_VISION_ANON_LIMIT, shared/config.py)
    TIER_FREE: 0,
    TIER_PRO: 10,
    TIER_PREMIUM: 100,
}

# Sprint 1 design 의 SCREENER_CUSTOM_PRESETS 는 기존 SCREENER_PRESETS_MAX 와 동일 개념 —
# alias 유지 (helper API 호환). Premium 값은 SCREENER_PRESETS_MAX 정의를 따른다 (50).
SCREENER_CUSTOM_PRESETS: Dict[str, Optional[int]] = SCREENER_PRESETS_MAX

RED_TEAM_AVAILABLE: Dict[str, bool] = {
    TIER_FREE: False,
    TIER_PRO: False,
    TIER_PREMIUM: True,
}


def get_nl_search_daily_limit(tier: Optional[str]) -> Optional[int]:
    """자연어 → SQL 검색 일일 한도. None=무제한 (현재는 모든 티어 한도 있음)."""
    return NL_SEARCH_DAILY.get(normalize_tier(tier), 5)


def get_chart_vision_daily_limit(tier: Optional[str]) -> Optional[int]:
    """차트 Vision 일일 한도. Free=0 (익명 1회만 별도 허용)."""
    return CHART_VISION_DAILY.get(normalize_tier(tier), 0)


def get_screener_custom_presets_limit(tier: Optional[str]) -> Optional[int]:
    """스크리너 커스텀 프리셋 저장 한도. Free=0 (시드만 사용)."""
    return SCREENER_CUSTOM_PRESETS.get(normalize_tier(tier), 0)


def is_red_team_available(tier: Optional[str]) -> bool:
    """Bull/Bear Red Team 분석 가용 여부. Premium 한정."""
    return RED_TEAM_AVAILABLE.get(normalize_tier(tier), False)
