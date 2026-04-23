"""stock_universe_ohlcv 데이터 무결성 검사 (Phase 7).

계획서 _docs/20260422235016_ohlcv-history-table-plan.md §9.1 기반 검증 쿼리 묶음.

실행:
    python -m tools.ohlcv_health_check
    python -m tools.ohlcv_health_check --json   # 기계 판독용 JSON 출력
    python -m tools.ohlcv_health_check --strict # 경고 있으면 exit code 1 반환 (cron/CI 용)

점검 항목:
    1. 시장별 날짜 커버리지 (거래일 수, 최신/최초 날짜)
    2. 시장별 종목 커버리지 (DISTINCT ticker 수)
    3. universe 대비 누락 종목 (listed=TRUE, has_preferred=FALSE 기준)
    4. change_pct 이상치 (절대값 > 30%) 분포
    5. change_pct 누락 (NULL) 수
    6. 전일 대비 row 수 증가율 (sudden drop 감지)
"""
from __future__ import annotations

import argparse
import json
import sys

from shared.config import AppConfig
from shared.db import get_connection


def _fetch_coverage_by_market(cur) -> list[dict]:
    cur.execute("""
        SELECT market,
               COUNT(DISTINCT trade_date) AS days,
               MIN(trade_date) AS first_date,
               MAX(trade_date) AS last_date,
               COUNT(DISTINCT ticker) AS tickers,
               COUNT(*) AS total_rows
        FROM stock_universe_ohlcv
        GROUP BY market
        ORDER BY market
    """)
    return [
        {
            "market": r[0],
            "days": r[1],
            "first_date": r[2].isoformat() if r[2] else None,
            "last_date": r[3].isoformat() if r[3] else None,
            "tickers": r[4],
            "total_rows": r[5],
        }
        for r in cur.fetchall()
    ]


def _fetch_missing_tickers(cur, *, limit: int = 20) -> list[dict]:
    """universe에는 있으나 OHLCV에 한 번도 등장하지 않은 종목 (신규 상장 IPO 당일 등 소수 예상)."""
    cur.execute("""
        SELECT u.ticker, u.market, u.asset_name, u.market_cap_bucket
        FROM stock_universe u
        LEFT JOIN (
            SELECT DISTINCT ticker, market FROM stock_universe_ohlcv
        ) o ON u.ticker = o.ticker AND u.market = o.market
        WHERE u.listed = TRUE
          AND u.has_preferred = FALSE
          AND o.ticker IS NULL
        ORDER BY u.market, u.ticker
        LIMIT %s
    """, (limit,))
    return [
        {"ticker": r[0], "market": r[1], "asset_name": r[2], "market_cap_bucket": r[3]}
        for r in cur.fetchall()
    ]


def _fetch_change_pct_outliers(cur, *, abs_threshold: float = 30.0, limit: int = 10) -> dict:
    cur.execute("""
        SELECT COUNT(*) FROM stock_universe_ohlcv
        WHERE ABS(change_pct) > %s
    """, (abs_threshold,))
    total = cur.fetchone()[0]
    cur.execute("""
        SELECT ticker, market, trade_date, close, change_pct
        FROM stock_universe_ohlcv
        WHERE ABS(change_pct) > %s
        ORDER BY ABS(change_pct) DESC
        LIMIT %s
    """, (abs_threshold, limit))
    samples = [
        {
            "ticker": r[0], "market": r[1],
            "trade_date": r[2].isoformat(),
            "close": float(r[3]) if r[3] is not None else None,
            "change_pct": float(r[4]) if r[4] is not None else None,
        }
        for r in cur.fetchall()
    ]
    return {"count": total, "threshold": abs_threshold, "samples": samples}


def _fetch_change_pct_nulls(cur) -> dict:
    """change_pct NULL 분석.

    first-day of ticker는 정상 NULL (이전 close 없음). 그 외는 이상 징후.
    """
    cur.execute("""
        WITH first_day AS (
            SELECT ticker, market, MIN(trade_date) AS min_d
            FROM stock_universe_ohlcv
            GROUP BY ticker, market
        )
        SELECT COUNT(*) FILTER (WHERE o.change_pct IS NULL)               AS total_null,
               COUNT(*) FILTER (WHERE o.change_pct IS NULL
                                AND o.trade_date = f.min_d)                AS null_first_day,
               COUNT(*) FILTER (WHERE o.change_pct IS NULL
                                AND o.trade_date > f.min_d)                AS null_unexpected
        FROM stock_universe_ohlcv o
        JOIN first_day f USING (ticker, market)
    """)
    row = cur.fetchone()
    return {
        "total_null": row[0] or 0,
        "null_first_day": row[1] or 0,  # 정상
        "null_unexpected": row[2] or 0,  # 비정상 — recompute_change_pct 미실행 의심
    }


