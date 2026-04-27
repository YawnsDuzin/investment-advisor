"""v41 마이그레이션 검증 — 4 신규 테이블 + 2 ALTER + 시드 INSERT.

기존 conftest.py 의 psycopg2 mock 활용 — 실제 DB 호출 없이 SQL emit 만 검증.
패턴: tests/test_migration_v39_v40.py 와 동일.
"""
from unittest.mock import MagicMock
import pytest


def test_v41_function_exists():
    from shared.db.migrations.versions import _migrate_to_v41
    assert callable(_migrate_to_v41)


def test_v41_creates_nl_search_history():
    from shared.db.migrations.versions import _migrate_to_v41
    cur = MagicMock()
    _migrate_to_v41(cur)
    sqls = " ".join(c.args[0] for c in cur.execute.call_args_list).upper()
    assert "CREATE TABLE IF NOT EXISTS NL_SEARCH_HISTORY" in sqls
    assert "QUERY_TEXT TEXT NOT NULL" in sqls.replace("\n", " ")


def test_v41_creates_chart_vision_log():
    from shared.db.migrations.versions import _migrate_to_v41
    cur = MagicMock()
    _migrate_to_v41(cur)
    sqls = " ".join(c.args[0] for c in cur.execute.call_args_list).upper()
    assert "CREATE TABLE IF NOT EXISTS CHART_VISION_LOG" in sqls
    assert "ANONYMOUS_TOKEN CHAR(64)" in sqls.replace("\n", " ")
    assert "IMAGE_HASH CHAR(64) NOT NULL" in sqls.replace("\n", " ")


def test_v41_creates_krx_investor_flow_daily():
    from shared.db.migrations.versions import _migrate_to_v41
    cur = MagicMock()
    _migrate_to_v41(cur)
    sqls = " ".join(c.args[0] for c in cur.execute.call_args_list).upper()
    assert "CREATE TABLE IF NOT EXISTS KRX_INVESTOR_FLOW_DAILY" in sqls
    assert "FOREIGN_STREAK INT" in sqls.replace("\n", " ")


def test_v41_creates_factor_snapshot():
    from shared.db.migrations.versions import _migrate_to_v41
    cur = MagicMock()
    _migrate_to_v41(cur)
    sqls = " ".join(c.args[0] for c in cur.execute.call_args_list).upper()
    assert "CREATE TABLE IF NOT EXISTS STOCK_UNIVERSE_FACTOR_SNAPSHOT" in sqls
    assert "R1M_PCTILE" in sqls
    assert "R3M_PCTILE" in sqls
    assert "VOL60_PCTILE" in sqls


def test_v41_alters_stock_analyses_for_red_team():
    from shared.db.migrations.versions import _migrate_to_v41
    cur = MagicMock()
    _migrate_to_v41(cur)
    sqls = " ".join(c.args[0] for c in cur.execute.call_args_list).upper()
    assert "ALTER TABLE STOCK_ANALYSES" in sqls
    assert "BULL_VIEW" in sqls
    assert "BEAR_VIEW" in sqls
    assert "SYNTHESIS_SUMMARY" in sqls
    assert "RED_TEAM_ENABLED" in sqls


def test_v41_alters_news_articles_for_multilang():
    from shared.db.migrations.versions import _migrate_to_v41
    cur = MagicMock()
    _migrate_to_v41(cur)
    sqls = " ".join(c.args[0] for c in cur.execute.call_args_list).upper()
    assert "ALTER TABLE NEWS_ARTICLES" in sqls
    assert "LANG VARCHAR(8)" in sqls.replace("\n", " ")
    assert "TITLE_ORIGINAL TEXT" in sqls.replace("\n", " ")
    assert "REGION VARCHAR(16)" in sqls.replace("\n", " ")


def test_v41_inserts_seed_presets():
    """psycopg2.extras.execute_values 로 10개 시드 UPSERT."""
    from shared.db.migrations.versions import _migrate_to_v41
    from psycopg2.extras import execute_values

    execute_values.reset_mock()
    cur = MagicMock()
    _migrate_to_v41(cur)

    assert execute_values.called, "execute_values 가 호출되지 않았다"
    # call_args.args = (cur, sql, rows[, ...])
    sql_arg = execute_values.call_args.args[1].upper()
    rows_arg = execute_values.call_args.args[2]
    assert "INSERT INTO SCREENER_PRESETS" in sql_arg
    assert len(rows_arg) == 10, f"시드 row 수가 10이 아니다: {len(rows_arg)}"


def test_v41_records_schema_version_41():
    from shared.db.migrations.versions import _migrate_to_v41
    cur = MagicMock()
    _migrate_to_v41(cur)
    sqls = " ".join(c.args[0] for c in cur.execute.call_args_list).upper()
    assert "INSERT INTO SCHEMA_VERSION (VERSION) VALUES (41)" in sqls


def test_v41_idempotent_via_if_not_exists():
    """CREATE TABLE / ADD COLUMN / CREATE INDEX 모두 IF NOT EXISTS 가드."""
    from shared.db.migrations.versions import _migrate_to_v41
    cur = MagicMock()
    _migrate_to_v41(cur)
    sqls = [c.args[0] for c in cur.execute.call_args_list]
    for sql in sqls:
        upper = sql.upper()
        if "CREATE TABLE" in upper:
            assert "IF NOT EXISTS" in upper, f"비-멱등 CREATE TABLE: {sql[:120]}"
        if "CREATE INDEX" in upper or "CREATE UNIQUE INDEX" in upper:
            assert "IF NOT EXISTS" in upper, f"비-멱등 CREATE INDEX: {sql[:120]}"
        if "ADD COLUMN" in upper:
            assert "IF NOT EXISTS" in upper, f"비-멱등 ADD COLUMN: {sql[:120]}"


def test_v41_seed_upsert_uses_on_conflict():
    """시드 INSERT 가 ON CONFLICT (strategy_key) DO UPDATE — 멱등 재실행 가능."""
    from shared.db.migrations.versions import _migrate_to_v41
    from psycopg2.extras import execute_values

    execute_values.reset_mock()
    cur = MagicMock()
    _migrate_to_v41(cur)

    assert execute_values.called
    sql_arg = execute_values.call_args.args[1].upper()
    assert "ON CONFLICT" in sql_arg
    assert "STRATEGY_KEY" in sql_arg
    assert "DO UPDATE" in sql_arg
