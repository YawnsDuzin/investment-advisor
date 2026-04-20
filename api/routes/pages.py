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
# Dashboard (Home) — 어제 대비 변화 + 투자 신호
# ──────────────────────────────────────────────
@router.get("/")
def dashboard(request: Request, user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    # 인증 활성 + 비로그인 → 랜딩 페이지로 안내 (UI-16)
    if auth_cfg.enabled and user is None:
        return RedirectResponse(url="/pages/landing", status_code=302)

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

            # 워치리스트 + 알림 구독 (로그인 사용자)
            watched_tickers = set()
            subscribed_tickers = set()
            subscribed_theme_keys = set()
            if user:
                cur.execute("SELECT ticker FROM user_watchlist WHERE user_id = %s", (user.id,))
                watched_tickers = {r["ticker"] for r in cur.fetchall()}

                cur.execute(
                    "SELECT sub_type, sub_key FROM user_subscriptions WHERE user_id = %s",
                    (user.id,),
                )
                for r in cur.fetchall():
                    if r["sub_type"] == "ticker":
                        subscribed_tickers.add((r["sub_key"] or "").upper())
                    elif r["sub_type"] == "theme":
                        subscribed_theme_keys.add(r["sub_key"])

            # ── Top Picks 조회 (v15) ──
            cur.execute("""
                SELECT dtp.rank, dtp.proposal_id, dtp.score_rule, dtp.score_final,
                       dtp.score_breakdown, dtp.rationale_text, dtp.key_risk, dtp.source,
                       p.ticker, p.asset_name, p.sector, p.currency, p.action,
                       p.conviction, p.discovery_type, p.price_momentum_check,
                       p.current_price, p.target_price_low, p.target_price_high,
                       p.upside_pct, p.price_source, p.target_allocation,
                       p.return_1m_pct, p.return_3m_pct, p.return_6m_pct, p.return_1y_pct,
                       p.rationale AS proposal_rationale, p.market,
                       p.foreign_net_buy_signal, p.squeeze_risk,
                       t.theme_name, t.theme_key, t.confidence_score AS theme_confidence,
                       EXISTS(SELECT 1 FROM stock_analyses sa WHERE sa.proposal_id = p.id) AS has_stock_analysis
                FROM daily_top_picks dtp
                JOIN investment_proposals p ON p.id = dtp.proposal_id
                JOIN investment_themes t    ON t.id = p.theme_id
                WHERE dtp.analysis_date = %s
                ORDER BY dtp.rank
            """, (today_date,))
            top_picks_raw = cur.fetchall()

            # 국채 금리 데이터 (bond_yields 테이블 — v20)
            bond_yields = None
            try:
                cur.execute("""
                    SELECT * FROM bond_yields
                    WHERE session_id = %s
                    ORDER BY snapshot_date DESC LIMIT 1
                """, (session_id,))
                by_row = cur.fetchone()
                if by_row:
                    bond_yields = _serialize_row(by_row)
            except Exception:
                pass  # v20 미적용 환경(테이블 미존재)에서도 동작

            # ── 전일 세션 대비 변화량 (delta) ──
            prev_issue_count = 0
            prev_theme_count = 0
            prev_buy_count = 0
            cur.execute("""
                SELECT id FROM analysis_sessions
                WHERE analysis_date < %s
                ORDER BY analysis_date DESC LIMIT 1
            """, (today_date,))
            prev_session = cur.fetchone()
            if prev_session:
                prev_sid = prev_session["id"]
                cur.execute("SELECT COUNT(*) AS cnt FROM global_issues WHERE session_id = %s", (prev_sid,))
                prev_issue_count = cur.fetchone()["cnt"]
                cur.execute("SELECT COUNT(*) AS cnt FROM investment_themes WHERE session_id = %s", (prev_sid,))
                prev_theme_count = cur.fetchone()["cnt"]
                cur.execute("""
                    SELECT COUNT(*) AS cnt FROM investment_proposals ip
                    JOIN investment_themes it ON ip.theme_id = it.id
                    WHERE it.session_id = %s AND ip.action = 'buy'
                """, (prev_sid,))
                prev_buy_count = cur.fetchone()["cnt"]

            # ── 최근 7일 히스토리 (스파크라인) ──
            cur.execute("""
                SELECT s.analysis_date,
                       (SELECT COUNT(*) FROM global_issues WHERE session_id = s.id) AS issue_cnt,
                       (SELECT COUNT(*) FROM investment_themes WHERE session_id = s.id) AS theme_cnt,
                       (SELECT COUNT(*) FROM investment_proposals ip
                        JOIN investment_themes it ON ip.theme_id = it.id
                        WHERE it.session_id = s.id AND ip.action = 'buy') AS buy_cnt
                FROM analysis_sessions s
                ORDER BY s.analysis_date DESC LIMIT 7
            """)
            history_rows = cur.fetchall()

            # 워치리스트 ∩ 오늘 분석 크로스 매칭
            watched_in_today = []
            if user and watched_tickers:
                for ticker in watched_tickers:
                    for theme in themes:
                        for p in (theme.get("proposals") or []):
                            if (p.get("ticker") or "").upper() == ticker.upper():
                                pick_rank = None
                                for pk_raw in top_picks_raw:
                                    if (pk_raw.get("ticker") or "").upper() == ticker.upper():
                                        pick_rank = pk_raw.get("rank")
                                        break
                                watched_in_today.append({
                                    "ticker": p.get("ticker"),
                                    "asset_name": p.get("asset_name"),
                                    "current_price": p.get("current_price"),
                                    "currency": p.get("currency"),
                                    "market": p.get("market"),
                                    "action": p.get("action"),
                                    "in_top_picks": pick_rank is not None,
                                    "pick_rank": pick_rank,
                                })
                                break

    finally:
        conn.close()

    # Top Picks 직렬화 + 개인화 플래그 주입
    top_picks = []
    for row in top_picks_raw:
        pk = _serialize_row(row)
        tk = (pk.get("ticker") or "").upper()
        pk["is_watched"] = tk in watched_tickers
        pk["is_subscribed"] = (
            tk in subscribed_tickers or pk.get("theme_key") in subscribed_theme_keys
        )
        # 불릿 차트용: 현재가/목표가 비율
        cp = float(pk.get("current_price") or 0)
        tp = float(pk.get("target_price_low") or 0)
        pk["price_pct"] = round(cp / tp * 100, 1) if tp > 0 and cp > 0 else None
        top_picks.append(pk)

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

    # 스파크라인 SVG 좌표 생성 (최근→과거 역순 → 시간순으로 뒤집기)
    def _spark_points(values, w=60, h=18):
        if not values or len(values) < 2:
            return ""
        mn, mx = min(values), max(values)
        rng = mx - mn if mx != mn else 1
        pts = []
        for i, v in enumerate(values):
            x = round(i / (len(values) - 1) * w, 1)
            y = round(h - (v - mn) / rng * (h - 2) - 1, 1)
            pts.append(f"{x},{y}")
        return " ".join(pts)

    hist_reversed = list(reversed(history_rows))  # 시간순
    spark_issues = _spark_points([r["issue_cnt"] for r in hist_reversed])
    spark_themes = _spark_points([r["theme_cnt"] for r in hist_reversed])
    spark_buys = _spark_points([r["buy_cnt"] for r in hist_reversed])

    # 리스크 온도 → 게이지 수치 매핑
    risk_temp = (_serialize_row(session) if session else {}).get("risk_temperature", "")
    risk_map = {"low": 20, "medium": 55, "high": 85}
    risk_pct = risk_map.get((risk_temp or "").lower(), 50)

    # 테마 뷰 한도 (tier_limits 참조)
    from shared.tier_limits import THEME_VIEW_LIMITS, normalize_tier
    theme_view_limit = THEME_VIEW_LIMITS.get(normalize_tier(ctx.get("tier")), None)

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
        "top_picks": top_picks,
        "bond_yields": bond_yields,
        # 신규 데이터
        "issue_delta": issue_count - prev_issue_count,
        "theme_delta": len(themes) - prev_theme_count,
        "buy_delta": buy_count - prev_buy_count,
        "spark_issues": spark_issues,
        "spark_themes": spark_themes,
        "spark_buys": spark_buys,
        "risk_pct": risk_pct,
        "watched_in_today": watched_in_today,
        "theme_view_limit": theme_view_limit,
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
# Stock Fundamentals (종목 기초정보)
# ──────────────────────────────────────────────
@router.get("/pages/stocks/{ticker}")
def stock_fundamentals_page(
    request: Request,
    ticker: str,
    market: str = Query(default="", description="시장 코드"),
    user: Optional[UserInDB] = Depends(get_current_user),
    auth_cfg: AuthConfig = Depends(_get_auth_cfg),
):
    """종목 기초정보 페이지 — 온디맨드 yfinance 조회"""
    ctx = _base_ctx(request, "proposals", user, auth_cfg)
    return templates.TemplateResponse(request=request, name="stock_fundamentals.html", context={
        **ctx,
        "ticker": ticker.upper(),
        "market": market.upper(),
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
    """채팅 세션 목록 — 로그인 필수, Pro 이상 티어 (admin/moderator는 무조건 허용)"""
    if auth_cfg.enabled:
        if user is None:
            return RedirectResponse("/auth/login?next=/pages/chat", status_code=302)
        if user.role not in ("admin", "moderator"):
            daily_limit = get_chat_daily_limit(user.effective_tier())
            if daily_limit is not None and daily_limit <= 0:
                from fastapi import HTTPException
                raise HTTPException(status_code=402, detail="AI 채팅은 Pro 이상 플랜에서 이용 가능합니다.")
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
    """새 채팅 세션 생성 → 채팅방으로 리다이렉트 (Pro 이상 티어)"""
    if auth_cfg.enabled:
        if user is None:
            return RedirectResponse(f"/auth/login?next=/pages/chat/new/{theme_id}", status_code=302)
        if user.role not in ("admin", "moderator"):
            daily_limit = get_chat_daily_limit(user.effective_tier())
            if daily_limit is not None and daily_limit <= 0:
                from fastapi import HTTPException
                raise HTTPException(status_code=402, detail="AI 채팅은 Pro 이상 플랜에서 이용 가능합니다.")
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
    """채팅 대화 화면 — 로그인 필수, Pro 이상 티어, 본인 세션만 (Admin은 전체)"""
    if auth_cfg.enabled:
        if user is None:
            return RedirectResponse(f"/auth/login?next=/pages/chat/{chat_session_id}", status_code=302)
        if user.role not in ("admin", "moderator"):
            daily_limit = get_chat_daily_limit(user.effective_tier())
            if daily_limit is not None and daily_limit <= 0:
                from fastapi import HTTPException
                raise HTTPException(status_code=402, detail="AI 채팅은 Pro 이상 플랜에서 이용 가능합니다.")
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


# ──────────────────────────────────────────────
# Education — 투자 교육
# ──────────────────────────────────────────────

# 카테고리 한글 매핑
_EDU_CATEGORIES = {
    "basics": "기초 개념",
    "analysis": "분석 기법",
    "risk": "리스크 관리",
    "macro": "매크로 경제",
    "practical": "실전 활용",
}


@router.get("/pages/education")
def education_page(request: Request, category: str | None = None, user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    """투자 교육 토픽 목록 페이지"""
    ctx = _base_ctx(request, "education", user, auth_cfg)
    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            query = "SELECT id, category, slug, title, summary, difficulty, sort_order FROM education_topics"
            params = []
            if category:
                query += " WHERE category = %s"
                params.append(category)
            query += " ORDER BY sort_order, id"
            cur.execute(query, params)
            topics = cur.fetchall()
    finally:
        conn.close()

    # 카테고리별 그룹핑
    grouped = {}
    for t in topics:
        cat = t["category"]
        if cat not in grouped:
            grouped[cat] = {"label": _EDU_CATEGORIES.get(cat, cat), "topics": []}
        grouped[cat]["topics"].append(_serialize_row(t))

    return templates.TemplateResponse(request=request, name="education.html", context={
        **ctx,
        "grouped_topics": grouped,
        "selected_category": category,
        "categories": _EDU_CATEGORIES,
    })


@router.get("/pages/education/topic/{slug}")
def education_topic_page(request: Request, slug: str, user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    """교육 토픽 상세 페이지"""
    ctx = _base_ctx(request, "education", user, auth_cfg)
    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM education_topics WHERE slug = %s", (slug,))
            topic = cur.fetchone()
            if not topic:
                return RedirectResponse(url="/pages/education", status_code=302)

            # 이전/다음 토픽 네비게이션
            cur.execute(
                """SELECT slug, title FROM education_topics
                   WHERE category = %s AND sort_order < %s
                   ORDER BY sort_order DESC LIMIT 1""",
                (topic["category"], topic["sort_order"]),
            )
            prev_topic = cur.fetchone()

            cur.execute(
                """SELECT slug, title FROM education_topics
                   WHERE category = %s AND sort_order > %s
                   ORDER BY sort_order ASC LIMIT 1""",
                (topic["category"], topic["sort_order"]),
            )
            next_topic = cur.fetchone()
    finally:
        conn.close()

    # examples가 JSON 문자열이면 파싱
    topic_data = _serialize_row(topic)
    examples = topic_data.get("examples")
    if isinstance(examples, str):
        import json as _json
        try:
            topic_data["examples"] = _json.loads(examples)
        except (ValueError, TypeError):
            topic_data["examples"] = []

    return templates.TemplateResponse(request=request, name="education_topic.html", context={
        **ctx,
        "topic": topic_data,
        "category_label": _EDU_CATEGORIES.get(topic["category"], topic["category"]),
        "prev_topic": _serialize_row(prev_topic) if prev_topic else None,
        "next_topic": _serialize_row(next_topic) if next_topic else None,
    })


@router.get("/pages/education/chat")
def education_chat_list_page(request: Request, user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    """AI 튜터 채팅 목록 페이지"""
    if auth_cfg.enabled and user is None:
        return RedirectResponse("/auth/login?next=/pages/education/chat", status_code=302)

    ctx = _base_ctx(request, "education_chat", user, auth_cfg)
    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 세션 목록
            q = """
                SELECT es.*, et.title AS topic_title, et.category, et.slug AS topic_slug,
                       (SELECT COUNT(*) FROM education_chat_messages m
                        WHERE m.chat_session_id = es.id) AS message_count
                FROM education_chat_sessions es
                LEFT JOIN education_topics et ON es.topic_id = et.id
            """
            params = []
            if user and user.role != "admin":
                q += " WHERE es.user_id = %s"
                params.append(user.id)
            q += " ORDER BY es.updated_at DESC"
            cur.execute(q, params)
            sessions = cur.fetchall()

            # 토픽 목록 (새 채팅 생성용)
            cur.execute("SELECT id, title, category FROM education_topics ORDER BY sort_order, id")
            topics = cur.fetchall()
    finally:
        conn.close()

    return templates.TemplateResponse(request=request, name="education_chat_list.html", context={
        **ctx,
        "chat_sessions": [_serialize_row(s) for s in sessions],
        "topics": [_serialize_row(t) for t in topics],
    })


@router.get("/pages/education/chat/new/{topic_id}")
def education_chat_new_redirect(request: Request, topic_id: int, user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    """AI 튜터 새 채팅 세션 생성 → 채팅방으로 리다이렉트"""
    if auth_cfg.enabled and user is None:
        return RedirectResponse(f"/auth/login?next=/pages/education/chat/new/{topic_id}", status_code=302)
    cfg = _get_cfg()
    conn = get_connection(cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, title FROM education_topics WHERE id = %s", (topic_id,))
            topic = cur.fetchone()
            if not topic:
                return RedirectResponse(url="/pages/education/chat", status_code=302)

            user_id = user.id if user else None
            cur.execute(
                """INSERT INTO education_chat_sessions (topic_id, title, user_id)
                   VALUES (%s, %s, %s) RETURNING id""",
                (topic_id, f"{topic['title']} 학습", user_id)
            )
            new_id = cur.fetchone()["id"]
        conn.commit()
    finally:
        conn.close()

    return RedirectResponse(url=f"/pages/education/chat/{new_id}", status_code=302)


@router.get("/pages/education/chat/{session_id}")
def education_chat_room_page(request: Request, session_id: int, user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    """AI 튜터 채팅방 페이지"""
    if auth_cfg.enabled and user is None:
        return RedirectResponse(f"/auth/login?next=/pages/education/chat/{session_id}", status_code=302)

    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT es.*, et.title AS topic_title, et.category, et.slug AS topic_slug,
                       et.difficulty, et.summary AS topic_summary
                FROM education_chat_sessions es
                LEFT JOIN education_topics et ON es.topic_id = et.id
                WHERE es.id = %s
            """, (session_id,))
            session = cur.fetchone()
            if not session:
                return RedirectResponse(url="/pages/education/chat", status_code=302)

            # 소유권 검증
            if auth_cfg.enabled and user and user.role != "admin" and session.get("user_id") != user.id:
                return RedirectResponse(url="/pages/education/chat", status_code=302)

            cur.execute("""
                SELECT id, role, content, created_at
                FROM education_chat_messages
                WHERE chat_session_id = %s
                ORDER BY created_at
            """, (session_id,))
            messages = cur.fetchall()
    finally:
        conn.close()

    ctx = _base_ctx(request, "education_chat", user, auth_cfg)
    return templates.TemplateResponse(request=request, name="education_chat_room.html", context={
        **ctx,
        "session": _serialize_row(session),
        "messages": [_serialize_row(m) for m in messages],
        "category_label": _EDU_CATEGORIES.get(session.get("category", ""), ""),
    })


# ──────────────────────────────────────────────
# Track Record & Pricing — 공개 페이지
# ──────────────────────────────────────────────
@router.get("/pages/track-record")
def track_record_page(request: Request, user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    """트랙레코드 공개 페이지 — 비로그인도 접근 가능."""
    ctx = _base_ctx(request, "track_record", user, auth_cfg)
    # 클라이언트에서 /api/track-record/summary fetch하여 렌더
    return templates.TemplateResponse(request=request, name="track_record.html", context=ctx)


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
