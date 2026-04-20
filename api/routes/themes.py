"""투자 테마 조회 API + 테마 페이지 라우트"""
from typing import Optional
from fastapi import APIRouter, Query, Depends, Request
from shared.config import AuthConfig
from shared.db import get_connection
from psycopg2.extras import RealDictCursor
from api.serialization import serialize_row as _serialize_row
from api.page_context import base_ctx as _base_ctx
from api.templates_provider import templates
from api.deps import get_db_cfg as _get_cfg
from api.auth.dependencies import get_current_user, get_current_user_required, _get_auth_cfg
from api.auth.models import UserInDB

router = APIRouter(prefix="/themes", tags=["테마"])
pages_router = APIRouter(prefix="/pages/themes", tags=["테마 페이지"])


@router.get("")
def list_themes(
    limit: int = Query(default=20, ge=1, le=100),
    horizon: str | None = Query(default=None, description="short|mid|long"),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    theme_type: str | None = Query(default=None, description="structural|cyclical"),
    validity: str | None = Query(default=None, description="strong|medium|weak"),
    _user: Optional[UserInDB] = Depends(get_current_user_required),
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
    _user: Optional[UserInDB] = Depends(get_current_user_required),
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


# ──────────────────────────────────────────────
# 테마 페이지 라우트 (pages_router)
# ──────────────────────────────────────────────

@pages_router.get("/history/{theme_key}")
def theme_history_page(request: Request, theme_key: str, user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    """특정 테마의 일자별 추이"""
    ctx = _base_ctx(request, "themes", user, auth_cfg)
    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 추적 정보
            cur.execute("SELECT * FROM theme_tracking WHERE theme_key = %s", (theme_key,))
            tracking = cur.fetchone()
            if not tracking:
                return templates.TemplateResponse(request=request, name="theme_history.html",
                    context={**ctx, "tracking": None, "history": []})

            # 일자별 테마 데이터 (이름이 유사한 것 모두)
            cur.execute("""
                SELECT t.*, s.analysis_date
                FROM investment_themes t
                JOIN analysis_sessions s ON t.session_id = s.id
                WHERE LOWER(REPLACE(REPLACE(REPLACE(t.theme_name, ' ', ''), '-', ''), '·', ''))
                      = %s
                ORDER BY s.analysis_date DESC
                LIMIT 30
            """, (theme_key,))
            history = cur.fetchall()

            for entry in history:
                cur.execute(
                    "SELECT * FROM investment_proposals WHERE theme_id = %s ORDER BY target_allocation DESC",
                    (entry["id"],)
                )
                entry["proposals"] = [_serialize_row(p) for p in cur.fetchall()]

                cur.execute(
                    "SELECT * FROM theme_scenarios WHERE theme_id = %s ORDER BY probability DESC",
                    (entry["id"],)
                )
                entry["scenarios"] = [_serialize_row(s) for s in cur.fetchall()]
    finally:
        conn.close()

    return templates.TemplateResponse(request=request, name="theme_history.html", context={
        **ctx,
        "tracking": _serialize_row(tracking),
        "history": [_serialize_row(h) for h in history],
    })


@pages_router.get("")
def themes_page(
    request: Request,
    horizon: str | None = Query(default=None),
    min_confidence: float = Query(default=0.0),
    q: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    user: Optional[UserInDB] = Depends(get_current_user),
    auth_cfg: AuthConfig = Depends(_get_auth_cfg),
):
    ctx = _base_ctx(request, "themes", user, auth_cfg)
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
            if q:
                query += " AND (t.theme_name ILIKE %s OR t.description ILIKE %s)"
                params.extend([f"%{q}%", f"%{q}%"])

            query += " ORDER BY s.analysis_date DESC, t.confidence_score DESC LIMIT %s"
            params.append(limit)
            cur.execute(query, params)
            themes = cur.fetchall()

            for theme in themes:
                # 시나리오
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
                theme["proposals"] = [_serialize_row(p) for p in cur.fetchall()]

            # 추적 데이터 매핑
            cur.execute("SELECT * FROM theme_tracking ORDER BY last_seen_date DESC")
            tracking_map = {}
            for row in cur.fetchall():
                tracking_map[row["theme_key"]] = _serialize_row(row)
    finally:
        conn.close()

    return templates.TemplateResponse(request=request, name="themes.html", context={
        **ctx,
        "themes": [_serialize_row(t) for t in themes],
        "tracking_map": tracking_map,
        "horizon": horizon,
        "min_confidence": min_confidence,
        "q": q,
    })
