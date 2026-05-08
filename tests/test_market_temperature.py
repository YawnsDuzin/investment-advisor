"""analyzer/market_temperature.py — 시장 체온계 산식 단위 테스트 (Tier 1 #2)."""
from __future__ import annotations

from analyzer.market_temperature import (
    compute_temperature,
    label_for_temperature,
    _trend_score,
    _breadth_score,
    _calm_score,
    _momentum_score,
)


# ── compute_temperature ──────────────────────────


def test_compute_temperature_returns_none_for_empty_snapshot():
    assert compute_temperature(None) is None
    assert compute_temperature({}) is None


def test_compute_temperature_returns_none_when_kospi_missing():
    snap = {"indices": {"SP500": {"above_200ma": True}}, "breadth_kr_pct": 0.6}
    assert compute_temperature(snap) is None


def test_compute_temperature_strong_bull():
    """추세↑ + 폭↑ + 변동성 낮음 + 모멘텀↑ → 75 이상 (강세/과열)."""
    snap = {
        "indices": {
            "KOSPI": {
                "above_200ma": True,
                "vol60_pct": 0.8,
                "return_1m_pct": 6.0,
            }
        },
        "breadth_kr_pct": 0.7,  # 70% 상승 종목
    }
    t = compute_temperature(snap)
    assert t is not None
    assert t >= 75
    assert label_for_temperature(t) in ("강세", "과열")


def test_compute_temperature_freeze_state():
    """추세↓ + 폭↓ + 변동성↑ + 모멘텀↓ → 25 미만 (빙하 가까이)."""
    snap = {
        "indices": {
            "KOSPI": {
                "above_200ma": False,
                "drawdown_from_52w_high_pct": -30.0,
                "vol60_pct": 3.0,
                "return_1m_pct": -7.0,
            }
        },
        "breadth_kr_pct": 0.05,
    }
    t = compute_temperature(snap)
    assert t is not None
    assert t <= 20
    assert label_for_temperature(t) == "빙하"


def test_compute_temperature_neutral_with_partial_data():
    """모든 지표 결측 (KOSPI 만 있고 빈 dict) → 50 (중립 = 12.5*4)."""
    snap = {
        "indices": {"KOSPI": {}},
    }
    t = compute_temperature(snap)
    assert t == 50  # 4 × 12.5


def test_compute_temperature_clamped_to_unit_range():
    """극단 입력 — 게이지는 [0, 100] 범위 안."""
    snap = {
        "indices": {
            "KOSPI": {
                "above_200ma": True,
                "vol60_pct": 0.0,
                "return_1m_pct": 999.0,
            }
        },
        "breadth_kr_pct": 9999.0,
    }
    t = compute_temperature(snap)
    assert 0 <= t <= 100


# ── _trend_score ─────────────────────────────────


def test_trend_above_200ma_full_score():
    assert _trend_score({"above_200ma": True}) == 25.0


def test_trend_below_200ma_with_no_drawdown_data():
    """200MA 아래·낙폭 모름 → 6.0 (보수적)."""
    assert _trend_score({"above_200ma": False}) == 6.0


def test_trend_below_200ma_drawdown_thirty_percent():
    """drawdown -30% → 0점."""
    s = _trend_score({"above_200ma": False, "drawdown_from_52w_high_pct": -30.0})
    assert s == 0.0


def test_trend_below_200ma_drawdown_zero():
    """drawdown 0% → 12.5 (200MA 아래지만 고점 직전 → 중립)."""
    s = _trend_score({"above_200ma": False, "drawdown_from_52w_high_pct": 0.0})
    assert s == 12.5


def test_trend_unknown_above_returns_neutral():
    """above_200ma 키 자체가 없으면 12.5 (데이터 부족 → 중립)."""
    assert _trend_score({}) == 12.5


# ── _breadth_score ───────────────────────────────


def test_breadth_handles_fraction_input():
    """0.7 (70%) → 25점 (50% 이상 → clamp 25)."""
    assert _breadth_score({"breadth_kr_pct": 0.7}) == 25.0


def test_breadth_handles_percent_input():
    """30 (= 30%) → 15점."""
    assert _breadth_score({"breadth_kr_pct": 30.0}) == 15.0


def test_breadth_zero_fraction_zero_score():
    assert _breadth_score({"breadth_kr_pct": 0.0}) == 0.0


def test_breadth_none_returns_neutral():
    assert _breadth_score({}) == 12.5
    assert _breadth_score({"breadth_kr_pct": None}) == 12.5


# ── _calm_score ──────────────────────────────────


def test_calm_low_vol_full_score():
    assert _calm_score({"vol60_pct": 0.5}) == 25.0
    assert _calm_score({"vol60_pct": 1.0}) == 25.0


def test_calm_high_vol_zero_score():
    assert _calm_score({"vol60_pct": 3.0}) == 0.0
    assert _calm_score({"vol60_pct": 5.0}) == 0.0


def test_calm_mid_vol_linear():
    """vol60 2.0 → (1.0 → 25, 3.0 → 0) 의 중간 = 12.5."""
    assert _calm_score({"vol60_pct": 2.0}) == 12.5


# ── _momentum_score ──────────────────────────────


def test_momentum_strong_positive_full_score():
    assert _momentum_score({"return_1m_pct": 5.0}) == 25.0
    assert _momentum_score({"return_1m_pct": 10.0}) == 25.0  # clamp


def test_momentum_strong_negative_zero_score():
    assert _momentum_score({"return_1m_pct": -5.0}) == 0.0
    assert _momentum_score({"return_1m_pct": -10.0}) == 0.0


def test_momentum_zero_neutral():
    assert _momentum_score({"return_1m_pct": 0.0}) == 12.5


# ── label_for_temperature ────────────────────────


def test_label_brackets():
    assert label_for_temperature(None) == "데이터 부족"
    assert label_for_temperature(80) == "과열"
    assert label_for_temperature(75) == "과열"
    assert label_for_temperature(70) == "강세"
    assert label_for_temperature(60) == "강세"
    assert label_for_temperature(50) == "중립"
    assert label_for_temperature(40) == "중립"
    assert label_for_temperature(30) == "약세"
    assert label_for_temperature(25) == "약세"
    assert label_for_temperature(10) == "빙하"
    assert label_for_temperature(0) == "빙하"
