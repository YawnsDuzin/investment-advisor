"""분석 세션 조회 API"""
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Request, Depends
from fastapi.responses import RedirectResponse
from shared.config import AuthConfig
from shared.db import get_connection
from psycopg2.extras import RealDictCursor
from api.auth.dependencies import get_current_user, get_current_user_required, _get_auth_cfg
from api.auth.models import UserInDB
from api.serialization import serialize_row as _serialize_row
from api.page_context import base_ctx as _base_ctx
from api.templates_provider import templates
from api.deps import get_db_cfg as _get_cfg

router = APIRouter(prefix="/sessions", tags=["세션"])

pages_router = APIRouter(prefix="/pages/sessions", tags=["세션 페이지"])


@router.get("")
def list_sessions(limit: int = Query(default=30, ge=1, le=100), _user: Optional[UserInDB] = Depends(get_current_user_required)):
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
def get_session(session_id: int, _user: Optional[UserInDB] = Depends(get_current_user_required)):
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
def get_session_by_date(analysis_date: str, _user: Optional[UserInDB] = Depends(get_current_user_required)):
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


# ──────────────────────────────────────────────
# Sessions 페이지 라우트 (B1 콜로케이션)
# ──────────────────────────────────────────────

@pages_router.get("")
def sessions_page(request: Request, limit: int = Query(default=30, ge=1, le=100), user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    ctx = _base_ctx(request, "sessions", user, auth_cfg)
    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT s.id, s.analysis_date, s.market_summary,
                       s.risk_temperature, s.created_at,
                       (SELECT COUNT(*) FROM global_issues gi WHERE gi.session_id = s.id) AS issue_count,
                       (SELECT COUNT(*) FROM investment_themes it WHERE it.session_id = s.id) AS theme_count
                FROM analysis_sessions s
                ORDER BY s.analysis_date DESC
                LIMIT %s
            """, (limit,))
            sessions = cur.fetchall()
    finally:
        conn.close()

    return templates.TemplateResponse(request=request, name="sessions.html", context={
        **ctx,
        "sessions": [_serialize_row(s) for s in sessions],
    })


@pages_router.get("/date/{analysis_date}")
def session_by_date_page(analysis_date: str):
    """날짜로 세션 상세 페이지 리다이렉트"""
    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id FROM analysis_sessions WHERE analysis_date = %s",
                (analysis_date,)
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return RedirectResponse(url="/pages/sessions", status_code=302)
    return RedirectResponse(url=f"/pages/sessions/{row['id']}", status_code=302)


@pages_router.get("/{session_id}")
def session_detail_page(request: Request, session_id: int, user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM analysis_sessions WHERE id = %s", (session_id,))
            session = cur.fetchone()
            if not session:
                return templates.TemplateResponse("dashboard.html", {
                    "request": request, "active_page": "sessions", "session": None,
                })

            cur.execute(
                "SELECT * FROM global_issues WHERE session_id = %s ORDER BY importance DESC",
                (session_id,)
            )
            issues = cur.fetchall()

            cur.execute(
                "SELECT * FROM investment_themes WHERE session_id = %s ORDER BY confidence_score DESC",
                (session_id,)
            )
            themes = cur.fetchall()

            for theme in themes:
                # 시나리오 분석
                cur.execute(
                    "SELECT * FROM theme_scenarios WHERE theme_id = %s ORDER BY probability DESC",
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
                    "SELECT * FROM investment_proposals WHERE theme_id = %s ORDER BY target_allocation DESC",
                    (theme["id"],)
                )
                proposals = cur.fetchall()
                for p in proposals:
                    cur.execute(
                        "SELECT id FROM stock_analyses WHERE proposal_id = %s LIMIT 1",
                        (p["id"],)
                    )
                    sa = cur.fetchone()
                    p["has_stock_analysis"] = sa is not None
                theme["proposals"] = [_serialize_row(p) for p in proposals]

            # 추적 데이터 연결
            cur.execute("""
                SELECT * FROM theme_tracking WHERE last_seen_date = %s
            """, (session["analysis_date"],))
            tracking_map = {}
            for row in cur.fetchall():
                tracking_map[row["theme_key"]] = _serialize_row(row)

    finally:
        conn.close()

    ctx = _base_ctx(request, "sessions", user, auth_cfg)
    return templates.TemplateResponse(request=request, name="session_detail.html", context={
        **ctx,
        "session": _serialize_row(session),
        "issues": [_serialize_row(i) for i in issues],
        "themes": [_serialize_row(t) for t in themes],
        "tracking_map": tracking_map,
    })


