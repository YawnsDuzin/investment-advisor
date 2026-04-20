"""PostgreSQL 데이터베이스 관리 모듈"""
import json
import psycopg2
from psycopg2.extras import execute_values, RealDictCursor
from shared.config import DatabaseConfig
from shared.db.connection import _ensure_database, get_connection, _get_schema_version  # noqa: F401
from shared.db.migrations.seeds import _seed_admin_user, _seed_education_topics  # noqa: F401
from shared.db.migrations.versions import (  # noqa: F401
    _migrate_to_v2, _migrate_to_v3, _migrate_to_v4, _migrate_to_v5,
    _migrate_to_v6, _migrate_to_v7, _migrate_to_v8, _migrate_to_v9,
    _migrate_to_v10, _migrate_to_v11, _migrate_to_v12, _migrate_to_v13,
    _migrate_to_v14, _migrate_to_v15, _migrate_to_v16, _migrate_to_v17,
    _migrate_to_v18, _migrate_to_v19, _migrate_to_v20, _migrate_to_v21,
    _migrate_to_v22, _migrate_to_v23,
)

# ── 스키마 버전 관리 ──────────────────────────────
SCHEMA_VERSION = 23  # v23: ai_query_archive + app_logs.context + incident_reports


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
    from shared.db.migrations import run_migrations
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
