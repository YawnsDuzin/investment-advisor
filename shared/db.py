"""PostgreSQL 데이터베이스 관리 모듈"""
import json
import psycopg2
from psycopg2.extras import execute_values, RealDictCursor
from shared.config import DatabaseConfig

# ── 스키마 버전 관리 ──────────────────────────────
SCHEMA_VERSION = 22  # v1~v21 + v22: 고객 문의(inquiry) 테이블


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


def _migrate_to_v19(cur) -> None:
    """v19: 추천 후 실제 수익률 추적 — entry_price, post_return, 가격 스냅샷

    기존 return_*_pct(과거 모멘텀)와 별개로, 추천 이후 실제 성과를 측정한다.
    entry_price: 추천 시점 확정 기준가 (current_price 복사)
    post_return_*_pct: 추천 후 N개월 실제 수익률
    proposal_price_snapshots: 일별 종가 스냅샷 (수익률 계산 원본)
    """
    # 1) entry_price + post_return 컬럼 추가
    cur.execute("""
        ALTER TABLE investment_proposals
            ADD COLUMN IF NOT EXISTS entry_price NUMERIC(15,2),
            ADD COLUMN IF NOT EXISTS post_return_1m_pct NUMERIC(7,2),
            ADD COLUMN IF NOT EXISTS post_return_3m_pct NUMERIC(7,2),
            ADD COLUMN IF NOT EXISTS post_return_6m_pct NUMERIC(7,2),
            ADD COLUMN IF NOT EXISTS post_return_1y_pct NUMERIC(7,2);
    """)

    # 2) 기존 데이터 백필 — current_price → entry_price
    cur.execute("""
        UPDATE investment_proposals
        SET entry_price = current_price
        WHERE entry_price IS NULL AND current_price IS NOT NULL;
    """)

    # 3) 가격 스냅샷 테이블
    cur.execute("""
        CREATE TABLE IF NOT EXISTS proposal_price_snapshots (
            id SERIAL PRIMARY KEY,
            proposal_id INT NOT NULL REFERENCES investment_proposals(id) ON DELETE CASCADE,
            snapshot_date DATE NOT NULL,
            price NUMERIC(15,2) NOT NULL,
            price_source VARCHAR(30),
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(proposal_id, snapshot_date)
        );
    """)

    # 4) 인덱스
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_pps_proposal_date
            ON proposal_price_snapshots(proposal_id, snapshot_date DESC);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_proposals_entry_price
            ON investment_proposals(entry_price)
            WHERE entry_price IS NOT NULL;
    """)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (19)
        ON CONFLICT (version) DO NOTHING;
    """)

    print("[DB] v19 마이그레이션 완료 — 추천 후 실제 수익률 추적 (entry_price, post_return, 스냅샷)")


