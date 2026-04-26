"""v39 — stock_universe_fundamentals 테이블 생성 검증.

기존 conftest.py 가 psycopg2 mock 처리하므로,
실제 DB 호출 대신 _migrate_to_v39 가 SQL을 어떤 순서로 실행했는지만 검증한다.
"""
from unittest.mock import MagicMock
from shared.db.migrations.versions import _migrate_to_v39


def test_v39_creates_fundamentals_table():
    cur = MagicMock()
    _migrate_to_v39(cur)

    sqls = [call.args[0] for call in cur.execute.call_args_list]
    joined = " ".join(sqls).upper()
    assert "CREATE TABLE IF NOT EXISTS STOCK_UNIVERSE_FUNDAMENTALS" in joined
    assert "PRIMARY KEY (TICKER, MARKET, SNAPSHOT_DATE)" in joined.replace("\n", " ")
    assert "IDX_FUND_LATEST" in joined
    assert "IDX_FUND_DATE" in joined
    assert "INSERT INTO SCHEMA_VERSION (VERSION) VALUES (39)" in joined


def test_v39_idempotent_via_if_not_exists():
    """IF NOT EXISTS 가드로 두 번 호출되어도 문제 없음."""
    cur = MagicMock()
    _migrate_to_v39(cur)
    _migrate_to_v39(cur)
    sqls = [call.args[0] for call in cur.execute.call_args_list]
    for sql in sqls:
        if "CREATE TABLE" in sql.upper():
            assert "IF NOT EXISTS" in sql.upper(), f"비-멱등 SQL: {sql[:100]}"
        if "CREATE INDEX" in sql.upper():
            assert "IF NOT EXISTS" in sql.upper(), f"비-멱등 SQL: {sql[:100]}"
