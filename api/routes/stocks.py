"""종목 기초정보 조회 API + 종목 페이지"""
from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg2.extras import RealDictCursor

from analyzer.stock_data import fetch_fundamentals
from api.auth.dependencies import get_current_user_required
from api.auth.models import UserInDB
from api.templates_provider import templates
from api.deps import make_page_ctx
from shared.config import AppConfig
from shared.db import get_connection

router = APIRouter(prefix="/api/stocks", tags=["종목 기초정보"])

pages_router = APIRouter(prefix="/pages/stocks", tags=["종목 페이지"])

indices_router = APIRouter(prefix="/api/indices", tags=["벤치마크 지수"])


@router.get("/{ticker}/fundamentals")
def get_fundamentals(
    ticker: str,
    market: str = Query(default="", description="시장 코드 (KRX, NASDAQ 등)"),
):
    """종목 기초정보 온디맨드 조회 (yfinance 기반, 1시간 캐싱)"""
    data = fetch_fundamentals(ticker, market)
    if not data:
        raise HTTPException(
            status_code=404,
            detail=f"종목 '{ticker}' 데이터를 조회할 수 없습니다",
        )
    return data


@router.get("/{ticker}/ohlcv")
def get_stock_ohlcv(
    ticker: str,
    market: str = Query(default="", description="시장 코드 (KOSPI/KOSDAQ/NASDAQ/NYSE 등, 빈 값이면 무시)"),
    days: int = Query(default=200, ge=1, le=1000, description="최근 N일 (최대 1000)"),
):
    """종목 OHLCV 이력 조회 — stock_universe_ohlcv 기반.

    UI-1 미니 스파크라인·52주 게이지·거래량 추이 등에 사용.
    OHLCV가 아직 수집되지 않은 신규 종목은 404 대신 빈 배열 반환 (차트는 "데이터 없음" 표시).
    """
    tk = ticker.strip().upper()
    mk = (market or "").strip().upper()
    cfg = AppConfig()

    # market 옵션 따라 WHERE 분기
    if mk:
        sql = """
            SELECT trade_date::text, open::float, high::float, low::float,
                   close::float, volume, change_pct::float
            FROM stock_universe_ohlcv
            WHERE UPPER(ticker) = %s AND UPPER(market) = %s
              AND trade_date >= CURRENT_DATE - (%s::int)
            ORDER BY trade_date ASC
        """
        params = (tk, mk, int(days))
    else:
        sql = """
            SELECT trade_date::text, open::float, high::float, low::float,
                   close::float, volume, change_pct::float
            FROM stock_universe_ohlcv
            WHERE UPPER(ticker) = %s
              AND trade_date >= CURRENT_DATE - (%s::int)
            ORDER BY trade_date ASC
        """
        params = (tk, int(days))

    conn = get_connection(cfg.db)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    finally:
        conn.close()

    series = [
        {
            "date": r[0], "open": r[1], "high": r[2], "low": r[3],
            "close": r[4], "volume": r[5], "change_pct": r[6],
        }
        for r in rows
    ]

    # 52주 고/저 (있으면 함께 반환 — UI 게이지용)
    if series:
        closes = [p["close"] for p in series[-252:] if p["close"] is not None]
        high_52w = max(closes) if closes else None
        low_52w = min(closes) if closes else None
        latest = series[-1]
    else:
        high_52w = low_52w = None
        latest = None

    return {
        "ticker": tk,
        "market": mk or None,
        "days": days,
        "count": len(series),
        "high_52w": high_52w,
        "low_52w": low_52w,
        "latest": latest,
        "series": series,
    }


# ──────────────────────────────────────────────
# 벤치마크 지수 OHLCV (B2 market_indices_ohlcv)
# ──────────────────────────────────────────────
_ALLOWED_INDICES = ("KOSPI", "KOSDAQ", "SP500", "NDX100")


