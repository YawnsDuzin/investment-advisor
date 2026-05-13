"""체온계 산식 튜닝 도구 — 과거 regime 스냅샷에 대해 소급 계산 + 분포 리포트.

`pre_market_briefings.regime_snapshot JSONB` (v34) 누적 데이터로 `market_temperature` 를
재산출. 산식 변경 후 분포 비교·dead 지표 진단에 사용.

사용 예:
  # 최근 90일 분포 + sub-score 진단 (DB 변경 안 함)
  python -m tools.backfill_temperature --since-days 90 --dry-run

  # DB write (현재 산식으로 NULL row 채움)
  python -m tools.backfill_temperature --since-days 90 --write

  # 산식 변경 후 강제 재계산 (이미 채워진 row 도 덮어쓰기)
  python -m tools.backfill_temperature --since-days 90 --write --force
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, timedelta
from typing import Iterable, Optional

from psycopg2.extras import RealDictCursor

from analyzer.market_temperature import (
    DEFAULT_CONFIG,
    MarketTemperatureConfig,
    compute_breakdown,
    label_for_temperature,
)
from shared.config import AppConfig, DatabaseConfig
from shared.db import get_connection
from shared.logger import get_logger

_log = get_logger("temp_backfill")


def fetch_briefings(
    db_cfg: DatabaseConfig,
    since: date,
) -> list[dict]:
    """pre_market_briefings 의 (briefing_date, regime_snapshot, market_temperature) 조회."""
    conn = get_connection(db_cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT briefing_date, regime_snapshot, market_temperature
                FROM pre_market_briefings
                WHERE briefing_date >= %s
                  AND status IN ('success', 'partial')
                ORDER BY briefing_date ASC
                """,
                (since,),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def update_temperature(
    db_cfg: DatabaseConfig,
    rows: Iterable[tuple[date, int]],
) -> int:
    """(briefing_date, temperature) 리스트로 UPDATE."""
    rows_list = list(rows)
    if not rows_list:
        return 0
    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            for d, t in rows_list:
                cur.execute(
                    "UPDATE pre_market_briefings SET market_temperature = %s "
                    "WHERE briefing_date = %s",
                    (t, d),
                )
        conn.commit()
        return len(rows_list)
    except Exception as e:
        conn.rollback()
        _log.error(f"[backfill] UPDATE 실패: {e}")
        return 0
    finally:
        conn.close()


