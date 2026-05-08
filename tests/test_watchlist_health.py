"""api/watchlist_health.py — 분산도 헬스 계산 단위 테스트 (Tier 1 #3).

순수 함수 → DB/SDK mock 불필요. conftest.py 가 psycopg2 만 mock 처리.
"""
from __future__ import annotations

from api.watchlist_health import (
    compute_watchlist_health,
    _classify_cap,
    _classify_market_group,
)


def test_empty_watchlist_returns_zero_count():
    out = compute_watchlist_health([])
    assert out["count"] == 0
    assert out["sector_concentration"] is None
    assert out["market_balance"] is None
    assert out["valuation"] is None
    assert out["warnings"] == []


def test_classify_cap_buckets():
    assert _classify_cap(None) == "unknown"
    assert _classify_cap(0) == "unknown"
    assert _classify_cap(-1) == "unknown"
    assert _classify_cap(500_000_000_000) == "small"        # 5천억
    assert _classify_cap(1_500_000_000_000) == "mid"        # 1.5조
    assert _classify_cap(8_000_000_000_000) == "large"      # 8조


def test_classify_market_group():
    assert _classify_market_group("KOSPI") == "kr"
    assert _classify_market_group("kospi") == "kr"  # 소문자도 처리
    assert _classify_market_group("KOSDAQ") == "kr"
    assert _classify_market_group("NASDAQ") == "us"
    assert _classify_market_group("NYSE") == "us"
    assert _classify_market_group("LSE") == "other"
    assert _classify_market_group(None) == "other"
    assert _classify_market_group("") == "other"


def test_sector_concentration_hhi_single_sector():
    """모두 같은 섹터 → HHI = 1.0, top_share = 1.0, 경고 발생."""
    rows = [
        {"ticker": "A", "market": "KOSPI", "sector_norm": "반도체", "market_cap_krw": 10_000_000_000_000},
        {"ticker": "B", "market": "KOSPI", "sector_norm": "반도체", "market_cap_krw": 10_000_000_000_000},
        {"ticker": "C", "market": "KOSPI", "sector_norm": "반도체", "market_cap_krw": 10_000_000_000_000},
    ]
    out = compute_watchlist_health(rows)
    sc = out["sector_concentration"]
    assert sc["hhi"] == 1.0
    assert sc["top_sector"] == "반도체"
    assert sc["top_sector_share"] == 1.0
    assert sc["sector_count"] == 1
    # KR 100% + 단일 섹터 100% → 두 경고 모두
    assert any("반도체" in w for w in out["warnings"])
    assert any("KR 시장" in w for w in out["warnings"])


def test_sector_concentration_hhi_balanced():
    """3개 섹터 균등 → HHI ≈ 0.33, 경고 없음."""
    rows = [
        {"ticker": "A", "market": "KOSPI", "sector_norm": "반도체", "market_cap_krw": 10_000_000_000_000},
        {"ticker": "B", "market": "NASDAQ", "sector_norm": "에너지", "market_cap_krw": 10_000_000_000_000},
        {"ticker": "C", "market": "NYSE", "sector_norm": "헬스케어", "market_cap_krw": 10_000_000_000_000},
    ]
    out = compute_watchlist_health(rows)
    sc = out["sector_concentration"]
    assert 0.32 < sc["hhi"] < 0.34
    assert sc["sector_count"] == 3
    # 각 섹터 33% → 단일 섹터 경고 없음
    assert not any("편중" in w for w in out["warnings"])


def test_sector_normalization_blank_to_others():
    """sector_norm 결측 → '기타' 로 그룹핑."""
    rows = [
        {"ticker": "A", "market": "KOSPI", "sector_norm": None, "market_cap_krw": 1_500_000_000_000},
        {"ticker": "B", "market": "KOSPI", "sector_norm": "", "market_cap_krw": 1_500_000_000_000},
        {"ticker": "C", "market": "KOSPI", "sector_norm": "  ", "market_cap_krw": 1_500_000_000_000},
    ]
    out = compute_watchlist_health(rows)
    assert out["sector_concentration"]["top_sector"] == "기타"
    assert out["sector_concentration"]["sector_count"] == 1


def test_market_balance_kr_dominant_warning():
    """KR 100% (n=3 이상) → 통화 분산 경고."""
    rows = [
        {"ticker": "A", "market": "KOSPI", "sector_norm": "반도체", "market_cap_krw": 10_000_000_000_000},
        {"ticker": "B", "market": "KOSDAQ", "sector_norm": "에너지", "market_cap_krw": 1_500_000_000_000},
        {"ticker": "C", "market": "KOSPI", "sector_norm": "헬스케어", "market_cap_krw": 1_500_000_000_000},
    ]
    out = compute_watchlist_health(rows)
    mb = out["market_balance"]
    assert mb["kr_count"] == 3
    assert mb["us_count"] == 0
    assert mb["kr_share"] == 1.0
    assert any("KR 시장" in w for w in out["warnings"])


