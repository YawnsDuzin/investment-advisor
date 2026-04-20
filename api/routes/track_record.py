"""트랙레코드 집계 API — 추천 후 실제 수익률 기반

v19부터 `post_return_*_pct` (추천 후 실제 수익률)를 메인 성과 지표로 사용.
기존 `return_*_pct` (과거 모멘텀)는 참고 지표로 별도 제공.
데이터가 아직 축적되지 않은 기간은 "측정 중"으로 표시.
"""
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, Request
from shared.config import DatabaseConfig, AuthConfig
from shared.db import get_connection
from psycopg2.extras import RealDictCursor
from api.page_context import base_ctx as _base_ctx
from api.templates_provider import templates
from api.deps import get_db_cfg as _get_cfg
from api.auth.dependencies import get_current_user, _get_auth_cfg
from api.auth.models import UserInDB

router = APIRouter(prefix="/api/track-record", tags=["트랙레코드"])
pages_router = APIRouter(prefix="/pages/track-record", tags=["트랙레코드 페이지"])


def _float(value):
    """Decimal / None 안전 변환."""
    if value is None:
        return None
    return float(value)


def _win_rate(win, n):
    if not n:
        return None
    return round(float(win) / float(n) * 100, 1)


@router.get("/summary")
def get_track_record_summary(cfg: DatabaseConfig = Depends(_get_cfg)):
    """전체·분류별 추천 후 실제 수익률 집계.

    비로그인도 접근 가능한 공개 엔드포인트.
    """
    conn = get_connection(cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # ── 1) 추천 후 실제 수익률 (post_return) ──
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE post_return_1m_pct IS NOT NULL) AS n_1m,
                    COUNT(*) FILTER (WHERE post_return_1m_pct > 0)         AS win_1m,
                    ROUND(AVG(post_return_1m_pct) FILTER (WHERE post_return_1m_pct IS NOT NULL)::numeric, 2) AS avg_1m,

                    COUNT(*) FILTER (WHERE post_return_3m_pct IS NOT NULL) AS n_3m,
                    COUNT(*) FILTER (WHERE post_return_3m_pct > 0)         AS win_3m,
                    ROUND(AVG(post_return_3m_pct) FILTER (WHERE post_return_3m_pct IS NOT NULL)::numeric, 2) AS avg_3m,

                    COUNT(*) FILTER (WHERE post_return_6m_pct IS NOT NULL) AS n_6m,
                    COUNT(*) FILTER (WHERE post_return_6m_pct > 0)         AS win_6m,
                    ROUND(AVG(post_return_6m_pct) FILTER (WHERE post_return_6m_pct IS NOT NULL)::numeric, 2) AS avg_6m,

                    COUNT(*) FILTER (WHERE post_return_1y_pct IS NOT NULL) AS n_1y,
                    COUNT(*) FILTER (WHERE post_return_1y_pct > 0)         AS win_1y,
                    ROUND(AVG(post_return_1y_pct) FILTER (WHERE post_return_1y_pct IS NOT NULL)::numeric, 2) AS avg_1y,

                    COUNT(*) AS total_proposals,
                    COUNT(*) FILTER (WHERE entry_price IS NOT NULL) AS total_tracked
                FROM investment_proposals
                WHERE action = 'buy'
                  AND entry_price IS NOT NULL
            """)
            overview = cur.fetchone() or {}

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

            # 측정 대기 중 건수 (entry_price 있지만 아직 post_return_1m 없는 건)
            pending_count = int(overview.get("total_tracked") or 0) - int(overview.get("n_1m") or 0)

            # ── 2) 과거 모멘텀 (참고 지표) ──
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE return_1m_pct IS NOT NULL) AS n_1m,
                    COUNT(*) FILTER (WHERE return_1m_pct > 0)         AS win_1m,
                    ROUND(AVG(return_1m_pct) FILTER (WHERE return_1m_pct IS NOT NULL)::numeric, 2) AS avg_1m,

                    COUNT(*) FILTER (WHERE return_3m_pct IS NOT NULL) AS n_3m,
                    COUNT(*) FILTER (WHERE return_3m_pct > 0)         AS win_3m,
                    ROUND(AVG(return_3m_pct) FILTER (WHERE return_3m_pct IS NOT NULL)::numeric, 2) AS avg_3m
                FROM investment_proposals
                WHERE action = 'buy'
                  AND current_price IS NOT NULL
            """)
            momentum_row = cur.fetchone() or {}
            momentum = {}
            for p in ("1m", "3m"):
                n = momentum_row.get(f"n_{p}") or 0
                win = momentum_row.get(f"win_{p}") or 0
                momentum[p] = {
                    "n": int(n),
                    "wins": int(win),
                    "win_rate_pct": _win_rate(win, n),
                    "avg_return_pct": _float(momentum_row.get(f"avg_{p}")),
                }

            # ── 3) 분류별 (discovery_type) — post_return 기준 ──
            cur.execute("""
                SELECT
                    LOWER(COALESCE(discovery_type, 'unknown')) AS discovery_type,
                    COUNT(*) FILTER (WHERE post_return_1m_pct IS NOT NULL) AS n,
                    COUNT(*) FILTER (WHERE post_return_1m_pct > 0)         AS wins,
                    ROUND(AVG(post_return_1m_pct) FILTER (WHERE post_return_1m_pct IS NOT NULL)::numeric, 2) AS avg_1m,
                    ROUND(AVG(post_return_3m_pct) FILTER (WHERE post_return_3m_pct IS NOT NULL)::numeric, 2) AS avg_3m,
                    ROUND(AVG(post_return_1y_pct) FILTER (WHERE post_return_1y_pct IS NOT NULL)::numeric, 2) AS avg_1y
                FROM investment_proposals
                WHERE action = 'buy' AND entry_price IS NOT NULL
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

            # ── 4) 최근 30일 Top Picks — 추천 후 수익률 포함 ──
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
                    p.entry_price,
                    p.current_price,
                    p.upside_pct,
                    p.post_return_1m_pct,
                    p.post_return_3m_pct,
                    p.return_1m_pct AS momentum_1m_pct,
                    t.theme_name,
                    -- 최신 스냅샷 가격 (추적 중인 경우)
                    (SELECT pps.price FROM proposal_price_snapshots pps
                     WHERE pps.proposal_id = p.id
                     ORDER BY pps.snapshot_date DESC LIMIT 1) AS latest_price
                FROM daily_top_picks dtp
                JOIN investment_proposals p ON dtp.proposal_id = p.id
                LEFT JOIN investment_themes t ON p.theme_id = t.id
                WHERE dtp.analysis_date >= CURRENT_DATE - INTERVAL '30 days'
                ORDER BY dtp.analysis_date DESC, dtp.rank ASC
                LIMIT 20
            """)
            top_picks = []
            for r in cur.fetchall():
                entry = _float(r.get("entry_price"))
                latest = _float(r.get("latest_price"))
                # 아직 post_return 미산정이면 최신 스냅샷으로 "현재까지 수익률" 계산
                live_return = None
                if entry and latest and entry > 0:
                    live_return = round((latest - entry) / entry * 100, 2)

                top_picks.append({
                    "analysis_date": r["analysis_date"].isoformat() if r.get("analysis_date") else None,
                    "rank": r.get("rank"),
                    "score_final": _float(r.get("score_final")),
                    "ticker": r.get("ticker"),
                    "asset_name": r.get("asset_name"),
                    "discovery_type": r.get("discovery_type"),
                    "conviction": r.get("conviction"),
                    "theme_name": r.get("theme_name"),
                    "entry_price": entry,
                    "current_price": _float(r.get("current_price")),
                    "latest_price": latest,
                    "upside_pct": _float(r.get("upside_pct")),
                    "post_return_1m_pct": _float(r.get("post_return_1m_pct")),
                    "post_return_3m_pct": _float(r.get("post_return_3m_pct")),
                    "live_return_pct": live_return,
                    "momentum_1m_pct": _float(r.get("momentum_1m_pct")),
                    "rationale_text": r.get("rationale_text"),
                    "source": r.get("source"),
                })

            # ── 5) 메타 ──
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
            "추천 후 실제 수익률은 추천일 entry_price 대비 실제 시세 변동을 추적합니다. "
            "매일 자동 갱신되며, 측정 기간 미도달 건은 '측정 중'으로 표시됩니다. "
            "본 정보는 투자 권유가 아닙니다."
        ),
        "overview": {
            "total_proposals": int(overview.get("total_proposals") or 0),
            "total_tracked": int(overview.get("total_tracked") or 0),
            "pending_count": max(0, pending_count),
            "periods": periods,
        },
        "momentum_reference": momentum,
        "by_discovery_type": by_type,
        "recent_top_picks": top_picks,
        "meta": {
            "earliest_date": meta_row.get("earliest").isoformat() if meta_row.get("earliest") else None,
            "latest_date": meta_row.get("latest").isoformat() if meta_row.get("latest") else None,
        },
    }


# ── 트랙레코드 HTML 페이지 ──────────────────────────────


@pages_router.get("")
def track_record_page(request: Request, user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    """트랙레코드 공개 페이지 — 비로그인도 접근 가능."""
    ctx = _base_ctx(request, "track_record", user, auth_cfg)
    # 클라이언트에서 /api/track-record/summary fetch하여 렌더
    return templates.TemplateResponse(request=request, name="track_record.html", context=ctx)
