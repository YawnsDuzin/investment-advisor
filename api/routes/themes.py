"""투자 테마 조회 API"""
from fastapi import APIRouter, Query
from shared.config import DatabaseConfig
from shared.db import get_connection
from psycopg2.extras import RealDictCursor
from api.routes.sessions import _serialize_row

router = APIRouter(prefix="/themes", tags=["테마"])


def _get_cfg() -> DatabaseConfig:
    return DatabaseConfig()


@router.get("")
def list_themes(
    limit: int = Query(default=20, ge=1, le=100),
    horizon: str | None = Query(default=None, description="short|mid|long"),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    theme_type: str | None = Query(default=None, description="structural|cyclical"),
    validity: str | None = Query(default=None, description="strong|medium|weak"),
):
    """투자 테마 목록 (최신순, 필터 가능)"""
    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            query = """
                SELECT t.*, s.analysis_date
                FROM investment_themes t
                JOIN analysis_sessions s ON t.session_id = s.id
                WHERE t.confidence_score >= %s
            """
            params: list = [min_confidence]

            if horizon:
                query += " AND t.time_horizon = %s"
                params.append(horizon)
            if theme_type:
                query += " AND t.theme_type = %s"
                params.append(theme_type)
            if validity:
                query += " AND t.theme_validity = %s"
                params.append(validity)

            query += " ORDER BY s.analysis_date DESC, t.confidence_score DESC LIMIT %s"
            params.append(limit)

            cur.execute(query, params)
            themes = cur.fetchall()

            for theme in themes:
                # 시나리오 분석
                cur.execute(
                    """SELECT * FROM theme_scenarios WHERE theme_id = %s
                       ORDER BY probability DESC""",
                    (theme["id"],)
                )
                theme["scenarios"] = [_serialize_row(s) for s in cur.fetchall()]

                # 매크로 영향
                cur.execute(
                    "SELECT * FROM macro_impacts WHERE theme_id = %s",
                    (theme["id"],)
                )
                theme["macro_impacts"] = [_serialize_row(m) for m in cur.fetchall()]

                # 투자 제안
                cur.execute(
                    """SELECT * FROM investment_proposals WHERE theme_id = %s
                       ORDER BY target_allocation DESC""",
                    (theme["id"],)
                )
                theme["proposals"] = [_serialize_row(p) for p in cur.fetchall()]
    finally:
        conn.close()

    return [_serialize_row(t) for t in themes]


@router.get("/search")
def search_themes(
    q: str = Query(description="테마명 또는 설명 검색어"),
    limit: int = Query(default=10, ge=1, le=50),
):
    """테마 검색 (테마명/설명에서 키워드 검색)"""
    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT t.*, s.analysis_date
                FROM investment_themes t
                JOIN analysis_sessions s ON t.session_id = s.id
                WHERE t.theme_name ILIKE %s OR t.description ILIKE %s
                ORDER BY s.analysis_date DESC
                LIMIT %s
            """, (f"%{q}%", f"%{q}%", limit))
            themes = cur.fetchall()
    finally:
        conn.close()

    return [_serialize_row(t) for t in themes]