def _migrate_to_v20(cur) -> None:
    """v20: KRX 확장 데이터 — 투자자 수급, 공매도, 국채 금리, 종목 메타

    Stage 2 분석에 외국인/기관 수급, 공매도 현황, 국채 금리 데이터를 주입하여
    투자 의사결정 품질을 향상시킨다.
    """
    # 1) 투자자별 매매 동향
    cur.execute("""
        CREATE TABLE IF NOT EXISTS investor_trading_data (
            id SERIAL PRIMARY KEY,
            proposal_id INT REFERENCES investment_proposals(id) ON DELETE CASCADE,
            snapshot_date DATE NOT NULL,
            foreign_net_buy_5d BIGINT,
            foreign_net_buy_20d BIGINT,
            inst_net_buy_5d BIGINT,
            inst_net_buy_20d BIGINT,
            foreign_consecutive_days INT,
            daily_data JSONB,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(proposal_id, snapshot_date)
        );
    """)

    # 2) 공매도 현황
    cur.execute("""
        CREATE TABLE IF NOT EXISTS short_selling_data (
            id SERIAL PRIMARY KEY,
            proposal_id INT REFERENCES investment_proposals(id) ON DELETE CASCADE,
            snapshot_date DATE NOT NULL,
            short_balance_ratio_pct NUMERIC(7,2),
            short_volume_ratio_pct NUMERIC(7,2),
            short_balance_change_5d_pct NUMERIC(7,2),
            squeeze_risk VARCHAR(10),
            daily_data JSONB,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(proposal_id, snapshot_date)
        );
    """)

    # 3) 국채 금리 스냅샷 (세션당 1건)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bond_yields (
            id SERIAL PRIMARY KEY,
            session_id INT REFERENCES analysis_sessions(id) ON DELETE CASCADE,
            snapshot_date DATE NOT NULL,
            kr_1y NUMERIC(6,3),
            kr_2y NUMERIC(6,3),
            kr_3y NUMERIC(6,3),
            kr_5y NUMERIC(6,3),
            kr_10y NUMERIC(6,3),
            kr_30y NUMERIC(6,3),
            corp_aa NUMERIC(6,3),
            cd_91d NUMERIC(6,3),
            spread_10y_2y NUMERIC(6,3),
            yield_curve_status VARCHAR(20),
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(session_id, snapshot_date)
        );
    """)

    # 4) investment_proposals 확장 컬럼
    cur.execute("""
        ALTER TABLE investment_proposals
            ADD COLUMN IF NOT EXISTS foreign_ownership_pct NUMERIC(6,2),
            ADD COLUMN IF NOT EXISTS index_membership TEXT[],
            ADD COLUMN IF NOT EXISTS squeeze_risk VARCHAR(10),
            ADD COLUMN IF NOT EXISTS foreign_net_buy_signal VARCHAR(20);
    """)

    # 5) 인덱스
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_investor_trading_proposal
            ON investor_trading_data(proposal_id, snapshot_date DESC);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_short_selling_proposal
            ON short_selling_data(proposal_id, snapshot_date DESC);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_bond_yields_date
            ON bond_yields(snapshot_date DESC);
    """)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (20)
        ON CONFLICT (version) DO NOTHING;
    """)

    print("[DB] v20 마이그레이션 완료 — KRX 확장 데이터 (수급/공매도/금리) 테이블 생성")


def _migrate_to_v21(cur) -> None:
    """v21: 투자 교육 콘텐츠 + AI 튜터 채팅 테이블"""

    # 1) 교육 토픽 (정적 콘텐츠)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS education_topics (
            id SERIAL PRIMARY KEY,
            category VARCHAR(50) NOT NULL,
            slug VARCHAR(100) UNIQUE NOT NULL,
            title VARCHAR(200) NOT NULL,
            summary TEXT,
            content TEXT NOT NULL,
            examples JSONB DEFAULT '[]'::jsonb,
            difficulty VARCHAR(20) DEFAULT 'beginner',
            sort_order INT DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)

    # 2) AI 튜터 채팅 세션
    cur.execute("""
        CREATE TABLE IF NOT EXISTS education_chat_sessions (
            id SERIAL PRIMARY KEY,
            user_id INT REFERENCES users(id) ON DELETE SET NULL,
            topic_id INT REFERENCES education_topics(id) ON DELETE SET NULL,
            title VARCHAR(200),
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """)

    # 3) AI 튜터 채팅 메시지
    cur.execute("""
        CREATE TABLE IF NOT EXISTS education_chat_messages (
            id SERIAL PRIMARY KEY,
            chat_session_id INT REFERENCES education_chat_sessions(id) ON DELETE CASCADE,
            role VARCHAR(10) NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)

    # 4) 인덱스
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_edu_topics_category
            ON education_topics(category, sort_order);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_edu_chat_sessions_user
            ON education_chat_sessions(user_id, updated_at DESC);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_edu_chat_messages_session
            ON education_chat_messages(chat_session_id, created_at);
    """)

    # 5) 시드 데이터 — 카테고리별 핵심 토픽
    cur.execute("SELECT COUNT(*) FROM education_topics")
    if cur.fetchone()[0] == 0:
        _seed_education_topics(cur)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (21)
        ON CONFLICT (version) DO NOTHING;
    """)

    print("[DB] v21 마이그레이션 완료 — 투자 교육 콘텐츠 + AI 튜터 채팅 테이블 생성")


def _migrate_to_v22(cur) -> None:
    """v22: 고객 문의/개선요청 게시판"""

    # 1) 문의 게시글
    cur.execute("""
        CREATE TABLE IF NOT EXISTS inquiries (
            id SERIAL PRIMARY KEY,
            user_id INT REFERENCES users(id) ON DELETE SET NULL,
            user_email VARCHAR(200),
            category VARCHAR(30) NOT NULL DEFAULT 'general',
            title VARCHAR(300) NOT NULL,
            content TEXT NOT NULL,
            is_private BOOLEAN NOT NULL DEFAULT FALSE,
            status VARCHAR(20) NOT NULL DEFAULT 'open',
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """)

    # 2) 문의 답변/코멘트
    cur.execute("""
        CREATE TABLE IF NOT EXISTS inquiry_replies (
            id SERIAL PRIMARY KEY,
            inquiry_id INT NOT NULL REFERENCES inquiries(id) ON DELETE CASCADE,
            user_id INT REFERENCES users(id) ON DELETE SET NULL,
            user_email VARCHAR(200),
            role VARCHAR(20) NOT NULL DEFAULT 'user',
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)

    # 3) 인덱스
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_inquiries_user
            ON inquiries(user_id, created_at DESC);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_inquiries_status
            ON inquiries(status, created_at DESC);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_inquiries_private
            ON inquiries(is_private, created_at DESC);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_inquiry_replies_inquiry
            ON inquiry_replies(inquiry_id, created_at);
    """)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (22)
        ON CONFLICT (version) DO NOTHING;
    """)

    print("[DB] v22 마이그레이션 완료 — 고객 문의 게시판 테이블 생성")


def _seed_education_topics(cur) -> None:
    """교육 토픽 시드 데이터 삽입"""
    topics = [
        # ── 기초 개념 ──
        {
            "category": "basics", "slug": "per-pbr-roe",
            "title": "PER·PBR·ROE — 가치평가의 3대 지표",
            "summary": "주식의 '비싼지 싼지'를 판단하는 핵심 밸류에이션 지표를 실제 사례로 배웁니다.",
            "difficulty": "beginner", "sort_order": 1,
            "content": """## PER (주가수익비율)

