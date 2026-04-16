"""Jinja2 템플릿 기반 웹 페이지 라우트"""
import re
from datetime import date
from typing import Optional
from fastapi import APIRouter, Request, Query, Depends
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from shared.config import DatabaseConfig, AuthConfig
from shared.db import get_connection
from psycopg2.extras import RealDictCursor
from api.routes.sessions import _serialize_row
from api.auth.dependencies import get_current_user, _get_auth_cfg
from api.auth.models import UserInDB

router = APIRouter(tags=["페이지"])

templates = Jinja2Templates(directory="api/templates")


def _nl_numbered(text: str) -> Markup:
    """①②③ 또는 1. 2. 3. 형태의 번호 리스트를 줄바꿈으로 분리"""
    if not text:
        return Markup("")
    # ① ② ③ … ⑳ 원문자 앞에 <br> 삽입
    parts = re.split(r'\s*(?=[①-⑳])', text)
    if len(parts) > 1:
        # 각 파트의 양쪽 공백 제거 후 결합
        stripped = [p.strip() for p in parts if p.strip()]
        result = '<br>'.join(stripped)
        return Markup(result)
    # 1. 2. 3. 형태 처리
    result = re.sub(r'(?<=\S)\s+(\d+)\.\s', r'<br>\1. ', text)
    return Markup(result)


templates.env.filters["nl_numbered"] = _nl_numbered


_CURRENCY_SYMBOLS = {"KRW": "₩", "USD": "$", "EUR": "€", "JPY": "¥", "GBP": "£", "CNY": "¥"}


def _fmt_price(value, currency: str = "") -> str:
    """가격을 통화 기호 + 천 단위 쉼표로 포맷팅 (정수 통화는 소수점 제거)"""
    if value is None:
        return "-"
    try:
        num = float(value)
    except (ValueError, TypeError):
        return str(value)
    if num == 0:
        return "-"
    symbol = _CURRENCY_SYMBOLS.get((currency or "").upper(), "")
    # KRW, JPY 등은 소수점 없이 표시
    if (currency or "").upper() in ("KRW", "JPY", "KRW"):
        return f"{symbol}{num:,.0f}"
    return f"{symbol}{num:,.2f}"


templates.env.filters["fmt_price"] = _fmt_price


def _get_cfg() -> DatabaseConfig:
    return DatabaseConfig()


def _base_ctx(request: Request, active_page: str, user: Optional[UserInDB], auth_cfg: AuthConfig) -> dict:
    """모든 템플릿에 공통으로 전달할 컨텍스트"""
    ctx = {
        "request": request,
        "active_page": active_page,
        "current_user": user,
        "auth_enabled": auth_cfg.enabled,
        "unread_notifications": 0,
    }
    if user and auth_cfg.enabled:
        try:
            conn = get_connection(_get_cfg())
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*) FROM user_notifications WHERE user_id = %s AND is_read = FALSE",
                        (user.id,),
                    )
                    ctx["unread_notifications"] = cur.fetchone()[0]
            finally:
                conn.close()
        except Exception:
            pass
    return ctx


