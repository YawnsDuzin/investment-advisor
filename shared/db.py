"""PostgreSQL 데이터베이스 관리 모듈"""
import json
import psycopg2
from psycopg2.extras import execute_values, RealDictCursor
from shared.config import DatabaseConfig

# ── 스키마 버전 관리 ──────────────────────────────
SCHEMA_VERSION = 18  # v1~v17 + v18: 범용 로그(app_runs/app_logs)


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


def _migrate_to_v2(cur) -> None:
    """v2: 멀티에이전트 확장 — 기존 테이블 컬럼 추가 + 신규 3테이블"""

    # ── 기존 테이블 컬럼 추가 (하위호환: 모두 NULLABLE) ──

    # analysis_sessions: 리스크 온도, 데이터 소스
    cur.execute("""
        ALTER TABLE analysis_sessions
            ADD COLUMN IF NOT EXISTS risk_temperature VARCHAR(10),
            ADD COLUMN IF NOT EXISTS data_sources TEXT[];
    """)

    # global_issues: 시계별 영향 분석, 과거 유사 사례
    cur.execute("""
        ALTER TABLE global_issues
            ADD COLUMN IF NOT EXISTS impact_short TEXT,
            ADD COLUMN IF NOT EXISTS impact_mid TEXT,
            ADD COLUMN IF NOT EXISTS impact_long TEXT,
            ADD COLUMN IF NOT EXISTS historical_analogue TEXT;
    """)

    # investment_themes: 테마 유형, 유효성
    cur.execute("""
        ALTER TABLE investment_themes
            ADD COLUMN IF NOT EXISTS theme_type VARCHAR(20),
            ADD COLUMN IF NOT EXISTS theme_validity VARCHAR(20);
    """)

    # investment_proposals: 가격 목표, 퀀트/센티먼트 스코어
    cur.execute("""
        ALTER TABLE investment_proposals
            ADD COLUMN IF NOT EXISTS current_price NUMERIC(15,2),
            ADD COLUMN IF NOT EXISTS target_price_low NUMERIC(15,2),
            ADD COLUMN IF NOT EXISTS target_price_high NUMERIC(15,2),
            ADD COLUMN IF NOT EXISTS upside_pct NUMERIC(7,2),
            ADD COLUMN IF NOT EXISTS sentiment_score NUMERIC(4,2),
            ADD COLUMN IF NOT EXISTS quant_score NUMERIC(3,1),
            ADD COLUMN IF NOT EXISTS sector VARCHAR(100),
            ADD COLUMN IF NOT EXISTS currency VARCHAR(10);
    """)

    # ── 신규 테이블: 시나리오 분석 (테마당 Bull/Base/Bear) ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS theme_scenarios (
            id SERIAL PRIMARY KEY,
            theme_id INT REFERENCES investment_themes(id) ON DELETE CASCADE,
            scenario_type VARCHAR(20) NOT NULL,
            probability NUMERIC(5,2),
            description TEXT,
            key_assumptions TEXT,
            market_impact TEXT
        );
    """)

    # ── 신규 테이블: 매크로 변수 영향 (테마당 변수별) ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS macro_impacts (
            id SERIAL PRIMARY KEY,
            theme_id INT REFERENCES investment_themes(id) ON DELETE CASCADE,
            variable_name VARCHAR(100) NOT NULL,
            base_case VARCHAR(200),
            worse_case VARCHAR(200),
            better_case VARCHAR(200),
            unit VARCHAR(20)
        );
    """)

    # ── 신규 테이블: 종목 심층분석 (제안 종목 중 핵심 종목) ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stock_analyses (
            id SERIAL PRIMARY KEY,
            proposal_id INT REFERENCES investment_proposals(id) ON DELETE CASCADE,
            company_overview TEXT,
            financial_summary JSONB,
            dcf_fair_value NUMERIC(15,2),
            dcf_wacc NUMERIC(5,2),
            industry_position TEXT,
            momentum_summary TEXT,
            risk_summary TEXT,
            bull_case TEXT,
            bear_case TEXT,
            factor_scores JSONB,
            report_markdown TEXT
        );
    """)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (2)
        ON CONFLICT (version) DO NOTHING;
    """)

    print("[DB] v2 마이그레이션 완료 — 기존 테이블 확장 + 신규 3테이블 생성")


def _migrate_to_v3(cur) -> None:
    """v3: 일자별 추적 — 테마·종목 연속성 추적 테이블"""

    # ── 테마 추적 테이블 ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS theme_tracking (
            id SERIAL PRIMARY KEY,
            theme_key VARCHAR(200) NOT NULL UNIQUE,
            theme_name VARCHAR(200) NOT NULL,
            first_seen_date DATE NOT NULL,
            last_seen_date DATE NOT NULL,
            streak_days INT DEFAULT 1,
            appearances INT DEFAULT 1,
            latest_confidence NUMERIC(3,2),
            prev_confidence NUMERIC(3,2),
            latest_theme_id INT REFERENCES investment_themes(id) ON DELETE SET NULL
        );
    """)

    # ── 종목 추적 테이블 ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS proposal_tracking (
            id SERIAL PRIMARY KEY,
            ticker VARCHAR(20) NOT NULL,
            asset_name VARCHAR(200),
            theme_key VARCHAR(200),
            first_recommended_date DATE NOT NULL,
            last_recommended_date DATE NOT NULL,
            recommendation_count INT DEFAULT 1,
            latest_action VARCHAR(10),
            prev_action VARCHAR(10),
            latest_conviction VARCHAR(10),
            latest_target_price_low NUMERIC(15,2),
            latest_target_price_high NUMERIC(15,2),
            prev_target_price_low NUMERIC(15,2),
            prev_target_price_high NUMERIC(15,2),
            latest_quant_score NUMERIC(3,1),
            latest_sentiment_score NUMERIC(4,2),
            latest_proposal_id INT REFERENCES investment_proposals(id) ON DELETE SET NULL,
            UNIQUE(ticker, theme_key)
        );
    """)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (3)
        ON CONFLICT (version) DO NOTHING;
    """)

    print("[DB] v3 마이그레이션 완료 — theme_tracking + proposal_tracking 생성")


def _migrate_to_v4(cur) -> None:
    """v4: 공급망 분석 — 벤더 티어, 공급망 위치 컬럼 추가"""
    cur.execute("""
        ALTER TABLE investment_proposals
            ADD COLUMN IF NOT EXISTS vendor_tier INT,
            ADD COLUMN IF NOT EXISTS supply_chain_position VARCHAR(200);
    """)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (4)
        ON CONFLICT (version) DO NOTHING;
    """)

    print("[DB] v4 마이그레이션 완료 — vendor_tier, supply_chain_position 컬럼 추가")


