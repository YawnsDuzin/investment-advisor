"""스키마 마이그레이션 v2 ~ v25.

새 마이그레이션 추가 시:
1. 이 파일에 `_migrate_to_vN(cur)` 함수 추가
2. `shared/db/migrations/__init__.py`의 `_MIGRATIONS` dict에 한 줄 추가
3. `shared/db/schema.py`의 `SCHEMA_VERSION` 상수 증가
"""
from shared.db.migrations.seeds import _seed_admin_user, _seed_education_topics  # noqa: F401


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


def _migrate_to_v23(cur) -> None:
    """v23: 진단 인프라 — ai_query_archive, app_logs.context, incident_reports

    - ai_query_archive: Claude SDK 쿼리의 원본 프롬프트/응답을 영구 보존.
      JSON 파싱 실패·빈 복구·타임아웃 등 사후 재현·재분석 가능하게 한다.
    - app_logs.context: 구조화 로그 컨텍스트 (stage/ticker/theme_key 등 JSONB).
    - incident_reports: 실행별 사건 요약 (이상 가격, JSON 실패, 미등록 티커 등).
    """
    # 1) ai_query_archive: AI 쿼리 원본 보존
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ai_query_archive (
            id SERIAL PRIMARY KEY,
            run_id INT REFERENCES app_runs(id) ON DELETE CASCADE,
            stage VARCHAR(50),
            target_key VARCHAR(200),
            model VARCHAR(80),
            prompt_system TEXT,
            prompt_user TEXT,
            response_raw TEXT,
            response_chars INT,
            elapsed_sec NUMERIC(8,2),
            parse_status VARCHAR(30),
            parse_error TEXT,
            recovered_fields JSONB,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_ai_query_run
            ON ai_query_archive(run_id, stage);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_ai_query_parse_fail
            ON ai_query_archive(parse_status)
            WHERE parse_status NOT IN ('success', NULL);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_ai_query_created
            ON ai_query_archive(created_at DESC);
    """)

    # 2) app_logs.context JSONB — 구조화 컨텍스트 (B-5)
    cur.execute("""
        ALTER TABLE app_logs
            ADD COLUMN IF NOT EXISTS context JSONB;
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_app_logs_context_ticker
            ON app_logs((context->>'ticker'))
            WHERE context ? 'ticker';
    """)

    # 3) incident_reports: 실행별 자동 요약 (B-3)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS incident_reports (
            id SERIAL PRIMARY KEY,
            run_id INT UNIQUE REFERENCES app_runs(id) ON DELETE CASCADE,
            session_id INT REFERENCES analysis_sessions(id) ON DELETE SET NULL,
            severity VARCHAR(20) DEFAULT 'info',
            issue_count INT DEFAULT 0,
            report JSONB NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_incident_reports_severity
            ON incident_reports(severity, created_at DESC);
    """)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (23)
        ON CONFLICT (version) DO NOTHING;
    """)

    print("[DB] v23 마이그레이션 완료 — ai_query_archive + app_logs.context + incident_reports")


def _migrate_to_v24(cur) -> None:
    """Education 신규 토픽 15개 추가 (basics 5 + analysis 2 + macro 1 + practical 2 + stories 5).

    stories 카테고리 신규 도입. 기존 11개 토픽은 ON CONFLICT (slug) DO NOTHING으로 보호.
    신규 DB의 경우 v21에서 26개 전체가 이미 시드되었으므로 v24는 사실상 no-op이 됨 (멱등).
    """
    from shared.db.migrations.seeds_education import NEW_TOPICS_V24
    for t in NEW_TOPICS_V24:
        cur.execute(
            """INSERT INTO education_topics (category, slug, title, summary, content,
                       examples, difficulty, sort_order)
               VALUES (%(category)s, %(slug)s, %(title)s, %(summary)s, %(content)s,
                       %(examples)s::jsonb, %(difficulty)s, %(sort_order)s)
               ON CONFLICT (slug) DO NOTHING""",
            t,
        )

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (24)
        ON CONFLICT (version) DO NOTHING;
    """)
    print(f"[DB] v24: 교육 토픽 {len(NEW_TOPICS_V24)}개 추가 (stories 카테고리 도입)")