def report_distribution(
    breakdowns: list[dict],
    cfg: MarketTemperatureConfig = DEFAULT_CONFIG,
) -> dict:
    """소급 계산 결과의 분포·통계 리포트.

    Args:
        breakdowns: `compute_breakdown()` 결과 리스트 (None 제외).

    Returns:
        {
            "n": int,
            "min": int, "max": int, "mean": float, "median": int,
            "label_counts": {"빙하": 0, "약세": 1, ...},
            "sub_score_means": {"trend": ..., "breadth": ..., ...},
            "input_coverage": {  # 각 입력 키가 결측 아닌 row 비율
                "above_200ma": 0.95, "drawdown_from_52w_high_pct": 0.92, ...
            },
        }
    """
    if not breakdowns:
        return {
            "n": 0, "min": None, "max": None, "mean": None, "median": None,
            "label_counts": {}, "sub_score_means": {}, "input_coverage": {},
        }
    totals = sorted(b["total"] for b in breakdowns)
    n = len(totals)
    label_counts = Counter(b["label"] for b in breakdowns)
    sub_keys = ["trend", "breadth", "calm", "momentum"]
    sub_means = {
        k: round(sum(b["scores"][k] for b in breakdowns) / n, 2)
        for k in sub_keys
    }
    input_keys = [
        "above_200ma", "drawdown_from_52w_high_pct",
        "breadth_kr_pct", "vol60_pct", "return_1m_pct",
    ]
    input_coverage = {
        k: round(
            sum(1 for b in breakdowns if b["inputs"].get(k) is not None) / n, 3
        )
        for k in input_keys
    }
    return {
        "n": n,
        "min": totals[0],
        "max": totals[-1],
        "mean": round(sum(totals) / n, 2),
        "median": totals[n // 2],
        "label_counts": dict(label_counts),
        "sub_score_means": sub_means,
        "input_coverage": input_coverage,
    }


def format_report(report: dict, label_brackets: tuple[tuple[int, str], ...]) -> str:
    """터미널 출력용 리포트 포맷."""
    if report["n"] == 0:
        return "[backfill] 분석 가능한 row 0건 — pre_market_briefings 가 비어있거나 regime_snapshot 결측"
    lines = [
        f"[backfill] N={report['n']}  range=[{report['min']}~{report['max']}]  "
        f"mean={report['mean']}  median={report['median']}",
        "",
        "─ 라벨 분포 ─",
    ]
    # 라벨 정렬 — bracket 순서대로
    label_order = [name for _, name in label_brackets]
    for name in label_order:
        cnt = report["label_counts"].get(name, 0)
        pct = cnt / report["n"] * 100 if report["n"] else 0
        bar = "█" * int(pct / 2)
        lines.append(f"  {name:>4} : {cnt:4d} ({pct:5.1f}%)  {bar}")
    lines += [
        "",
        "─ Sub-score 평균 (각 0~25, dead 지표 진단용) ─",
    ]
    for k, v in report["sub_score_means"].items():
        # 12.5 (중립) 근처 = dead 가능성
        flag = " ⚠ dead?" if 11.5 <= v <= 13.5 else ""
        lines.append(f"  {k:<10} : {v:5.2f}{flag}")
    lines += [
        "",
        "─ 입력 키 결측률 ─",
    ]
    for k, v in report["input_coverage"].items():
        cov_pct = v * 100
        flag = " ⚠ low" if cov_pct < 80 else ""
        lines.append(f"  {k:<35} : {cov_pct:5.1f}%{flag}")
    return "\n".join(lines)


def run(
    db_cfg: DatabaseConfig,
    *,
    since_days: int = 90,
    write: bool = False,
    force: bool = False,
    cfg: MarketTemperatureConfig = DEFAULT_CONFIG,
) -> dict:
    """엔트리포인트.

    Args:
        since_days: 백필 윈도우 (KST 일).
        write: True 면 DB UPDATE. False 면 dry-run.
        force: write 시 이미 채워진 row 도 재계산해 덮어씀.

    Returns:
        report_distribution() 결과.
    """
    since = date.today() - timedelta(days=since_days)
    briefings = fetch_briefings(db_cfg, since)
    if not briefings:
        _log.warning(f"[backfill] {since} 이후 row 0건")
        return report_distribution([], cfg)

    breakdowns: list[dict] = []
    updates: list[tuple[date, int]] = []
    skipped = 0
    null_only = 0

    for row in briefings:
        snap = row.get("regime_snapshot")
        if isinstance(snap, str):
            try:
                snap = json.loads(snap)
            except Exception:
                snap = None
        if not snap:
            skipped += 1
            continue
        bd = compute_breakdown(snap, cfg)
        if bd is None:
            skipped += 1
            continue
        breakdowns.append(bd)

        existing = row.get("market_temperature")
        if write:
            should_write = force or existing is None
            if existing is None:
                null_only += 1
            if should_write and existing != bd["total"]:
                updates.append((row["briefing_date"], bd["total"]))

    report = report_distribution(breakdowns, cfg)

    _log.info(
        f"[backfill] briefings={len(briefings)} "
        f"computable={len(breakdowns)} skipped={skipped} "
        f"null_existing={null_only}"
    )

    if write and updates:
        n = update_temperature(db_cfg, updates)
        _log.info(f"[backfill] UPDATE {n}건 적용")
    elif write:
        _log.info("[backfill] 갱신 대상 0건 (force 옵션 없이 NULL 만 채움)")

    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="체온계 산식 backfill + 분포 리포트",
    )
    parser.add_argument(
        "--since-days", type=int, default=90,
        help="백필 윈도우 (기본 90일)",
    )
    parser.add_argument(
        "--write", action="store_true",
        help="DB UPDATE 실행. 미지정 시 dry-run (분포 리포트만).",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="--write 시 이미 채워진 market_temperature 도 재계산해 덮어씀.",
    )
    args = parser.parse_args()

    cfg = AppConfig()
    report = run(
        cfg.db,
        since_days=args.since_days,
        write=args.write,
        force=args.force,
    )
    print(format_report(report, DEFAULT_CONFIG.brackets))
    print()
    print("[backfill] 모드:", "WRITE" if args.write else "DRY-RUN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