def _migrate_to_v5(cur) -> None:
    """v5: 발굴 유형 — discovery_type, price_momentum_check 컬럼 추가"""
    cur.execute("""
        ALTER TABLE investment_proposals
            ADD COLUMN IF NOT EXISTS discovery_type VARCHAR(20),
            ADD COLUMN IF NOT EXISTS price_momentum_check VARCHAR(20);
    """)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (5)
        ON CONFLICT (version) DO NOTHING;
    """)

    print("[DB] v5 마이그레이션 완료 — discovery_type, price_momentum_check 컬럼 추가")


def _migrate_to_v6(cur) -> None:
    """v6: 테마 채팅 — 대화 세션 + 메시지 테이블"""

    # ── 채팅 대화 세션 (테마 1개당 여러 대화 가능) ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS theme_chat_sessions (
            id SERIAL PRIMARY KEY,
            theme_id INT REFERENCES investment_themes(id) ON DELETE CASCADE,
            title VARCHAR(500),
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """)

    # ── 개별 메시지 (질문/답변 쌍) ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS theme_chat_messages (
            id SERIAL PRIMARY KEY,
            chat_session_id INT REFERENCES theme_chat_sessions(id) ON DELETE CASCADE,
            role VARCHAR(10) NOT NULL CHECK (role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_chat_messages_session
            ON theme_chat_messages(chat_session_id, created_at);
    """)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (6)
        ON CONFLICT (version) DO NOTHING;
    """)

    print("[DB] v6 마이그레이션 완료 — theme_chat_sessions + theme_chat_messages 생성")


def _migrate_to_v7(cur) -> None:
    """v7: 뉴스 기사 저장 — 수집된 RSS 뉴스를 세션별로 보관"""

    cur.execute("""
        CREATE TABLE IF NOT EXISTS news_articles (
            id SERIAL PRIMARY KEY,
            session_id INT REFERENCES analysis_sessions(id) ON DELETE CASCADE,
            category VARCHAR(50) NOT NULL,
            source VARCHAR(200),
            title VARCHAR(500) NOT NULL,
            summary TEXT,
            link VARCHAR(1000),
            published VARCHAR(100),
            created_at TIMESTAMP DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_news_articles_session
            ON news_articles(session_id, category);
    """)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (7)
        ON CONFLICT (version) DO NOTHING;
    """)

    print("[DB] v7 마이그레이션 완료 — news_articles 테이블 생성")


def _migrate_to_v8(cur) -> None:
    """v8: 뉴스 기사 한글 번역 컬럼 추가"""

    cur.execute("""
        ALTER TABLE news_articles
        ADD COLUMN IF NOT EXISTS title_ko VARCHAR(500);
    """)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (8)
        ON CONFLICT (version) DO NOTHING;
    """)

    print("[DB] v8 마이그레이션 완료 — news_articles.title_ko 컬럼 추가")


def _migrate_to_v9(cur) -> None:
    """v9: 뉴스 기사 요약 한글 번역 컬럼 추가"""

    cur.execute("""
        ALTER TABLE news_articles
        ADD COLUMN IF NOT EXISTS summary_ko TEXT;
    """)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (9)
        ON CONFLICT (version) DO NOTHING;
    """)

    print("[DB] v9 마이그레이션 완료 — news_articles.summary_ko 컬럼 추가")


def _migrate_to_v10(cur) -> None:
    """v10: 가격 데이터 출처 추적 컬럼 추가"""

    cur.execute("""
        ALTER TABLE investment_proposals
        ADD COLUMN IF NOT EXISTS price_source VARCHAR(20);
    """)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (10)
        ON CONFLICT (version) DO NOTHING;
    """)

    print("[DB] v10 마이그레이션 완료 — investment_proposals.price_source 컬럼 추가")


def _seed_admin_user(cur) -> None:
    """최초 Admin 계정 시드 — 이미 존재하면 스킵"""
    import os
    from api.auth.password import hash_password

    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_password = os.getenv("ADMIN_PASSWORD", "changeme123")

    cur.execute("SELECT 1 FROM users WHERE email = %s", (admin_email,))
    if cur.fetchone():
        return

    pw_hash = hash_password(admin_password)
    cur.execute(
        "INSERT INTO users (email, password_hash, nickname, role) VALUES (%s, %s, %s, %s)",
        (admin_email, pw_hash, "Admin", "admin"),
    )
    print(f"[DB] 최초 Admin 계정 생성: {admin_email}")
    if admin_password == "changeme123":
        print("[DB] ⚠ 기본 Admin 비밀번호 사용 중 — 프로덕션에서 반드시 변경하세요!")