def _migrate_to_v25(cur) -> None:
    """v25: stock_universe — 검증된 종목 유니버스 (Phase 1a, recommendation-engine-redesign).

    LLM이 자유롭게 티커를 생성하지 못하도록 유니버스에서만 후보를 선택할 수 있게
    하기 위한 기반 테이블. 일별 가격 동기화 + 주간 메타데이터 동기화로 갱신된다.
    초기 범위: KRX(KOSPI+KOSDAQ 보통주), Phase 1b에서 US(S&P500+Nasdaq100) 추가 예정.

    참고: 재설계 계획서(_docs/20260422172248_recommendation-engine-redesign.md)에서는
    스키마 v23으로 표기되었으나, v23/v24가 이미 다른 용도로 선점되어 v25로 재할당.
    """
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stock_universe (
            id              SERIAL PRIMARY KEY,
            ticker          TEXT NOT NULL,
            market          TEXT NOT NULL,
            asset_name      TEXT NOT NULL,
            asset_name_en   TEXT,
            sector_gics     TEXT,
            sector_krx      TEXT,
            sector_norm     TEXT,
            industry        TEXT,
            market_cap_krw  BIGINT,
            market_cap_bucket TEXT,
            last_price      NUMERIC(18,4),
            last_price_ccy  TEXT,
            last_price_at   TIMESTAMPTZ,
            listed          BOOLEAN DEFAULT TRUE,
            delisted_at     DATE,
            has_preferred   BOOLEAN DEFAULT FALSE,
            aliases         JSONB,
            data_source     TEXT,
            meta_synced_at  TIMESTAMPTZ,
            price_synced_at TIMESTAMPTZ,
            created_at      TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(ticker, market)
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_universe_sector_norm
            ON stock_universe(sector_norm);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_universe_market_cap
            ON stock_universe(market_cap_krw);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_universe_listed
            ON stock_universe(listed) WHERE listed = TRUE;
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_universe_market
            ON stock_universe(market) WHERE listed = TRUE;
    """)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (25)
        ON CONFLICT (version) DO NOTHING;
    """)
    print("[DB] v25 마이그레이션 완료 — stock_universe 테이블 생성 (Phase 1a)")


