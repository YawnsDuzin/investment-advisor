"""PostgreSQL 데이터베이스 관리 모듈"""
import json
import psycopg2
from psycopg2.extras import execute_values, RealDictCursor
from shared.config import DatabaseConfig

# ── 스키마 버전 관리 ──────────────────────────────
SCHEMA_VERSION = 3  # v1: 초기 4테이블, v2: 멀티에이전트 확장, v3: 일자별 추적


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

        conn.commit()
        print("[DB] 테이블 초기화 완료")
    finally:
        conn.close()


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
                       (session_id, theme_name, description, related_issue_ids,
                        confidence_score, time_horizon, key_indicators,
                        theme_type, theme_validity)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                    (session_id, theme.get("theme_name"), theme.get("description"),
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
                    cur.execute(
                        """INSERT INTO investment_proposals
                           (theme_id, asset_type, asset_name, ticker, market,
                            action, conviction, rationale, risk_factors,
                            entry_condition, exit_condition, target_allocation,
                            current_price, target_price_low, target_price_high,
                            upside_pct, sentiment_score, quant_score,
                            sector, currency)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           RETURNING id""",
                        (theme_id, proposal.get("asset_type"),
                         proposal.get("asset_name"), proposal.get("ticker"),
                         proposal.get("market"), proposal.get("action"),
                         proposal.get("conviction"),
                         proposal.get("rationale"), proposal.get("risk_factors"),
                         proposal.get("entry_condition"), proposal.get("exit_condition"),
                         proposal.get("target_allocation"),
                         proposal.get("current_price"), proposal.get("target_price_low"),
                         proposal.get("target_price_high"), proposal.get("upside_pct"),
                         proposal.get("sentiment_score"), proposal.get("quant_score"),
                         proposal.get("sector"), proposal.get("currency"))
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

        conn.commit()
        print(f"[DB] 세션 #{session_id} 저장 완료 — 이슈 {len(issues)}건, 테마 {len(themes)}건")
        return session_id
    finally:
        conn.close()


def _normalize_theme_key(name: str) -> str:
    """테마명 정규화 — 동일 테마 매칭용 키 생성"""
    import re
    key = name.strip().lower()
    key = re.sub(r'[·\-/\s]+', '', key)  # 공백, 하이픈, 가운뎃점 제거
    return key


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
        theme_key = _normalize_theme_key(theme_name)
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
