"""분석 세션 저장 pipeline — save_analysis + 검증/알림/추적 private 유틸."""
import json
import re

from shared.config import DatabaseConfig, ValidationConfig
from shared.db.connection import get_connection


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
    saved_proposals: list[tuple[int, dict]] = []  # (proposal_id, proposal) for Phase 3 validation
    try:
        with conn.cursor() as cur:
            # 1) 세션 생성 (같은 날짜면 기존 데이터 삭제 후 재생성)
            cur.execute(
                "DELETE FROM analysis_sessions WHERE analysis_date = %s",
                (analysis_date,)
            )
            market_regime = result.get("market_regime")
            cur.execute(
                """INSERT INTO analysis_sessions
                   (analysis_date, market_summary, risk_temperature, data_sources, market_regime)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (analysis_date, result.get("market_summary"),
                 result.get("risk_temperature"), result.get("data_sources"),
                 json.dumps(market_regime, ensure_ascii=False) if market_regime else None)
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
                    spec_snapshot = proposal.get("spec_snapshot")
                    factor_snapshot = proposal.get("factor_snapshot")
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
                            squeeze_risk, foreign_net_buy_signal,
                            spec_snapshot, screener_match_reason, factor_snapshot)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
                         proposal.get("foreign_net_buy_signal"),
                         json.dumps(spec_snapshot, ensure_ascii=False) if spec_snapshot else None,
                         proposal.get("screener_match_reason"),
                         json.dumps(factor_snapshot, ensure_ascii=False) if factor_snapshot else None)
                    )
                    proposal_id = cur.fetchone()[0]
                    saved_proposals.append((proposal_id, proposal))

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
    finally:
        conn.close()

    # 7) Phase 3: Evidence Validation — 별도 트랜잭션 (실패해도 저장 결과는 유지)
    try:
        validation_cfg = ValidationConfig()
        if validation_cfg.enabled and saved_proposals:
            from analyzer.validator import validate_and_persist
            validate_and_persist(cfg, saved_proposals, validation_cfg)
    except Exception as e:
        # 검증 실패는 저장 자체를 무효화하지 않음 — 경고만 출력
        print(f"[DB] 세션 #{session_id} validation 실패 (무시): {e}")

    return session_id


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
    key = name.strip().lower()
    key = re.sub(r'[·\-/\s]+', '', key)  # 공백, 하이픈, 가운뎃점 제거
    return key


def _resolve_theme_key(theme: dict) -> str:
    """AI 제공 theme_key 우선 사용, 유효하지 않으면 한국어 정규화 폴백"""
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