def _migrate_to_v26(cur) -> None:
    """v26: Evidence Validation Layer (Phase 3, recommendation-engine-redesign).

    - proposal_validation_log: AI 제시 vs 실측 데이터 크로스체크 결과 영구 보존
    - investment_proposals.spec_snapshot: Stage 1-B1 스펙 JSON 보존 (audit trail)
    - investment_proposals.screener_match_reason: 어떤 키워드/조건으로 매칭됐는지

    참고: 재설계 계획서의 v24가 v26으로 시프트(v23/v24 선점).
    """
    # 1) proposal_validation_log
    cur.execute("""
        CREATE TABLE IF NOT EXISTS proposal_validation_log (
            id              SERIAL PRIMARY KEY,
            proposal_id     INT REFERENCES investment_proposals(id) ON DELETE CASCADE,
            field_name      TEXT NOT NULL,
            ai_value        TEXT,
            actual_value    TEXT,
            evidence_source TEXT,
            mismatch        BOOLEAN DEFAULT FALSE,
            mismatch_pct    NUMERIC(10,4),
            checked_at      TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_validation_proposal
            ON proposal_validation_log(proposal_id);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_validation_mismatch
            ON proposal_validation_log(mismatch) WHERE mismatch = TRUE;
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_validation_field
            ON proposal_validation_log(field_name, mismatch);
    """)

    # 2) investment_proposals 컬럼 추가 — audit trail
    cur.execute("""
        ALTER TABLE investment_proposals
            ADD COLUMN IF NOT EXISTS spec_snapshot JSONB,
            ADD COLUMN IF NOT EXISTS screener_match_reason TEXT;
    """)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (26)
        ON CONFLICT (version) DO NOTHING;
    """)
    print("[DB] v26 마이그레이션 완료 — proposal_validation_log + spec_snapshot/screener_match_reason (Phase 3)")


def _migrate_to_v27(cur) -> None:
    """v27: stock_universe_ohlcv — 종목별 일별 OHLCV 이력 테이블 (Phase 7, ohlcv-history).

    `stock_universe`는 종목별 현재 상태 1 row만 관리한다. 시계열 분석(팩터 백테스트,
    레짐 판별, 모멘텀 계산)을 위해 일별 OHLCV를 rolling 보관하는 이력 테이블을 추가한다.

    설계 결정 (계획서 _docs/20260422235016_ohlcv-history-table-plan.md §3):
    - 원시 OHLCV + change_pct만 저장 (지표는 on-demand 계산)
    - KRX + US 모두 포함
    - 우선주/상폐 종목도 수집 (스크리너 레이어에서 필터) → PIT 원칙
    - stock_universe 와의 FK 미설정 — 상폐 종목 OHLCV 이력 유지
    - PK (ticker, market, trade_date) → 멱등 UPSERT

    Retention은 환경변수 OHLCV_RETENTION_DAYS(기본 400일)로 별도 정리.
    """
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stock_universe_ohlcv (
            ticker        TEXT NOT NULL,
            market        TEXT NOT NULL,
            trade_date    DATE NOT NULL,
            open          NUMERIC(18,4),
            high          NUMERIC(18,4),
            low           NUMERIC(18,4),
            close         NUMERIC(18,4) NOT NULL,
            volume        BIGINT,
            change_pct    NUMERIC(7,4),
            data_source   TEXT NOT NULL,
            adjusted      BOOLEAN DEFAULT FALSE,
            created_at    TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (ticker, market, trade_date)
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_ohlcv_date
            ON stock_universe_ohlcv(trade_date);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_ohlcv_ticker_desc
            ON stock_universe_ohlcv(ticker, market, trade_date DESC);
    """)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (27)
        ON CONFLICT (version) DO NOTHING;
    """)
    print("[DB] v27 마이그레이션 완료 — stock_universe_ohlcv 테이블 생성 (Phase 7)")


def _migrate_to_v28(cur) -> None:
    """v28: stock_universe_ohlcv.change_pct 정밀도 확장 — NUMERIC(7,4) → NUMERIC(10,4).

    배경: 800일 백필 시 역분할(10:1 이상)·상폐 직전 급변·수정주가 미반영 혼입
    등으로 |change_pct| ≥ 1000% row가 발생하여 NUMERIC(7,4) 오버플로우 발생.
    정수부 여유 3자리(±999.9999%)는 현실 데이터에 과소. ±999999.9999%로 확장.

    다른 percent 필드들(return_*_pct 등 NUMERIC(7,2))은 정수부 5자리라 안전.
    """
    cur.execute("""
        ALTER TABLE stock_universe_ohlcv
            ALTER COLUMN change_pct TYPE NUMERIC(10,4);
    """)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (28)
        ON CONFLICT (version) DO NOTHING;
    """)
    print("[DB] v28 마이그레이션 완료 — stock_universe_ohlcv.change_pct NUMERIC(10,4)로 확장")