def _fetch_daily_row_trend(cur, *, days: int = 10) -> list[dict]:
    """최근 N 거래일 row 수 추이."""
    cur.execute("""
        SELECT trade_date, COUNT(*) AS rows
        FROM stock_universe_ohlcv
        WHERE trade_date >= CURRENT_DATE - (%s::int)
        GROUP BY trade_date
        ORDER BY trade_date DESC
    """, (days * 2,))  # 주말 제외하고 N거래일 확보 위해 2배
    return [{"trade_date": r[0].isoformat(), "rows": r[1]} for r in cur.fetchall()[:days]]


def run_health_check() -> dict:
    cfg = AppConfig()
    conn = get_connection(cfg.db)
    try:
        with conn.cursor() as cur:
            coverage = _fetch_coverage_by_market(cur)
            missing = _fetch_missing_tickers(cur)
            outliers = _fetch_change_pct_outliers(cur)
            nulls = _fetch_change_pct_nulls(cur)
            trend = _fetch_daily_row_trend(cur)
    finally:
        conn.close()

    # 경고 집계
    warnings: list[str] = []
    if not coverage:
        warnings.append("ERROR: OHLCV 테이블이 비어 있습니다. backfill을 먼저 실행하세요.")
    for c in coverage:
        if c["days"] < 100:
            warnings.append(f"[{c['market']}] 거래일 수가 부족합니다 ({c['days']}일) — backfill 권장")
    if nulls["null_unexpected"] > 0:
        warnings.append(
            f"change_pct NULL 비정상 {nulls['null_unexpected']}건 — "
            "`python -m analyzer.universe_sync --mode price`로 recompute 가능"
        )
    if len(missing) > 100:
        warnings.append(f"universe 대비 누락 종목 많음 ({len(missing)}+건) — meta/backfill 점검")
    if len(trend) >= 2:
        last, prev = trend[0]["rows"], trend[1]["rows"]
        if prev and abs(last - prev) / prev > 0.5:
            warnings.append(
                f"최근 거래일 row 수 급변: {trend[1]['trade_date']}={prev} → "
                f"{trend[0]['trade_date']}={last}"
            )

    return {
        "coverage": coverage,
        "missing_tickers_sample": missing,
        "missing_count_displayed": len(missing),
        "change_pct_outliers": outliers,
        "change_pct_nulls": nulls,
        "recent_daily_rows": trend,
        "warnings": warnings,
    }


def _print_human(report: dict) -> None:
    print("=" * 70)
    print("[OHLCV Health Check] stock_universe_ohlcv 무결성 검사")
    print("=" * 70)

    print("\n■ 시장별 커버리지")
    if not report["coverage"]:
        print("  (데이터 없음)")
    for c in report["coverage"]:
        print(f"  - {c['market']:7s}: {c['days']:4d}일 × {c['tickers']:4d}종목 "
              f"= {c['total_rows']:>9,}행  ({c['first_date']} ~ {c['last_date']})")

    print("\n■ universe 대비 누락 종목 (최대 20건 표시)")
    missing = report["missing_tickers_sample"]
    if not missing:
        print("  (없음 — 완전 커버리지)")
    else:
        for m in missing[:20]:
            print(f"  - {m['ticker']:8s} [{m['market']:6s}] {m['asset_name']} "
                  f"bucket={m['market_cap_bucket']}")

    print("\n■ change_pct 이상치 (|%| > 30%)")
    out = report["change_pct_outliers"]
    print(f"  총 {out['count']}건")
    for s in out["samples"]:
        print(f"  - {s['ticker']:8s} [{s['market']:6s}] {s['trade_date']} "
              f"close={s['close']:.2f}  change={s['change_pct']:+.2f}%")

    print("\n■ change_pct NULL 분석")
    n = report["change_pct_nulls"]
    print(f"  전체 NULL   : {n['total_null']:>8,}")
    print(f"  └ 첫 거래일 : {n['null_first_day']:>8,}  (정상)")
    print(f"  └ 비정상     : {n['null_unexpected']:>8,}  "
          "(>0이면 recompute_change_pct 필요)")

    print("\n■ 최근 거래일 row 수 추이")
    for t in report["recent_daily_rows"]:
        print(f"  - {t['trade_date']}: {t['rows']:>6,}행")

    print("\n■ 경고")
    w = report["warnings"]
    if not w:
        print("  (이상 없음)")
    else:
        for msg in w:
            print(f"  ⚠ {msg}")
    print("=" * 70)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="OHLCV 데이터 무결성 검사 (Phase 7)")
    p.add_argument("--json", action="store_true", help="JSON 출력 (기계 판독용)")
    p.add_argument("--strict", action="store_true",
                   help="경고가 하나라도 있으면 exit code 1 반환 (cron/CI용)")
    args = p.parse_args(argv)

    try:
        report = run_health_check()
    except Exception as e:
        print(f"[health-check] 실행 실패: {e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report)

    if args.strict and report["warnings"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
