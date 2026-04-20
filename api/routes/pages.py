"""Jinja2 템플릿 기반 웹 페이지 라우트 — B1 진행 중 (단계적 도메인 이전)."""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Request, Query, Depends
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from psycopg2.extras import RealDictCursor

from shared.config import DatabaseConfig, AuthConfig
from shared.db import get_connection
from shared.tier_limits import (
    TIER_INFO,
    WATCHLIST_LIMITS,
    SUBSCRIPTION_LIMITS,
    STAGE2_DAILY_LIMITS,
    CHAT_DAILY_TURNS,
    HISTORY_DAYS_LIMITS,
    get_watchlist_limit,
    get_subscription_limit,
    get_chat_daily_limit,
)
from api.serialization import serialize_row as _serialize_row
from api.page_context import base_ctx as _base_ctx
from api.template_filters import register as _register_filters
from api.auth.dependencies import get_current_user, _get_auth_cfg
from api.auth.models import UserInDB

router = APIRouter(tags=["페이지"])
templates = Jinja2Templates(directory="api/templates")
_register_filters(templates.env)


def _get_cfg() -> DatabaseConfig:
    return DatabaseConfig()


# ──────────────────────────────────────────────
# Sessions
# ──────────────────────────────────────────────
@router.get("/pages/sessions")
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


@router.get("/pages/sessions/date/{analysis_date}")
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


@router.get("/pages/sessions/{session_id}")
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


# ──────────────────────────────────────────────
# Theme History (신규)
# ──────────────────────────────────────────────
@router.get("/pages/themes/history/{theme_key}")
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


