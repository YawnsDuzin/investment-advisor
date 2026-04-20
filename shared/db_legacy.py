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
from shared.db.news_repo import (  # noqa: F401
    save_news_articles,
    get_untranslated_news,
    update_news_title_ko,
    update_news_translation,
    get_latest_news_titles,
)
from shared.db.query_repo import get_recent_recommendations, get_existing_theme_keys  # noqa: F401


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