**공식**: PER = 주가 ÷ 주당순이익(EPS)

\"이 회사의 순이익 대비 주가가 몇 배인가?\"를 보여줍니다.

- **PER 10** → 현재 이익 수준으로 투자금 회수에 10년
- **PER이 낮으면** → 저평가 *가능성* (but 이익이 줄어들 예정일 수도)
- **PER이 높으면** → 고평가 *가능성* (but 고성장 기대가 반영된 것일 수도)

### 업종별 PER 기준이 다르다

| 업종 | 평균 PER | 이유 |
|------|----------|------|
| 은행 | 5~8배 | 안정적이지만 저성장 |
| IT/플랫폼 | 25~40배 | 고성장 기대 프리미엄 |
| 바이오 | 적자(음수) | 미래 매출에 베팅 |

## PBR (주가순자산비율)

**공식**: PBR = 주가 ÷ 주당순자산(BPS)

\"회사를 당장 청산하면 주가 대비 얼마를 돌려받을 수 있는가?\"

- **PBR < 1** → 장부가치보다 싸게 거래 (자산주·가치주 후보)
- **PBR > 3** → 브랜드·기술력 등 무형자산 프리미엄

## ROE (자기자본이익률)

**공식**: ROE = 순이익 ÷ 자기자본 × 100

\"주주의 돈으로 얼마나 효율적으로 벌고 있는가?\"

- **ROE 15%+** → 우량 기업의 기준선 (워런 버핏 기준)
- **ROE가 지속 상승** → 경쟁력 강화 신호

### 세 지표의 관계

> PER = PBR ÷ ROE

ROE가 높은데 PBR이 낮다면? → PER도 낮아지므로 **저평가 가능성** 높음.""",
            "examples": json.dumps([
                {"title": "삼성전자 (2023)", "description": "PER 14배, PBR 1.3배, ROE 9%. 반도체 다운사이클에서 이익 감소 → PER 상승. 이전 업사이클(2021)에는 PER 8배, ROE 20%였음. 사이클 주식은 PER만 보면 함정에 빠질 수 있다.", "period": "2021~2023", "lesson": "사이클 산업에서 PER이 낮을 때가 오히려 고점(이익 극대화)일 수 있다"},
                {"title": "카카오 vs 은행주 (2021)", "description": "카카오 PER 100배+ vs KB금융 PER 5배. 결과적으로 카카오는 2022년 급락(-60%), KB금융은 배당과 함께 안정. 고PER 성장주는 기대치 하락 시 낙폭이 크다.", "period": "2021~2022", "lesson": "PER이 높은 성장주는 기대가 꺾이면 낙폭도 크다. 밸류에이션 지표는 산업 맥락에서 해석해야 한다"}
            ]),
        },
        {
            "category": "basics", "slug": "market-cap",
            "title": "시가총액 — 기업 크기의 진짜 의미",
            "summary": "시가총액이 왜 '주가'보다 중요한지, 대형주·중형주·소형주의 특성 차이를 배웁니다.",
            "difficulty": "beginner", "sort_order": 2,
            "content": """## 시가총액이란?

**공식**: 시가총액 = 현재 주가 × 발행주식 수

\"시장이 이 회사에 매기는 총 가격표\"

### 주가 vs 시가총액

| | A회사 | B회사 |
|--|-------|-------|
| 주가 | 5,000원 | 500,000원 |
| 발행주식 | 1억주 | 10만주 |
| **시가총액** | **5,000억** | **500억** |

→ 주가가 100배 비싼 B회사가 실제로는 10배 작은 회사!

### 시총 구간별 특성

| 구간 | 한국 기준 | 특성 |
|------|-----------|------|
| 대형주 | 10조+ | 안정적, 유동성 풍부, 기관 선호 |
| 중형주 | 1~10조 | 성장+안정의 균형 |
| 소형주 | 1조 미만 | 고성장 가능, but 변동성·유동성 리스크 |

### 시가총액을 보는 실전 팁