def _migrate_to_v11(cur) -> None:
    """v11: JWT 인증 — users, refresh_tokens, chat_sessions.user_id"""

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email VARCHAR(255) NOT NULL UNIQUE,
            password_hash VARCHAR(255),
            nickname VARCHAR(100) NOT NULL,
            role VARCHAR(20) NOT NULL DEFAULT 'user'
                CHECK (role IN ('admin', 'moderator', 'user')),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW(),
            last_login_at TIMESTAMP,
            oauth_provider VARCHAR(50),
            oauth_provider_id VARCHAR(255)
        );
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            id SERIAL PRIMARY KEY,
            user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash VARCHAR(255) NOT NULL UNIQUE,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            revoked_at TIMESTAMP
        );
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user
            ON refresh_tokens(user_id, expires_at);
    """)

    cur.execute("""
        ALTER TABLE theme_chat_sessions
            ADD COLUMN IF NOT EXISTS user_id INT REFERENCES users(id) ON DELETE SET NULL;
    """)

    _seed_admin_user(cur)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (11)
        ON CONFLICT (version) DO NOTHING;
    """)

    print("[DB] v11 마이그레이션 완료 — users + refresh_tokens 생성")


def _migrate_to_v12(cur) -> None:
    """v12: 개인화 — 워치리스트, 알림 구독, 알림 이력, 제안 메모"""

    # 관심 종목 워치리스트
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_watchlist (
            id SERIAL PRIMARY KEY,
            user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            ticker VARCHAR(20) NOT NULL,
            asset_name VARCHAR(200),
            memo TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, ticker)
        );
    """)

    # 테마/종목 알림 구독
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_subscriptions (
            id SERIAL PRIMARY KEY,
            user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            sub_type VARCHAR(10) NOT NULL CHECK (sub_type IN ('ticker', 'theme')),
            sub_key VARCHAR(200) NOT NULL,
            label VARCHAR(200),
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, sub_type, sub_key)
        );
    """)

    # 알림 이력
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_notifications (
            id SERIAL PRIMARY KEY,
            user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            sub_id INT REFERENCES user_subscriptions(id) ON DELETE SET NULL,
            session_id INT REFERENCES analysis_sessions(id) ON DELETE CASCADE,
            title VARCHAR(300) NOT NULL,
            detail TEXT,
            link VARCHAR(500),
            is_read BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_notifications_unread
            ON user_notifications(user_id, is_read) WHERE is_read = FALSE;
    """)

    # 제안 메모
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_proposal_memos (
            id SERIAL PRIMARY KEY,
            user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            proposal_id INT NOT NULL REFERENCES investment_proposals(id) ON DELETE CASCADE,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, proposal_id)
        );
    """)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (12)
        ON CONFLICT (version) DO NOTHING;
    """)

    print("[DB] v12 마이그레이션 완료 — 워치리스트/구독/알림/메모 테이블 생성")


def _migrate_to_v13(cur) -> None:
    """v13: investment_themes 테이블에 theme_key 컬럼 추가 (AI 생성 영문 키)"""
    cur.execute("""
        ALTER TABLE investment_themes
        ADD COLUMN IF NOT EXISTS theme_key VARCHAR(200);
    """)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (13)
        ON CONFLICT (version) DO NOTHING;
    """)

    print("[DB] v13 마이그레이션 완료 — investment_themes.theme_key 컬럼 추가")


def _migrate_to_v14(cur) -> None:
    """v14: 기간별 수익률 컬럼 추가 (1m/3m/6m/1y)"""
    cur.execute("""
        ALTER TABLE investment_proposals
            ADD COLUMN IF NOT EXISTS return_1m_pct NUMERIC(7,2),
            ADD COLUMN IF NOT EXISTS return_3m_pct NUMERIC(7,2),
            ADD COLUMN IF NOT EXISTS return_6m_pct NUMERIC(7,2),
            ADD COLUMN IF NOT EXISTS return_1y_pct NUMERIC(7,2);
    """)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (14)
        ON CONFLICT (version) DO NOTHING;
    """)

    print("[DB] v14 마이그레이션 완료 — 기간별 수익률 컬럼 추가 (return_1m/3m/6m/1y_pct)")


def _migrate_to_v15(cur) -> None:
    """v15: 일별 Top Picks — 룰 기반 + AI 재정렬 결과 영속화"""

    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_top_picks (
            id SERIAL PRIMARY KEY,
            session_id INT REFERENCES analysis_sessions(id) ON DELETE CASCADE,
            analysis_date DATE NOT NULL,
            rank INT NOT NULL,
            proposal_id INT REFERENCES investment_proposals(id) ON DELETE CASCADE,
            score_rule NUMERIC(7,2),
            score_final NUMERIC(7,2),
            score_breakdown JSONB,
            rationale_text TEXT,
            key_risk TEXT,
            source VARCHAR(20) DEFAULT 'rule',
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(analysis_date, rank)
        );
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_daily_top_picks_date
            ON daily_top_picks(analysis_date, rank);
    """)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (15)
        ON CONFLICT (version) DO NOTHING;
    """)

    print("[DB] v15 마이그레이션 완료 — daily_top_picks 테이블 생성")


def _migrate_to_v16(cur) -> None:
    """v16: 구독 티어 — users.tier, users.tier_expires_at 컬럼 추가"""
    cur.execute("""
        ALTER TABLE users
            ADD COLUMN IF NOT EXISTS tier VARCHAR(20) NOT NULL DEFAULT 'free'
                CHECK (tier IN ('free', 'pro', 'premium')),
            ADD COLUMN IF NOT EXISTS tier_expires_at TIMESTAMP;
    """)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (16)
        ON CONFLICT (version) DO NOTHING;
    """)

    print("[DB] v16 마이그레이션 완료 — users.tier/tier_expires_at 컬럼 추가")