# ──────────────────────────────────────────────
# Stock Deep Analysis Page (종목 심층분석)
# ──────────────────────────────────────────────
@router.get("/proposals/{proposal_id}/stock-analysis")
def stock_analysis_page(
    request: Request,
    proposal_id: int,
    user: Optional[UserInDB] = Depends(get_current_user),
    auth_cfg: AuthConfig = Depends(_get_auth_cfg),
):
    """투자 제안 종목의 심층분석 리포트 페이지"""
    ctx = _base_ctx(request, "proposals", user, auth_cfg)
    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT sa.*,
                       p.ticker, p.asset_name, p.market, p.currency, p.sector,
                       p.action, p.conviction, p.target_allocation,
                       p.current_price, p.target_price_low, p.target_price_high,
                       p.upside_pct, p.quant_score, p.sentiment_score,
                       p.rationale, p.risk_factors,
                       p.entry_condition, p.exit_condition,
                       t.theme_name, t.confidence_score, t.time_horizon,
                       s.analysis_date
                FROM stock_analyses sa
                JOIN investment_proposals p ON sa.proposal_id = p.id
                JOIN investment_themes t ON p.theme_id = t.id
                JOIN analysis_sessions s ON t.session_id = s.id
                WHERE sa.proposal_id = %s
            """, (proposal_id,))
            row = cur.fetchone()
    finally:
        conn.close()

    return templates.TemplateResponse(request=request, name="stock_analysis.html", context={
        **ctx,
        "proposal_id": proposal_id,
        "analysis": _serialize_row(row) if row else None,
    })


# ──────────────────────────────────────────────
# Ticker History (신규)
# ──────────────────────────────────────────────
@router.get("/pages/proposals/history/{ticker}")
def ticker_history_page(request: Request, ticker: str, user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    """특정 종목의 일자별 추천 이력"""
    ctx = _base_ctx(request, "proposals", user, auth_cfg)
    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 추적 정보
            cur.execute("""
                SELECT * FROM proposal_tracking
                WHERE UPPER(ticker) = UPPER(%s)
                ORDER BY last_recommended_date DESC
            """, (ticker,))
            tracking_list = [_serialize_row(r) for r in cur.fetchall()]

            # 일자별 제안 이력
            cur.execute("""
                SELECT p.*, t.theme_name, t.confidence_score AS theme_confidence,
                       s.analysis_date
                FROM investment_proposals p
                JOIN investment_themes t ON p.theme_id = t.id
                JOIN analysis_sessions s ON t.session_id = s.id
                WHERE UPPER(p.ticker) = UPPER(%s)
                ORDER BY s.analysis_date DESC
                LIMIT 30
            """, (ticker,))
            history = [_serialize_row(r) for r in cur.fetchall()]
    finally:
        conn.close()

    # tracking_list 를 ticker 단위 단일 요약으로 집계 — 같은 종목이 여러 테마에서 추천되면 테마별 행이 생기므로 통합 표시
    tracking = None
    if tracking_list:
        currency = history[0].get("currency") if history else None
        latest = tracking_list[0]  # ORDER BY last_recommended_date DESC 첫 행 = 최신
        lows = [tr["latest_target_price_low"] for tr in tracking_list if tr.get("latest_target_price_low") is not None]
        highs = [tr["latest_target_price_high"] for tr in tracking_list if tr.get("latest_target_price_high") is not None]
        first_dates = [tr["first_recommended_date"] for tr in tracking_list if tr.get("first_recommended_date")]
        last_dates = [tr["last_recommended_date"] for tr in tracking_list if tr.get("last_recommended_date")]
        distinct_dates = {h["analysis_date"] for h in history if h.get("analysis_date")} if history else set()

        tracking = {
            "asset_name": latest.get("asset_name") or ticker.upper(),
            "theme_count": len(tracking_list),
            "recommendation_count": len(distinct_dates) if distinct_dates else sum(tr.get("recommendation_count") or 0 for tr in tracking_list),
            "first_recommended_date": min(first_dates) if first_dates else None,
            "last_recommended_date": max(last_dates) if last_dates else None,
            "latest_action": latest.get("latest_action"),
            "prev_action": latest.get("prev_action"),
            "latest_conviction": latest.get("latest_conviction"),
            "latest_target_price_low": min(lows) if lows else None,
            "latest_target_price_high": max(highs) if highs else None,
            "latest_currency": currency,
        }

    return templates.TemplateResponse(request=request, name="ticker_history.html", context={
        **ctx,
        "ticker": ticker.upper(),
        "tracking": tracking,
        "history": history,
    })


# ──────────────────────────────────────────────
# Themes
# ──────────────────────────────────────────────
@router.get("/pages/themes")
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


# ──────────────────────────────────────────────
# Proposals
# ──────────────────────────────────────────────
@router.get("/pages/proposals")
def proposals_page(
    request: Request,
    action: str | None = Query(default=None),
    asset_type: str | None = Query(default=None),
    conviction: str | None = Query(default=None),
    ticker: str | None = Query(default=None),
    date_from: str | None = Query(default=None, description="조회 시작일 (YYYY-MM-DD)"),
    date_to: str | None = Query(default=None, description="조회 종료일 (YYYY-MM-DD)"),
    market: str | None = Query(default=None, description="시장 (KRX, NASDAQ 등)"),
    sector: str | None = Query(default=None, description="섹터"),
    discovery_type: str | None = Query(default=None, description="발굴유형"),
    time_horizon: str | None = Query(default=None, description="투자기간"),
    sort: str | None = Query(default=None, description="정렬 기준"),
    limit: int = Query(default=50, ge=1, le=200),
    user: Optional[UserInDB] = Depends(get_current_user),
    auth_cfg: AuthConfig = Depends(_get_auth_cfg),
):
    # 날짜 기본값: 오늘
    today = date.today().isoformat()
    if not date_from:
        date_from = today
    if not date_to:
        date_to = today

    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            query = """
                SELECT p.*, t.theme_name, t.time_horizon, s.analysis_date
                FROM investment_proposals p
                JOIN investment_themes t ON p.theme_id = t.id
                JOIN analysis_sessions s ON t.session_id = s.id
                WHERE 1=1
            """
            params: list = []

            if action:
                query += " AND p.action = %s"
                params.append(action)
            if asset_type:
                query += " AND p.asset_type = %s"
                params.append(asset_type)
            if conviction:
                query += " AND p.conviction = %s"
                params.append(conviction)
            if ticker:
                query += " AND UPPER(p.ticker) = UPPER(%s)"
                params.append(ticker)
            if date_from:
                query += " AND s.analysis_date >= %s"
                params.append(date_from)
            if date_to:
                query += " AND s.analysis_date <= %s"
                params.append(date_to)
            if market:
                query += " AND UPPER(p.market) = UPPER(%s)"
                params.append(market)
            if sector:
                query += " AND p.sector ILIKE %s"
                params.append(f"%{sector}%")
            if discovery_type:
                query += " AND p.discovery_type = %s"
                params.append(discovery_type)
            if time_horizon:
                query += " AND t.time_horizon = %s"
                params.append(time_horizon)

            # 정렬
            sort_map = {
                "date": "s.analysis_date DESC",
                "upside": "p.upside_pct DESC NULLS LAST",
                "quant": "p.quant_score DESC NULLS LAST",
                "allocation": "p.target_allocation DESC NULLS LAST",
                "conviction_sort": "CASE p.conviction WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END",
            }
            order_by = sort_map.get(sort, "s.analysis_date DESC, p.target_allocation DESC")
            query += f" ORDER BY {order_by} LIMIT %s"
            params.append(limit)
            cur.execute(query, params)
            proposals = cur.fetchall()

            # 필터 옵션용 고유값 조회
            cur.execute("SELECT DISTINCT market FROM investment_proposals WHERE market IS NOT NULL ORDER BY market")
            market_options = [r["market"] for r in cur.fetchall()]

            cur.execute("SELECT DISTINCT sector FROM investment_proposals WHERE sector IS NOT NULL ORDER BY sector")
            sector_options = [r["sector"] for r in cur.fetchall()]

            cur.execute("SELECT DISTINCT discovery_type FROM investment_proposals WHERE discovery_type IS NOT NULL ORDER BY discovery_type")
            discovery_type_options = [r["discovery_type"] for r in cur.fetchall()]

            cur.execute("SELECT DISTINCT time_horizon FROM investment_themes WHERE time_horizon IS NOT NULL ORDER BY time_horizon")
            time_horizon_options = [r["time_horizon"] for r in cur.fetchall()]

            # 추적 데이터
            cur.execute("SELECT * FROM proposal_tracking ORDER BY last_recommended_date DESC")
            prop_tracking = {}
            for row in cur.fetchall():
                key = f"{row['ticker']}_{row['theme_key']}"
                prop_tracking[key] = _serialize_row(row)

            # 워치리스트 + 메모 (로그인 사용자)
            watched_tickers = set()
            user_memos = {}
            if user:
                cur.execute("SELECT ticker FROM user_watchlist WHERE user_id = %s", (user.id,))
                watched_tickers = {r["ticker"] for r in cur.fetchall()}

                proposal_ids = [p["id"] for p in proposals]
                if proposal_ids:
                    cur.execute(
                        "SELECT proposal_id, content FROM user_proposal_memos "
                        "WHERE user_id = %s AND proposal_id = ANY(%s)",
                        (user.id, proposal_ids),
                    )
                    user_memos = {r["proposal_id"]: r["content"] for r in cur.fetchall()}
    finally:
        conn.close()

    ctx = _base_ctx(request, "proposals", user, auth_cfg)
    return templates.TemplateResponse(request=request, name="proposals.html", context={
        **ctx,
        "proposals": [_serialize_row(p) for p in proposals],
        "prop_tracking": prop_tracking,
        "watched_tickers": watched_tickers,
        "user_memos": user_memos,
        "action": action,
        "asset_type": asset_type,
        "conviction": conviction,
        "ticker": ticker,
        "date_from": date_from,
        "date_to": date_to,
        "market": market,
        "sector": sector,
        "discovery_type": discovery_type,
        "time_horizon": time_horizon,
        "sort": sort,
        "market_options": market_options,
        "sector_options": sector_options,
        "discovery_type_options": discovery_type_options,
        "time_horizon_options": time_horizon_options,
    })


# ──────────────────────────────────────────────
# Track Record & Pricing — 공개 페이지
# ── 고객 문의 페이지 ──────────────────────────────


@router.get("/pages/inquiry")
def inquiry_list_page(
    request: Request,
    category: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    user: Optional[UserInDB] = Depends(get_current_user),
    auth_cfg: AuthConfig = Depends(_get_auth_cfg),
):
    """문의 게시판 목록 페이지"""
    # 유효하지 않은 필터값 무시
    if category and category not in ("general", "bug", "feature"):
        category = None
    if status and status not in ("open", "answered", "closed"):
        status = None

    ctx = _base_ctx(request, "inquiry", user, auth_cfg)

    per_page = 20
    offset = (page - 1) * per_page
    can_view_private = user is not None and user.role in ("admin", "moderator")

    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            conditions = []
            params = []

            if not can_view_private:
                if user:
                    conditions.append("(i.is_private = FALSE OR i.user_id = %s)")
                    params.append(user.id)
                else:
                    conditions.append("i.is_private = FALSE")

            if category:
                conditions.append("i.category = %s")
                params.append(category)
            if status:
                conditions.append("i.status = %s")
                params.append(status)

            where = "WHERE " + " AND ".join(conditions) if conditions else ""

            cur.execute(
                f"""
                SELECT i.*, u.nickname AS user_nickname,
                       (SELECT COUNT(*) FROM inquiry_replies r WHERE r.inquiry_id = i.id) AS reply_count
                FROM inquiries i
                LEFT JOIN users u ON u.id = i.user_id
                {where}
                ORDER BY i.created_at DESC
                LIMIT %s OFFSET %s
                """,
                (*params, per_page, offset),
            )
            inquiries = [_serialize_row(dict(r)) for r in cur.fetchall()]

            cur.execute(f"SELECT COUNT(*) FROM inquiries i {where}", tuple(params))
            total = cur.fetchone()["count"]
    finally:
        conn.close()

    total_pages = max(1, (total + per_page - 1) // per_page)

    return templates.TemplateResponse(request=request, name="inquiry_list.html", context={
        **ctx,
        "inquiries": inquiries,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "selected_category": category,
        "selected_status": status,
    })


@router.get("/pages/inquiry/new")
def inquiry_new_page(
    request: Request,
    user: Optional[UserInDB] = Depends(get_current_user),
    auth_cfg: AuthConfig = Depends(_get_auth_cfg),
):
    """문의 작성 페이지 — 로그인 필수"""
    if auth_cfg.enabled and not user:
        return RedirectResponse(url="/auth/login?next=/pages/inquiry/new", status_code=302)
    ctx = _base_ctx(request, "inquiry", user, auth_cfg)
    return templates.TemplateResponse(request=request, name="inquiry_new.html", context=ctx)


@router.get("/pages/inquiry/{inquiry_id}")
def inquiry_detail_page(
    request: Request,
    inquiry_id: int,
    user: Optional[UserInDB] = Depends(get_current_user),
    auth_cfg: AuthConfig = Depends(_get_auth_cfg),
):
    """문의 상세 페이지"""
    ctx = _base_ctx(request, "inquiry", user, auth_cfg)

    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT i.*, u.nickname AS user_nickname
                FROM inquiries i
                LEFT JOIN users u ON u.id = i.user_id
                WHERE i.id = %s
                """,
                (inquiry_id,),
            )
            inquiry = cur.fetchone()
            if not inquiry:
                return RedirectResponse(url="/pages/inquiry", status_code=302)

            # 비공개 접근 제어
            if inquiry["is_private"]:
                is_author = user and inquiry["user_id"] == user.id
                can_view = user and user.role in ("admin", "moderator")
                if not is_author and not can_view:
                    return RedirectResponse(url="/pages/inquiry", status_code=302)

            # 답변 목록
            cur.execute(
                """
                SELECT r.*, u.nickname AS user_nickname
                FROM inquiry_replies r
                LEFT JOIN users u ON u.id = r.user_id
                WHERE r.inquiry_id = %s
                ORDER BY r.created_at ASC
                """,
                (inquiry_id,),
            )
            replies = [_serialize_row(dict(r)) for r in cur.fetchall()]
    finally:
        conn.close()

    inquiry = _serialize_row(dict(inquiry))
    is_author = user and inquiry.get("user_id") == user.id
    is_staff = user and user.role in ("admin", "moderator")

    return templates.TemplateResponse(request=request, name="inquiry_detail.html", context={
        **ctx,
        "inquiry": inquiry,
        "replies": replies,
        "is_author": is_author,
        "is_staff": is_staff,
    })