1. **같은 업종 내 비교**: 시총이 비슷한 회사끼리 PER·PBR 비교가 의미 있음
2. **M&A 관점**: \"이 회사를 통째로 사려면 얼마?\" → 시총이 그 가격
3. **지수 편입 효과**: 시총이 커지면 KOSPI200·MSCI 등에 편입 → 패시브 자금 유입""",
            "examples": json.dumps([
                {"title": "삼성전자 액면분할 (2018)", "description": "주가 256만원 → 5만원대로 분할. 시가총액은 변함없이 약 300조원. 주가가 급락한 게 아니라 주식 수가 50배로 늘어난 것. 개인 투자자 접근성이 높아지며 거래량은 크게 증가.", "period": "2018.05", "lesson": "주가 자체는 의미가 없다. 시가총액이 기업의 진짜 크기다"},
                {"title": "테슬라 시총 도요타 추월 (2020)", "description": "테슬라 시총 $200B → 도요타($180B) 추월. 당시 테슬라 연 판매량 50만대 vs 도요타 1,000만대. 시장은 '미래 가치'를 시총에 반영한다는 것을 보여준 사례.", "period": "2020.07", "lesson": "시가총액은 현재 실적이 아닌 시장의 미래 기대를 반영한다"}
            ]),
        },
        {
            "category": "basics", "slug": "dividend-yield",
            "title": "배당수익률 — 주식으로 월급 받기",
            "summary": "배당의 구조, 배당수익률 계산법, 배당주 투자의 장단점을 실전 사례로 배웁니다.",
            "difficulty": "beginner", "sort_order": 3,
            "content": """## 배당이란?

회사가 벌어들인 이익의 일부를 **주주에게 직접 현금으로 돌려주는 것**.

**배당수익률** = 주당 배당금 ÷ 현재 주가 × 100

### 예시
- 주가 50,000원, 연 배당금 2,500원 → 배당수익률 **5%**
- 은행 예금 금리 3.5%보다 높음 + 주가 상승 가능성

### 배당의 종류

| 종류 | 설명 | 예시 |
|------|------|------|
| 현금배당 | 가장 일반적. 현금 지급 | 삼성전자 분기배당 |
| 주식배당 | 주식으로 지급 | 소규모 회사에서 가끔 |
| 특별배당 | 일시적 대규모 배당 | 자산 매각 후 |

### 배당주 투자의 핵심 체크리스트

1. **배당성향** (= 배당금 ÷ 순이익): 40~60%가 건전. 100% 넘으면 위험
2. **배당 지속성**: 최근 5~10년 연속 배당 유지·증가 여부
3. **배당락일**: 이 날 이전에 주식을 보유해야 배당 수령 자격
4. **함정**: 주가 급락으로 배당수익률이 높아 보이는 경우 주의""",
            "examples": json.dumps([
                {"title": "한국 고배당주 성과 (2022~2023)", "description": "금리 인상기에 성장주가 급락하는 동안, KT&G(배당수익률 5%+), 하나금융(6%+) 등 고배당주는 상대적으로 안정적 수익. 배당이 하방을 지지하는 '쿠션' 역할.", "period": "2022~2023", "lesson": "약세장에서 배당은 하방 방어와 현금흐름 확보에 효과적이다"},
                {"title": "AT&T 배당 삭감 (2022)", "description": "36년 연속 배당 증가한 '배당귀족' AT&T가 2022년 배당을 47% 삭감. 과도한 부채와 사업 분할이 원인. 고배당에만 의존하면 배당 삭감 시 주가도 동반 하락.", "period": "2022.02", "lesson": "높은 배당수익률만 보지 말고, 배당을 유지할 수 있는 재무 건전성을 확인해야 한다"}
            ]),
        },
        # ── 분석 기법 ──
        {
            "category": "analysis", "slug": "fundamental-vs-technical",
            "title": "펀더멘털 vs 기술적 분석",
            "summary": "두 분석법의 차이, 각각의 장단점, 실전에서 어떻게 조합하는지 배웁니다.",
            "difficulty": "intermediate", "sort_order": 10,
            "content": """## 두 가지 접근법

### 펀더멘털 분석 (Fundamental Analysis)
\"이 회사의 **진짜 가치**는 얼마인가?\"

분석 대상:
- 재무제표 (매출, 이익, 부채)
- 사업 모델·경쟁 우위 (해자, moat)
- 산업 성장성·시장 점유율
- 경영진 역량

**대표 투자자**: 워런 버핏, 피터 린치

### 기술적 분석 (Technical Analysis)
\"가격과 거래량의 **패턴**이 무엇을 말하는가?\"

분석 대상:
- 차트 패턴 (이동평균, 지지/저항선)
- 거래량 변화
- 기술적 지표 (RSI, MACD, 볼린저밴드)
- 추세·모멘텀

**대표 투자자**: 제시 리버모어, 마크 미너비니

### 비교

| 관점 | 펀더멘털 | 기술적 |
|------|----------|--------|
| 시간 | 장기 (6개월~수년) | 단기~중기 (일~수개월) |
| 질문 | \"뭘 살까?\" | \"언제 살까?\" |
| 장점 | 근본 가치 파악 | 타이밍 포착 |
| 단점 | 타이밍 부재 | 근본 가치 무시 |

### 실전: 둘을 조합하라

> 펀더멘털로 **좋은 종목**을 고르고, 기술적 분석으로 **좋은 시점**에 진입

