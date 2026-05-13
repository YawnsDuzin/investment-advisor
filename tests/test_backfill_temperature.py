"""tools/backfill_temperature.py — 분포 리포트·소급 계산 단위 테스트.

순수 헬퍼(report_distribution / compute_breakdown / format_report) 검증.
DB I/O 함수(fetch_briefings/update_temperature) 는 통합 테스트 별도.
"""
from __future__ import annotations

from analyzer.market_temperature import (
    DEFAULT_CONFIG,
    MarketTemperatureConfig,
    compute_breakdown,
    compute_temperature,
)
from tools.backfill_temperature import (
    report_distribution,
    format_report,
)


# ── compute_breakdown ────────────────────────────


def test_breakdown_returns_none_for_empty_snapshot():
    assert compute_breakdown(None) is None
    assert compute_breakdown({}) is None


def test_breakdown_returns_none_when_kospi_missing():
    assert compute_breakdown({"indices": {"SP500": {}}}) is None


def test_breakdown_structure_is_complete():
    snap = {
        "indices": {"KOSPI": {
            "above_200ma": True,
            "vol60_pct": 1.2,
            "return_1m_pct": 2.0,
        }},
        "breadth_kr_pct": 0.6,
    }
    bd = compute_breakdown(snap)
    assert bd is not None
    assert set(bd.keys()) == {"total", "label", "scores", "inputs"}
    assert set(bd["scores"].keys()) == {"trend", "breadth", "calm", "momentum"}
    assert set(bd["inputs"].keys()) == {
        "above_200ma", "drawdown_from_52w_high_pct",
        "breadth_kr_pct", "vol60_pct", "return_1m_pct",
    }
    # total 은 sub-score 합과 일치 (반올림)
    assert abs(sum(bd["scores"].values()) - bd["total"]) < 1.0


def test_breakdown_total_matches_compute_temperature():
    """compute_breakdown 의 total 은 compute_temperature 와 동일."""
    snap = {
        "indices": {"KOSPI": {
            "above_200ma": False,
            "drawdown_from_52w_high_pct": -15.0,
            "vol60_pct": 2.0,
            "return_1m_pct": -2.0,
        }},
        "breadth_kr_pct": 0.3,
    }
    assert compute_breakdown(snap)["total"] == compute_temperature(snap)


# ── Config 파라미터화 ─────────────────────────────


def test_custom_config_changes_score():
    """cfg.momentum_pct_range 를 좁히면 같은 r1m 으로도 다른 점수."""
    snap = {
        "indices": {"KOSPI": {
            "above_200ma": True,
            "vol60_pct": 1.0,
            "return_1m_pct": 2.5,
        }},
        "breadth_kr_pct": 0.5,
    }
    default = compute_temperature(snap)
    tighter = compute_temperature(
        snap,
        MarketTemperatureConfig(momentum_pct_range=2.5),  # +2.5% → max 25
    )
    assert tighter > default


def test_custom_brackets_change_label():
    """라벨 임계 변경 시 같은 점수도 다른 라벨."""
    snap = {
        "indices": {"KOSPI": {
            "above_200ma": True,
            "vol60_pct": 1.0,
            "return_1m_pct": 2.0,
        }},
        "breadth_kr_pct": 0.6,
    }
    bd_default = compute_breakdown(snap)
    # 임계를 50/30/15/5 로 낮추면 같은 점수가 더 강한 라벨
    cfg2 = MarketTemperatureConfig(
        brackets=((50, "과열"), (30, "강세"), (15, "중립"), (5, "약세"), (0, "빙하")),
    )
    bd_strict = compute_breakdown(snap, cfg2)
    assert bd_default["total"] == bd_strict["total"]
    # default 라벨이 "강세" 정도라면 strict 에선 "과열"
    if bd_default["total"] >= 50:
        assert bd_strict["label"] == "과열"


# ── report_distribution ───────────────────────────


def test_report_empty_returns_zero_n():
    out = report_distribution([])
    assert out["n"] == 0
    assert out["mean"] is None
    assert out["label_counts"] == {}


