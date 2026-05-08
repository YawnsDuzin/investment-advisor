"""api/similar_stocks.py — 유사도 거리/벡터 헬퍼 단위 테스트 (Tier 1 #5).

순수 헬퍼 검증. compute_similar 자체는 DB 의존이라 통합 테스트 별도.
"""
from __future__ import annotations

import math

from api.similar_stocks import (
    _vec,
    _euclidean,
    _to_similarity,
    _PCTILE_KEYS,
)


# ── _vec ─────────────────────────────────────────


def test_vec_extracts_six_pctile_keys():
    snap = {
        "r1m_pctile": 0.7, "r3m_pctile": 0.6, "r6m_pctile": 0.5,
        "r12m_pctile": 0.8, "low_vol_pctile": 0.3, "volume_pctile": 0.9,
        "r1m_pct": 5.0,  # 무관한 키
    }
    v = _vec(snap)
    assert len(v) == 6
    assert v == [0.7, 0.6, 0.5, 0.8, 0.3, 0.9]


def test_vec_nulls_imputed_to_neutral_05():
    """None pctile → 0.5 (중립). 결측 종목도 후보 유지."""
    snap = {
        "r1m_pctile": None, "r3m_pctile": 0.6, "r6m_pctile": None,
        "r12m_pctile": 0.8, "low_vol_pctile": None, "volume_pctile": None,
    }
    v = _vec(snap)
    assert v[0] == 0.5
    assert v[1] == 0.6
    assert v[2] == 0.5
    assert v[3] == 0.8
    assert v[4] == 0.5
    assert v[5] == 0.5


def test_vec_empty_dict_all_neutral():
    v = _vec({})
    assert v == [0.5] * 6


# ── _euclidean ───────────────────────────────────


def test_euclidean_zero_distance_for_identical():
    a = [0.5, 0.5, 0.5, 0.5, 0.5, 0.5]
    assert _euclidean(a, a) == 0.0


def test_euclidean_max_distance_at_extremes():
    """[0,...] vs [1,...] → √6."""
    a = [0.0] * 6
    b = [1.0] * 6
    assert math.isclose(_euclidean(a, b), math.sqrt(6.0), rel_tol=1e-9)


def test_euclidean_known_value():
    """단순 검증 — (0.1, 0.2) vs (0.4, 0.6) → √(0.09+0.16) = 0.5."""
    a = [0.1, 0.2, 0.5, 0.5, 0.5, 0.5]
    b = [0.4, 0.6, 0.5, 0.5, 0.5, 0.5]
    assert math.isclose(_euclidean(a, b), 0.5, rel_tol=1e-9)


# ── _to_similarity ───────────────────────────────


def test_similarity_zero_distance_is_one():
    assert _to_similarity(0.0) == 1.0


def test_similarity_max_distance_is_zero():
    """√6 → 0."""
    sim = _to_similarity(math.sqrt(6.0))
    assert sim == 0.0


def test_similarity_clamps_to_unit_range():
    """음수 거리는 발생 안 하지만 가드 — 1 로 clamp."""
    assert _to_similarity(-0.5) == 1.0
    assert _to_similarity(99.0) == 0.0


def test_similarity_monotonic_decreasing_with_distance():
    """가까울수록 sim 1 에 가까움. 단조 감소."""
    sims = [_to_similarity(d) for d in [0.0, 0.5, 1.0, 1.5, 2.0]]
    assert all(sims[i] >= sims[i + 1] for i in range(len(sims) - 1))


# ── 키 동기화 ────────────────────────────────────


def test_pctile_keys_match_factor_engine_output():
    """`_PCTILE_KEYS` 가 factor_engine 의 출력 키와 일치하는지 — drift 방지."""
    expected = {
        "r1m_pctile", "r3m_pctile", "r6m_pctile", "r12m_pctile",
        "low_vol_pctile", "volume_pctile",
    }
    assert set(_PCTILE_KEYS) == expected
