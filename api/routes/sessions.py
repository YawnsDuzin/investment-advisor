"""분석 세션 조회 API"""
from fastapi import APIRouter, HTTPException, Query
from shared.config import DatabaseConfig
from shared.db import get_connection
from psycopg2.extras import RealDictCursor

router = APIRouter(prefix="/sessions", tags=["세션"])


def _get_cfg() -> DatabaseConfig:
    return DatabaseConfig()


@router.get("")
def list_sessions(limit: int = Query(default=30, ge=1, le=100)):
    """분석 세션 목록 (최신순)"""
    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT s.id, s.analysis_date, s.market_summary,
                       s.risk_temperature, s.data_sources, s.created_at,
                       (SELECT COUNT(*) FROM global_issues gi WHERE gi.session_id = s.id) AS issue_count,
                       (SELECT COUNT(*) FROM investment_themes it WHERE it.session_id = s.id) AS theme_count
                FROM analysis_sessions s
                ORDER BY s.analysis_date DESC
                LIMIT %s
            """, (limit,))
            rows = cur.fetchall()
    finally:
        conn.close()

    return [_serialize_row(r) for r in rows]


@router.get("/{session_id}")
def get_session(session_id: int):
    """세션 상세 — 이슈, 테마, 시나리오, 매크로, 투자 제안 모두 포함"""
    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 세션
            cur.execute(
                "SELECT * FROM analysis_sessions WHERE id = %s", (session_id,)
            )
            session = cur.fetchone()
            if not session:
                raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다")

            # 이슈 (확장 필드 포함)
            cur.execute(
                """SELECT * FROM global_issues WHERE session_id = %s
                   ORDER BY importance DESC""",
                (session_id,)
            )
            issues = cur.fetchall()

            # 테마 + 시나리오 + 매크로 + 제안
            cur.execute(
                """SELECT * FROM investment_themes WHERE session_id = %s
                   ORDER BY confidence_score DESC""",
                (session_id,)
            )
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
                proposals = cur.fetchall()
                for p in proposals:
                    # 종목 심층분석 존재 여부 확인
                    cur.execute(
                        "SELECT id FROM stock_analyses WHERE proposal_id = %s",
                        (p["id"],)
                    )
                    sa = cur.fetchone()
                    p["has_stock_analysis"] = sa is not None
                    p["stock_analysis_id"] = sa["id"] if sa else None

                theme["proposals"] = [_serialize_row(p) for p in proposals]
    finally:
        conn.close()

    return {
        "session": _serialize_row(session),
        "issues": [_serialize_row(i) for i in issues],
        "themes": [_serialize_row(t) for t in themes],
    }


@router.get("/date/{analysis_date}")
def get_session_by_date(analysis_date: str):
    """날짜로 세션 조회 (YYYY-MM-DD)"""
    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id FROM analysis_sessions WHERE analysis_date = %s",
                (analysis_date,)
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"{analysis_date} 분석 결과 없음")
    finally:
        conn.close()

    return get_session(row["id"])


def _serialize_row(row: dict) -> dict:
    """RealDictRow의 date/datetime/Decimal 타입을 JSON 직렬화 가능하도록 변환"""
    from datetime import date, datetime
    from decimal import Decimal
    result = {}
    for k, v in row.items():
        if isinstance(v, (date, datetime)):
            result[k] = v.isoformat()
        elif isinstance(v, Decimal):
            result[k] = float(v)
        else:
            result[k] = v
    return result
