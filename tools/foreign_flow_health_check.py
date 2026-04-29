"""외국인 수급 결측률 진단 도구.

`stock_universe`(활성+보통주, KRX 한정) vs `stock_universe_foreign_flow`(staleness_days 내) 비교
→ 시장별 결측 비율 + 마지막 sync 시각.

CLI:
    python -m tools.foreign_flow_health_check
    # → 표 출력 + 임계 초과 시 exit code 1
"""
from __future__ import annotations

import sys
from typing import Optional

from shared.config import DatabaseConfig, ForeignFlowConfig
from shared.db import get_connection


def compute_missing_rate(conn, *, staleness_days: int = 2) -> list[dict]:
    """KRX 시장(KOSPI/KOSDAQ)별 외국인 수급 결측률.

    Returns: [{"market", "total", "with_data", "missing_pct", "last_fetched_at"}, ...]
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                u.market,
                COUNT(*) AS total,
                COUNT(ff.ticker) AS with_data,
                MAX(ff.fetched_at) AS last_fetched_at
            FROM stock_universe u
            LEFT JOIN LATERAL (
                SELECT ticker, fetched_at
                FROM stock_universe_foreign_flow ff
                WHERE ff.ticker = u.ticker
                  AND ff.market = u.market
                  AND ff.snapshot_date >= CURRENT_DATE - %s::int
                ORDER BY snapshot_date DESC LIMIT 1
            ) ff ON TRUE
            WHERE u.listed = TRUE AND u.has_preferred = FALSE
              AND u.market IN ('KOSPI', 'KOSDAQ')
            GROUP BY u.market
            ORDER BY u.market
        """, (int(staleness_days),))
        rows = cur.fetchall()
    out = []
    for market, total, with_data, last_at in rows:
        missing = (total - with_data)
        pct = round((missing / total) * 100, 3) if total > 0 else 0.0
        out.append({
            "market": market,
            "total": int(total),
            "with_data": int(with_data),
            "missing_pct": pct,
            "last_fetched_at": last_at,
        })
    return out


def main(
    db_cfg: Optional[DatabaseConfig] = None,
    cfg: Optional[ForeignFlowConfig] = None,
) -> int:
    db_cfg = db_cfg or DatabaseConfig()
    cfg = cfg or ForeignFlowConfig()

    conn = get_connection(db_cfg)
    try:
        results = compute_missing_rate(conn, staleness_days=cfg.staleness_days)
    finally:
        conn.close()

    print(f"{'시장':<10} {'활성종목':>8} {'수급보유':>8} {'결측%':>8} {'마지막sync':>22}")
    exit_code = 0
    for r in results:
        last = str(r["last_fetched_at"]) if r["last_fetched_at"] else "(없음)"
        threshold = cfg.missing_pct_threshold(r["market"])
        marker = ""
        if r["missing_pct"] > threshold:
            marker = f" ⚠ 임계({threshold:.1f}%) 초과"
            exit_code = 1
        print(f"{r['market']:<10} {r['total']:>8} {r['with_data']:>8} "
              f"{r['missing_pct']:>7.2f}% {last:>22}{marker}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
