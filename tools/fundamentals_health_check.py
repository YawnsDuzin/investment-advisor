"""펀더 결측률 진단 도구.

`stock_universe`(활성+보통주) vs `stock_universe_fundamentals`(최근 7일 내) 비교 →
시장별 결측 비율 + 마지막 sync 시각 산출.

CLI:
    python -m tools.fundamentals_health_check
    # → 표 출력 + 임계 초과 시 exit code 1
"""
from __future__ import annotations

import sys
from typing import Optional

from shared.config import DatabaseConfig
from shared.db import get_connection
from shared.logger import get_logger

_log = get_logger("fundamentals_health_check")

# 시장별 결측률 임계 (% 초과 시 빨간 경고)
_MISSING_PCT_THRESHOLD = {"KOSPI": 5.0, "KOSDAQ": 5.0, "NASDAQ": 3.0, "NYSE": 3.0}


def compute_missing_rate(conn) -> list[dict]:
    """시장별 결측 비율.

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
                  AND f.snapshot_date >= CURRENT_DATE - 7
                ORDER BY snapshot_date DESC LIMIT 1
            ) f ON TRUE
            WHERE u.listed = TRUE AND u.has_preferred = FALSE
            GROUP BY u.market
            ORDER BY u.market
        """)
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


def main(db_cfg: Optional[DatabaseConfig] = None) -> int:
    cfg = db_cfg or DatabaseConfig()
    conn = get_connection(cfg)
    try:
        results = compute_missing_rate(conn)
    finally:
        conn.close()

    print(f"{'시장':<10} {'활성종목':>8} {'펀더보유':>8} {'결측%':>8} {'마지막sync':>22}")
    exit_code = 0
    for r in results:
        last = str(r["last_fetched_at"]) if r["last_fetched_at"] else "(없음)"
        marker = ""
        threshold = _MISSING_PCT_THRESHOLD.get(r["market"], 10.0)
        if r["missing_pct"] > threshold:
            marker = " ⚠ 임계 초과"
            exit_code = 1
        print(f"{r['market']:<10} {r['total']:>8} {r['with_fund']:>8} "
              f"{r['missing_pct']:>7.2f}% {last:>22}{marker}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