def _migrate_to_v17(cur) -> None:
    """v17: 관리자 감사 로그 — admin_audit_logs 테이블

    actor/target 이메일을 denormalize해 계정 삭제 후에도 이력 유지.
    action 구분: tier_change / role_change / status_change / password_reset / user_delete.
    """
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin_audit_logs (
            id SERIAL PRIMARY KEY,
            actor_id INT REFERENCES users(id) ON DELETE SET NULL,
            actor_email VARCHAR(255),
            target_user_id INT REFERENCES users(id) ON DELETE SET NULL,
            target_email VARCHAR(255),
            action VARCHAR(40) NOT NULL,
            before_state JSONB,
            after_state JSONB,
            reason TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_admin_audit_logs_created
            ON admin_audit_logs(created_at DESC);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_admin_audit_logs_target
            ON admin_audit_logs(target_user_id);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_admin_audit_logs_action
            ON admin_audit_logs(action);
    """)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (17)
        ON CONFLICT (version) DO NOTHING;
    """)

    print("[DB] v17 마이그레이션 완료 — admin_audit_logs 테이블 생성")


def _migrate_to_v18(cur) -> None:
    """v18: 범용 로그 시스템 — app_runs(실행 이력) + app_logs(상세 로그)

    analyzer, api, 관리 작업 등 모든 실행의 로그를 DB에 저장하여
    웹 UI에서 조회하고 문제를 진단할 수 있다.
    """
    cur.execute("""
        CREATE TABLE IF NOT EXISTS app_runs (
            id SERIAL PRIMARY KEY,
            run_type VARCHAR(50) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'running',
            started_at TIMESTAMP DEFAULT NOW(),
            finished_at TIMESTAMP,
            duration_sec NUMERIC(10,2),
            summary TEXT,
            error_message TEXT,
            meta JSONB,
            session_id INT REFERENCES analysis_sessions(id) ON DELETE SET NULL
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS app_logs (
            id SERIAL PRIMARY KEY,
            run_id INT REFERENCES app_runs(id) ON DELETE CASCADE,
            level VARCHAR(10) NOT NULL DEFAULT 'INFO',
            source VARCHAR(100),
            message TEXT NOT NULL,
            detail TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)

    # 인덱스
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_app_runs_type_started
            ON app_runs(run_type, started_at DESC);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_app_runs_status
            ON app_runs(status);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_app_logs_run_id
            ON app_logs(run_id, created_at);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_app_logs_level
            ON app_logs(level);
    """)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (18)
        ON CONFLICT (version) DO NOTHING;
    """)

    print("[DB] v18 마이그레이션 완료 — app_runs/app_logs 테이블 생성")


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

            if current < 2:
                _migrate_to_v2(cur)

            if current < 3:
                _migrate_to_v3(cur)

            if current < 4:
                _migrate_to_v4(cur)

            if current < 5:
                _migrate_to_v5(cur)

            if current < 6:
                _migrate_to_v6(cur)

            if current < 7:
                _migrate_to_v7(cur)

            if current < 8:
                _migrate_to_v8(cur)

            if current < 9:
                _migrate_to_v9(cur)

            if current < 10:
                _migrate_to_v10(cur)

            if current < 11:
                _migrate_to_v11(cur)

            if current < 12:
                _migrate_to_v12(cur)

            if current < 13:
                _migrate_to_v13(cur)

            if current < 14:
                _migrate_to_v14(cur)

            if current < 15:
                _migrate_to_v15(cur)

            if current < 16:
                _migrate_to_v16(cur)

            if current < 17:
                _migrate_to_v17(cur)

            if current < 18:
                _migrate_to_v18(cur)

        conn.commit()
        print("[DB] 테이블 초기화 완료")
    finally:
        conn.close()


def _validate_proposal(proposal: dict) -> dict:
    """투자 제안 저장 전 가격 데이터 검증 — 잘못된 값보다 NULL이 낫다"""

    cur_price = proposal.get("current_price")
    tgt_low = proposal.get("target_price_low")
    tgt_high = proposal.get("target_price_high")

    # 1) 가격 소스가 없으면(AI 추정치) current_price 제거
    if proposal.get("price_source") is None and cur_price is not None:
        proposal["current_price"] = None
        cur_price = None

    # 2) 현재가 비정상 값 필터 (0 이하)
    if cur_price is not None:
        try:
            if float(cur_price) <= 0:
                proposal["current_price"] = None
                proposal["price_source"] = None
                cur_price = None
        except (ValueError, TypeError):
            proposal["current_price"] = None
            proposal["price_source"] = None
            cur_price = None

    # 3) 목표가 상한 < 하한이면 스왑
    if tgt_low is not None and tgt_high is not None:
        try:
            tl, th = float(tgt_low), float(tgt_high)
            if tl > th:
                proposal["target_price_low"] = tgt_high
                proposal["target_price_high"] = tgt_low
                tgt_low, tgt_high = tgt_high, tgt_low
        except (ValueError, TypeError):
            pass

    # 4) 현재가 없으면 upside 계산 불가 → null
    if cur_price is None:
        proposal["upside_pct"] = None

    # 5) 목표가가 현재가의 50% 미만이면 AI 추정 목표가로 판단 → 무효화
    if cur_price is not None and tgt_low is not None:
        try:
            cp, tl = float(cur_price), float(tgt_low)
            if cp > 0 and tl < cp * 0.5:
                proposal["target_price_low"] = None
                proposal["target_price_high"] = None
                proposal["upside_pct"] = None
        except (ValueError, TypeError):
            pass

    return proposal


def save_analysis(cfg: DatabaseConfig, analysis_date: str, result: dict) -> int:
    """분석 결과를 DB에 저장하고 session_id 반환

    v2 확장 필드는 있으면 저장, 없으면 NULL (하위호환)
    """
    conn = get_connection(cfg)
    try:
        with conn.cursor() as cur:
            # 1) 세션 생성 (같은 날짜면 기존 데이터 삭제 후 재생성)
            cur.execute(
                "DELETE FROM analysis_sessions WHERE analysis_date = %s",
                (analysis_date,)
            )
            cur.execute(
                """INSERT INTO analysis_sessions
                   (analysis_date, market_summary, risk_temperature, data_sources)
                   VALUES (%s, %s, %s, %s) RETURNING id""",
                (analysis_date, result.get("market_summary"),
                 result.get("risk_temperature"), result.get("data_sources"))
            )
            session_id = cur.fetchone()[0]

            # 2) 글로벌 이슈 저장
            issues = result.get("issues", [])
            issue_id_map = {}
            for i, issue in enumerate(issues):
                cur.execute(
                    """INSERT INTO global_issues
                       (session_id, category, region, title, summary, source,
                        importance, impact_short, impact_mid, impact_long,
                        historical_analogue)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       RETURNING id""",
                    (session_id, issue.get("category"), issue.get("region"),
                     issue.get("title"), issue.get("summary"),
                     issue.get("source"), issue.get("importance", 3),
                     issue.get("impact_short"), issue.get("impact_mid"),
                     issue.get("impact_long"), issue.get("historical_analogue"))
                )
                issue_id_map[i] = cur.fetchone()[0]

            # 3) 투자 테마 저장
            themes = result.get("themes", [])
            for theme in themes:
                related_ids = [
                    issue_id_map[idx]
                    for idx in theme.get("related_issue_indices", [])
                    if idx in issue_id_map
                ]
                cur.execute(
                    """INSERT INTO investment_themes
                       (session_id, theme_name, theme_key, description, related_issue_ids,
                        confidence_score, time_horizon, key_indicators,
                        theme_type, theme_validity)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                    (session_id, theme.get("theme_name"),
                     _resolve_theme_key(theme),
                     theme.get("description"),
                     related_ids, theme.get("confidence_score"),
                     theme.get("time_horizon"), theme.get("key_indicators"),
                     theme.get("theme_type"), theme.get("theme_validity"))
                )
                theme_id = cur.fetchone()[0]

                # 3-a) 시나리오 분석 저장
                for scenario in theme.get("scenarios", []):
                    cur.execute(
                        """INSERT INTO theme_scenarios
                           (theme_id, scenario_type, probability, description,
                            key_assumptions, market_impact)
                           VALUES (%s, %s, %s, %s, %s, %s)""",
                        (theme_id, scenario.get("scenario_type"),
                         scenario.get("probability"), scenario.get("description"),
                         scenario.get("key_assumptions"), scenario.get("market_impact"))
                    )

                # 3-b) 매크로 영향 저장
                for macro in theme.get("macro_impacts", []):
                    cur.execute(
                        """INSERT INTO macro_impacts
                           (theme_id, variable_name, base_case, worse_case,
                            better_case, unit)
                           VALUES (%s, %s, %s, %s, %s, %s)""",
                        (theme_id, macro.get("variable_name"),
                         macro.get("base_case"), macro.get("worse_case"),
                         macro.get("better_case"), macro.get("unit"))
                    )

                # 4) 투자 제안 저장
                for proposal in theme.get("proposals", []):
                    # 가격 데이터 검증
                    proposal = _validate_proposal(proposal)

                    # upside_pct를 현재가·목표저가 기반으로 재계산
                    cur_price = proposal.get("current_price")
                    tgt_low = proposal.get("target_price_low")
                    upside = proposal.get("upside_pct")
                    if cur_price and tgt_low:
                        try:
                            cp = float(cur_price)
                            tl = float(tgt_low)
                            if cp > 0 and tl > 0:
                                upside = round((tl - cp) / cp * 100, 2)
                        except (ValueError, TypeError):
                            pass
                    cur.execute(
                        """INSERT INTO investment_proposals
                           (theme_id, asset_type, asset_name, ticker, market,
                            action, conviction, rationale, risk_factors,
                            entry_condition, exit_condition, target_allocation,
                            current_price, target_price_low, target_price_high,
                            upside_pct, sentiment_score, quant_score,
                            sector, currency, vendor_tier, supply_chain_position,
                            discovery_type, price_momentum_check, price_source,
                            return_1m_pct, return_3m_pct, return_6m_pct, return_1y_pct)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           RETURNING id""",
                        (theme_id, proposal.get("asset_type"),
                         proposal.get("asset_name"), proposal.get("ticker"),
                         proposal.get("market"), proposal.get("action"),
                         proposal.get("conviction"),
                         proposal.get("rationale"), proposal.get("risk_factors"),
                         proposal.get("entry_condition"), proposal.get("exit_condition"),
                         proposal.get("target_allocation"),
                         proposal.get("current_price"), proposal.get("target_price_low"),
                         proposal.get("target_price_high"), upside,
                         proposal.get("sentiment_score"), proposal.get("quant_score"),
                         proposal.get("sector"), proposal.get("currency"),
                         proposal.get("vendor_tier"), proposal.get("supply_chain_position"),
                         proposal.get("discovery_type"), proposal.get("price_momentum_check"),
                         proposal.get("price_source"),
                         proposal.get("return_1m_pct"), proposal.get("return_3m_pct"),
                         proposal.get("return_6m_pct"), proposal.get("return_1y_pct"))
                    )
                    proposal_id = cur.fetchone()[0]

                    # 4-a) 종목 심층분석 저장 (있는 경우)
                    stock_detail = proposal.get("stock_analysis")
                    if stock_detail:
                        cur.execute(
                            """INSERT INTO stock_analyses
                               (proposal_id, company_overview, financial_summary,
                                dcf_fair_value, dcf_wacc, industry_position,
                                momentum_summary, risk_summary, bull_case,
                                bear_case, factor_scores, report_markdown)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (proposal_id,
                             stock_detail.get("company_overview"),
                             json.dumps(stock_detail.get("financial_summary"), ensure_ascii=False)
                                if stock_detail.get("financial_summary") else None,
                             stock_detail.get("dcf_fair_value"),
                             stock_detail.get("dcf_wacc"),
                             stock_detail.get("industry_position"),
                             stock_detail.get("momentum_summary"),
                             stock_detail.get("risk_summary"),
                             stock_detail.get("bull_case"),
                             stock_detail.get("bear_case"),
                             json.dumps(stock_detail.get("factor_scores"), ensure_ascii=False)
                                if stock_detail.get("factor_scores") else None,
                             stock_detail.get("report_markdown"))
                        )

            # 5) 추적 데이터 갱신
            _update_tracking(cur, analysis_date, themes, session_id)

            # 6) 구독 알림 생성
            _generate_notifications(cur, session_id, themes)

        conn.commit()
        print(f"[DB] 세션 #{session_id} 저장 완료 — 이슈 {len(issues)}건, 테마 {len(themes)}건")
        return session_id
    finally:
        conn.close()


def save_news_articles(cfg: DatabaseConfig, session_id: int, articles: list[dict]) -> int:
    """수집된 뉴스 기사를 DB에 저장

    Args:
        articles: [{"category", "source", "title", "title_ko",
                     "summary", "summary_ko", "link", "published"}]
    Returns:
        저장된 기사 수
    """
    if not articles:
        return 0

    conn = get_connection(cfg)
    try:
        with conn.cursor() as cur:
            for a in articles:
                cur.execute(
                    """INSERT INTO news_articles
                       (session_id, category, source, title, title_ko, summary, summary_ko, link, published)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (session_id, a.get("category"), a.get("source"),
                     a.get("title"), a.get("title_ko"),
                     a.get("summary"), a.get("summary_ko"),
                     a.get("link"), a.get("published"))
                )
        conn.commit()
        return len(articles)
    finally:
        conn.close()


