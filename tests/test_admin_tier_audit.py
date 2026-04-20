"""관리자 티어 부여 + 감사 로그 (v17) 단위 테스트

외부 의존성(psycopg2)은 conftest에서 mock — 순수 파이썬 로직 검증.
실제 엔드포인트 통합 테스트는 TestClient로 별도 가능하나, 여기서는
핵심 규칙(validation, audit insert SQL 형태, v17 스키마) 검증에 집중.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest


class TestSchemaMigrationV17:
    """shared/db.py — v17 마이그레이션 존재 확인"""

    def test_schema_version_bumped_to_17(self):
        import importlib
        import shared.db as db
        importlib.reload(db)
        assert db.SCHEMA_VERSION == 17

    def test_migrate_v17_function_exists(self):
        from shared.db.migrations.versions import _migrate_to_v17
        assert callable(_migrate_to_v17)

    def test_migrate_v17_creates_audit_table(self):
        from shared.db.migrations.versions import _migrate_to_v17

        cur = MagicMock()
        _migrate_to_v17(cur)

        all_sql = " ".join(call.args[0] for call in cur.execute.call_args_list).lower()
        assert "create table if not exists admin_audit_logs" in all_sql
        assert "actor_id" in all_sql
        assert "target_user_id" in all_sql
        assert "before_state jsonb" in all_sql
        assert "after_state jsonb" in all_sql
        assert "action varchar" in all_sql
        # 인덱스 3종
        assert "idx_admin_audit_logs_created" in all_sql
        assert "idx_admin_audit_logs_target" in all_sql
        assert "idx_admin_audit_logs_action" in all_sql
        # schema_version 기록
        assert "insert into schema_version (version) values (17)" in all_sql


class TestParseExpiresAt:
    """user_admin._parse_expires_at 동작"""

    def test_empty_returns_none(self):
        from api.routes.user_admin import _parse_expires_at
        assert _parse_expires_at(None) is None
        assert _parse_expires_at("") is None
        assert _parse_expires_at("   ") is None

    def test_date_only(self):
        from api.routes.user_admin import _parse_expires_at
        dt = _parse_expires_at("2026-12-31")
        assert dt == datetime(2026, 12, 31, 0, 0, 0)

    def test_datetime_with_time(self):
        from api.routes.user_admin import _parse_expires_at
        dt = _parse_expires_at("2026-12-31T23:59")
        assert dt.year == 2026 and dt.month == 12 and dt.day == 31
        assert dt.hour == 23 and dt.minute == 59

    def test_invalid_raises_400(self):
        from fastapi import HTTPException
        from api.routes.user_admin import _parse_expires_at
        with pytest.raises(HTTPException) as exc_info:
            _parse_expires_at("not-a-date")
        assert exc_info.value.status_code == 400


class TestLogAdminActionSQL:
    """_log_admin_action이 올바른 INSERT SQL을 생성하는지"""

    def test_insert_has_all_columns(self):
        from api.routes.user_admin import _log_admin_action
        from api.auth.models import UserInDB

        actor = UserInDB(
            id=1, email="admin@x.com", nickname="a", role="admin",
            tier="premium", is_active=True, created_at=datetime.now(timezone.utc),
        )
        cur = MagicMock()

        _log_admin_action(
            cur,
            actor=actor,
            target_user_id=42,
            target_email="user@x.com",
            action="tier_change",
            before={"tier": "free"},
            after={"tier": "pro"},
            reason="베타 테스터",
        )

        assert cur.execute.called
        sql, params = cur.execute.call_args.args
        sql_lower = sql.lower()
        assert "insert into admin_audit_logs" in sql_lower
        assert "actor_id" in sql_lower
        assert "actor_email" in sql_lower
        assert "target_user_id" in sql_lower
        assert "target_email" in sql_lower
        assert "action" in sql_lower
        assert "before_state" in sql_lower
        assert "after_state" in sql_lower
        assert "reason" in sql_lower
        # 파라미터 8개 (actor_id, actor_email, target_user_id, target_email, action, before, after, reason)
        assert len(params) == 8
        assert params[0] == 1
        assert params[1] == "admin@x.com"
        assert params[2] == 42
        assert params[3] == "user@x.com"
        assert params[4] == "tier_change"
        assert params[7] == "베타 테스터"

    def test_handles_none_actor(self):
        """AUTH_ENABLED=false 등으로 actor가 None인 경우에도 기록은 가능"""
        from api.routes.user_admin import _log_admin_action

        cur = MagicMock()
        _log_admin_action(
            cur,
            actor=None,
            target_user_id=10,
            target_email="t@x.com",
            action="tier_change",
            before={"tier": "free"},
            after={"tier": "pro"},
        )
        sql, params = cur.execute.call_args.args
        assert params[0] is None  # actor_id
        assert params[1] is None  # actor_email


class TestChangeTierValidation:
    """change_tier 엔드포인트의 검증 규칙 — HTTPException 발생 조건만 확인"""

    def _make_admin(self, user_id=1):
        from api.auth.models import UserInDB
        return UserInDB(
            id=user_id, email="admin@x.com", nickname="a", role="admin",
            tier="premium", is_active=True, created_at=datetime.now(timezone.utc),
        )

    def test_rejects_invalid_tier(self):
        from fastapi import HTTPException
        from api.routes.user_admin import change_tier

        with pytest.raises(HTTPException) as exc:
            change_tier(
                user_id=2, tier="platinum",
                expires_at=None, reason=None,
                actor=self._make_admin(), db_cfg=MagicMock(),
            )
        assert exc.value.status_code == 400
        assert "유효하지 않은 티어" in exc.value.detail

    def test_rejects_self_tier_change(self):
        from fastapi import HTTPException
        from api.routes.user_admin import change_tier

        admin = self._make_admin(user_id=99)
        with pytest.raises(HTTPException) as exc:
            change_tier(
                user_id=99, tier="premium",
                expires_at=None, reason=None,
                actor=admin, db_cfg=MagicMock(),
            )
        assert exc.value.status_code == 400
        assert "자기 자신" in exc.value.detail

    def test_rejects_past_expiry(self):
        from fastapi import HTTPException
        from api.routes.user_admin import change_tier

        yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        with pytest.raises(HTTPException) as exc:
            change_tier(
                user_id=2, tier="pro",
                expires_at=yesterday, reason=None,
                actor=self._make_admin(), db_cfg=MagicMock(),
            )
        assert exc.value.status_code == 400
        assert "현재 시각 이후" in exc.value.detail

    def test_free_tier_ignores_expires_at(self):
        """free로 강등 시 expires_at이 past여도 에러 없이 통과 (강제 NULL)"""
        from api.routes.user_admin import change_tier

        # DB 목: user lookup → 기존 pro 사용자, update → 성공
        fake_cur = MagicMock()
        fake_cur.fetchone.return_value = {
            "id": 5, "email": "u@x.com", "tier": "pro",
            "tier_expires_at": datetime(2027, 1, 1),
        }
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = fake_cur
        conn.cursor.return_value.__exit__.return_value = False

        # get_connection 패치
        import api.routes.user_admin as ua
        orig = ua.get_connection
        ua.get_connection = lambda cfg: conn
        try:
            result = change_tier(
                user_id=5, tier="free",
                expires_at="2020-01-01",  # 과거 날짜이지만 free라 무시
                reason="강등",
                actor=self._make_admin(), db_cfg=MagicMock(),
            )
        finally:
            ua.get_connection = orig

        assert result["tier"] == "free"
        assert result["tier_expires_at"] is None


class TestRoleChangeSelfBlock:
    """change_role — 본인 역할 변경 금지"""

    def test_self_role_change_blocked(self):
        from fastapi import HTTPException
        from api.routes.user_admin import change_role
        from api.auth.models import UserInDB

        admin = UserInDB(
            id=7, email="admin@x.com", nickname="a", role="admin",
            tier="premium", is_active=True, created_at=datetime.now(timezone.utc),
        )

        # DB 목: 대상 조회 시 본인 반환
        fake_cur = MagicMock()
        fake_cur.fetchone.return_value = {"id": 7, "email": "admin@x.com", "role": "admin"}
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = fake_cur
        conn.cursor.return_value.__exit__.return_value = False

        import api.routes.user_admin as ua
        orig = ua.get_connection
        ua.get_connection = lambda cfg: conn
        try:
            with pytest.raises(HTTPException) as exc:
                change_role(
                    user_id=7, role="user", reason=None,
                    actor=admin, db_cfg=MagicMock(),
                )
            assert exc.value.status_code == 400
            assert "자기 자신" in exc.value.detail
        finally:
            ua.get_connection = orig
