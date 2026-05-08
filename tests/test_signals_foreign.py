"""analyzer/signals.py — 외국인 수급 시그널 헬퍼 단위 테스트 (Tier 1 #1).

순수 헬퍼 (`_is_streak_buy_5d`, `_is_ownership_jump_5d`) 와 라벨 동기화 검증.
detect_foreign_signals 자체는 DB 의존이라 별도 통합 테스트로 운영기에서 검증.
"""
from __future__ import annotations

from analyzer.signals import (
    _is_streak_buy_5d,
    _is_ownership_jump_5d,
    _SIGNAL_LABELS_KR,
)


# ── _is_streak_buy_5d ─────────────────────────────


def test_streak_buy_5d_all_positive():
    """5거래일 모두 순매수 → True."""
    assert _is_streak_buy_5d(rows_5=5, pos_days_5=5) is True


def test_streak_buy_5d_one_negative_breaks_streak():
    """5일 중 1일이라도 순매도 또는 0 → False."""
    assert _is_streak_buy_5d(rows_5=5, pos_days_5=4) is False


def test_streak_buy_5d_insufficient_history():
    """rows_5 < 5 (3일치만 보유 등) → False."""
    assert _is_streak_buy_5d(rows_5=3, pos_days_5=3) is False


def test_streak_buy_5d_none_inputs():
    """결측 입력 → False (보수적)."""
    assert _is_streak_buy_5d(rows_5=None, pos_days_5=5) is False
    assert _is_streak_buy_5d(rows_5=5, pos_days_5=None) is False
    assert _is_streak_buy_5d(rows_5=None, pos_days_5=None) is False


# ── _is_ownership_jump_5d ─────────────────────────


def test_ownership_jump_5d_above_threshold():
    """0.5pp 이상 상승 → True."""
    assert _is_ownership_jump_5d(own_latest=12.5, own_5d_ago=12.0, threshold_pp=0.5) is True
    assert _is_ownership_jump_5d(own_latest=15.0, own_5d_ago=14.0, threshold_pp=0.5) is True


def test_ownership_jump_5d_at_threshold_inclusive():
    """경계값 (정확히 0.5pp) → True (>=)."""
    assert _is_ownership_jump_5d(own_latest=10.5, own_5d_ago=10.0, threshold_pp=0.5) is True


def test_ownership_jump_5d_below_threshold():
    """0.4pp 상승 → False."""
    assert _is_ownership_jump_5d(own_latest=12.4, own_5d_ago=12.0, threshold_pp=0.5) is False


def test_ownership_jump_5d_drop_returns_false():
    """하락 → False (jump 시그널은 상승만)."""
    assert _is_ownership_jump_5d(own_latest=11.5, own_5d_ago=12.0, threshold_pp=0.5) is False


def test_ownership_jump_5d_none_inputs():
    """결측 입력 → False."""
    assert _is_ownership_jump_5d(own_latest=None, own_5d_ago=10.0, threshold_pp=0.5) is False
    assert _is_ownership_jump_5d(own_latest=10.5, own_5d_ago=None, threshold_pp=0.5) is False


def test_ownership_jump_5d_custom_threshold():
    """threshold 가변 — 1.0pp 임계."""
    assert _is_ownership_jump_5d(own_latest=11.0, own_5d_ago=10.0, threshold_pp=1.0) is True
    assert _is_ownership_jump_5d(own_latest=10.9, own_5d_ago=10.0, threshold_pp=1.0) is False


# ── 라벨 동기화 — analyzer/signals.py vs api/routes/signals.py ──


def test_foreign_signal_labels_present_in_analyzer():
    """analyzer 쪽 _SIGNAL_LABELS_KR 에 신규 외국인 타입이 등록되어 있는지."""
    assert "foreign_streak_buy_5d" in _SIGNAL_LABELS_KR
    assert "foreign_ownership_jump_5d" in _SIGNAL_LABELS_KR
    assert _SIGNAL_LABELS_KR["foreign_streak_buy_5d"] == "외국인 5일 연속 순매수"
    assert _SIGNAL_LABELS_KR["foreign_ownership_jump_5d"] == "외국인 지분율 급증"


def test_signal_labels_synced_with_routes():
    """analyzer/signals._SIGNAL_LABELS_KR 와 routes/signals.SIGNAL_LABELS 가
    같은 키 집합을 가지는지 — 한쪽만 추가되어 UI/알림 라벨 불일치 방지."""
    from api.routes.signals import SIGNAL_LABELS as ROUTE_LABELS

    analyzer_keys = set(_SIGNAL_LABELS_KR.keys())
    route_keys = set(ROUTE_LABELS.keys())

    assert analyzer_keys == route_keys, (
        f"라벨 키 불일치: analyzer-only={analyzer_keys - route_keys}, "
        f"route-only={route_keys - analyzer_keys}"
    )