def get_untranslated_news(cfg: DatabaseConfig) -> list[dict]:
    """title_ko 또는 summary_ko가 NULL인 뉴스 기사 조회"""
    conn = get_connection(cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, title, summary FROM news_articles
                WHERE title_ko IS NULL OR summary_ko IS NULL
                ORDER BY id
            """)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def update_news_title_ko(cfg: DatabaseConfig, updates: list[tuple[int, str]]) -> int:
    """뉴스 기사 제목 한글 번역 일괄 업데이트

    Args:
        updates: [(article_id, title_ko), ...]
    Returns:
        업데이트된 건수
    """
    if not updates:
        return 0

    conn = get_connection(cfg)
    try:
        with conn.cursor() as cur:
            for article_id, title_ko in updates:
                cur.execute(
                    "UPDATE news_articles SET title_ko = %s WHERE id = %s",
                    (title_ko, article_id)
                )
        conn.commit()
        return len(updates)
    finally:
        conn.close()


def update_news_translation(cfg: DatabaseConfig,
                            updates: list[tuple[int, str, str]]) -> int:
    """뉴스 기사 제목+요약 한글 번역 일괄 업데이트

    Args:
        updates: [(article_id, title_ko, summary_ko), ...]
    Returns:
        업데이트된 건수
    """
    if not updates:
        return 0

    conn = get_connection(cfg)
    try:
        with conn.cursor() as cur:
            for article_id, title_ko, summary_ko in updates:
                cur.execute(
                    "UPDATE news_articles SET title_ko = %s, summary_ko = %s WHERE id = %s",
                    (title_ko, summary_ko, article_id)
                )
        conn.commit()
        return len(updates)
    finally:
        conn.close()


def get_recent_recommendations(cfg: DatabaseConfig, days: int = 7) -> list[dict]:
    """최근 N일간 추천된 종목 이력 조회 (중복 제거 피드백용)

    Returns:
        [{"ticker": "005930", "asset_name": "삼성전자", "theme_name": "AI 반도체",
          "action": "buy", "conviction": "high", "count": 3,
          "first_date": "2026-04-07", "last_date": "2026-04-13"}]
    """
    conn = get_connection(cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    p.ticker,
                    p.asset_name,
                    t.theme_name,
                    p.action,
                    p.conviction,
                    COUNT(*) as count,
                    MIN(s.analysis_date)::text as first_date,
                    MAX(s.analysis_date)::text as last_date
                FROM investment_proposals p
                JOIN investment_themes t ON p.theme_id = t.id
                JOIN analysis_sessions s ON t.session_id = s.id
                WHERE s.analysis_date >= CURRENT_DATE - %s
                  AND p.ticker IS NOT NULL
                GROUP BY p.ticker, p.asset_name, t.theme_name, p.action, p.conviction
                ORDER BY count DESC, p.ticker
            """, (days,))
            return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        print(f"[DB] 최근 추천 이력 조회 실패: {e}")
        return []
    finally:
        conn.close()