1. 펀더멘털 필터링 → ROE 15%+, 부채비율 100% 미만
2. 기술적 확인 → 이동평균선 위, 거래량 증가 확인
3. 진입 → 지지선 부근에서 매수""",
            "examples": json.dumps([
                {"title": "엔비디아 분석 조합 (2023)", "description": "펀더멘털: AI 수요 폭증 → 데이터센터 GPU 매출 3배 성장. 기술적: 2023년 초 $150에서 200일 이동평균 돌파 + 거래량 급증 → 매수 신호. 두 분석이 같은 방향을 가리킬 때 확신도가 높다.", "period": "2023", "lesson": "펀더멘털과 기술적 신호가 동시에 긍정적일 때가 가장 강력한 매수 시점이다"}
            ]),
        },
        {
            "category": "analysis", "slug": "momentum-investing",
            "title": "모멘텀 투자 — 추세를 따라가는 전략",
            "summary": "모멘텀의 원리, 이 앱의 수익률 데이터를 활용한 모멘텀 판단법을 배웁니다.",
            "difficulty": "intermediate", "sort_order": 11,
            "content": """## 모멘텀이란?

\"오르는 주식은 계속 오르고, 내리는 주식은 계속 내리는 경향\"

학술적으로도 입증된 현상 (Jegadeesh & Titman, 1993):
- **3~12개월** 수익률이 높은 종목은 이후에도 초과 수익을 내는 경향

### 모멘텀 지표 읽는 법

이 앱에서 제공하는 기간별 수익률의 의미:

| 지표 | 해석 |
|------|------|
| **1개월 수익률 > +10%** | 단기 강세 모멘텀 |
| **3개월 수익률 > +20%** | 중기 추세 확인 |
| **6개월 > +30%, 1년 > +50%** | 장기 상승 추세 (강한 모멘텀) |
| **1개월 급등(+20%↑)** | 과열 주의 → 단기 조정 가능성 |

### 모멘텀 투자 규칙

1. **추세 확인**: 단기·중기·장기 수익률이 모두 양(+)
2. **진입**: 신고가 근처 or 조정 후 반등 시
3. **손절**: 추세 이탈 시 기계적 손절 (예: -8%)
4. **회전**: 모멘텀 약화 종목 → 모멘텀 강화 종목으로 교체

### 모멘텀의 함정: 급등주 추격매수

- 1개월 +40%인 종목은 **이미 모멘텀이 소진**되었을 가능성
- 이 앱에서 `price_momentum_check: overheated`로 태깅된 종목은 주의""",
            "examples": json.dumps([
                {"title": "2차전지 모멘텀 사이클 (2023)", "description": "에코프로비엠: 6개월 수익률 +400%. 모멘텀 극대화 구간에서 개인 투자자 대량 매수. 이후 2023년 하반기 -60% 급락. 극단적 모멘텀은 반전 리스크도 극대화된다.", "period": "2023", "lesson": "모멘텀은 따라가되, 과열 신호(급등률, 거래량 폭증)가 보이면 추격매수를 자제해야 한다"}
            ]),
        },
        # ── 리스크 관리 ──
        {
            "category": "risk", "slug": "diversification",
            "title": "분산투자 — 달걀을 한 바구니에 담지 마라",
            "summary": "분산투자의 원리, 적정 종목 수, 자산배분 전략을 실제 포트폴리오 예시로 배웁니다.",
            "difficulty": "beginner", "sort_order": 20,
            "content": """## 분산투자의 원리

> \"예측이 틀려도 살아남기 위한 전략\"

아무리 분석을 잘해도 개별 종목의 미래는 불확실합니다.
분산은 **한 종목의 실패가 포트폴리오 전체를 무너뜨리지 않게** 하는 것.

### 분산의 차원

| 차원 | 예시 |
|------|------|
| **종목** | 삼성전자 + 현대차 + 카카오 + ... |
| **업종/섹터** | IT + 금융 + 헬스케어 + 에너지 |
| **지역** | 한국 + 미국 + 유럽 + 신흥국 |
| **자산군** | 주식 + 채권 + 금 + 부동산(REITs) |
| **시간** | 적립식 매수 (DCA) |

### 적정 종목 수

| 종목 수 | 리스크 감소 효과 |
|---------|-----------------|
| 1~5 | 개별 종목 리스크 매우 높음 |
| 10~15 | **비체계적 리스크 대부분 제거** |
| 20~30 | 추가 분산 효과 미미 |
| 50+ | 관리 어려움, 시장 수익률에 수렴 |

→ **개인 투자자 최적: 10~15종목**, 3~5개 섹터에 분산

### 이 앱 활용법

