"""Jinja2 템플릿 기반 웹 페이지 라우트"""
import re
from datetime import date
from fastapi import APIRouter, Request, Query
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from shared.config import DatabaseConfig
from shared.db import get_connection
from psycopg2.extras import RealDictCursor
from api.routes.sessions import _serialize_row

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


# ──────────────────────────────────────────────
# Dashboard (Home) — 어제 대비 변화 + 투자 신호
# ──────────────────────────────────────────────
@router.get("/")
def dashboard(request: Request):
    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 최신 세션
            cur.execute("SELECT * FROM analysis_sessions ORDER BY analysis_date DESC LIMIT 1")
            session = cur.fetchone()
            if not session:
                return templates.TemplateResponse(
                    request=request, name="dashboard.html",
                    context={"active_page": "dashboard", "session": None},
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
                SELECT category, source, title, title_ko, summary, link, published
                FROM news_articles
                WHERE session_id = %s
                ORDER BY category, id
            """, (session_id,))
            raw_news = cur.fetchall()

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
        "active_page": "dashboard",
        "session": _serialize_row(session),
        "themes": [_serialize_row(t) for t in themes],
        "issue_count": issue_count,
        "theme_count": len(themes),
        "buy_count": buy_count,
        "total_alloc": total_alloc,
        "active_tracking": active_tracking,
        "disappeared_themes": disappeared_themes,
        "news_by_category": news_by_category,
    })


# ──────────────────────────────────────────────
# Sessions
# ──────────────────────────────────────────────
@router.get("/pages/sessions")
def sessions_page(request: Request, limit: int = Query(default=30, ge=1, le=100)):
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
        "active_page": "sessions",
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
def session_detail_page(request: Request, session_id: int):
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

    return templates.TemplateResponse(request=request, name="session_detail.html", context={
        "active_page": "sessions",
        "session": _serialize_row(session),
        "issues": [_serialize_row(i) for i in issues],
        "themes": [_serialize_row(t) for t in themes],
        "tracking_map": tracking_map,
    })


# ──────────────────────────────────────────────
# Theme History (신규)
# ──────────────────────────────────────────────
@router.get("/pages/themes/history/{theme_key}")
def theme_history_page(request: Request, theme_key: str):
    """특정 테마의 일자별 추이"""
    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 추적 정보
            cur.execute("SELECT * FROM theme_tracking WHERE theme_key = %s", (theme_key,))
            tracking = cur.fetchone()
            if not tracking:
                return templates.TemplateResponse(request=request, name="theme_history.html",
                    context={"active_page": "themes", "tracking": None, "history": []})

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
        "active_page": "themes",
        "tracking": _serialize_row(tracking),
        "history": [_serialize_row(h) for h in history],
    })


# ──────────────────────────────────────────────
# Ticker History (신규)
# ──────────────────────────────────────────────
@router.get("/pages/proposals/history/{ticker}")
def ticker_history_page(request: Request, ticker: str):
    """특정 종목의 일자별 추천 이력"""
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
        "active_page": "proposals",
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
):
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
        "active_page": "themes",
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
    finally:
        conn.close()

    return templates.TemplateResponse(request=request, name="proposals.html", context={
        "active_page": "proposals",
        "proposals": [_serialize_row(p) for p in proposals],
        "prop_tracking": prop_tracking,
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
# Theme Chat
# ────────────────────────────��─────────────────
@router.get("/pages/chat")
def chat_list_page(request: Request, theme_id: int | None = Query(default=None)):
    """채팅 세션 목록"""
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

            # 채팅 세션 목록
            query = """
                SELECT cs.*, t.theme_name, s.analysis_date AS theme_date,
                       (SELECT COUNT(*) FROM theme_chat_messages m
                        WHERE m.chat_session_id = cs.id) AS message_count
                FROM theme_chat_sessions cs
                JOIN investment_themes t ON cs.theme_id = t.id
                JOIN analysis_sessions s ON t.session_id = s.id
            """
            params = []
            if theme_id is not None:
                query += " WHERE cs.theme_id = %s"
                params.append(theme_id)
            query += " ORDER BY cs.updated_at DESC"
            cur.execute(query, params)
            chat_sessions = cur.fetchall()
    finally:
        conn.close()

    return templates.TemplateResponse(request=request, name="chat_list.html", context={
        "active_page": "chat",
        "themes": [_serialize_row(t) for t in themes],
        "chat_sessions": [_serialize_row(s) for s in chat_sessions],
        "selected_theme_id": theme_id,
    })


@router.get("/pages/chat/new/{theme_id}")
def chat_new_redirect(request: Request, theme_id: int):
    """새 채팅 세션 생성 → 채팅방으로 리다이렉트"""
    cfg = _get_cfg()
    conn = get_connection(cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, theme_name FROM investment_themes WHERE id = %s",
                        (theme_id,))
            theme = cur.fetchone()
            if not theme:
                return RedirectResponse(url="/pages/chat", status_code=302)

            cur.execute(
                """INSERT INTO theme_chat_sessions (theme_id, title)
                   VALUES (%s, %s) RETURNING id""",
                (theme_id, f"{theme['theme_name']} 채팅")
            )
            new_id = cur.fetchone()["id"]
        conn.commit()
    finally:
        conn.close()

    return RedirectResponse(url=f"/pages/chat/{new_id}", status_code=302)


@router.get("/pages/chat/{chat_session_id}")
def chat_room_page(request: Request, chat_session_id: int):
    """채팅 대화 화면"""
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

    return templates.TemplateResponse(request=request, name="chat_room.html", context={
        "active_page": "chat",
        "session": _serialize_row(session),
        "messages": [_serialize_row(m) for m in messages],
    })