def get_latest_news_titles(cfg: DatabaseConfig) -> list[str]:
    """최근 세션의 뉴스 제목 목록 조회 (뉴스 세트 지문 비교용)"""
    conn = get_connection(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT na.title
                FROM news_articles na
                JOIN analysis_sessions s ON na.session_id = s.id
                WHERE s.analysis_date = (
                    SELECT MAX(analysis_date) FROM analysis_sessions
                )
                ORDER BY na.title
            """)
            return [row[0] for row in cur.fetchall()]
    except Exception as e:
        print(f"[DB] 최근 뉴스 제목 조회 실패: {e}")
        return []
    finally:
        conn.close()


def get_existing_theme_keys(cfg: DatabaseConfig) -> list[dict]:
    """기존 theme_key 목록 조회 (AI 프롬프트 피드백용 — 키 재사용 유도)

    Returns:
        [{"theme_key": "secondary_battery_oversupply",
          "theme_name": "2차전지 공급과잉",
          "last_seen_date": "2026-04-15",
          "appearances": 5}]
    """
    conn = get_connection(cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT theme_key, theme_name, last_seen_date::text, appearances
                FROM theme_tracking
                ORDER BY last_seen_date DESC
                LIMIT 100
            """)
            return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        print(f"[DB] 기존 theme_key 조회 실패: {e}")
        return []
    finally:
        conn.close()