def _migrate_to_v29(cur) -> None:
    """v29: investment_proposals 성과 메트릭 확장 — max_drawdown + alpha (로드맵 A3).

    추천 후 실제 수익률 추적(`price_tracker.py`)이 OHLCV 이력 테이블로 통합되면서
    post_return_*_pct 외에 다음 메트릭을 함께 기록할 수 있다.

    - `max_drawdown_pct`: entry_price 대비 추천 이후 최저점 낙폭(%). 항상 음수 또는 0.
      예) entry 100 → 추천 후 최저가 70이면 max_drawdown_pct = -30.0
    - `max_drawdown_date`: 해당 최저점이 기록된 거래일 (관측일 기준)
    - `alpha_vs_benchmark_pct`: post_return 1y 기준 벤치마크 대비 초과수익(%).
      벤치마크(KOSPI/S&P500) OHLCV 인프라 구축 전까지는 NULL 유지. B2 레짐 레이어에서 채움.
    """
    cur.execute("""
        ALTER TABLE investment_proposals
            ADD COLUMN IF NOT EXISTS max_drawdown_pct NUMERIC(7,2),
            ADD COLUMN IF NOT EXISTS max_drawdown_date DATE,
            ADD COLUMN IF NOT EXISTS alpha_vs_benchmark_pct NUMERIC(7,2);
    """)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (29)
        ON CONFLICT (version) DO NOTHING;
    """)
    print("[DB] v29 마이그레이션 완료 — investment_proposals max_drawdown_pct / max_drawdown_date / alpha_vs_benchmark_pct")


def _migrate_to_v30(cur) -> None:
    """v30: investment_proposals.factor_snapshot JSONB — 정량 팩터 스냅샷 (로드맵 B1).

    Stage 2 분석 시점에 `analyzer/factor_engine.py`가 OHLCV 이력에서
    산출한 cross-section percentile·raw 값을 JSONB로 보관. 예:

        {
          "r1m_pct": 5.2, "r3m_pct": 18.4, "r6m_pct": 35.1, "r12m_pct": 42.0,
          "r1m_pctile": 0.72, "r3m_pctile": 0.85, "r6m_pctile": 0.88,
          "vol60_pct": 2.15, "low_vol_pctile": 0.55,
          "volume_ratio": 1.35, "volume_pctile": 0.78,
          "universe_size": 2987,
          "computed_at": "2026-04-23T13:00:00+09:00"
        }

    STAGE2 프롬프트에 실측값으로 주입되어 LLM의 수치 환각을 제거한다.
    UI에서는 "AI가 본 실측 데이터" 섹션으로 투명화 가능(UI-7).
    """
    cur.execute("""
        ALTER TABLE investment_proposals
            ADD COLUMN IF NOT EXISTS factor_snapshot JSONB;
    """)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (30)
        ON CONFLICT (version) DO NOTHING;
    """)
    print("[DB] v30 마이그레이션 완료 — investment_proposals.factor_snapshot JSONB (B1)")