def test_report_aggregates_basic_stats():
    breakdowns = [
        {"total": 30, "label": "약세",
         "scores": {"trend": 5.0, "breadth": 10.0, "calm": 10.0, "momentum": 5.0},
         "inputs": {"above_200ma": False, "drawdown_from_52w_high_pct": -10.0,
                    "breadth_kr_pct": 0.4, "vol60_pct": 1.5, "return_1m_pct": -2.0}},
        {"total": 65, "label": "강세",
         "scores": {"trend": 25.0, "breadth": 15.0, "calm": 15.0, "momentum": 10.0},
         "inputs": {"above_200ma": True, "drawdown_from_52w_high_pct": -3.0,
                    "breadth_kr_pct": 0.6, "vol60_pct": 1.2, "return_1m_pct": 1.0}},
        {"total": 50, "label": "중립",
         "scores": {"trend": 12.5, "breadth": 12.5, "calm": 12.5, "momentum": 12.5},
         "inputs": {"above_200ma": None, "drawdown_from_52w_high_pct": None,
                    "breadth_kr_pct": None, "vol60_pct": None, "return_1m_pct": None}},
    ]
    out = report_distribution(breakdowns)
    assert out["n"] == 3
    assert out["min"] == 30
    assert out["max"] == 65
    assert out["mean"] == round((30 + 65 + 50) / 3, 2)
    assert out["median"] == 50
    assert out["label_counts"] == {"약세": 1, "강세": 1, "중립": 1}
    # sub-score 평균
    assert out["sub_score_means"]["trend"] == round((5 + 25 + 12.5) / 3, 2)
    # input 결측률 — 2/3 만 채움
    assert out["input_coverage"]["above_200ma"] == round(2 / 3, 3)


def test_report_all_neutral_flags_dead_indicator():
    """모든 row 의 trend 가 12.5 (중립) → dead 지표 의심 신호."""
    breakdowns = [
        {"total": 50, "label": "중립",
         "scores": {"trend": 12.5, "breadth": 12.5, "calm": 12.5, "momentum": 12.5},
         "inputs": {"above_200ma": None, "drawdown_from_52w_high_pct": None,
                    "breadth_kr_pct": None, "vol60_pct": None, "return_1m_pct": None}},
    ] * 5
    out = report_distribution(breakdowns)
    assert out["sub_score_means"]["trend"] == 12.5


# ── format_report ─────────────────────────────────


def test_format_report_empty():
    out = format_report({"n": 0}, DEFAULT_CONFIG.brackets)
    assert "0건" in out


def test_format_report_includes_label_bars():
    breakdowns = [
        {"total": 30, "label": "약세",
         "scores": {"trend": 5.0, "breadth": 10.0, "calm": 10.0, "momentum": 5.0},
         "inputs": {k: 1.0 for k in (
             "above_200ma", "drawdown_from_52w_high_pct",
             "breadth_kr_pct", "vol60_pct", "return_1m_pct")}},
    ]
    out = format_report(report_distribution(breakdowns), DEFAULT_CONFIG.brackets)
    assert "약세" in out
    assert "Sub-score" in out
    assert "결측률" in out


def test_format_report_flags_dead_indicators():
    """모든 sub-score 가 12.5 면 dead 경고 마크."""
    breakdowns = [
        {"total": 50, "label": "중립",
         "scores": {"trend": 12.5, "breadth": 12.5, "calm": 12.5, "momentum": 12.5},
         "inputs": {k: None for k in (
             "above_200ma", "drawdown_from_52w_high_pct",
             "breadth_kr_pct", "vol60_pct", "return_1m_pct")}},
    ]
    out = format_report(report_distribution(breakdowns), DEFAULT_CONFIG.brackets)
    assert "dead?" in out  # ⚠ dead? 마크


def test_format_report_flags_low_input_coverage():
    breakdowns = [
        {"total": 50, "label": "중립",
         "scores": {"trend": 12.5, "breadth": 12.5, "calm": 12.5, "momentum": 12.5},
         "inputs": {k: None for k in (
             "above_200ma", "drawdown_from_52w_high_pct",
             "breadth_kr_pct", "vol60_pct", "return_1m_pct")}},
    ]
    out = format_report(report_distribution(breakdowns), DEFAULT_CONFIG.brackets)
    assert "low" in out  # 결측률 100% → ⚠ low
