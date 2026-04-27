"""펀더 결측률 진단 도구.

`stock_universe`(활성+보통주) vs `stock_universe_fundamentals`(최근 staleness_days 내) 비교 →
시장별 결측 비율 + 마지막 sync 시각 산출.

CLI:
    python -m tools.fundamentals_health_check
    # → 표 출력 + 임계 초과 시 exit code 1
"""
from __future__ import annotations

import sys
from typing import Optional

from shared.config import DatabaseConfig, FundamentalsConfig
from shared.db import get_connection


def compute_missing_rate(conn, *, staleness_days: int = 2) -> list[dict]:
    """시장별 결측 비율.

    Args:
        staleness_days: '최근 N일 내 펀더 row 보유' 기준일. 기본 2 (어제 + 오늘 grace).

    Returns:
        [{"market", "total", "with_fund", "missing_pct", "last_fetched_at"}, ...]
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                u.market,
                COUNT(*) AS total,
                COUNT(f.ticker) AS with_fund,
                MAX(f.fetched_at) AS last_fetched_at
            FROM stock_universe u
            LEFT JOIN LATERAL (
                SELECT ticker, fetched_at
                FROM stock_universe_fundamentals f
                WHERE f.ticker = u.ticker
                  AND f.market = u.market
                  AND f.snapshot_date >= CURRENT_DATE - %s::int
                ORDER BY snapshot_date DESC LIMIT 1
            ) f ON TRUE
            WHERE u.listed = TRUE AND u.has_preferred = FALSE
            GROUP BY u.market
            ORDER BY u.market
        """, (int(staleness_days),))
        rows = cur.fetchall()
    out = []
    for market, total, with_fund, last_at in rows:
        missing = (total - with_fund)
        pct = round((missing / total) * 100, 3) if total > 0 else 0.0
        out.append({
            "market": market,
            "total": int(total),
            "with_fund": int(with_fund),
            "missing_pct": pct,
            "last_fetched_at": last_at,
        })
    return out


def main(
    db_cfg: Optional[DatabaseConfig] = None,
    cfg: Optional[FundamentalsConfig] = None,
) -> int:
    db_cfg = db_cfg or DatabaseConfig()
    cfg = cfg or FundamentalsConfig()

    conn = get_connection(db_cfg)
    try:
        results = compute_missing_rate(conn, staleness_days=cfg.staleness_days)
    finally:
        conn.close()

    print(f"{'시장':<10} {'활성종목':>8} {'펀더보유':>8} {'결측%':>8} {'마지막sync':>22}")
    exit_code = 0
    for r in results:
        last = str(r["last_fetched_at"]) if r["last_fetched_at"] else "(없음)"
        threshold = cfg.missing_pct_threshold(r["market"])
        marker = ""
        if r["missing_pct"] > threshold:
            marker = " ⚠ 임계 초과"
            exit_code = 1
        print(f"{r['market']:<10} {r['total']:>8} {r['with_fund']:>8} "
              f"{r['missing_pct']:>7.2f}% {last:>22}{marker}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