제안 카드의 `sector`와 `target_allocation`을 확인하여:
- 한 섹터에 30% 이상 집중되지 않았는지 체크
- 다양한 `time_horizon` (단기/중기/장기) 믹스
- `conviction` 높은 종목에 더 많은 비중, 낮은 종목은 소량""",
            "examples": json.dumps([
                {"title": "코로나 쇼크와 섹터 분산 (2020.03)", "description": "항공(-70%), 여행(-60%) 집중 포트폴리오는 치명적 손실. 반면 IT(+20%)·헬스케어(+15%)·필수소비재(0%) 분산 포트폴리오는 3개월 만에 회복. 섹터 분산이 극단적 이벤트에서 포트폴리오를 보호.", "period": "2020.03~06", "lesson": "예측 불가능한 이벤트(블랙스완)에 대비하는 유일한 방법이 분산이다"}
            ]),
        },
        {
            "category": "risk", "slug": "stop-loss",
            "title": "손절과 익절 — 수익을 지키는 기술",
            "summary": "손절·익절의 원칙, 감정적 매매를 피하는 규칙 기반 전략을 배웁니다.",
            "difficulty": "intermediate", "sort_order": 21,
            "content": """## 왜 손절이 중요한가?

### 손실의 비대칭성

| 손실률 | 원금 회복에 필요한 수익률 |
|--------|-------------------------|
| -10% | +11% 필요 |
| -20% | +25% 필요 |
| -30% | +43% 필요 |
| **-50%** | **+100% 필요** |
| -70% | +233% 필요 |

→ **손실이 커질수록 회복이 기하급수적으로 어려워진다**

### 손절 전략

#### 고정 비율 손절
- 매수가 대비 **-7~10%** 하락 시 기계적 매도
- 윌리엄 오닐 (CAN SLIM): \"-7% 룰\"

#### 기술적 손절
- 주요 지지선 이탈 시
- 이동평균선(20일/60일) 하향 돌파 시

### 익절 전략

#### 목표가 도달 시
- 이 앱의 `target_price_low`~`target_price_high` 구간 참고
- 목표가 도달 시 **일부(50%)** 매도 → 나머지는 추세 추종

#### 트레일링 스탑
- 최고가 대비 **-15%** 하락 시 전량 매도
- 수익을 극대화하면서 이익 보호

### 핵심 원칙
> 손절은 작게, 익절은 크게 (손소익대)
> 3번 중 1번만 맞아도 전체 수익이 나는 구조를 만들어라""",
            "examples": json.dumps([
                {"title": "개인 투자자 손실 패턴 연구 (한국거래소)", "description": "개인 투자자의 평균 보유: 수익 종목 23일, 손실 종목 45일. 수익은 빨리 확정하고 손실은 오래 끌어안는 '처분효과'. 기계적 손절 규칙이 없으면 본능적으로 반대로 행동하게 된다.", "period": "2020~2022 분석", "lesson": "사람의 본능(손실 회피)은 투자에서 불리하게 작용한다. 규칙 기반 손절이 감정적 의사결정을 막아준다"}
            ]),
        },
        # ── 매크로 경제 ──
        {
            "category": "macro", "slug": "interest-rates",
            "title": "금리와 주식시장의 관계",
            "summary": "금리 변동이 주가에 미치는 메커니즘, 금리 사이클별 투자 전략을 배웁니다.",
            "difficulty": "intermediate", "sort_order": 30,
            "content": """## 금리는 왜 중요한가?

금리는 **돈의 가격**. 모든 자산의 가치평가에 영향을 미치는 가장 중요한 매크로 변수.

### 금리 ↑ 가 주가에 미치는 영향

1. **할인율 상승** → 미래 이익의 현재 가치 ↓ → 특히 성장주 타격
2. **대출 비용 증가** → 기업 이익 감소, 소비 위축
3. **채권 매력 상승** → 주식에서 채권으로 자금 이동
4. **부동산·레버리지 기업** → 이자 부담 증가

### 금리 사이클과 투자 전략

| 국면 | 금리 방향 | 유리한 자산 | 불리한 자산 |
|------|-----------|-------------|-------------|
| 인상 초기 | ↑ | 금융주, 에너지 | 성장주, 부동산 |
| 인상 후기 | ↑↑ | 현금, 단기채권 | 대부분 주식 |
| 동결/피벗 | → | 우량 성장주 | 방어주 |
| 인하 초기 | ↓ | 기술주, 성장주 | 은행주, 달러 |
| 인하 후기 | ↓↓ | 소형주, 신흥국 | 채권(가격 이미 상승) |

### 이 앱의 매크로 분석 활용

Stage 1 분석의 `macro_impacts`에서 금리 시나리오를 확인:
- `base_case`: 기본 전망
- `worse_case`: 금리 더 오를 경우
- `better_case`: 금리 빨리 내릴 경우

→ 각 시나리오에서 포트폴리오 영향을 사전 점검""",
            "examples": json.dumps([
                {"title": "미국 금리 인상 사이클 (2022~2023)", "description": "Fed 기준금리 0.25% → 5.50%로 525bp 인상. 나스닥 -33%(2022), S&P500 -19%. 특히 ARK Innovation ETF -67%. 금리 인상은 '미래 가치'에 베팅한 성장주를 가장 크게 타격.", "period": "2022~2023", "lesson": "금리 인상기에는 현재 이익이 확실한 가치주·배당주가 방어적이다. 금리 방향만 알아도 큰 실수를 피할 수 있다"}
            ]),
        },
        {
            "category": "macro", "slug": "exchange-rates",
            "title": "환율이 투자에 미치는 영향",
            "summary": "원/달러 환율 변동이 한국 주식과 해외 투자에 미치는 이중 효과를 배웁니다.",
            "difficulty": "intermediate", "sort_order": 31,
            "content": """## 환율의 기본

