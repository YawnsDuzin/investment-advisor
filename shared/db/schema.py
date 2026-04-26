"""스키마 버전 상수 + 기본 스키마 생성 + init_db 오케스트레이터."""
from shared.config import DatabaseConfig
from shared.db.connection import (
    _ensure_database,
    get_connection,
    _get_schema_version,
)
from shared.db.migrations import run_migrations


# ── 스키마 버전 관리 ──────────────────────────────
SCHEMA_VERSION = 35  # v35: education Tier A·B 14 토픽 + tools 카테고리 신설


def _create_base_schema(cur) -> None:
    """v1: 기존 4테이블 생성"""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INT PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS analysis_sessions (
            id SERIAL PRIMARY KEY,
            analysis_date DATE NOT NULL UNIQUE,
            market_summary TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS global_issues (
            id SERIAL PRIMARY KEY,
            session_id INT REFERENCES analysis_sessions(id) ON DELETE CASCADE,
            category VARCHAR(50),
            region VARCHAR(100),
            title VARCHAR(500),
            summary TEXT,
            source VARCHAR(300),
            importance INT CHECK (importance BETWEEN 1 AND 5)
        );

        CREATE TABLE IF NOT EXISTS investment_themes (
            id SERIAL PRIMARY KEY,
            session_id INT REFERENCES analysis_sessions(id) ON DELETE CASCADE,
            theme_name VARCHAR(200),
            description TEXT,
            related_issue_ids INT[],
            confidence_score NUMERIC(3,2),
            time_horizon VARCHAR(20),
            key_indicators TEXT[]
        );

        CREATE TABLE IF NOT EXISTS investment_proposals (
            id SERIAL PRIMARY KEY,
            theme_id INT REFERENCES investment_themes(id) ON DELETE CASCADE,
            asset_type VARCHAR(50),
            asset_name VARCHAR(200),
            ticker VARCHAR(20),
            market VARCHAR(50),
            action VARCHAR(10),
            conviction VARCHAR(10),
            rationale TEXT,
            risk_factors TEXT,
            entry_condition TEXT,
            exit_condition TEXT,
            target_allocation NUMERIC(5,2)
        );

        INSERT INTO schema_version (version) VALUES (1)
        ON CONFLICT (version) DO NOTHING;
    """)


def init_db(cfg: DatabaseConfig) -> None:
    """PostgreSQL 설치 확인 → 데이터베이스 생성 → 스키마 마이그레이션"""
    from shared.pg_setup import ensure_postgresql
    if not ensure_postgresql(cfg.host, cfg.port):
        raise RuntimeError("PostgreSQL을 사용할 수 없습니다. 설치 후 다시 실행하세요.")
    _ensure_database(cfg)
    conn = get_connection(cfg)
    try:
        with conn.cursor() as cur:
            current = _get_schema_version(cur)

            if current < 1:
                _create_base_schema(cur)
                print("[DB] v1 기본 스키마 생성 완료")
                current = 1

            run_migrations(cur, current, SCHEMA_VERSION)

        conn.commit()
        print("[DB] 테이블 초기화 완료")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