def _migrate_to_v31(cur) -> None:
    """v31: 시장 레짐 레이어 (로드맵 B2).

    1) `analysis_sessions.market_regime JSONB` — Stage 1 진입 시점의 시장 국면 스냅샷
       예: {
         "kospi": {"close": 2650.3, "above_200ma": true, "pct_from_ma200": 3.4,
                    "vol60_pct": 1.12, "vol_regime": "mid",
                    "drawdown_from_52w_high_pct": -5.2},
         "sp500": {...},
         "breadth_kr_pct": 58.2,   -- universe 중 20일 수익률 > 0 비율
         "computed_at": "2026-04-23T13:00:00+09:00"
       }

    2) `market_indices_ohlcv` — 벤치마크 지수(KOSPI/S&P500 등) 일별 OHLCV 이력.
       stock_universe_ohlcv와 분리하여 의미 구분. PK (index_code, trade_date).
       `analyzer/regime.py`가 200일 이평·변동성 국면·낙폭 계산에 사용.
       alpha_vs_benchmark_pct(v29) 채우기에도 활용 예정(B2b).
    """
    cur.execute("""
        ALTER TABLE analysis_sessions
            ADD COLUMN IF NOT EXISTS market_regime JSONB;
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS market_indices_ohlcv (
            index_code    TEXT NOT NULL,
            trade_date    DATE NOT NULL,
            open          NUMERIC(18,4),
            high          NUMERIC(18,4),
            low           NUMERIC(18,4),
            close         NUMERIC(18,4) NOT NULL,
            volume        BIGINT,
            change_pct    NUMERIC(10,4),
            data_source   TEXT NOT NULL,
            created_at    TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (index_code, trade_date)
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_market_indices_date
            ON market_indices_ohlcv(trade_date);
    """)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (31)
        ON CONFLICT (version) DO NOTHING;
    """)
    print("[DB] v31 마이그레이션 완료 — analysis_sessions.market_regime + market_indices_ohlcv (B2)")


def _migrate_to_v32(cur) -> None:
    """v32: market_signals 테이블 — 일별 이상 시그널 탐지 결과 저장 (로드맵 Step 3-2).

    `analyzer/signals.py`가 stock_universe_ohlcv 기반 단일 SQL 배치로 탐지:
      - new_52w_high / new_52w_low: 당일 close가 최근 252일 최고/최저
      - volume_surge: 당일 volume >= 20일 평균 × 3
      - above_200ma_cross / below_200ma_cross: 200MA 상/하향 돌파
      - gap_up / gap_down: 오늘 open vs 어제 close ±3% 갭

    UI-3 "오늘의 이상 시그널" 카드와 UI-5 워치리스트 알림의 소스.
    PK (signal_date, signal_type, ticker, market) — 멱등 UPSERT.
    """
    cur.execute("""
        CREATE TABLE IF NOT EXISTS market_signals (
            id            SERIAL PRIMARY KEY,
            signal_date   DATE NOT NULL,
            signal_type   TEXT NOT NULL,
            ticker        TEXT NOT NULL,
            market        TEXT NOT NULL,
            metric        JSONB,
            created_at    TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE (signal_date, signal_type, ticker, market)
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_market_signals_date
            ON market_signals(signal_date DESC);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_market_signals_ticker
            ON market_signals(ticker, market);
    """)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (32)
        ON CONFLICT (version) DO NOTHING;
    """)
    print("[DB] v32 마이그레이션 완료 — market_signals 테이블 (로드맵 Step 3-2)")


def _migrate_to_v34(cur) -> None:
    """v34: pre_market_briefings — 프리마켓 브리핑 결과 영속화.

    매일 KST 06:30 배치(`analyzer.briefing_main`)가 미국 오버나이트 데이터와
    한국 수혜 매핑 LLM 결과를 한 row로 저장한다. UI `/pages/briefing` 카드 + 알림.

    구조 (모두 JSONB):
      - us_summary: stock_universe_ohlcv 집계 원본 (top_movers/sector_aggregates/indices)
      - briefing_data: LLM 출력 — us_summary.groups + kr_impact + morning_brief
      - regime_snapshot: analyzer.regime.compute_regime() 결과 (B2)
      - status: success / partial / skipped / failed
      - source_trade_date: 미국 OHLCV 거래일 (briefing_date 와 다름 — KST/EST 시차)

    PK = briefing_date (하루 1건). 재실행 시 UPSERT로 갱신.
    """
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pre_market_briefings (
            briefing_date     DATE PRIMARY KEY,
            source_trade_date DATE,
            status            VARCHAR(20) NOT NULL DEFAULT 'success',
            us_summary        JSONB,
            briefing_data     JSONB,
            regime_snapshot   JSONB,
            error_message     TEXT,
            generated_at      TIMESTAMPTZ DEFAULT NOW(),
            updated_at        TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_pre_market_briefings_status
            ON pre_market_briefings(status, briefing_date DESC);
    """)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (34)
        ON CONFLICT (version) DO NOTHING;
    """)
    print("[DB] v34 마이그레이션 완료 — pre_market_briefings 테이블 (프리마켓 브리핑)")