**원/달러 환율 상승(원화 약세)** = 1달러를 사려면 더 많은 원화 필요

### 환율과 한국 주식

| 환율 방향 | 수혜 업종 | 피해 업종 |
|-----------|-----------|-----------|
| 원화 약세(↑) | 수출주(반도체, 자동차) | 수입주(항공, 정유) |
| 원화 강세(↓) | 내수주, 수입 의존 기업 | 수출 비중 높은 기업 |

### 해외 주식 투자와 환율

미국 주식에 투자하면 **이중 수익/손실**:

> 총 수익 = 주가 수익률 + 환율 수익률

#### 예시
- S&P500 +10% 수익 + 환율(1,200→1,300) +8%
- → 원화 기준 실제 수익 약 **+18.8%**

반대로:
- S&P500 +10% + 환율(1,300→1,200) -7.7%
- → 원화 기준 실제 수익 약 **+1.5%**

### 환 헤지 여부 결정

| 상황 | 전략 |
|------|------|
| 원화 약세 전망 | 환 노출 유지 (헤지 X) |
| 원화 강세 전망 | 환 헤지 (H형 ETF) |
| 모르겠다 | **50% 헤지** (리스크 분산) |""",
            "examples": json.dumps([
                {"title": "원/달러 1,440원 시대 (2022.10)", "description": "환율 1,200 → 1,440원 급등. 같은 기간 S&P500 -20%였지만, 원화 기준으로는 -6% 수준. 환율이 해외 투자 손실을 상쇄. 반면 2023년 환율 하락 시에는 수익도 깎임.", "period": "2022~2023", "lesson": "해외 투자 시 주가와 환율, 두 가지 변수를 동시에 관리해야 한다"}
            ]),
        },
        # ── 실전 활용 ──
        {
            "category": "practical", "slug": "reading-proposal-cards",
            "title": "제안 카드 200% 활용법",
            "summary": "이 앱의 투자 제안 카드에 담긴 정보를 제대로 읽고 활용하는 방법을 배웁니다.",
            "difficulty": "beginner", "sort_order": 40,
            "content": """## 제안 카드의 구성 요소

### 1. 기본 정보
- **asset_name / ticker**: 종목명과 코드
- **action**: `BUY` / `SELL` / `HOLD` / `WATCH` — 추천 행동
- **conviction**: `HIGH` / `MEDIUM` / `LOW` — AI의 확신도

### 2. 가격 정보
- **current_price**: 분석 시점의 실시간 가격 (yfinance/pykrx 출처)
- **target_price_low / high**: AI 추정 목표가 범위
- **upside_pct**: 상승 여력 (%) = (목표가_하한 - 현재가) / 현재가

⚠️ Stage 1의 목표가는 AI 추정치로 참고용. Stage 2 분석된 종목만 신뢰도 높음.

### 3. 분류·맥락
- **sector**: 섹터 분류
- **discovery_type**: 발견 유형
  - `consensus`: 시장 컨센서스와 일치
  - `early_signal`: 초기 신호 포착
  - `contrarian`: 역발상 관점
  - `deep_value`: 깊은 가치 발굴

### 4. 모멘텀 데이터
- **return_1m/3m/6m/1y_pct**: 기간별 과거 수익률
- **price_momentum_check**: `overheated` / `neutral` / `undervalued`

### 5. 리스크·근거
- **rationale**: 추천 이유 (가장 중요!)
- **risk_factors**: 주요 리스크

### 활용 팁

| 투자자 유형 | 집중할 필드 |
|------------|------------|
| 보수적 | conviction HIGH + action BUY + 배당 종목 |
| 성장 추구 | early_signal + 높은 upside_pct |
| 역발상 | contrarian + deep_value |
| 단기 트레이딩 | momentum 데이터 + 기술적 분석 조합 |""",
            "examples": json.dumps([
                {"title": "제안 카드 해석 실전", "description": "conviction: HIGH, action: BUY, discovery_type: early_signal, upside_pct: 35%, return_1m: +5%, return_3m: -2%. 해석: AI가 높은 확신으로 매수 추천. 아직 시장에 덜 반영된 초기 신호(early_signal). 3개월 수익률이 마이너스인데 upside가 35%면 아직 저평가 구간일 가능성.", "period": "활용 예시", "lesson": "discovery_type과 모멘텀 데이터를 조합하면 '이미 오른 종목'과 '아직 기회가 있는 종목'을 구분할 수 있다"}
            ]),
        },
        {
            "category": "practical", "slug": "using-track-record",
            "title": "트랙레코드로 AI 분석 신뢰도 검증하기",
            "summary": "이 앱의 과거 추천 성과(트랙레코드)를 통해 AI 분석의 강점과 한계를 파악하는 법을 배웁니다.",
            "difficulty": "beginner", "sort_order": 41,
            "content": """## 트랙레코드란?

