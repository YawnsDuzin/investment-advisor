"""투자 제안 조회 API"""
from datetime import date
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Request, Depends
from fastapi.responses import HTMLResponse
from shared.config import AuthConfig
from psycopg2.extras import RealDictCursor
from api.serialization import serialize_row as _serialize_row
from api.page_context import base_ctx as _base_ctx
from api.templates_provider import templates
from api.deps import get_db_conn
from api.auth.dependencies import get_current_user, get_current_user_required, _get_auth_cfg
from api.auth.models import UserInDB

router = APIRouter(prefix="/proposals", tags=["투자 제안"])
api_router = APIRouter(prefix="/api/proposals", tags=["투자 제안 (API)"])
pages_router = APIRouter(prefix="/pages/proposals", tags=["투자 제안 페이지"])


@router.get("")
def list_proposals(
    conn=Depends(get_db_conn),
    limit: int = Query(default=30, ge=1, le=100),
    action: str | None = Query(default=None, description="buy|sell|hold|watch"),
    asset_type: str | None = Query(default=None, description="stock|etf|commodity|currency|bond|crypto"),
    conviction: str | None = Query(default=None, description="high|medium|low"),
    sector: str | None = Query(default=None, description="섹터 필터"),
    date_from: str | None = Query(default=None, description="조회 시작일 (YYYY-MM-DD)"),
    date_to: str | None = Query(default=None, description="조회 종료일 (YYYY-MM-DD)"),
    market: str | None = Query(default=None, description="시장 (KRX, NASDAQ 등)"),
    discovery_type: str | None = Query(default=None, description="발굴유형"),
    time_horizon: str | None = Query(default=None, description="투자기간"),
    ticker: str | None = Query(default=None, description="티커 검색"),
    sort: str | None = Query(default=None, description="정렬: date|upside|quant|allocation|conviction_sort"),
    _user: Optional[UserInDB] = Depends(get_current_user_required),
):
    """투자 제안 목록 (최신순, 필터 가능)"""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        query = """
            SELECT p.*, t.theme_name, t.time_horizon, t.theme_type,
                   t.theme_validity, s.analysis_date
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
        if sector:
            query += " AND p.sector ILIKE %s"
            params.append(f"%{sector}%")
        if date_from:
            query += " AND s.analysis_date >= %s"
            params.append(date_from)
        if date_to:
            query += " AND s.analysis_date <= %s"
            params.append(date_to)
        if market:
            query += " AND UPPER(p.market) = UPPER(%s)"
            params.append(market)
        if discovery_type:
            query += " AND p.discovery_type = %s"
            params.append(discovery_type)
        if time_horizon:
            query += " AND t.time_horizon = %s"
            params.append(time_horizon)
        if ticker:
            query += " AND UPPER(p.ticker) = UPPER(%s)"
            params.append(ticker)

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
        rows = cur.fetchall()

    return [_serialize_row(r) for r in rows]


@router.get("/ticker/{ticker}")
def get_by_ticker(
    ticker: str,
    conn=Depends(get_db_conn),
    limit: int = Query(default=10, ge=1, le=50),
    _user: Optional[UserInDB] = Depends(get_current_user_required),
):
    """특정 티커의 투자 제안 이력"""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT p.*, t.theme_name, t.confidence_score, t.time_horizon,
                   t.theme_type, t.theme_validity, s.analysis_date
            FROM investment_proposals p
            JOIN investment_themes t ON p.theme_id = t.id
            JOIN analysis_sessions s ON t.session_id = s.id
            WHERE UPPER(p.ticker) = UPPER(%s)
            ORDER BY s.analysis_date DESC
            LIMIT %s
        """, (ticker, limit))
        rows = cur.fetchall()

    return [_serialize_row(r) for r in rows]


@router.get("/summary/latest")
def latest_portfolio_summary(conn=Depends(get_db_conn), _user: Optional[UserInDB] = Depends(get_current_user_required)):
    """최신 분석의 포트폴리오 요약 (buy 제안만, 비중순)"""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # 최신 세션 ID
        cur.execute("""
            SELECT id, analysis_date, market_summary, risk_temperature
            FROM analysis_sessions ORDER BY analysis_date DESC LIMIT 1
        """)
        session = cur.fetchone()
        if not session:
            return {"message": "분석 데이터 없음"}

        cur.execute("""
            SELECT p.asset_name, p.ticker, p.market, p.asset_type,
                   p.conviction, p.target_allocation, p.rationale,
                   p.entry_condition, p.exit_condition,
                   p.current_price, p.target_price_low, p.target_price_high,
                   p.upside_pct, p.sentiment_score, p.quant_score,
                   p.sector, p.currency,
                   t.theme_name, t.confidence_score, t.theme_validity
            FROM investment_proposals p
            JOIN investment_themes t ON p.theme_id = t.id
            WHERE t.session_id = %s AND p.action = 'buy'
            ORDER BY p.target_allocation DESC
        """, (session["id"],))
        proposals = cur.fetchall()

    total_alloc = sum(float(p.get("target_allocation") or 0) for p in proposals)

    return {
        "analysis_date": session["analysis_date"].isoformat(),
        "market_summary": session["market_summary"],
        "risk_temperature": session.get("risk_temperature"),
        "total_allocation": total_alloc,
        "buy_proposals": [_serialize_row(p) for p in proposals],
    }


@api_router.get("/{proposal_id}/stock-analysis")
def get_stock_analysis(proposal_id: int, conn=Depends(get_db_conn), _user: Optional[UserInDB] = Depends(get_current_user_required)):
    """투자 제안에 대한 종목 심층분석 조회 (JSON)"""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM stock_analyses WHERE proposal_id = %s",
            (proposal_id,)
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(
                status_code=404,
                detail="해당 제안에 대한 종목 심층분석이 없습니다"
            )

    return _serialize_row(row)


# ──────────────────────────────────────────────
# Pages routes
# ──────────────────────────────────────────────

@router.get("/{proposal_id}/stock-analysis", response_class=HTMLResponse)
def stock_analysis_page(
    request: Request,
    proposal_id: int,
    conn=Depends(get_db_conn),
    user: Optional[UserInDB] = Depends(get_current_user),
    auth_cfg: AuthConfig = Depends(_get_auth_cfg),
):
    """투자 제안 종목의 심층분석 리포트 페이지"""
    ctx = _base_ctx(request, "proposals", user, auth_cfg)
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

    return templates.TemplateResponse(request=request, name="stock_analysis.html", context={
        **ctx,
        "proposal_id": proposal_id,
        "analysis": _serialize_row(row) if row else None,
    })


@pages_router.get("/history/{ticker}")
def ticker_history_page(request: Request, ticker: str, conn=Depends(get_db_conn), user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    """특정 종목의 일자별 추천 이력"""
    ctx = _base_ctx(request, "proposals", user, auth_cfg)
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


@pages_router.get("")
def proposals_page(
    request: Request,
    conn=Depends(get_db_conn),
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