def _migrate_to_v35(cur) -> None:
    """Education 신규 토픽 14개 추가 (Tier A·B + tools 카테고리 신설).

    분포: basics +2, analysis +2, risk +3, macro +1, stories +3, tools(신규) +3
    기존 26개 토픽은 ON CONFLICT (slug) DO NOTHING으로 보호.
    신규 DB의 경우 v21에서 ALL_TOPICS 전체가 이미 시드되었으므로 v35는 사실상 no-op (멱등).

    education_topics.category VARCHAR(50)에 CHECK 제약 없음 — 'tools' 추가에 ALTER 불필요.
    UI 라벨은 api/routes/education.py:_EDU_CATEGORIES에서 분리 관리.
    """
    from shared.db.migrations.seeds_education import NEW_TOPICS_V35
    for t in NEW_TOPICS_V35:
        cur.execute(
            """INSERT INTO education_topics (category, slug, title, summary, content,
                       examples, difficulty, sort_order)
               VALUES (%(category)s, %(slug)s, %(title)s, %(summary)s, %(content)s,
                       %(examples)s::jsonb, %(difficulty)s, %(sort_order)s)
               ON CONFLICT (slug) DO NOTHING""",
            t,
        )

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (35)
        ON CONFLICT (version) DO NOTHING;
    """)
    print(f"[DB] v35: 교육 토픽 {len(NEW_TOPICS_V35)}개 추가 (tools 카테고리 신설)")


def _migrate_to_v36(cur) -> None:
    """Education 시각화 적용 — 14개 토픽 markdown content 갱신 (UPDATE 패턴 신설).

    v35까지의 모든 마이그레이션은 신규 row INSERT (ON CONFLICT DO NOTHING).
    본 v36은 본 시스템 첫 콘텐츠 갱신 마이그레이션 — 기존 row 의 content 를
    SVG 이미지 참조 포함 버전으로 UPDATE 한다.

    멱등성: WHERE content IS DISTINCT FROM 가드로 동일 content 재할당 시 no-op.
    신규 DB 의 경우 v21에서 ALL_TOPICS 전체가 이미 시각화 포함 버전으로 시드되었으므로
    v36 UPDATE 도 변화 없음 (멱등).

    대상 슬러그: spec doc 2026-04-26-education-svg-visualizations-design.md 참조.
    """
    from shared.db.migrations.seeds_education import ALL_TOPICS

    visual_slugs = {
        "per-pbr-roe", "business-cycle", "chart-key-five",
        "momentum-investing", "diversification", "risk-adjusted-return",
        "correlation-trap", "interest-rates", "yield-curve-inversion",
        "what-if-2015", "korea-market-timeline", "tesla-eight-years",
        "factor-six-axes", "market-regime-reading",
    }

    affected = 0
    for t in ALL_TOPICS:
        if t["slug"] not in visual_slugs:
            continue
        cur.execute(
            """UPDATE education_topics
               SET content = %s
               WHERE slug = %s
                 AND content IS DISTINCT FROM %s""",
            (t["content"], t["slug"], t["content"]),
        )
        affected += cur.rowcount

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (36)
        ON CONFLICT (version) DO NOTHING;
    """)
    print(f"[DB] v36: 시각화 토픽 content 갱신 {affected}건 (대상 14건, 동일 content 는 no-op)")


def _migrate_to_v33(cur) -> None:
    """v33: screener_presets — 사용자 커스텀 스크리너 프리셋 저장 (로드맵 UI-6).

    프리미엄 스크리너 페이지에서 유저가 필터 조합을 저장·재사용·공유할 수 있게 한다.
    Pro/Premium 티어 기능 (Free는 read-only로 공개 프리셋만 이용).
    """
    cur.execute("""
        CREATE TABLE IF NOT EXISTS screener_presets (
            id          SERIAL PRIMARY KEY,
            user_id     INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name        VARCHAR(200) NOT NULL,
            description TEXT,
            spec        JSONB NOT NULL,
            is_public   BOOLEAN DEFAULT FALSE,
            created_at  TIMESTAMP DEFAULT NOW(),
            updated_at  TIMESTAMP DEFAULT NOW(),
            UNIQUE (user_id, name)
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_screener_presets_public
            ON screener_presets(is_public, created_at DESC) WHERE is_public = TRUE;
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_screener_presets_user
            ON screener_presets(user_id, updated_at DESC);
    """)

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (33)
        ON CONFLICT (version) DO NOTHING;
    """)
    print("[DB] v33 마이그레이션 완료 — screener_presets (로드맵 UI-6)")
