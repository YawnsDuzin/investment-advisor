"""티어 한도·모델 단위 테스트 (v16, A-002, A-003)

외부 의존성은 conftest.py에서 mock — 순수 파이썬 로직 검증.
"""
from datetime import datetime, timedelta, timezone

import pytest


class TestTierLimits:
    """shared/tier_limits.py — 상수·헬퍼 검증"""

    def test_watchlist_limits_by_tier(self):
        from shared.tier_limits import get_watchlist_limit

        assert get_watchlist_limit("free") == 5
        assert get_watchlist_limit("pro") == 30
        assert get_watchlist_limit("premium") is None  # 무제한

    def test_chat_daily_limits(self):
        from shared.tier_limits import get_chat_daily_limit

        assert get_chat_daily_limit("free") == 0
        assert get_chat_daily_limit("pro") == 10
        assert get_chat_daily_limit("premium") is None

    def test_history_days_limits(self):
        from shared.tier_limits import get_history_days_limit

        assert get_history_days_limit("free") == 7
        assert get_history_days_limit("pro") == 90
        assert get_history_days_limit("premium") is None

    def test_subscription_and_stage2_helpers(self):
        from shared.tier_limits import get_subscription_limit, get_stage2_daily_limit

        assert get_subscription_limit("free") == 3
        assert get_subscription_limit("pro") == 30
        assert get_subscription_limit("premium") is None

        assert get_stage2_daily_limit("free") == 1
        assert get_stage2_daily_limit("pro") == 5
        assert get_stage2_daily_limit("premium") is None

    def test_helpers_normalize_invalid_tier(self):
        from shared.tier_limits import (
            get_subscription_limit, get_watchlist_limit, get_history_days_limit,
        )
        # 알 수 없는 티어 → free 기준 반환
        assert get_subscription_limit("platinum") == 3
        assert get_watchlist_limit(None) == 5
        assert get_history_days_limit("") == 7

    def test_normalize_tier_invalid_falls_back_to_free(self):
        from shared.tier_limits import normalize_tier

        assert normalize_tier("free") == "free"
        assert normalize_tier("pro") == "pro"
        assert normalize_tier("premium") == "premium"
        # 잘못된 값
        assert normalize_tier("platinum") == "free"
        assert normalize_tier(None) == "free"
        assert normalize_tier("") == "free"

    def test_is_unlimited_helper(self):
        from shared.tier_limits import is_unlimited

        assert is_unlimited(None) is True
        assert is_unlimited(0) is False
        assert is_unlimited(5) is False

    def test_tier_info_has_all_three_tiers(self):
        from shared.tier_limits import TIER_INFO

        assert set(TIER_INFO.keys()) == {"free", "pro", "premium"}
        assert TIER_INFO["pro"].price_krw_monthly == 9_900
        assert TIER_INFO["premium"].price_krw_monthly == 29_900
        assert TIER_INFO["free"].price_krw_monthly == 0


class TestUserEffectiveTier:
    """api/auth/models.py — UserInDB.effective_tier() 검증"""

    def _make_user(self, tier, expires_at=None):
        """UserInDB 생성 헬퍼"""
        from api.auth.models import UserInDB
        return UserInDB(
            id=1, email="t@t.com", nickname="t", role="user",
            tier=tier, tier_expires_at=expires_at,
            is_active=True, created_at=datetime.now(timezone.utc),
        )

    def test_free_tier_always_returns_free(self):
        u = self._make_user("free")
        assert u.effective_tier() == "free"

    def test_pro_tier_no_expiry_returns_pro(self):
        u = self._make_user("pro", expires_at=None)
        assert u.effective_tier() == "pro"

    def test_pro_tier_future_expiry_returns_pro(self):
        future = datetime.now(timezone.utc) + timedelta(days=30)
        u = self._make_user("pro", expires_at=future)
        assert u.effective_tier() == "pro"

    def test_pro_tier_past_expiry_returns_free(self):
        past = datetime.now(timezone.utc) - timedelta(days=1)
        u = self._make_user("pro", expires_at=past)
        assert u.effective_tier() == "free"

    def test_premium_tier_past_expiry_returns_free(self):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        u = self._make_user("premium", expires_at=past)
        assert u.effective_tier() == "free"


class TestQuotaExceededDetail:
    """api/auth/dependencies.py — 402 응답 표준 구조"""

    def test_basic_payload_shape(self):
        from api.auth.dependencies import quota_exceeded_detail

        d = quota_exceeded_detail(feature="watchlist", current_tier="free", usage=5, limit=5)
        assert d["error"] == "quota_exceeded"
        assert d["feature"] == "watchlist"
        assert d["current_tier"] == "free"
        assert d["usage"] == 5
        assert d["limit"] == 5
        assert d["upgrade_url"] == "/pages/pricing"
        assert "message" in d

    def test_default_message_when_none(self):
        from api.auth.dependencies import quota_exceeded_detail

        d = quota_exceeded_detail(feature="chat", current_tier="free")
        assert d["message"]  # 기본 메시지 존재
        assert d["usage"] is None
        assert d["limit"] is None