@indices_router.get("/{index_code}/ohlcv")
def get_index_ohlcv(
    index_code: str,
    days: int = Query(default=252, ge=1, le=1000, description="최근 N일 (최대 1000)"),
):
    """벤치마크 지수 OHLCV 이력.

    index_code: KOSPI / KOSDAQ / SP500 / NDX100
    UI-2 테마 상대성과·UI-3 대시보드 시장 추이 등에 사용.
    """
    code = index_code.strip().upper()
    if code not in _ALLOWED_INDICES:
        raise HTTPException(
            status_code=400,
            detail=f"index_code는 {_ALLOWED_INDICES} 중 하나여야 합니다 (받음: {code})",
        )

    cfg = AppConfig()
    sql = """
        SELECT trade_date::text, open::float, high::float, low::float,
               close::float, volume, change_pct::float
        FROM market_indices_ohlcv
        WHERE index_code = %s
          AND trade_date >= CURRENT_DATE - (%s::int)
        ORDER BY trade_date ASC
    """
    conn = get_connection(cfg.db)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (code, int(days)))
            rows = cur.fetchall()
    finally:
        conn.close()

    series = [
        {
            "date": r[0], "open": r[1], "high": r[2], "low": r[3],
            "close": r[4], "volume": r[5], "change_pct": r[6],
        }
        for r in rows
    ]
    latest = series[-1] if series else None
    return {
        "index_code": code,
        "days": days,
        "count": len(series),
        "latest": latest,
        "series": series,
    }


# ──────────────────────────────────────────────
# Stock Cockpit — Hero overview API
# ──────────────────────────────────────────────
_AI_SCORE_WEIGHTS = {"factor": 0.5, "hist": 0.3, "consensus": 0.2}

_CONSENSUS_MAP = {
    "STRONG_BUY": 1.0, "BUY": 0.75, "HOLD": 0.5,
    "SELL": 0.25, "STRONG_SELL": 0.0,
}


def _clamp(v, lo=0.0, hi=1.0):
    """v를 [lo, hi] 범위로 제한."""
    return max(lo, min(hi, v))


def _compute_ai_score(factor_snapshot, avg_post_return_3m, consensus):
    """AI 종합 점수 0~100. 컴포넌트 누락 시 0.5 중립값."""
    if factor_snapshot:
        pctiles = [
            factor_snapshot.get(k)
            for k in ("r1m_pctile", "r3m_pctile", "r6m_pctile", "r12m_pctile")
            if factor_snapshot.get(k) is not None
        ]
        factor_score = sum(pctiles) / len(pctiles) if pctiles else 0.5
    else:
        factor_score = 0.5

    if avg_post_return_3m is None:
        hist_score = 0.5
    else:
        hist_score = _clamp(float(avg_post_return_3m) / 30.0)

    consensus_score = _CONSENSUS_MAP.get(
        (consensus or "").upper(), 0.5
    )

    score = (
        _AI_SCORE_WEIGHTS["factor"] * factor_score
        + _AI_SCORE_WEIGHTS["hist"] * hist_score
        + _AI_SCORE_WEIGHTS["consensus"] * consensus_score
    )
    return {
        "ai_score": round(score * 100),
        "factor_score": round(factor_score, 4),
        "hist_score": round(hist_score, 4),
        "consensus_score": round(consensus_score, 4),
    }