# ──────────────────────────────────────────────
# Dashboard (Home) — 어제 대비 변화 + 투자 신호
# ──────────────────────────────────────────────
@router.get("/")
def dashboard(request: Request, user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    ctx = _base_ctx(request, "dashboard", user, auth_cfg)
    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 최신 세션
            cur.execute("SELECT * FROM analysis_sessions ORDER BY analysis_date DESC LIMIT 1")
            session = cur.fetchone()
            if not session:
                return templates.TemplateResponse(
                    request=request, name="dashboard.html",
                    context={**ctx, "session": None},
                )

            session_id = session["id"]
            today_date = session["analysis_date"]

            # 이슈 수
            cur.execute("SELECT COUNT(*) AS cnt FROM global_issues WHERE session_id = %s", (session_id,))
            issue_count = cur.fetchone()["cnt"]

            # 테마 (요약만 — 시나리오/제안 상세는 세션 상세에서)
            cur.execute(
                "SELECT * FROM investment_themes WHERE session_id = %s ORDER BY confidence_score DESC",
                (session_id,)
            )
            themes = cur.fetchall()

            buy_count = 0
            total_alloc = 0.0
            high_conviction_count = 0
            early_signal_count = 0
            discovery_counts = {}  # discovery_type별 카운트
            sector_counts = {}    # 섹터별 카운트
            all_proposals = []
            for theme in themes:
                cur.execute(
                    "SELECT * FROM investment_proposals WHERE theme_id = %s ORDER BY target_allocation DESC",
                    (theme["id"],)
                )
                proposals = cur.fetchall()
                theme["proposals"] = [_serialize_row(p) for p in proposals]
                for p in proposals:
                    if p.get("action") == "buy":
                        buy_count += 1
                    total_alloc += float(p.get("target_allocation") or 0)
                    # 추가 통계
                    if p.get("conviction") == "high":
                        high_conviction_count += 1
                    dt = p.get("discovery_type") or "unknown"
                    discovery_counts[dt] = discovery_counts.get(dt, 0) + 1
                    if dt == "early_signal":
                        early_signal_count += 1
                    sec = p.get("sector")
                    if sec:
                        sector_counts[sec] = sector_counts.get(sec, 0) + 1
                    all_proposals.append(p)

            # 상위 섹터 (최대 5개)
            top_sectors = sorted(sector_counts.items(), key=lambda x: -x[1])[:5]
            # 평균 신뢰도
            avg_confidence = 0.0
            if themes:
                avg_confidence = sum(float(t.get("confidence_score") or 0) for t in themes) / len(themes)

            # ── 추적 데이터 ──
            cur.execute("""
                SELECT * FROM theme_tracking WHERE last_seen_date = %s
                ORDER BY streak_days DESC, appearances DESC
            """, (today_date,))
            active_tracking = [_serialize_row(r) for r in cur.fetchall()]

            # 소멸 테마
            cur.execute("""
                SELECT * FROM theme_tracking
                WHERE last_seen_date < %s
                  AND last_seen_date >= %s::date - INTERVAL '3 days'
                ORDER BY last_seen_date DESC
            """, (today_date, today_date))
            disappeared_themes = [_serialize_row(r) for r in cur.fetchall()]

            # ── 뉴스 기사 (카테고리별 그룹핑) ──
            cur.execute("""
                SELECT category, source, title, title_ko, summary, summary_ko, link, published
                FROM news_articles
                WHERE session_id = %s
                ORDER BY category, id
            """, (session_id,))
            raw_news = cur.fetchall()

            # 워치리스트 (로그인 사용자)
            watched_tickers = set()
            if user:
                cur.execute("SELECT ticker FROM user_watchlist WHERE user_id = %s", (user.id,))
                watched_tickers = {r["ticker"] for r in cur.fetchall()}

    finally:
        conn.close()

    # 뉴스를 카테고리별로 그룹핑
    from analyzer.news_collector import CATEGORY_LABELS
    news_by_category = {}
    for row in raw_news:
        cat = row["category"]
        if cat not in news_by_category:
            news_by_category[cat] = {
                "label": CATEGORY_LABELS.get(cat, cat),
                "articles": [],
            }
        news_by_category[cat]["articles"].append(_serialize_row(row))

    return templates.TemplateResponse(request=request, name="dashboard.html", context={
        **ctx,
        "session": _serialize_row(session),
        "themes": [_serialize_row(t) for t in themes],
        "issue_count": issue_count,
        "theme_count": len(themes),
        "buy_count": buy_count,
        "total_alloc": total_alloc,
        "high_conviction_count": high_conviction_count,
        "early_signal_count": early_signal_count,
        "discovery_counts": discovery_counts,
        "top_sectors": top_sectors,
        "avg_confidence": avg_confidence,
        "active_tracking": active_tracking,
        "disappeared_themes": disappeared_themes,
        "news_by_category": news_by_category,
        "watched_tickers": watched_tickers,
    })


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
                theme["proposals"] = [_serialize_row(p) for p in cur.fetchall()]

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

    # tracking에 currency 보충 (최신 이력에서 가져옴)
    if history and tracking_list:
        currency = history[0].get("currency")
        for tr in tracking_list:
            tr["latest_currency"] = currency

    return templates.TemplateResponse(request=request, name="ticker_history.html", context={
        **ctx,
        "ticker": ticker.upper(),
        "tracking_list": tracking_list,
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
# Watchlist (관심 종목)
# ──────────────────────────────────────────────
@router.get("/pages/watchlist")
def watchlist_page(request: Request, user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    """관심 종목 워치리스트 — 로그인 사용자만"""
    if not auth_cfg.enabled or user is None:
        return RedirectResponse("/auth/login?next=/pages/watchlist", status_code=302)

    ctx = _base_ctx(request, "watchlist", user, auth_cfg)
    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM user_watchlist WHERE user_id = %s ORDER BY created_at DESC",
                (user.id,),
            )
            watchlist = [_serialize_row(r) for r in cur.fetchall()]

            for item in watchlist:
                cur.execute("""
                    SELECT p.action, p.conviction, p.current_price, p.currency,
                           p.upside_pct, p.target_allocation, t.theme_name, s.analysis_date
                    FROM investment_proposals p
                    JOIN investment_themes t ON p.theme_id = t.id
                    JOIN analysis_sessions s ON t.session_id = s.id
                    WHERE UPPER(p.ticker) = UPPER(%s)
                    ORDER BY s.analysis_date DESC LIMIT 1
                """, (item["ticker"],))
                latest = cur.fetchone()
                item["latest"] = _serialize_row(latest) if latest else None

            cur.execute(
                "SELECT * FROM user_subscriptions WHERE user_id = %s ORDER BY created_at DESC",
                (user.id,),
            )
            subscriptions = [_serialize_row(r) for r in cur.fetchall()]
    finally:
        conn.close()

    return templates.TemplateResponse(request=request, name="watchlist.html", context={
        **ctx,
        "watchlist": watchlist,
        "subscriptions": subscriptions,
    })


# ──────────────────────────────────────────────
# Notifications (알림)
# ──────────────────────────────────────────────
@router.get("/pages/notifications")
def notifications_page(request: Request, user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    """알림 목록 — 로그인 사용자만"""
    if not auth_cfg.enabled or user is None:
        return RedirectResponse("/auth/login?next=/pages/notifications", status_code=302)

    ctx = _base_ctx(request, "notifications", user, auth_cfg)
    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM user_notifications WHERE user_id = %s ORDER BY created_at DESC LIMIT 100",
                (user.id,),
            )
            notifications = [_serialize_row(r) for r in cur.fetchall()]
            unread_count = sum(1 for n in notifications if not n.get("is_read"))
    finally:
        conn.close()

    return templates.TemplateResponse(request=request, name="notifications.html", context={
        **ctx,
        "notifications": notifications,
        "unread_count": unread_count,
    })


# ──────────────────────────────────────────────
# Theme Chat
# ──────────────────────────────────────────────
# ──────────────────────────────────────────────
# Profile (비밀번호 변경)
# ──────────────────────────────────────────────
@router.get("/pages/profile")
def profile_page(request: Request, user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    """프로필 페이지 — 로그인 사용자만"""
    if not auth_cfg.enabled or user is None:
        return RedirectResponse("/auth/login?next=/pages/profile", status_code=302)
    ctx = _base_ctx(request, "profile", user, auth_cfg)
    return templates.TemplateResponse(request=request, name="profile.html", context={
        **ctx,
        "error": "",
        "success": "",
    })


@router.get("/pages/chat")
def chat_list_page(request: Request, theme_id: int | None = Query(default=None), user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    """채팅 세션 목록 — Moderator 이상, 본인 세션만 (Admin은 전체)"""
    if auth_cfg.enabled:
        if user is None:
            return RedirectResponse("/auth/login?next=/pages/chat", status_code=302)
        if user.role not in ("admin", "moderator"):
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="채팅 기능은 Moderator 이상 권한이 필요합니다")
    ctx = _base_ctx(request, "chat", user, auth_cfg)
    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 테마 목록 (드롭다운용)
            cur.execute("""
                SELECT t.id, t.theme_name, s.analysis_date
                FROM investment_themes t
                JOIN analysis_sessions s ON t.session_id = s.id
                ORDER BY s.analysis_date DESC, t.confidence_score DESC
            """)
            themes = cur.fetchall()

            # 채팅 세션 목록 — 본인 세션만 (Admin은 전체)
            query = """
                SELECT cs.*, t.theme_name, s.analysis_date AS theme_date,
                       (SELECT COUNT(*) FROM theme_chat_messages m
                        WHERE m.chat_session_id = cs.id) AS message_count
                FROM theme_chat_sessions cs
                JOIN investment_themes t ON cs.theme_id = t.id
                JOIN analysis_sessions s ON t.session_id = s.id
            """
            conditions = []
            params = []

            # Admin이 아니면 본인 세션만
            if user and user.role != "admin":
                conditions.append("cs.user_id = %s")
                params.append(user.id)

            if theme_id is not None:
                conditions.append("cs.theme_id = %s")
                params.append(theme_id)

            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY cs.updated_at DESC"
            cur.execute(query, params)
            chat_sessions = cur.fetchall()
    finally:
        conn.close()

    return templates.TemplateResponse(request=request, name="chat_list.html", context={
        **ctx,
        "themes": [_serialize_row(t) for t in themes],
        "chat_sessions": [_serialize_row(s) for s in chat_sessions],
        "selected_theme_id": theme_id,
    })


@router.get("/pages/chat/new/{theme_id}")
def chat_new_redirect(request: Request, theme_id: int, user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    """새 채팅 세션 생성 → 채팅방으로 리다이렉트 (Moderator 이상)"""
    if auth_cfg.enabled:
        if user is None:
            return RedirectResponse(f"/auth/login?next=/pages/chat/new/{theme_id}", status_code=302)
        if user.role not in ("admin", "moderator"):
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="채팅 기능은 Moderator 이상 권한이 필요합니다")
    cfg = _get_cfg()
    conn = get_connection(cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, theme_name FROM investment_themes WHERE id = %s",
                        (theme_id,))
            theme = cur.fetchone()
            if not theme:
                return RedirectResponse(url="/pages/chat", status_code=302)

            user_id = user.id if user else None
            cur.execute(
                """INSERT INTO theme_chat_sessions (theme_id, title, user_id)
                   VALUES (%s, %s, %s) RETURNING id""",
                (theme_id, f"{theme['theme_name']} 채팅", user_id)
            )
            new_id = cur.fetchone()["id"]
        conn.commit()
    finally:
        conn.close()

    return RedirectResponse(url=f"/pages/chat/{new_id}", status_code=302)


@router.get("/pages/chat/{chat_session_id}")
def chat_room_page(request: Request, chat_session_id: int, user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    """채팅 대화 화면 — Moderator 이상, 본인 세션만 (Admin은 전체)"""
    if auth_cfg.enabled:
        if user is None:
            return RedirectResponse(f"/auth/login?next=/pages/chat/{chat_session_id}", status_code=302)
        if user.role not in ("admin", "moderator"):
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="채팅 기능은 Moderator 이상 권한이 필요합니다")
    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 세션 정보
            cur.execute("""
                SELECT cs.*, t.theme_name, t.description AS theme_description,
                       t.confidence_score, t.time_horizon, t.theme_type,
                       s.analysis_date
                FROM theme_chat_sessions cs
                JOIN investment_themes t ON cs.theme_id = t.id
                JOIN analysis_sessions s ON t.session_id = s.id
                WHERE cs.id = %s
            """, (chat_session_id,))
            session = cur.fetchone()
            if not session:
                return RedirectResponse(url="/pages/chat", status_code=302)

            # 소유권 검증 (Admin은 모든 세션 접근 가능)
            if auth_cfg.enabled and user and user.role != "admin" and session.get("user_id") != user.id:
                return RedirectResponse(url="/pages/chat", status_code=302)

            # 메시지 이력
            cur.execute("""
                SELECT id, role, content, created_at
                FROM theme_chat_messages
                WHERE chat_session_id = %s
                ORDER BY created_at
            """, (chat_session_id,))
            messages = cur.fetchall()
    finally:
        conn.close()

    ctx = _base_ctx(request, "chat", user, auth_cfg)
    return templates.TemplateResponse(request=request, name="chat_room.html", context={
        **ctx,
        "session": _serialize_row(session),
        "messages": [_serialize_row(m) for m in messages],
    })
