"""v50 마이그레이션 검증 — macro_observations 테이블 (Tier 2 #4 인프라)."""
from unittest.mock import MagicMock


def test_v50_function_exists():
    from shared.db.migrations.versions import _migrate_to_v50
    assert callable(_migrate_to_v50)


def test_v50_creates_macro_observations_table():
    from shared.db.migrations.versions import _migrate_to_v50
    cur = MagicMock()
    _migrate_to_v50(cur)
    sqls = " ".join(c.args[0] for c in cur.execute.call_args_list).upper().replace("\n", " ")
    assert "CREATE TABLE IF NOT EXISTS MACRO_OBSERVATIONS" in sqls
    assert "VARIABLE_NAME" in sqls
    assert "OBSERVED_AT" in sqls
    assert "VALUE" in sqls
    assert "PRIMARY KEY (VARIABLE_NAME, OBSERVED_AT)" in sqls


def test_v50_creates_index():
    from shared.db.migrations.versions import _migrate_to_v50
    cur = MagicMock()
    _migrate_to_v50(cur)
    sqls = " ".join(c.args[0] for c in cur.execute.call_args_list).upper().replace("\n", " ")
    assert "CREATE INDEX IF NOT EXISTS IDX_MACRO_OBS_VAR_DATE" in sqls


def test_v50_inserts_schema_version_50():
    from shared.db.migrations.versions import _migrate_to_v50
    cur = MagicMock()
    _migrate_to_v50(cur)
    sqls = " ".join(c.args[0] for c in cur.execute.call_args_list).upper()
    assert "INSERT INTO SCHEMA_VERSION (VERSION) VALUES (50)" in sqls


def test_v50_registered_in_migrations_dict():
    from shared.db.migrations import _MIGRATIONS
    from shared.db.migrations.versions import _migrate_to_v50
    assert 50 in _MIGRATIONS
    assert _MIGRATIONS[50] is _migrate_to_v50


def test_schema_version_bumped_to_50():
    from shared.db.schema import SCHEMA_VERSION
    assert SCHEMA_VERSION >= 50
