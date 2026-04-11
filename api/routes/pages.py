"""Jinja2 템플릿 기반 웹 페이지 라우트"""
from fastapi import APIRouter, Request, Query
from fastapi.templating import Jinja2Templates
from shared.config import DatabaseConfig
from shared.db import get_connection
from psycopg2.extras import RealDictCursor
from api.routes.sessions import _serialize_row

router = APIRouter(tags=["페이지"])

templates = Jinja2Templates(directory="api/templates")


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

            # 테마 + 시나리오 + 제안
            cur.execute(
                "SELECT * FROM investment_themes WHERE session_id = %s ORDER BY confidence_score DESC",
                (session_id,)
            )
            themes = cur.fetchall()

            buy_count = 0
            total_alloc = 0.0
            for theme in themes:
                # 시나리오
                cur.execute(
                    "SELECT * FROM theme_scenarios WHERE theme_id = %s ORDER BY probability DESC",
                    (theme["id"],)
                )
                theme["scenarios"] = [_serialize_row(s) for s in cur.fetchall()]

                # 제안
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

            # ── 추적 데이터: 테마 변화 감지 ──
            # 연속 등장 테마 (streak >= 2)
            cur.execute("""
                SELECT * FROM theme_tracking
                WHERE last_seen_date = %s
                ORDER BY streak_days DESC, appearances DESC
            """, (today_date,))
            active_tracking = [_serialize_row(r) for r in cur.fetchall()]

            # 신규 테마 (오늘 처음 등장)
            new_themes = [t for t in active_tracking if t["appearances"] == 1]

            # 소멸 테마 (어제까지 있었는데 오늘 없음)
            cur.execute("""
                SELECT * FROM theme_tracking
                WHERE last_seen_date < %s
                  AND last_seen_date >= %s::date - INTERVAL '3 days'
                ORDER BY last_seen_date DESC
            """, (today_date, today_date))
            disappeared_themes = [_serialize_row(r) for r in cur.fetchall()]

            # 신뢰도 변동 큰 테마
            confidence_changes = []
            for t in active_tracking:
                if t.get("prev_confidence") is not None and t.get("latest_confidence") is not None:
                    diff = t["latest_confidence"] - t["prev_confidence"]
                    if abs(diff) >= 0.05:
                        t["confidence_diff"] = round(diff, 2)
                        confidence_changes.append(t)
            confidence_changes.sort(key=lambda x: abs(x["confidence_diff"]), reverse=True)

            # ── 추적 데이터: 종목 변화 감지 ──
            # 신규 진입 종목 (오늘 처음 추천)
            cur.execute("""
                SELECT * FROM proposal_tracking
                WHERE first_recommended_date = %s
                ORDER BY latest_action, ticker
            """, (today_date,))
            new_proposals = [_serialize_row(r) for r in cur.fetchall()]

            # 액션 변경 종목 (hold→buy, buy→sell 등)
            cur.execute("""
                SELECT * FROM proposal_tracking
                WHERE last_recommended_date = %s
                  AND prev_action IS NOT NULL
                  AND prev_action != latest_action
                ORDER BY ticker
            """, (today_date,))
            action_changes = [_serialize_row(r) for r in cur.fetchall()]

    finally:
        conn.close()

    # 투자 신호 생성
    signals = []
    for p in new_proposals:
        if p.get("latest_action") == "buy":
            signals.append({"type": "new_buy", "icon": "new",
                            "text": f"{p['asset_name'] or p['ticker']} ({p['ticker']}) 신규 매수 진입"})
    for p in action_changes:
        signals.append({"type": "action_change", "icon": "change",
                        "text": f"{p['asset_name'] or p['ticker']} ({p['ticker']}) "
                                f"{p['prev_action']}→{p['latest_action']}"})
    for t in confidence_changes[:3]:
        direction = "up" if t["confidence_diff"] > 0 else "down"
        signals.append({"type": f"confidence_{direction}", "icon": direction,
                        "text": f"'{t['theme_name']}' 신뢰도 "
                                f"{t['prev_confidence']*100:.0f}%→{t['latest_confidence']*100:.0f}%"})
    for t in disappeared_themes[:2]:
        signals.append({"type": "disappeared", "icon": "gone",
                        "text": f"'{t['theme_name']}' 테마 소멸 (마지막: {t['last_seen_date']})"})

    return templates.TemplateResponse(request=request, name="dashboard.html", context={
        "active_page": "dashboard",
        "session": _serialize_row(session),
        "themes": [_serialize_row(t) for t in themes],
        "issue_count": issue_count,
        "theme_count": len(themes),
        "buy_count": buy_count,
        "total_alloc": total_alloc,
        "signals": signals,
        "active_tracking": active_tracking,
        "new_themes": new_themes,
        "disappeared_themes": disappeared_themes,
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
    limit: int = Query(default=50, ge=1, le=200),
):
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

            query += " ORDER BY s.analysis_date DESC, p.target_allocation DESC LIMIT %s"
            params.append(limit)
            cur.execute(query, params)
            proposals = cur.fetchall()

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
    })
