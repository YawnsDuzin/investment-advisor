"""워치리스트 분산도 헬스 체크 (Tier 1 #3).

순수 함수 — DB 조회 없이 입력 dict 리스트로 HHI/시장 편향/시총 분포/평균 PER 계산.
라우트는 `compute_watchlist_health()` 만 호출. 테스트 가능 단위로 분리.
"""
from __future__ import annotations

from typing import Optional

KR_MARKETS = {"KOSPI", "KOSDAQ"}
US_MARKETS = {"NASDAQ", "NYSE", "AMEX"}

LARGE_CAP_KRW = 5_000_000_000_000   # 5조
MID_CAP_KRW = 1_000_000_000_000     # 1조


def _classify_cap(market_cap_krw: Optional[int]) -> str:
    if not market_cap_krw or market_cap_krw <= 0:
        return "unknown"
    if market_cap_krw >= LARGE_CAP_KRW:
        return "large"
    if market_cap_krw >= MID_CAP_KRW:
        return "mid"
    return "small"


def _classify_market_group(market: Optional[str]) -> str:
    if not market:
        return "other"
    m = market.upper()
    if m in KR_MARKETS:
        return "kr"
    if m in US_MARKETS:
        return "us"
    return "other"


def compute_watchlist_health(
    rows: list[dict],
    market_per_medians: Optional[dict[str, Optional[float]]] = None,
) -> dict:
    """워치리스트 분산도 헬스 체크.

    Args:
        rows: 각 항목 — {ticker, market, sector_norm, market_cap_krw, per (optional)}
        market_per_medians: 시장별 PER 중앙값. {"KOSPI": 13.5, "NASDAQ": 28.2}.
                            None 또는 결측 → valuation.bench_per/premium_pct = None.

    Returns:
        {count, sector_concentration, market_balance, cap_distribution, valuation, warnings}
        count == 0 이면 모든 metric None, warnings 빈 리스트.
    """
    n = len(rows)
    if n == 0:
        return {
            "count": 0,
            "sector_concentration": None,
            "market_balance": None,
            "cap_distribution": None,
            "valuation": None,
            "warnings": [],
        }

    # ── 섹터 집중도 (HHI = Σ share^2) ─────────────
    sector_count: dict[str, int] = {}
    for r in rows:
        key = (r.get("sector_norm") or "기타").strip() or "기타"
        sector_count[key] = sector_count.get(key, 0) + 1

    sector_breakdown = sorted(
        ({"sector": k, "count": v, "share": round(v / n, 4)} for k, v in sector_count.items()),
        key=lambda x: x["count"],
        reverse=True,
    )
    hhi = round(sum((v / n) ** 2 for v in sector_count.values()), 4)
    top_sector = sector_breakdown[0]
    sector_concentration = {
        "hhi": hhi,
        "top_sector": top_sector["sector"],
        "top_sector_share": top_sector["share"],
        "sector_count": len(sector_count),
        "breakdown": sector_breakdown,
    }

    # ── 시장 편향 (KR vs US) ──────────────────────
    group_count: dict[str, int] = {"kr": 0, "us": 0, "other": 0}
    for r in rows:
        group_count[_classify_market_group(r.get("market"))] += 1
    market_balance = {
        "kr_count": group_count["kr"],
        "us_count": group_count["us"],
        "other_count": group_count["other"],
        "kr_share": round(group_count["kr"] / n, 4),
        "us_share": round(group_count["us"] / n, 4),
    }

    # ── 시총 분포 ──────────────────────────────────
    cap_count: dict[str, int] = {"large": 0, "mid": 0, "small": 0, "unknown": 0}
    for r in rows:
        cap_count[_classify_cap(r.get("market_cap_krw"))] += 1
    cap_distribution = {
        "large_share": round(cap_count["large"] / n, 4),
        "mid_share": round(cap_count["mid"] / n, 4),
        "small_share": round(cap_count["small"] / n, 4),
        "unknown_share": round(cap_count["unknown"] / n, 4),
        "breakdown": cap_count,
    }

    # ── 밸류에이션 (평균 PER vs 시장별 PER 중앙값 가중평균) ──
    pers = [float(r["per"]) for r in rows if r.get("per") is not None and float(r["per"]) > 0]
    valuation: Optional[dict] = None
    if pers:
        avg_per = round(sum(pers) / len(pers), 2)
        bench_pieces = []
        if market_per_medians:
            for r in rows:
                m = (r.get("market") or "").upper()
                med = market_per_medians.get(m)
                if med:
                    bench_pieces.append(float(med))
        bench_per = round(sum(bench_pieces) / len(bench_pieces), 2) if bench_pieces else None
        premium_pct = (
            round((avg_per - bench_per) / bench_per * 100, 1)
            if bench_per
            else None
        )
        valuation = {
            "avg_per": avg_per,
            "per_count": len(pers),
            "per_coverage": round(len(pers) / n, 4),
            "bench_per": bench_per,
            "premium_pct": premium_pct,
        }

    # ── 경고 메시지 (사용자가 와닿게) ──────────────
    warnings: list[str] = []
    if sector_concentration["top_sector_share"] >= 0.5:
        warnings.append(
            f"{sector_concentration['top_sector']} 비중 "
            f"{int(sector_concentration['top_sector_share'] * 100)}% — "
            "단일 섹터 편중, 매크로 충격에 취약"
        )
    if n >= 3 and market_balance["kr_share"] >= 0.9:
        warnings.append("KR 시장 90% 이상 — 통화 분산 부족")
    elif n >= 3 and market_balance["us_share"] >= 0.9:
        warnings.append("US 시장 90% 이상 — 통화 분산 부족")
    if cap_distribution["small_share"] >= 0.6 and n >= 3:
        warnings.append(
            f"소형주 {int(cap_distribution['small_share'] * 100)}% — "
            "유동성·변동성 리스크 점검 필요"
        )
    if (
        valuation
        and valuation.get("premium_pct") is not None
        and valuation["premium_pct"] >= 30
    ):
        warnings.append(
            f"평균 PER {valuation['avg_per']}x (시장 대비 +{int(valuation['premium_pct'])}%) — 고밸류 편향"
        )

    return {
        "count": n,
        "sector_concentration": sector_concentration,
        "market_balance": market_balance,
        "cap_distribution": cap_distribution,
        "valuation": valuation,
        "warnings": warnings,
    }