def _generate_notifications(cur, session_id: int, themes: list) -> None:
    """구독 매칭 알림 생성 — 분석 저장 시 호출"""
    # user_subscriptions 테이블이 없으면 스킵 (v12 미적용 환경)
    cur.execute(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'user_subscriptions')"
    )
    if not cur.fetchone()[0]:
        return

    # 이번 분석에 등장한 ticker, theme_key 수집
    tickers = set()
    theme_keys = {}  # key -> theme_name
    for theme in themes:
        tk = _resolve_theme_key(theme)
        if tk:
            theme_keys[tk] = theme.get("theme_name", "")
        # 폴백: 한국어 정규화 키로도 매칭 (기존 구독 호환)
        tk_legacy = _normalize_theme_key(theme.get("theme_name", ""))
        if tk_legacy and tk_legacy != tk:
            theme_keys[tk_legacy] = theme.get("theme_name", "")
        for p in theme.get("proposals", []):
            t = (p.get("ticker") or "").upper().strip()
            if t:
                tickers.add(t)

    if not tickers and not theme_keys:
        return

    # 매칭 구독 조회
    cur.execute(
        "SELECT id, user_id, sub_type, sub_key, label FROM user_subscriptions"
    )
    subs = cur.fetchall()

    noti_count = 0
    for sub in subs:
        title = None
        link = None
        if sub["sub_type"] == "ticker" and sub["sub_key"].upper() in tickers:
            label = sub["label"] or sub["sub_key"]
            title = f"구독 종목 '{label}'이(가) 분석에 등장했습니다"
            link = f"/pages/proposals/history/{sub['sub_key']}"
        elif sub["sub_type"] == "theme" and sub["sub_key"] in theme_keys:
            label = sub["label"] or theme_keys[sub["sub_key"]]
            title = f"구독 테마 '{label}'이(가) 분석에 등장했습니다"
            link = f"/pages/themes/history/{sub['sub_key']}"

        if title:
            cur.execute(
                "INSERT INTO user_notifications (user_id, sub_id, session_id, title, link) "
                "VALUES (%s, %s, %s, %s, %s)",
                (sub["user_id"], sub["id"], session_id, title, link),
            )
            noti_count += 1

    if noti_count:
        print(f"[DB] 구독 알림 {noti_count}건 생성")


def _normalize_theme_key(name: str) -> str:
    """테마명 정규화 — 동일 테마 매칭용 키 생성 (폴백용)"""
    import re
    key = name.strip().lower()
    key = re.sub(r'[·\-/\s]+', '', key)  # 공백, 하이픈, 가운뎃점 제거
    return key


def _resolve_theme_key(theme: dict) -> str:
    """AI 제공 theme_key 우선 사용, 유효하지 않으면 한국어 정규화 폴백"""
    import re
    raw_key = (theme.get("theme_key") or "").strip()
    if raw_key and re.match(r'^[a-z][a-z0-9_]{2,60}$', raw_key):
        return raw_key
    return _normalize_theme_key(theme.get("theme_name", ""))


