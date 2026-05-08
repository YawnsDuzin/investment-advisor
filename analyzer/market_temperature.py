"""시장 체온계 (Tier 1 #2).

KOSPI 중심 regime 스냅샷에서 0(빙하) ~ 100(과열) 단일 게이지를 산출.
산식은 추후 분포 보고 튜닝하므로 보수적·범용적으로 시작.

산식 (각 지표 0~25점, 합 0~100):
  1. trend (추세): KOSPI above_200ma=True → 25 / False → drawdown 기반 0~12.5 점진 강하
  2. breadth (시장폭): KRX 20일 상승 종목 비율 — 50% baseline=25점, 0%→0, 100%→25 (clamp)
  3. calm (저변동): KOSPI vol60_pct — 1.0% 이하 → 25점, 3.0% 이상 → 0
  4. momentum (모멘텀): KOSPI 1m 수익률 — +5%→25, -5%→0, 0%→12.5

지표 결측 → 그 지표는 12.5점(중립) 으로 처리. 최소 1개 지표라도 있으면 결과 반환.
모든 indices 결측 → None.

함수는 순수 — DB 미사용. 입력 dict 만으로 결정. 테스트 용이.
"""
from __future__ import annotations

from typing import Optional


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _trend_score(kospi: dict) -> float:
    """추세 점수 (0~25). above_200ma + drawdown 보정."""
    above = kospi.get("above_200ma")
    if above is True:
        return 25.0
    if above is False:
        dd = kospi.get("drawdown_from_52w_high_pct")
        if dd is None:
            return 6.0  # 200MA 아래·낙폭 모름 → 보수적 약세
        # dd 는 음수 (예: -15.0). 0 → 12.5, -30% → 0.
        # 산식: 12.5 + dd / 30 * 12.5  → -30 일 때 0, 0 일 때 12.5
        return _clamp(12.5 + float(dd) / 30.0 * 12.5, 0.0, 12.5)
    return 12.5  # 데이터 없음 → 중립


def _breadth_score(snap: dict) -> float:
    """시장폭 점수 (0~25). KRX 20일 상승 종목 비율 기반."""
    bv = snap.get("breadth_kr_pct")
    if bv is None:
        return 12.5
    # regime.py 가 0~100 (퍼센트) 또는 0~1 둘 중 하나로 줄 수 있음 — 양쪽 처리
    f = float(bv)
    if 0.0 <= f <= 1.0:
        f *= 100.0
    # 50% baseline = 25점 (전부 가산), 0% → 0, 100% → 50 → clamp 25
    return _clamp(f / 2.0, 0.0, 25.0)


def _calm_score(kospi: dict) -> float:
    """저변동 점수 (0~25). vol60 1.0 이하 → 25, 3.0 이상 → 0.

    Note: 저변동을 "안정 = 가산" 으로 해석 (체온계 = 시장 컨디션 양호도).
    과열/탐욕 별도 지표는 향후 추가 가능.
    """
    vol = kospi.get("vol60_pct")
    if vol is None:
        return 12.5
    v = float(vol)
    if v <= 1.0:
        return 25.0
    if v >= 3.0:
        return 0.0
    # 1~3 사이 선형 — 1.0 → 25, 3.0 → 0
    return _clamp(25.0 - (v - 1.0) / 2.0 * 25.0, 0.0, 25.0)


def _momentum_score(kospi: dict) -> float:
    """모멘텀 점수 (0~25). KOSPI 1m 수익률 기반."""
    r1m = kospi.get("return_1m_pct")
    if r1m is None:
        return 12.5
    # +5%→25, -5%→0, 0%→12.5. 선형, ±5% 바깥은 clamp.
    score = 12.5 + float(r1m) * 2.5
    return _clamp(score, 0.0, 25.0)


def compute_temperature(regime_snapshot: Optional[dict]) -> Optional[int]:
    """0~100 시장 체온계.

    Args:
        regime_snapshot: `analyzer.regime.compute_regime()` 의 결과 dict.
                         keys: indices.{KOSPI,KOSDAQ,SP500,NDX100}, breadth_kr_pct, ...

    Returns:
        int 0~100. KOSPI 데이터 결측 시 None.
    """
    if not regime_snapshot:
        return None
    indices = regime_snapshot.get("indices") or {}
    kospi = indices.get("KOSPI")
    if kospi is None:  # key 자체 부재 → 산출 포기. 빈 dict 는 중립 점수로 진행.
        return None
    if not isinstance(kospi, dict):
        return None

    total = (
        _trend_score(kospi)
        + _breadth_score(regime_snapshot)
        + _calm_score(kospi)
        + _momentum_score(kospi)
    )
    return int(round(_clamp(total, 0.0, 100.0)))


def label_for_temperature(t: Optional[int]) -> str:
    """체온계 → 한국어 라벨 (UI 노출용)."""
    if t is None:
        return "데이터 부족"
    if t >= 75:
        return "과열"
    if t >= 60:
        return "강세"
    if t >= 40:
        return "중립"
    if t >= 25:
        return "약세"
    return "빙하"
