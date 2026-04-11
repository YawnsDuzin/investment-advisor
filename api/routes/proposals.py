"""투자 제안 조회 API"""
from fastapi import APIRouter, HTTPException, Query
from shared.config import DatabaseConfig
from shared.db import get_connection
from psycopg2.extras import RealDictCursor
from api.routes.sessions import _serialize_row

router = APIRouter(prefix="/proposals", tags=["투자 제안"])


def _get_cfg() -> DatabaseConfig:
    return DatabaseConfig()


@router.get("")
def list_proposals(
    limit: int = Query(default=30, ge=1, le=100),
    action: str | None = Query(default=None, description="buy|sell|hold|watch"),
    asset_type: str | None = Query(default=None, description="stock|etf|commodity|currency|bond|crypto"),
    conviction: str | None = Query(default=None, description="high|medium|low"),
    sector: str | None = Query(default=None, description="섹터 필터"),
):
    """투자 제안 목록 (최신순, 필터 가능)"""
    conn = get_connection(_get_cfg())
    try:
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

            query += " ORDER BY s.analysis_date DESC, p.target_allocation DESC LIMIT %s"
            params.append(limit)

            cur.execute(query, params)
            rows = cur.fetchall()
    finally:
        conn.close()

    return [_serialize_row(r) for r in rows]


@router.get("/ticker/{ticker}")
def get_by_ticker(
    ticker: str,
    limit: int = Query(default=10, ge=1, le=50),
):
    """특정 티커의 투자 제안 이력"""
    conn = get_connection(_get_cfg())
    try:
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
    finally:
        conn.close()

    return [_serialize_row(r) for r in rows]


@router.get("/summary/latest")
def latest_portfolio_summary():
    """최신 분석의 포트폴리오 요약 (buy 제안만, 비중순)"""
    conn = get_connection(_get_cfg())
    try:
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
    finally:
        conn.close()

    total_alloc = sum(float(p.get("target_allocation") or 0) for p in proposals)

    return {
        "analysis_date": session["analysis_date"].isoformat(),
        "market_summary": session["market_summary"],
        "risk_temperature": session.get("risk_temperature"),
        "total_allocation": total_alloc,
        "buy_proposals": [_serialize_row(p) for p in proposals],
    }


@router.get("/{proposal_id}/stock-analysis")
def get_stock_analysis(proposal_id: int):
    """투자 제안에 대한 종목 심층분석 조회"""
    conn = get_connection(_get_cfg())
    try:
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
    finally:
        conn.close()

    return _serialize_row(row)
