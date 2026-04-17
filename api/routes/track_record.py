"""트랙레코드 집계 API

*참고 지표* — 본 API가 반환하는 `return_*_pct`는 `analyzer/stock_data.py`에서
추천일 시점에 계산된 "추천 당일 기준의 과거 N개월 모멘텀"이며,
"추천 이후 N개월 수익률"이 아님에 주의.
추천 이후 성과 측정은 별도 스냅샷 로직이 필요(향후 과제).
"""
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends
from shared.config import DatabaseConfig
from shared.db import get_connection
from psycopg2.extras import RealDictCursor

router = APIRouter(prefix="/api/track-record", tags=["트랙레코드"])


def _get_cfg() -> DatabaseConfig:
    return DatabaseConfig()


def _float(value):
    """Decimal / None 안전 변환."""
    if value is None:
        return None
    return float(value)


@router.get("/summary")
def get_track_record_summary(cfg: DatabaseConfig = Depends(_get_cfg)):
    """전체·분류별 승률과 평균 수익률 집계.

    비로그인도 접근 가능한 공개 엔드포인트.
    """
    conn = get_connection(cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 1) 전체 개요 (각 기간별 승률 · 평균 수익률)
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE return_1m_pct IS NOT NULL) AS n_1m,
                    COUNT(*) FILTER (WHERE return_1m_pct > 0)         AS win_1m,
                    ROUND(AVG(return_1m_pct) FILTER (WHERE return_1m_pct IS NOT NULL)::numeric, 2) AS avg_1m,

                    COUNT(*) FILTER (WHERE return_3m_pct IS NOT NULL) AS n_3m,
                    COUNT(*) FILTER (WHERE return_3m_pct > 0)         AS win_3m,
                    ROUND(AVG(return_3m_pct) FILTER (WHERE return_3m_pct IS NOT NULL)::numeric, 2) AS avg_3m,

                    COUNT(*) FILTER (WHERE return_6m_pct IS NOT NULL) AS n_6m,
                    COUNT(*) FILTER (WHERE return_6m_pct > 0)         AS win_6m,
                    ROUND(AVG(return_6m_pct) FILTER (WHERE return_6m_pct IS NOT NULL)::numeric, 2) AS avg_6m,

                    COUNT(*) FILTER (WHERE return_1y_pct IS NOT NULL) AS n_1y,
                    COUNT(*) FILTER (WHERE return_1y_pct > 0)         AS win_1y,
                    ROUND(AVG(return_1y_pct) FILTER (WHERE return_1y_pct IS NOT NULL)::numeric, 2) AS avg_1y,

                    COUNT(*) AS total_proposals
                FROM investment_proposals
                WHERE action = 'buy'
                  AND current_price IS NOT NULL
            """)
            overview = cur.fetchone() or {}

            def _win_rate(win, n):
                if not n:
                    return None
                return round(float(win) / float(n) * 100, 1)

            periods = {}
            for p in ("1m", "3m", "6m", "1y"):
                n = overview.get(f"n_{p}") or 0
                win = overview.get(f"win_{p}") or 0
                periods[p] = {
                    "n": int(n),
                    "wins": int(win),
                    "win_rate_pct": _win_rate(win, n),
                    "avg_return_pct": _float(overview.get(f"avg_{p}")),
                }

            # 2) 분류별 (discovery_type) — 대소문자 혼입 방어를 위해 LOWER() 정규화
            cur.execute("""
                SELECT
                    LOWER(COALESCE(discovery_type, 'unknown')) AS discovery_type,
                    COUNT(*) FILTER (WHERE return_1m_pct IS NOT NULL) AS n,
                    COUNT(*) FILTER (WHERE return_1m_pct > 0)         AS wins,
                    ROUND(AVG(return_1m_pct) FILTER (WHERE return_1m_pct IS NOT NULL)::numeric, 2) AS avg_1m,
                    ROUND(AVG(return_3m_pct) FILTER (WHERE return_3m_pct IS NOT NULL)::numeric, 2) AS avg_3m,
                    ROUND(AVG(return_1y_pct) FILTER (WHERE return_1y_pct IS NOT NULL)::numeric, 2) AS avg_1y
                FROM investment_proposals
                WHERE action = 'buy' AND current_price IS NOT NULL
                GROUP BY LOWER(COALESCE(discovery_type, 'unknown'))
                ORDER BY n DESC NULLS LAST
            """)
            by_type = []
            for r in cur.fetchall():
                n = int(r.get("n") or 0)
                wins = int(r.get("wins") or 0)
                by_type.append({
                    "discovery_type": r["discovery_type"],
                    "n": n,
                    "wins": wins,
                    "win_rate_pct": _win_rate(wins, n),
                    "avg_return_1m_pct": _float(r.get("avg_1m")),
                    "avg_return_3m_pct": _float(r.get("avg_3m")),
                    "avg_return_1y_pct": _float(r.get("avg_1y")),
                })

            # 3) 최근 30일 Top Picks (daily_top_picks 기반)
            cur.execute("""
                SELECT
                    dtp.analysis_date,
                    dtp.rank,
                    dtp.score_final,
                    dtp.rationale_text,
                    dtp.source,
                    p.ticker,
                    p.asset_name,
                    p.discovery_type,
                    p.conviction,
                    p.current_price,
                    p.upside_pct,
                    p.return_1m_pct,
                    p.return_3m_pct,
                    t.theme_name
                FROM daily_top_picks dtp
                JOIN investment_proposals p ON dtp.proposal_id = p.id
                LEFT JOIN investment_themes t ON p.theme_id = t.id
                WHERE dtp.analysis_date >= CURRENT_DATE - INTERVAL '30 days'
                ORDER BY dtp.analysis_date DESC, dtp.rank ASC
                LIMIT 20
            """)
            top_picks = []
            for r in cur.fetchall():
                top_picks.append({
                    "analysis_date": r["analysis_date"].isoformat() if r.get("analysis_date") else None,
                    "rank": r.get("rank"),
                    "score_final": _float(r.get("score_final")),
                    "ticker": r.get("ticker"),
                    "asset_name": r.get("asset_name"),
                    "discovery_type": r.get("discovery_type"),
                    "conviction": r.get("conviction"),
                    "theme_name": r.get("theme_name"),
                    "current_price": _float(r.get("current_price")),
                    "upside_pct": _float(r.get("upside_pct")),
                    "return_1m_pct": _float(r.get("return_1m_pct")),
                    "return_3m_pct": _float(r.get("return_3m_pct")),
                    "rationale_text": r.get("rationale_text"),
                    "source": r.get("source"),
                })

            # 4) 메타 (가장 이르고 최신인 분석 날짜)
            cur.execute("""
                SELECT
                    MIN(analysis_date) AS earliest,
                    MAX(analysis_date) AS latest
                FROM analysis_sessions
            """)
            meta_row = cur.fetchone() or {}

    finally:
        conn.close()

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": (
            "수익률은 '추천 당일 기준의 과거 N개월 모멘텀'입니다. "
            "추천 이후 실제 성과와 다를 수 있으며, 'action=buy' 제안만 집계합니다. "
            "0% 수익률은 패로 집계되며, 본 정보는 투자 권유가 아닙니다."
        ),
        "overview": {
            "total_proposals": int(overview.get("total_proposals") or 0),
            "periods": periods,
        },
        "by_discovery_type": by_type,
        "recent_top_picks": top_picks,
        "meta": {
            "earliest_date": meta_row.get("earliest").isoformat() if meta_row.get("earliest") else None,
            "latest_date": meta_row.get("latest").isoformat() if meta_row.get("latest") else None,
        },
    }
