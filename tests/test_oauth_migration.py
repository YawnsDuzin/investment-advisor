"""v51 마이그레이션 SQL 검증 (mock cursor 로 호출 기록 확인)."""
from unittest.mock import MagicMock

from shared.db.migrations.versions import _migrate_to_v51


def test_v51_creates_user_oauth_accounts_table():
    cur = MagicMock()
    _migrate_to_v51(cur)
    sql_calls = [str(c.args[0]) for c in cur.execute.call_args_list]
    joined = "\n".join(sql_calls)
    assert "CREATE TABLE IF NOT EXISTS user_oauth_accounts" in joined


def test_v51_has_required_columns():
    cur = MagicMock()
    _migrate_to_v51(cur)
    joined = "\n".join(str(c.args[0]) for c in cur.execute.call_args_list)
    for col in ("user_id", "provider", "provider_user_id", "provider_email",
                "provider_name", "linked_at", "last_login_at"):
        assert col in joined, f"column {col} 누락"


def test_v51_has_unique_constraints():
    cur = MagicMock()
    _migrate_to_v51(cur)
    joined = "\n".join(str(c.args[0]) for c in cur.execute.call_args_list)
    # (provider, provider_user_id) UNIQUE
    assert "UNIQUE (provider, provider_user_id)" in joined
    # (user_id, provider) UNIQUE
    assert "UNIQUE (user_id, provider)" in joined


def test_v51_has_user_id_index():
    cur = MagicMock()
    _migrate_to_v51(cur)
    joined = "\n".join(str(c.args[0]) for c in cur.execute.call_args_list)
    assert "idx_user_oauth_accounts_user" in joined


def test_v51_has_cascade_on_user_delete():
    cur = MagicMock()
    _migrate_to_v51(cur)
    joined = "\n".join(str(c.args[0]) for c in cur.execute.call_args_list)
    assert "ON DELETE CASCADE" in joined


def test_v51_registered_in_migrations_dict():
    from shared.db.migrations import _MIGRATIONS
    assert 51 in _MIGRATIONS
    assert _MIGRATIONS[51] is _migrate_to_v51


def test_schema_version_is_51():
    from shared.db.schema import SCHEMA_VERSION
    assert SCHEMA_VERSION == 51