@router.get("/{ticker}/overview")
def get_stock_overview(
    ticker: str,
    market: str = Query(default="", description="시장 코드"),
):
    """Cockpit Hero 종합 응답 — 메타 + 최신가 + 추천 통계 + AI 종합 점수."""
    cfg = AppConfig()
    tk = ticker.strip().upper()
    mk = (market or "").strip().upper()

    conn = get_connection(cfg.db)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 1) 종목 메타 — stock_universe 우선
            if mk:
                cur.execute("""
                    SELECT name, sector, industry, currency, market
                    FROM stock_universe
                    WHERE UPPER(ticker) = %s AND UPPER(market) = %s
                    LIMIT 1
                """, (tk, mk))
            else:
                cur.execute("""
                    SELECT name, sector, industry, currency, market
                    FROM stock_universe
                    WHERE UPPER(ticker) = %s
                    ORDER BY (CASE WHEN listing_status='active' THEN 0 ELSE 1 END)
                    LIMIT 1
                """, (tk,))
            meta = cur.fetchone() or {}

            # 2) 최신 2 거래일 종가 — 변동률 계산용
            cur.execute("""
                SELECT trade_date, close, volume
                FROM stock_universe_ohlcv
                WHERE UPPER(ticker) = %s
                  AND (%s = '' OR UPPER(market) = %s)
                ORDER BY trade_date DESC
                LIMIT 2
            """, (tk, mk, mk))
            latest_rows = cur.fetchall()

            # 3) 추천 통계 — 같은 ticker 모든 proposals 집계
            cur.execute("""
                SELECT
                    COUNT(*) AS proposal_count,
                    AVG(post_return_3m_pct) AS avg_post_return_3m_pct,
                    AVG(alpha_vs_benchmark_pct) AS avg_alpha_vs_benchmark_pct,
                    (
                        SELECT analyst_recommendation
                        FROM investment_proposals
                        WHERE UPPER(ticker) = %s
                          AND analyst_recommendation IS NOT NULL
                        ORDER BY created_at DESC LIMIT 1
                    ) AS latest_consensus
                FROM investment_proposals
                WHERE UPPER(ticker) = %s
            """, (tk, tk))
            stats = cur.fetchone() or {}

            # 4) 최신 factor_snapshot — 가장 최근 추천에서
            cur.execute("""
                SELECT factor_snapshot
                FROM investment_proposals
                WHERE UPPER(ticker) = %s
                  AND factor_snapshot IS NOT NULL
                ORDER BY created_at DESC LIMIT 1
            """, (tk,))
            factor_row = cur.fetchone() or {}
    finally:
        conn.close()

    # 최신가 + 변동률
    latest = None
    if latest_rows:
        last = latest_rows[0]
        prev = latest_rows[1] if len(latest_rows) >= 2 else None
        change_pct = None
        if prev and prev.get("close") and float(prev["close"]) > 0:
            change_pct = round(
                (float(last["close"]) - float(prev["close"])) / float(prev["close"]) * 100,
                2,
            )
        latest = {
            "date": last["trade_date"].isoformat(),
            "close": float(last["close"]) if last.get("close") is not None else None,
            "change_pct": change_pct,
            "volume": int(last["volume"]) if last.get("volume") is not None else None,
            "source": "ohlcv_db",
        }

    score = _compute_ai_score(
        factor_row.get("factor_snapshot"),
        stats.get("avg_post_return_3m_pct"),
        stats.get("latest_consensus"),
    )

    return {
        "ticker": tk,
        "market": meta.get("market") or mk or None,
        "name": meta.get("name") or tk,
        "sector": meta.get("sector"),
        "industry": meta.get("industry"),
        "currency": meta.get("currency"),
        "latest": latest,
        "stats": {
            "ai_score": score["ai_score"],
            "proposal_count": int(stats.get("proposal_count") or 0),
            "avg_post_return_3m_pct": (
                round(float(stats["avg_post_return_3m_pct"]), 2)
                if stats.get("avg_post_return_3m_pct") is not None else None
            ),
            "alpha_vs_benchmark_pct": (
                round(float(stats["avg_alpha_vs_benchmark_pct"]), 2)
                if stats.get("avg_alpha_vs_benchmark_pct") is not None else None
            ),
            "factor_pctile_avg": (
                round(score["factor_score"], 4) if factor_row.get("factor_snapshot") else None
            ),
        },
        "score_breakdown": {
            "factor_score": score["factor_score"],
            "hist_score": score["hist_score"],
            "consensus_score": score["consensus_score"],
            "weights": dict(_AI_SCORE_WEIGHTS),
        },
    }