과거 AI가 추천한 종목이 **실제로 얼마나 올랐는지/내렸는지** 추적한 성과 기록.

### 확인할 핵심 지표

| 지표 | 의미 | 좋은 수준 |
|------|------|-----------|
| **적중률** | 추천 중 실제 수익 낸 비율 | 55%+ |
| **평균 수익률** | 전체 추천의 평균 수익 | 시장 수익률 초과 |
| **최대 손실** | 가장 크게 실패한 추천 | -20% 이내 |
| **수익/손실 비율** | 평균 수익 ÷ 평균 손실 | 1.5:1 이상 |

### 올바른 해석법

1. **전체 기간으로 판단**: 한두 건의 대박/쪽박에 흔들리지 말 것
2. **시장 대비 비교**: 시장이 +20%일 때 +15%면 사실상 부진
3. **카테고리별 확인**: sector별, conviction별로 성과 차이 확인
4. **시장 국면별**: 상승장/하락장/횡보장에서 각각의 성과

### AI 분석의 강점과 한계

**강점:**
- 감정 없는 객관적 분석
- 대량의 뉴스·데이터 동시 처리
- 일관된 분석 프레임워크

**한계:**
- 돌발 이벤트(지정학, 자연재해) 예측 불가
- 과거 패턴에 기반 → 전례 없는 상황에 취약
- 시장 심리·수급 변화 실시간 반영 어려움

> AI 분석은 **의사결정 보조 도구**이지, 맹신하는 오라클이 아닙니다.""",
            "examples": json.dumps([
                {"title": "AI 투자 성과 사례 (참고)", "description": "AI 기반 헤지펀드 르네상스 테크놀로지의 메달리온 펀드: 30년간 연평균 66% 수익. 하지만 이는 극도로 정교한 퀀트 모델 + 초단타 매매. 일반적인 AI 분석은 이 수준을 기대하기 어렵지만, 인간의 편향을 줄이는 것만으로도 큰 가치.", "period": "1988~2018", "lesson": "AI의 가치는 '완벽한 예측'이 아니라 '인간의 감정적 실수를 줄여주는 것'에 있다"}
            ]),
        },
    ]

    for t in topics:
        cur.execute(
            """INSERT INTO education_topics (category, slug, title, summary, content,
                       examples, difficulty, sort_order)
               VALUES (%(category)s, %(slug)s, %(title)s, %(summary)s, %(content)s,
                       %(examples)s::jsonb, %(difficulty)s, %(sort_order)s)
               ON CONFLICT (slug) DO NOTHING""",
            t,
        )
    print(f"[DB] 교육 토픽 {len(topics)}건 시드 데이터 삽입")


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

            if current < 19:
                _migrate_to_v19(cur)

            if current < 20:
                _migrate_to_v20(cur)

            if current < 21:
                _migrate_to_v21(cur)

            if current < 22:
                _migrate_to_v22(cur)

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
                            return_1m_pct, return_3m_pct, return_6m_pct, return_1y_pct,
                            entry_price,
                            foreign_ownership_pct, index_membership,
                            squeeze_risk, foreign_net_buy_signal)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
                         proposal.get("return_6m_pct"), proposal.get("return_1y_pct"),
                         proposal.get("current_price"),
                         proposal.get("foreign_ownership_pct"),
                         proposal.get("index_membership"),
                         proposal.get("squeeze_risk"),
                         proposal.get("foreign_net_buy_signal"))
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

    # 매칭 구독 조회 — 일반 커서이므로 컬럼 인덱스로 접근
    cur.execute(
        "SELECT id, user_id, sub_type, sub_key, label FROM user_subscriptions"
    )
    subs = cur.fetchall()

    noti_count = 0
    for sub in subs:
        # (id, user_id, sub_type, sub_key, label)
        sub_id, user_id, sub_type, sub_key, label = sub
        title = None
        link = None
        if sub_type == "ticker" and sub_key.upper() in tickers:
            display_label = label or sub_key
            title = f"구독 종목 '{display_label}'이(가) 분석에 등장했습니다"
            link = f"/pages/proposals/history/{sub_key}"
        elif sub_type == "theme" and sub_key in theme_keys:
            display_label = label or theme_keys[sub_key]
            title = f"구독 테마 '{display_label}'이(가) 분석에 등장했습니다"
            link = f"/pages/themes/history/{sub_key}"

        if title:
            cur.execute(
                "INSERT INTO user_notifications (user_id, sub_id, session_id, title, link) "
                "VALUES (%s, %s, %s, %s, %s)",
                (user_id, sub_id, session_id, title, link),
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
