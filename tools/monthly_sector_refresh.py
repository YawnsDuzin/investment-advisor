"""월간 섹터 분류 리프레시 — KOSDAQ 신규 상장 자동 커버.

P1-ext2 (2026-04-24) 완료 후 설계.
시간 경과로 틀어지는 3가지를 자동 교정:

1. KOSDAQ 신규 상장 종목의 industry 컬럼 백필 (pykrx가 세분 industry 미제공)
2. yfinance industry 갱신분 반영 (신규 상장 직후 NULL이었다가 차후 채워지는 케이스)
3. 재정규화로 `_INDUSTRY_OVERRIDES` 영문 키워드 재발동 (반도체/biotech/banks 등 세분)

실행 내용 (순차):
    A. `backfill_industry_kr(only_missing=True)` — industry=NULL 종목만 yfinance 조회
    B. `renormalize_sectors --market KRX --apply` — 전체 KRX 재매핑
    C. 분포 리포트 생성 (변화량·신규 종목·이상치 감지)

실행 주기 (권장): 매월 1일 03:45 KST

CLI:
    python -m tools.monthly_sector_refresh              # 실제 실행
    python -m tools.monthly_sector_refresh --dry-run    # 변경 없이 리포트만
    python -m tools.monthly_sector_refresh --skip-backfill  # 재정규화만 (긴급)

이상치 감지 임계값 (기본 10% 이상 변동 시 WARN):
    --anomaly-threshold 0.1
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Any

from shared.config import AppConfig
from shared.db import get_connection
from shared.logger import get_logger

_log = get_logger("monthly_sector_refresh")
KST = timezone(timedelta(hours=9))


def _snapshot_distribution(db_cfg) -> Counter[str]:
    """현재 listed=TRUE 종목의 sector_norm 분포 스냅샷."""
    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT sector_norm, COUNT(*) FROM stock_universe "
                "WHERE listed = TRUE GROUP BY sector_norm"
            )
            return Counter({(s or "(null)"): n for s, n in cur.fetchall()})
    finally:
        conn.close()


def _count_null_industry(db_cfg, markets: tuple[str, ...]) -> int:
    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM stock_universe "
                "WHERE market = ANY(%s) AND industry IS NULL AND listed = TRUE",
                (list(markets),),
            )
            return cur.fetchone()[0]
    finally:
        conn.close()


def _detect_anomalies(before: Counter, after: Counter, threshold: float) -> list[str]:
    """분포 변화가 threshold(비율)를 초과하는 버킷 목록 — 수동 검토 대상."""
    warnings: list[str] = []
    all_keys = set(before.keys()) | set(after.keys())
    for key in sorted(all_keys):
        b = before.get(key, 0)
        a = after.get(key, 0)
        if b == 0 and a == 0:
            continue
        if b == 0:
            warnings.append(f"  [NEW]  {key}: 0 → {a} (신규 버킷)")
            continue
        diff = a - b
        ratio = abs(diff) / max(b, 1)
        if ratio >= threshold:
            sign = "+" if diff >= 0 else ""
            warnings.append(f"  [{ratio*100:>5.1f}%]  {key}: {b} → {a} ({sign}{diff})")
    return warnings


def run(
    dry_run: bool = False,
    skip_backfill: bool = False,
    skip_renormalize: bool = False,
    markets: tuple[str, ...] = ("KOSPI", "KOSDAQ"),
    backfill_limit: int | None = None,
    anomaly_threshold: float = 0.10,
) -> dict[str, Any]:
    """월간 섹터 리프레시 본체."""
    cfg = AppConfig()
    started = datetime.now(KST)
    _log.info(f"[monthly-sector-refresh] 시작 (dry_run={dry_run}, markets={markets})")

    # ── 0) 사전 스냅샷 ─────────────────────────────
    before_dist = _snapshot_distribution(cfg.db)
    before_null = _count_null_industry(cfg.db, markets)
    _log.info(f"  사전 상태: industry NULL {before_null}건, 활성 버킷 {len(before_dist)}개")

    result: dict[str, Any] = {
        "started_at": started.isoformat(),
        "before": {
            "null_industry": before_null,
            "bucket_count": len(before_dist),
        },
        "stages": {},
    }

    # ── 1) industry 백필 ───────────────────────────
    if skip_backfill:
        _log.info("  [Stage A] industry_kr 백필 SKIP (--skip-backfill)")
        result["stages"]["backfill"] = {"skipped": True}
    elif dry_run:
        _log.info("  [Stage A] industry_kr 백필 SKIP (dry-run)")
        result["stages"]["backfill"] = {"skipped": True, "reason": "dry-run"}
    else:
        from analyzer.universe_sync import backfill_industry_kr

        stage_started = datetime.now(KST)
        bf_result = backfill_industry_kr(
            cfg.db,
            markets=markets,
            only_missing=True,
            limit=backfill_limit,
        )
        duration = (datetime.now(KST) - stage_started).total_seconds()
        _log.info(
            f"  [Stage A] 백필 완료: {bf_result['fetched']}/{bf_result['target']} 수집 "
            f"({bf_result['fetched']*100//max(bf_result['target'],1)}%), "
            f"UPDATE {bf_result['updated']}건 / {duration:.1f}s"
        )
        result["stages"]["backfill"] = bf_result

    # ── 2) 재정규화 ────────────────────────────────
    if skip_renormalize:
        _log.info("  [Stage B] 재정규화 SKIP (--skip-renormalize)")
        result["stages"]["renormalize"] = {"skipped": True}
    else:
        from tools.renormalize_sectors import run as renorm_run

        stage_started = datetime.now(KST)
        # KRX 만 대상 (US는 이번 리프레시 범위 밖)
        rn_result = renorm_run(
            apply=not dry_run,
            market_filter="KRX",
            verbose=False,
            sample_n=10,
            batch=500,
        )
        duration = (datetime.now(KST) - stage_started).total_seconds()
        _log.info(
            f"  [Stage B] 재정규화: scanned={rn_result['scanned']}, "
            f"changes={rn_result['changes']}, applied={rn_result['applied']} / {duration:.1f}s"
        )
        result["stages"]["renormalize"] = rn_result

    # ── 3) 사후 스냅샷 및 변화 분석 ─────────────────
    after_dist = _snapshot_distribution(cfg.db)
    after_null = _count_null_industry(cfg.db, markets)

    null_reduced = before_null - after_null
    coverage_before = 1 - before_null / max(sum(before_dist.values()), 1)
    coverage_after = 1 - after_null / max(sum(after_dist.values()), 1)

    _log.info(f"  사후 상태: industry NULL {after_null}건 (감소 {null_reduced}건)")

    # 분포 변화 요약
    diffs: list[tuple[str, int, int, int]] = []
    all_keys = set(before_dist.keys()) | set(after_dist.keys())
    for k in sorted(all_keys):
        b = before_dist.get(k, 0)
        a = after_dist.get(k, 0)
        if b == a:
            continue
        diffs.append((k, b, a, a - b))

    result["after"] = {
        "null_industry": after_null,
        "null_reduced": null_reduced,
        "coverage_pct": round(coverage_after * 100, 2),
        "bucket_count": len(after_dist),
    }
    result["distribution_changes"] = [
        {"sector": k, "before": b, "after": a, "diff": d} for k, b, a, d in diffs
    ]

    # 이상치 감지
    anomalies = _detect_anomalies(before_dist, after_dist, anomaly_threshold)
    result["anomalies"] = anomalies

    # ── 4) 리포트 출력 ────────────────────────────
    print("\n" + "=" * 60)
    print(f"월간 섹터 리프레시 리포트 ({started.strftime('%Y-%m-%d %H:%M KST')})")
    print("=" * 60)
    print(f"\n커버리지: {coverage_before*100:.1f}% → {coverage_after*100:.1f}%"
          f" (industry NULL {before_null} → {after_null}, 감소 {null_reduced})")

    if diffs:
        print(f"\n분포 변화 ({len(diffs)}개 버킷):")
        print(f"  {'sector_norm':<25} {'before':>6} {'after':>6} {'diff':>6}")
        print("  " + "-" * 47)
        for k, b, a, d in sorted(diffs, key=lambda x: -abs(x[3])):
            sign = "+" if d >= 0 else ""
            print(f"  {k:<25} {b:>6} {a:>6} {sign}{d:>5}")
    else:
        print("\n분포 변화 없음")

    if anomalies:
        print(f"\n⚠ 이상치 감지 ({anomaly_threshold*100:.0f}% 이상 변동) — 수동 검토 권장:")
        for w in anomalies:
            print(w)
    else:
        print(f"\n이상치 없음 (임계값 {anomaly_threshold*100:.0f}%)")

    duration = (datetime.now(KST) - started).total_seconds()
    print(f"\n총 소요: {duration:.1f}s")
    print("=" * 60)

    result["duration_sec"] = duration
    result["ok"] = True
    return result


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="월간 섹터 분류 리프레시 (KOSDAQ 신규 상장 자동 커버)")
    p.add_argument("--dry-run", action="store_true",
                   help="변경 없이 리포트만 생성 (백필·UPDATE 모두 스킵)")
    p.add_argument("--skip-backfill", action="store_true",
                   help="yfinance 백필 생략 (재정규화만 수행)")
    p.add_argument("--skip-renormalize", action="store_true",
                   help="재정규화 생략 (백필만 수행)")
    p.add_argument("--market", choices=("KRX", "KOSPI", "KOSDAQ"), default="KRX",
                   help="대상 시장 (기본 KRX = KOSPI+KOSDAQ)")
    p.add_argument("--limit", type=int, default=None,
                   help="백필 최대 건수 (테스트용)")
    p.add_argument("--anomaly-threshold", type=float, default=0.10,
                   help="이상치 감지 임계값 (기본 0.10 = 10%%)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.market == "KRX":
        markets = ("KOSPI", "KOSDAQ")
    else:
        markets = (args.market,)
    result = run(
        dry_run=args.dry_run,
        skip_backfill=args.skip_backfill,
        skip_renormalize=args.skip_renormalize,
        markets=markets,
        backfill_limit=args.limit,
        anomaly_threshold=args.anomaly_threshold,
    )
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