def test_market_balance_below_threshold_no_warning():
    """n=2 → KR 100% 여도 경고 없음 (작은 표본 보호)."""
    rows = [
        {"ticker": "A", "market": "KOSPI", "sector_norm": "반도체", "market_cap_krw": 10_000_000_000_000},
        {"ticker": "B", "market": "KOSPI", "sector_norm": "에너지", "market_cap_krw": 10_000_000_000_000},
    ]
    out = compute_watchlist_health(rows)
    assert not any("통화 분산" in w for w in out["warnings"])


def test_valuation_premium_warning():
    """평균 PER 28 vs 시장 14 → +100% premium → 경고."""
    rows = [
        {"ticker": "A", "market": "KOSPI", "sector_norm": "반도체", "market_cap_krw": 10_000_000_000_000, "per": 30.0},
        {"ticker": "B", "market": "KOSPI", "sector_norm": "에너지", "market_cap_krw": 10_000_000_000_000, "per": 26.0},
    ]
    medians = {"KOSPI": 14.0}
    out = compute_watchlist_health(rows, medians)
    vl = out["valuation"]
    assert vl["avg_per"] == 28.0
    assert vl["bench_per"] == 14.0
    assert vl["premium_pct"] == 100.0
    assert any("PER" in w and "고밸류" in w for w in out["warnings"])


def test_valuation_no_bench_means_no_premium():
    """시장 medians 결측 → premium_pct=None, 경고 없음."""
    rows = [
        {"ticker": "A", "market": "KOSPI", "sector_norm": "반도체", "market_cap_krw": 10_000_000_000_000, "per": 30.0},
    ]
    out = compute_watchlist_health(rows, {})
    vl = out["valuation"]
    assert vl["avg_per"] == 30.0
    assert vl["bench_per"] is None
    assert vl["premium_pct"] is None
    assert not any("고밸류" in w for w in out["warnings"])


def test_valuation_skips_invalid_per():
    """PER NULL/0/음수 무시. coverage 분모는 전체 종목 수."""
    rows = [
        {"ticker": "A", "market": "KOSPI", "sector_norm": "반도체", "market_cap_krw": 10_000_000_000_000, "per": None},
        {"ticker": "B", "market": "KOSPI", "sector_norm": "에너지", "market_cap_krw": 10_000_000_000_000, "per": 0},
        {"ticker": "C", "market": "KOSPI", "sector_norm": "헬스케어", "market_cap_krw": 10_000_000_000_000, "per": -5},
        {"ticker": "D", "market": "KOSPI", "sector_norm": "유틸리티", "market_cap_krw": 10_000_000_000_000, "per": 20.0},
    ]
    out = compute_watchlist_health(rows, {"KOSPI": 14.0})
    vl = out["valuation"]
    assert vl["per_count"] == 1
    assert vl["per_coverage"] == 0.25
    assert vl["avg_per"] == 20.0


def test_cap_distribution_small_dominant_warning():
    """소형주 비중 ≥60% (n≥3) → 유동성 경고."""
    rows = [
        {"ticker": "A", "market": "KOSPI", "sector_norm": "반도체", "market_cap_krw": 100_000_000_000},   # 소
        {"ticker": "B", "market": "NASDAQ", "sector_norm": "에너지", "market_cap_krw": 200_000_000_000},  # 소
        {"ticker": "C", "market": "NYSE", "sector_norm": "헬스케어", "market_cap_krw": 50_000_000_000_000}, # 대
    ]
    out = compute_watchlist_health(rows)
    cd = out["cap_distribution"]
    assert cd["small_share"] > 0.6
    assert any("소형주" in w for w in out["warnings"])


def test_full_payload_structure():
    """반환 구조 — 라우트 응답·템플릿 호환 키 검증."""
    rows = [
        {"ticker": "A", "market": "KOSPI", "sector_norm": "반도체", "market_cap_krw": 10_000_000_000_000, "per": 15.0},
        {"ticker": "B", "market": "NASDAQ", "sector_norm": "에너지", "market_cap_krw": 10_000_000_000_000, "per": 25.0},
    ]
    out = compute_watchlist_health(rows, {"KOSPI": 14.0, "NASDAQ": 28.0})
    assert set(out.keys()) >= {
        "count",
        "sector_concentration",
        "market_balance",
        "cap_distribution",
        "valuation",
        "warnings",
    }
    assert out["count"] == 2
    assert "breakdown" in out["sector_concentration"]
    assert "kr_share" in out["market_balance"]
    assert "large_share" in out["cap_distribution"]