# ──────────────────────────────────────────────
# Stock Cockpit — 추천 이력 타임라인 API
# ──────────────────────────────────────────────
@router.get("/{ticker}/proposals")
def get_stock_proposals(ticker: str):
    """이 종목의 모든 investment_proposals 시계열 + validation_log 조인."""
    cfg = AppConfig()
    tk = ticker.strip().upper()

    conn = get_connection(cfg.db)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    p.id, s.analysis_date, p.created_at,
                    t.id AS theme_id, t.theme_name, t.theme_validity,
                    p.action, p.conviction, p.discovery_type,
                    p.rationale, p.entry_price,
                    p.target_price_low, p.target_price_high,
                    p.post_return_1m_pct, p.post_return_3m_pct,
                    p.post_return_6m_pct, p.post_return_1y_pct,
                    p.max_drawdown_pct, p.max_drawdown_date,
                    p.alpha_vs_benchmark_pct
                FROM investment_proposals p
                JOIN investment_themes t ON p.theme_id = t.id
                JOIN analysis_sessions s ON t.session_id = s.id
                WHERE UPPER(p.ticker) = %s
                ORDER BY s.analysis_date DESC, p.id DESC
            """, (tk,))
            prop_rows = cur.fetchall()

            if prop_rows:
                proposal_ids = [r["id"] for r in prop_rows]
                cur.execute("""
                    SELECT proposal_id, field_name, mismatch_pct, mismatch
                    FROM proposal_validation_log
                    WHERE proposal_id = ANY(%s) AND mismatch = TRUE
                """, (proposal_ids,))
                validation_rows = cur.fetchall()
            else:
                validation_rows = []
    finally:
        conn.close()

    # 검증 mismatch 를 proposal_id 별로 그룹화
    mismatches_by_pid = {}
    for vr in validation_rows:
        pid = vr["proposal_id"]
        mismatches_by_pid.setdefault(pid, []).append({
            "field_name": vr["field_name"],
            "mismatch_pct": (
                round(float(vr["mismatch_pct"]), 2)
                if vr.get("mismatch_pct") is not None else None
            ),
        })

    def _f(v):
        return float(v) if v is not None else None

    def _d(v):
        return v.isoformat() if v is not None else None

    items = []
    for r in prop_rows:
        items.append({
            "proposal_id": r["id"],
            "analysis_date": _d(r["analysis_date"]),
            "created_at": _d(r["created_at"]),
            "theme_id": r["theme_id"],
            "theme_name": r["theme_name"],
            "theme_validity": r["theme_validity"],
            "action": r["action"],
            "conviction": r["conviction"],
            "discovery_type": r["discovery_type"],
            "rationale": r["rationale"],
            "entry_price": _f(r["entry_price"]),
            "target_price_low": _f(r["target_price_low"]),
            "target_price_high": _f(r["target_price_high"]),
            "post_return_1m_pct": _f(r["post_return_1m_pct"]),
            "post_return_3m_pct": _f(r["post_return_3m_pct"]),
            "post_return_6m_pct": _f(r["post_return_6m_pct"]),
            "post_return_1y_pct": _f(r["post_return_1y_pct"]),
            "max_drawdown_pct": _f(r["max_drawdown_pct"]),
            "max_drawdown_date": _d(r["max_drawdown_date"]),
            "alpha_vs_benchmark_pct": _f(r["alpha_vs_benchmark_pct"]),
            "validation_mismatches": mismatches_by_pid.get(r["id"], []),
        })

    return {"ticker": tk, "count": len(items), "items": items}


# ──────────────────────────────────────────────
# Stock Fundamentals Page (종목 기초정보 페이지)
# ──────────────────────────────────────────────
@pages_router.get("/{ticker}")
def stock_fundamentals_page(
    ticker: str,
    market: str = Query(default="", description="시장 코드"),
    ctx: dict = Depends(make_page_ctx("proposals")),
):
    """Stock Cockpit — 종합 종목 페이지 (in-place 교체)."""
    return templates.TemplateResponse(request=ctx["request"], name="stock_cockpit.html", context={
        **ctx,
        "ticker": ticker.upper(),
        "market": market.upper(),
    })
