"""stock_universe.sector_norm 일괄 재정규화.

P0-A (2026-04-24 개편) 적용 후 기존 DB 레코드의 sector_norm을 새 매핑 규칙으로
재계산. 기존 ticker/asset_name/market/sector_krx/sector_gics/industry 값을 그대로
`normalize_sector()`에 다시 통과시켜 UPDATE한다.

- dry-run: 변경 예정 건수 + 샘플 diff만 출력
- --apply: 실제 UPDATE 수행
- 배치 청크(기본 500) 단위 commit — 중단 시에도 부분 반영 남음

사용:
    python -m tools.renormalize_sectors               # dry-run (기본)
    python -m tools.renormalize_sectors --apply       # 실제 UPDATE
    python -m tools.renormalize_sectors --apply --market KRX
    python -m tools.renormalize_sectors --apply --verbose

P0-A 설계 문서: _docs/_prompts/20260424_prompt.md
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta

from shared.config import AppConfig
from shared.db import get_connection
from shared.logger import get_logger
from shared.sector_mapping import normalize_sector

_log = get_logger("renormalize_sectors")
KST = timezone(timedelta(hours=9))


def _recompute_rows(rows: list[tuple]) -> list[tuple[str, str, str, str]]:
    """각 row에 대해 새 sector_norm 계산. 변경 있는 것만 반환.

    Args:
        rows: (ticker, market, asset_name, sector_krx, sector_gics, industry, current_norm)

    Returns:
        [(ticker, market, old_norm, new_norm), ...]  — 변경 있는 레코드만
    """
    changes: list[tuple[str, str, str, str]] = []
    for ticker, market, asset_name, sector_krx, sector_gics, industry, current in rows:
        new_norm = normalize_sector(
            ticker=ticker,
            asset_name=asset_name,
            market=market,
            sector_krx=sector_krx,
            sector_gics=sector_gics,
            industry=industry,
            warn_on_miss=False,
        )
        old = current or ""
        if new_norm != old:
            changes.append((ticker, market, old, new_norm))
    return changes


def run(apply: bool = False, market_filter: str | None = None, verbose: bool = False,
        sample_n: int = 20, batch: int = 500) -> dict:
    cfg = AppConfig()
    started = datetime.now(KST)

    # ── 1) 대상 row 조회 ─────────────────────────────
    where = ""
    params: list = []
    if market_filter:
        mf = market_filter.upper()
        if mf == "KRX":
            where = "WHERE market IN ('KOSPI','KOSDAQ')"
        elif mf == "US":
            where = "WHERE market IN ('NASDAQ','NYSE')"
        elif mf in ("KOSPI", "KOSDAQ", "NASDAQ", "NYSE"):
            where = "WHERE market = %s"
            params.append(mf)
        else:
            _log.error(f"알 수 없는 --market 값: {market_filter}")
            return {"error": "invalid_market"}

    conn = get_connection(cfg.db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT ticker, market, asset_name, sector_krx, sector_gics, industry, sector_norm
                FROM stock_universe
                {where}
                ORDER BY market, ticker
                """,
                params,
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    _log.info(f"대상 {len(rows)}종목 로드 (market_filter={market_filter or 'ALL'})")

    # ── 2) 재계산 ────────────────────────────────
    changes = _recompute_rows(rows)
    _log.info(f"변경 예정: {len(changes)}/{len(rows)}건 ({len(changes) * 100 // max(len(rows), 1)}%)")

    # ── 3) 분포 변화 요약 ─────────────────────────
    before = Counter(r[6] or "(null)" for r in rows)
    after: Counter[str] = Counter()
    changed_map = {(tk, mk): new for tk, mk, _, new in changes}
    for tk, mk, _name, _skrx, _sgics, _ind, current in rows:
        after[changed_map.get((tk, mk), current or "(null)")] += 1

    print("\n── 분포 변화 (before → after) ──")
    all_sectors = sorted(set(before.keys()) | set(after.keys()))
    print(f"{'sector_norm':<25} {'before':>8} {'after':>8} {'diff':>8}")
    print("-" * 55)
    for s in all_sectors:
        b = before.get(s, 0)
        a = after.get(s, 0)
        if b == 0 and a == 0:
            continue
        diff = a - b
        marker = "  *" if diff != 0 else ""
        diff_str = f"{diff:+d}" if diff != 0 else "0"
        print(f"{s:<25} {b:>8} {a:>8} {diff_str:>8}{marker}")

    # ── 4) 변경 샘플 출력 ─────────────────────────
    if changes:
        print(f"\n── 변경 샘플 (최대 {sample_n}건) ──")
        print(f"{'ticker':<10} {'market':<8} {'old':<20} → {'new':<20}")
        print("-" * 72)
        sample = changes if verbose else changes[:sample_n]
        # 재조회해서 asset_name도 함께 표시
        name_map = {(r[0], r[1]): r[2] for r in rows}
        for tk, mk, old, new in sample:
            nm = name_map.get((tk, mk), "")
            print(f"{tk:<10} {mk:<8} {old or '(null)':<20} → {new:<20} {nm[:30]}")
        if not verbose and len(changes) > sample_n:
            print(f"... 외 {len(changes) - sample_n}건 (--verbose로 전체 확인)")

    # ── 5) UPDATE 수행 (apply) ─────────────────
    if not apply:
        print("\n[dry-run] --apply 추가 시 실제 UPDATE 수행")
        return {
            "scanned": len(rows),
            "changes": len(changes),
            "applied": 0,
            "duration_sec": (datetime.now(KST) - started).total_seconds(),
        }

    if not changes:
        _log.info("변경 건이 없어 UPDATE 생략")
        return {
            "scanned": len(rows),
            "changes": 0,
            "applied": 0,
            "duration_sec": (datetime.now(KST) - started).total_seconds(),
        }

    _log.info(f"UPDATE 시작 (batch={batch})")
    applied = 0
    conn = get_connection(cfg.db)
    try:
        with conn.cursor() as cur:
            for i in range(0, len(changes), batch):
                chunk = changes[i:i + batch]
                # execute_batch 대체 — tuple 전송
                for tk, mk, _old, new in chunk:
                    cur.execute(
                        "UPDATE stock_universe SET sector_norm = %s "
                        "WHERE ticker = %s AND market = %s",
                        (new, tk, mk),
                    )
                    applied += cur.rowcount
                conn.commit()
                _log.info(f"  진행 {i + len(chunk)}/{len(changes)} — 누적 {applied}건 UPDATE")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    duration = (datetime.now(KST) - started).total_seconds()
    _log.info(f"재정규화 완료: {applied}건 UPDATE / {duration:.1f}s")
    return {
        "scanned": len(rows),
        "changes": len(changes),
        "applied": applied,
        "duration_sec": duration,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="stock_universe.sector_norm 일괄 재정규화")
    p.add_argument("--apply", action="store_true",
                   help="실제 UPDATE 수행 (미지정 시 dry-run)")
    p.add_argument("--market", type=str, default=None,
                   help="KRX / US / KOSPI / KOSDAQ / NASDAQ / NYSE (미지정 시 전체)")
    p.add_argument("--verbose", action="store_true",
                   help="변경 샘플을 전부 출력 (기본 20건)")
    p.add_argument("--sample-n", type=int, default=20,
                   help="변경 샘플 출력 건수 (기본 20)")
    p.add_argument("--batch", type=int, default=500,
                   help="UPDATE 배치 청크 크기 (기본 500)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = run(
        apply=args.apply,
        market_filter=args.market,
        verbose=args.verbose,
        sample_n=args.sample_n,
        batch=args.batch,
    )
    print(f"\n결과: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