class TestRequireTierFactory:
    """require_tier()의 에러 케이스 검증 — DB 없이 동작"""

    def test_rejects_unknown_tier(self):
        from api.auth.dependencies import require_tier

        with pytest.raises(ValueError):
            require_tier("platinum")

    def test_accepts_valid_tiers(self):
        from api.auth.dependencies import require_tier

        dep = require_tier("pro", "premium")
        assert callable(dep)


class TestSchemaMigrationV16:
    """shared/db.py — v16 마이그레이션 존재 확인"""

    def test_schema_version_bumped_to_16(self):
        import importlib
        import shared.db as db
        importlib.reload(db)  # conftest의 mock 이후 재로드로 최신 상수 확보

        # v16 이상이면 users.tier 컬럼이 존재 — 이후 버전에서도 유지되어야 함
        assert db.SCHEMA_VERSION >= 16

    def test_migrate_v16_function_exists(self):
        from shared.db.migrations.versions import _migrate_to_v16

        assert callable(_migrate_to_v16)

    def test_migrate_v16_executes_expected_sql(self):
        """_migrate_to_v16 호출 시 tier 컬럼 ALTER + schema_version INSERT 확인"""
        from shared.db.migrations.versions import _migrate_to_v16
        from unittest.mock import MagicMock

        cur = MagicMock()
        _migrate_to_v16(cur)

        all_sql = " ".join(call.args[0] for call in cur.execute.call_args_list).lower()
        assert "alter table users" in all_sql
        assert "tier" in all_sql
        assert "tier_expires_at" in all_sql
        assert "check (tier in" in all_sql
        assert "schema_version" in all_sql


class TestPricingPageContext:
    """pages.py pricing_page가 올바른 tier_cards를 구성하는지 확인"""

    def test_tier_cards_structure(self):
        """직접 실행 불가 (FastAPI Depends 필요) — 구조만 검증"""
        from shared.tier_limits import (
            TIER_INFO,
            WATCHLIST_LIMITS,
            SUBSCRIPTION_LIMITS,
            STAGE2_DAILY_LIMITS,
            CHAT_DAILY_TURNS,
            HISTORY_DAYS_LIMITS,
        )
        # pricing_page 내부 로직과 동일한 변환
        for key in ("free", "pro", "premium"):
            assert key in TIER_INFO
            assert key in WATCHLIST_LIMITS
            assert key in SUBSCRIPTION_LIMITS
            assert key in STAGE2_DAILY_LIMITS
            assert key in CHAT_DAILY_TURNS
            assert key in HISTORY_DAYS_LIMITS


class TestSprint1TierLimits:
    """Sprint 1 신규 한도: NL_SEARCH / CHART_VISION / SCREENER_CUSTOM / RED_TEAM."""

    def test_nl_search_daily_limits(self):
        from shared.tier_limits import get_nl_search_daily_limit
        assert get_nl_search_daily_limit("free") == 5
        assert get_nl_search_daily_limit("pro") == 50
        assert get_nl_search_daily_limit("premium") == 500

    def test_chart_vision_daily_limits(self):
        from shared.tier_limits import get_chart_vision_daily_limit
        assert get_chart_vision_daily_limit("free") == 0
        assert get_chart_vision_daily_limit("pro") == 10
        assert get_chart_vision_daily_limit("premium") == 100

    def test_screener_custom_presets_limits(self):
        from shared.tier_limits import get_screener_custom_presets_limit
        assert get_screener_custom_presets_limit("free") == 0
        assert get_screener_custom_presets_limit("pro") == 10
        assert get_screener_custom_presets_limit("premium") == 50

    def test_red_team_availability_by_tier(self):
        from shared.tier_limits import is_red_team_available
        assert is_red_team_available("free") is False
        assert is_red_team_available("pro") is False
        assert is_red_team_available("premium") is True

    def test_invalid_tier_falls_back_to_free(self):
        from shared.tier_limits import (
            get_nl_search_daily_limit,
            get_chart_vision_daily_limit,
            get_screener_custom_presets_limit,
            is_red_team_available,
        )
        assert get_nl_search_daily_limit("platinum") == 5
        assert get_chart_vision_daily_limit(None) == 0
        assert get_screener_custom_presets_limit("") == 0
        assert is_red_team_available("unknown") is False

    def test_constants_exposed(self):
        from shared.tier_limits import (
            NL_SEARCH_DAILY,
            CHART_VISION_DAILY,
            SCREENER_CUSTOM_PRESETS,
            RED_TEAM_AVAILABLE,
        )
        assert set(NL_SEARCH_DAILY.keys()) == {"free", "pro", "premium"}
        assert set(CHART_VISION_DAILY.keys()) == {"free", "pro", "premium"}
        assert set(SCREENER_CUSTOM_PRESETS.keys()) == {"free", "pro", "premium"}
        assert set(RED_TEAM_AVAILABLE.keys()) == {"free", "pro", "premium"}

    def test_screener_presets_max_is_alias(self):
        """SCREENER_CUSTOM_PRESETS 는 기존 SCREENER_PRESETS_MAX 의 alias —
        후속 PR 작성자가 어느 이름을 써도 같은 값을 본다."""
        from shared.tier_limits import SCREENER_PRESETS_MAX, SCREENER_CUSTOM_PRESETS
        assert SCREENER_CUSTOM_PRESETS is SCREENER_PRESETS_MAX
        assert SCREENER_PRESETS_MAX["premium"] == 50  # Sprint 1 spec — 무제한 X
