"""PostgreSQL 연결 관리 — DB 자동 생성 + 연결 + 스키마 버전 조회."""
import psycopg2

from shared.config import DatabaseConfig


def _ensure_database(cfg: DatabaseConfig) -> None:
    """데이터베이스가 없으면 자동 생성"""
    conn = psycopg2.connect(
        host=cfg.host, port=cfg.port,
        dbname="postgres", user=cfg.user, password=cfg.password,
    )
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (cfg.dbname,)
            )
            if not cur.fetchone():
                cur.execute(f'CREATE DATABASE "{cfg.dbname}"')
                print(f"[DB] 데이터베이스 '{cfg.dbname}' 생성 완료")
    finally:
        conn.close()


def get_connection(cfg: DatabaseConfig):
    """DB 커넥션 반환"""
    return psycopg2.connect(cfg.dsn)


def _get_schema_version(cur) -> int:
    """현재 스키마 버전 조회 (테이블 없으면 0)"""
    cur.execute("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'schema_version'
        )
    """)
    if not cur.fetchone()[0]:
        return 0
    cur.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1")
    row = cur.fetchone()
    return row[0] if row else 0
