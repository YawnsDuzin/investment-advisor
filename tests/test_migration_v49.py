"""v49 마이그레이션 검증 — pre_market_briefings.one_liner / market_temperature 추가 (Tier 1 #2)."""
from unittest.mock import MagicMock


def test_v49_function_exists():
    from shared.db.migrations.versions import _migrate_to_v49
    assert callable(_migrate_to_v49)


def test_v49_adds_one_liner_and_temperature_columns():
    from shared.db.migrations.versions import _migrate_to_v49
    cur = MagicMock()
    _migrate_to_v49(cur)
    sqls = " ".join(c.args[0] for c in cur.execute.call_args_list).upper().replace("\n", " ")
    assert "ALTER TABLE PRE_MARKET_BRIEFINGS" in sqls
    assert "ADD COLUMN IF NOT EXISTS ONE_LINER TEXT" in sqls
    assert "ADD COLUMN IF NOT EXISTS MARKET_TEMPERATURE INT" in sqls


def test_v49_inserts_schema_version_49():
    from shared.db.migrations.versions import _migrate_to_v49
    cur = MagicMock()
    _migrate_to_v49(cur)
    sqls = " ".join(c.args[0] for c in cur.execute.call_args_list).upper()
    assert "INSERT INTO SCHEMA_VERSION (VERSION) VALUES (49)" in sqls


def test_v49_registered_in_migrations_dict():
    from shared.db.migrations import _MIGRATIONS
    from shared.db.migrations.versions import _migrate_to_v49
    assert 49 in _MIGRATIONS
    assert _MIGRATIONS[49] is _migrate_to_v49


def test_schema_version_bumped_to_49():
    from shared.db.schema import SCHEMA_VERSION
    assert SCHEMA_VERSION >= 49
