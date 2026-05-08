"""시장 체온계 (Tier 1 #2).

KOSPI 중심 regime 스냅샷에서 0(빙하) ~ 100(과열) 단일 게이지를 산출.
산식 파라미터는 `MarketTemperatureConfig` 로 분리되어 분포 데이터 누적 후 튜닝 가능.

산식 (각 지표 0~25점, 합 0~100):
  1. trend (추세): KOSPI above_200ma=True → 25 / False → drawdown 기반 0~12.5 점진 강하
  2. breadth (시장폭): KRX 20일 상승 종목 비율 — 50% baseline=25점, 0%→0, 100%→25 (clamp)
  3. calm (저변동): KOSPI vol60_pct — 1.0% 이하 → 25점, 3.0% 이상 → 0
  4. momentum (모멘텀): KOSPI 1m 수익률 — +5%→25, -5%→0, 0%→12.5

지표 결측 → 그 지표는 12.5점(중립) 으로 처리. 최소 1개 지표라도 있으면 결과 반환.
모든 indices 결측 → None.

함수는 순수 — DB 미사용. 입력 dict 만으로 결정. 테스트 용이.

튜닝 워크플로우:
  1. `tools/backfill_temperature.py` 로 과거 regime 스냅샷에 대해 소급 계산
  2. 분포 리포트로 dead 지표 / 편향 진단
  3. `MarketTemperatureConfig` 인스턴스를 조정해 재계산 → 분포 비교
  4. 만족스러우면 코드 default 갱신 + 운영기 재배포
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ── 라벨 임계 ─────────────────────────────────────
# (lower_bound, label) — 큰 값부터. 첫 매치 적용.
_DEFAULT_BRACKETS: tuple[tuple[int, str], ...] = (
    (75, "과열"),
    (60, "강세"),
    (40, "중립"),
    (25, "약세"),
    (0, "빙하"),
)


@dataclass(frozen=True)
class MarketTemperatureConfig:
    """체온계 산식 파라미터.

    `frozen=True` 로 누구도 실수로 mutate 못 함. 튜닝 시 새 인스턴스 생성.
    기본값 = 1차 산식 (분포 누적 전).
    """
    # ── trend ──
    trend_max: float = 25.0
    trend_neutral: float = 12.5
    trend_below_no_dd: float = 6.0  # above_200ma=False·dd 결측 → 보수적 약세
    trend_dd_floor_pct: float = -30.0  # dd 가 이 값일 때 trend = 0

    # ── breadth ──
    breadth_max: float = 25.0
    breadth_neutral: float = 12.5
    breadth_baseline_pct: float = 50.0  # 50% 상승 = 만점 (50/2=25)
    # breadth = breadth_max * (breadth_pct / breadth_baseline_pct), clamp [0, breadth_max]

    # ── calm ──
    calm_max: float = 25.0
    calm_neutral: float = 12.5
    calm_vol_low_pct: float = 1.0  # 이하면 만점
    calm_vol_high_pct: float = 3.0  # 이상이면 0

    # ── momentum ──
    momentum_max: float = 25.0
    momentum_neutral: float = 12.5
    momentum_pct_range: float = 5.0  # ±N% 바깥 clamp; +N% → momentum_max, -N% → 0

    # ── label ──
    brackets: tuple[tuple[int, str], ...] = field(default_factory=lambda: _DEFAULT_BRACKETS)


DEFAULT_CONFIG = MarketTemperatureConfig()


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _trend_score(kospi: dict, cfg: MarketTemperatureConfig = DEFAULT_CONFIG) -> float:
    """추세 점수. above_200ma + drawdown 보정."""
    above = kospi.get("above_200ma")
    if above is True:
        return cfg.trend_max
    if above is False:
        dd = kospi.get("drawdown_from_52w_high_pct")
        if dd is None:
            return cfg.trend_below_no_dd
        # dd 는 음수 (예: -15.0). 0 → trend_neutral, dd_floor → 0.
        floor = cfg.trend_dd_floor_pct  # 음수
        if floor == 0:
            return cfg.trend_neutral
        ratio = float(dd) / floor  # dd=floor 면 1, dd=0 이면 0
        score = cfg.trend_neutral * (1.0 - _clamp(ratio, 0.0, 1.0))
        return _clamp(score, 0.0, cfg.trend_neutral)
    return cfg.trend_neutral  # above_200ma 키 부재


def _breadth_score(snap: dict, cfg: MarketTemperatureConfig = DEFAULT_CONFIG) -> float:
    """시장폭 점수. KRX 20일 상승 종목 비율 기반."""
    bv = snap.get("breadth_kr_pct")
    if bv is None:
        return cfg.breadth_neutral
    # regime.py 가 0~100 (퍼센트) 또는 0~1 둘 중 하나로 줄 수 있음 — 양쪽 처리
    f = float(bv)
    if 0.0 <= f <= 1.0:
        f *= 100.0
    # baseline_pct 까지 비례, 그 이상은 clamp
    score = cfg.breadth_max * (f / cfg.breadth_baseline_pct) if cfg.breadth_baseline_pct > 0 else 0.0
    return _clamp(score, 0.0, cfg.breadth_max)


def _calm_score(kospi: dict, cfg: MarketTemperatureConfig = DEFAULT_CONFIG) -> float:
    """저변동 점수. vol_low 이하 → 만점, vol_high 이상 → 0."""
    vol = kospi.get("vol60_pct")
    if vol is None:
        return cfg.calm_neutral
    v = float(vol)
    if v <= cfg.calm_vol_low_pct:
        return cfg.calm_max
    if v >= cfg.calm_vol_high_pct:
        return 0.0
    span = cfg.calm_vol_high_pct - cfg.calm_vol_low_pct
    if span <= 0:
        return cfg.calm_neutral
    score = cfg.calm_max - (v - cfg.calm_vol_low_pct) / span * cfg.calm_max
    return _clamp(score, 0.0, cfg.calm_max)


def _momentum_score(kospi: dict, cfg: MarketTemperatureConfig = DEFAULT_CONFIG) -> float:
    """모멘텀 점수. KOSPI 1m 수익률 기반."""
    r1m = kospi.get("return_1m_pct")
    if r1m is None:
        return cfg.momentum_neutral
    if cfg.momentum_pct_range <= 0:
        return cfg.momentum_neutral
    # +range% → momentum_max, -range% → 0, 0% → neutral. 선형.
    half_max = cfg.momentum_max / 2.0
    score = cfg.momentum_neutral + float(r1m) * (half_max / cfg.momentum_pct_range)
    return _clamp(score, 0.0, cfg.momentum_max)


def compute_temperature(
    regime_snapshot: Optional[dict],
    cfg: MarketTemperatureConfig = DEFAULT_CONFIG,
) -> Optional[int]:
    """0~100 시장 체온계.

    Args:
        regime_snapshot: `analyzer.regime.compute_regime()` 의 결과 dict.
                         keys: indices.{KOSPI,KOSDAQ,SP500,NDX100}, breadth_kr_pct, ...
        cfg: 산식 파라미터 (튜닝용 — 기본은 DEFAULT_CONFIG).

    Returns:
        int 0~100. KOSPI 데이터 결측 시 None.
    """
    breakdown = compute_breakdown(regime_snapshot, cfg)
    if breakdown is None:
        return None
    return breakdown["total"]


def compute_breakdown(
    regime_snapshot: Optional[dict],
    cfg: MarketTemperatureConfig = DEFAULT_CONFIG,
) -> Optional[dict]:
    """sub-score 별 점수 + 입력값 + 합계 dict (진단·UI 노출용).

    Returns:
        {
            "total": int 0~100,
            "label": "강세",
            "scores": {"trend": 25.0, "breadth": 22.5, "calm": 18.7, "momentum": 12.5},
            "inputs": {
                "above_200ma": True,
                "drawdown_from_52w_high_pct": -3.2,
                "breadth_kr_pct": 0.45,
                "vol60_pct": 1.2,
                "return_1m_pct": 0.8,
            },
        }
        KOSPI 결측 시 None.
    """
    if not regime_snapshot:
        return None
    indices = regime_snapshot.get("indices") or {}
    kospi = indices.get("KOSPI")
    if kospi is None or not isinstance(kospi, dict):
        return None

    scores = {
        "trend": round(_trend_score(kospi, cfg), 2),
        "breadth": round(_breadth_score(regime_snapshot, cfg), 2),
        "calm": round(_calm_score(kospi, cfg), 2),
        "momentum": round(_momentum_score(kospi, cfg), 2),
    }
    total = int(round(_clamp(sum(scores.values()), 0.0, 100.0)))
    return {
        "total": total,
        "label": label_for_temperature(total, cfg),
        "scores": scores,
        "inputs": {
            "above_200ma": kospi.get("above_200ma"),
            "drawdown_from_52w_high_pct": kospi.get("drawdown_from_52w_high_pct"),
            "breadth_kr_pct": regime_snapshot.get("breadth_kr_pct"),
            "vol60_pct": kospi.get("vol60_pct"),
            "return_1m_pct": kospi.get("return_1m_pct"),
        },
    }


def label_for_temperature(
    t: Optional[int],
    cfg: MarketTemperatureConfig = DEFAULT_CONFIG,
) -> str:
    """체온계 → 한국어 라벨 (UI 노출용)."""
    if t is None:
        return "데이터 부족"
    for lower, name in cfg.brackets:
        if t >= lower:
            return name
    return "데이터 부족"
