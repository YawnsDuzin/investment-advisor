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
from shared.db.schema import SCHEMA_VERSION, _create_base_schema, init_db  # noqa: F401
from shared.db.session_repo import save_analysis  # noqa: F401
from shared.db.session_repo import (  # noqa: F401
    _validate_proposal,
    _generate_notifications,
    _normalize_theme_key,
    _resolve_theme_key,
    _update_tracking,
)


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
