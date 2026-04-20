"""분석 세션 조회 API"""
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.responses import RedirectResponse
from psycopg2.extras import RealDictCursor
from api.auth.dependencies import get_current_user_required
from api.auth.models import UserInDB
from api.serialization import serialize_row as _serialize_row
from api.templates_provider import templates
from api.deps import get_db_conn, make_page_ctx

router = APIRouter(prefix="/sessions", tags=["세션"])

pages_router = APIRouter(prefix="/pages/sessions", tags=["세션 페이지"])


@router.get("")
def list_sessions(conn = Depends(get_db_conn), limit: int = Query(default=30, ge=1, le=100), _user: Optional[UserInDB] = Depends(get_current_user_required)):
    """분석 세션 목록 (최신순)"""
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

    return [_serialize_row(r) for r in rows]


@router.get("/{session_id}")
def get_session(session_id: int, conn = Depends(get_db_conn), _user: Optional[UserInDB] = Depends(get_current_user_required)):
    """세션 상세 — 이슈, 테마, 시나리오, 매크로, 투자 제안 모두 포함"""
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

    return {
        "session": _serialize_row(session),
        "issues": [_serialize_row(i) for i in issues],
        "themes": [_serialize_row(t) for t in themes],
    }


@router.get("/date/{analysis_date}")
def get_session_by_date(analysis_date: str, conn = Depends(get_db_conn), _user: Optional[UserInDB] = Depends(get_current_user_required)):
    """날짜로 세션 조회 (YYYY-MM-DD)"""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT id FROM analysis_sessions WHERE analysis_date = %s",
            (analysis_date,)
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"{analysis_date} 분석 결과 없음")

    return get_session(row["id"], conn)


# ──────────────────────────────────────────────
# Sessions 페이지 라우트 (B1 콜로케이션)
# ──────────────────────────────────────────────

@pages_router.get("")
def sessions_page(conn = Depends(get_db_conn), limit: int = Query(default=30, ge=1, le=100), ctx: dict = Depends(make_page_ctx("sessions"))):
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

    return templates.TemplateResponse(request=ctx["request"], name="sessions.html", context={
        **ctx,
        "sessions": [_serialize_row(s) for s in sessions],
    })


@pages_router.get("/date/{analysis_date}")
def session_by_date_page(analysis_date: str, conn = Depends(get_db_conn)):
    """날짜로 세션 상세 페이지 리다이렉트"""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT id FROM analysis_sessions WHERE analysis_date = %s",
            (analysis_date,)
        )
        row = cur.fetchone()
    if not row:
        return RedirectResponse(url="/pages/sessions", status_code=302)
    return RedirectResponse(url=f"/pages/sessions/{row['id']}", status_code=302)


@pages_router.get("/{session_id}")
def session_detail_page(session_id: int, conn = Depends(get_db_conn), ctx: dict = Depends(make_page_ctx("sessions"))):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM analysis_sessions WHERE id = %s", (session_id,))
        session = cur.fetchone()
        if not session:
            return templates.TemplateResponse("dashboard.html", {
                "request": ctx["request"], "active_page": "sessions", "session": None,
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

    return templates.TemplateResponse(request=ctx["request"], name="session_detail.html", context={
        **ctx,
        "session": _serialize_row(session),
        "issues": [_serialize_row(i) for i in issues],
        "themes": [_serialize_row(t) for t in themes],
        "tracking_map": tracking_map,
    })