def _update_tracking(cur, analysis_date: str, themes: list, session_id: int) -> None:
    """테마·종목 추적 데이터를 갱신"""
    # 이전 세션 날짜 조회 (어제 분석이 존재하는지)
    cur.execute("""
        SELECT analysis_date FROM analysis_sessions
        WHERE analysis_date < %s ORDER BY analysis_date DESC LIMIT 1
    """, (analysis_date,))
    prev_row = cur.fetchone()
    prev_date = prev_row[0] if prev_row else None

    # 오늘 등장한 테마 키 목록 (나중에 streak 초기화용)
    today_theme_keys = set()

    for theme in themes:
        theme_name = theme.get("theme_name", "")
        theme_key = _resolve_theme_key(theme)
        if not theme_key:
            continue
        today_theme_keys.add(theme_key)

        # 해당 테마의 최신 theme_id 조회
        cur.execute("""
            SELECT id FROM investment_themes
            WHERE session_id = %s AND theme_name = %s
        """, (session_id, theme_name))
        theme_row = cur.fetchone()
        latest_theme_id = theme_row[0] if theme_row else None
        confidence = theme.get("confidence_score")

        # UPSERT theme_tracking
        cur.execute("""
            INSERT INTO theme_tracking
                (theme_key, theme_name, first_seen_date, last_seen_date,
                 streak_days, appearances, latest_confidence, prev_confidence,
                 latest_theme_id)
            VALUES (%s, %s, %s, %s, 1, 1, %s, NULL, %s)
            ON CONFLICT (theme_key) DO UPDATE SET
                theme_name = EXCLUDED.theme_name,
                last_seen_date = EXCLUDED.last_seen_date,
                appearances = theme_tracking.appearances + 1,
                prev_confidence = theme_tracking.latest_confidence,
                latest_confidence = EXCLUDED.latest_confidence,
                latest_theme_id = EXCLUDED.latest_theme_id,
                streak_days = CASE
                    WHEN theme_tracking.last_seen_date = %s::date - INTERVAL '1 day'
                    THEN theme_tracking.streak_days + 1
                    ELSE 1
                END
        """, (theme_key, theme_name, analysis_date, analysis_date,
              confidence, latest_theme_id, analysis_date))

        # 종목 추적 갱신
        for proposal in theme.get("proposals", []):
            ticker = proposal.get("ticker")
            if not ticker:
                continue

            cur.execute("""
                SELECT id FROM investment_proposals
                WHERE theme_id = %s AND ticker = %s
                ORDER BY id DESC LIMIT 1
            """, (latest_theme_id, ticker))
            prop_row = cur.fetchone()
            latest_proposal_id = prop_row[0] if prop_row else None

            cur.execute("""
                INSERT INTO proposal_tracking
                    (ticker, asset_name, theme_key, first_recommended_date,
                     last_recommended_date, recommendation_count,
                     latest_action, prev_action, latest_conviction,
                     latest_target_price_low, latest_target_price_high,
                     prev_target_price_low, prev_target_price_high,
                     latest_quant_score, latest_sentiment_score,
                     latest_proposal_id)
                VALUES (%s, %s, %s, %s, %s, 1, %s, NULL, %s, %s, %s, NULL, NULL, %s, %s, %s)
                ON CONFLICT (ticker, theme_key) DO UPDATE SET
                    asset_name = EXCLUDED.asset_name,
                    last_recommended_date = EXCLUDED.last_recommended_date,
                    recommendation_count = proposal_tracking.recommendation_count + 1,
                    prev_action = proposal_tracking.latest_action,
                    latest_action = EXCLUDED.latest_action,
                    latest_conviction = EXCLUDED.latest_conviction,
                    prev_target_price_low = proposal_tracking.latest_target_price_low,
                    prev_target_price_high = proposal_tracking.latest_target_price_high,
                    latest_target_price_low = EXCLUDED.latest_target_price_low,
                    latest_target_price_high = EXCLUDED.latest_target_price_high,
                    latest_quant_score = EXCLUDED.latest_quant_score,
                    latest_sentiment_score = EXCLUDED.latest_sentiment_score,
                    latest_proposal_id = EXCLUDED.latest_proposal_id
            """, (ticker, proposal.get("asset_name"), theme_key,
                  analysis_date, analysis_date,
                  proposal.get("action"), proposal.get("conviction"),
                  proposal.get("target_price_low"), proposal.get("target_price_high"),
                  proposal.get("quant_score"), proposal.get("sentiment_score"),
                  latest_proposal_id))


def save_top_picks(
    cfg: DatabaseConfig, session_id: int, analysis_date: str,
    picks: list[dict], source: str = "rule",
) -> int:
    """일별 Top Picks 저장 (기존 분 삭제 후 재삽입)

    Args:
        picks: [{proposal_id, rank, score_rule, score_final, score_breakdown,
                 rationale_text, key_risk}, ...]
        source: 'rule' | 'ai_rerank'
    Returns:
        저장된 픽 수
    """
    if not picks:
        return 0

    conn = get_connection(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM daily_top_picks WHERE analysis_date = %s",
                (analysis_date,),
            )
            for pk in picks:
                cur.execute(
                    """INSERT INTO daily_top_picks
                       (session_id, analysis_date, rank, proposal_id,
                        score_rule, score_final, score_breakdown,
                        rationale_text, key_risk, source)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (session_id, analysis_date, pk["rank"], pk["proposal_id"],
                     pk.get("score_rule"),
                     pk.get("score_final", pk.get("score_rule")),
                     json.dumps(pk.get("score_breakdown") or {}, ensure_ascii=False),
                     pk.get("rationale_text"),
                     pk.get("key_risk"),
                     source),
                )
        conn.commit()
        print(f"[DB] Top Picks {len(picks)}건 저장 완료 (source={source})")
        return len(picks)
    finally:
        conn.close()


def update_top_picks_ai_rerank(
    cfg: DatabaseConfig, analysis_date: str, ai_results: list[dict],
) -> int:
    """AI 재정렬 결과로 기존 Top Picks 덮어쓰기

    Args:
        ai_results: [{proposal_id, rank, rationale_text, key_risk, score_final}, ...]
    Returns:
        업데이트된 픽 수
    """
    if not ai_results:
        return 0

    conn = get_connection(cfg)
    try:
        with conn.cursor() as cur:
            # 기존 rule 레코드 삭제 → AI 재정렬 결과로 교체
            cur.execute(
                "SELECT session_id, proposal_id, score_rule, score_breakdown "
                "FROM daily_top_picks WHERE analysis_date = %s",
                (analysis_date,),
            )
            existing = {
                row[1]: {"session_id": row[0], "score_rule": row[2], "score_breakdown": row[3]}
                for row in cur.fetchall()
            }

            cur.execute(
                "DELETE FROM daily_top_picks WHERE analysis_date = %s",
                (analysis_date,),
            )

            for r in ai_results:
                proposal_id = r.get("proposal_id")
                if proposal_id is None or proposal_id not in existing:
                    continue
                ex = existing[proposal_id]
                cur.execute(
                    """INSERT INTO daily_top_picks
                       (session_id, analysis_date, rank, proposal_id,
                        score_rule, score_final, score_breakdown,
                        rationale_text, key_risk, source)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'ai_rerank')""",
                    (ex["session_id"], analysis_date, r["rank"], proposal_id,
                     ex["score_rule"],
                     r.get("score_final", ex["score_rule"]),
                     json.dumps(ex["score_breakdown"] or {}, ensure_ascii=False)
                       if not isinstance(ex["score_breakdown"], str)
                       else ex["score_breakdown"],
                     r.get("rationale_text"),
                     r.get("key_risk")),
                )
        conn.commit()
        print(f"[DB] Top Picks AI 재정렬 {len(ai_results)}건 반영 완료")
        return len(ai_results)
    finally:
        conn.close()
