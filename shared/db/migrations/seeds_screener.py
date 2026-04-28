"""스크리너 시드 프리셋 — 거장 5 + 운영 자동 5.

각 dict 는 screener_presets 테이블 컬럼과 1:1 매핑:
  strategy_key, name, description, persona, persona_summary, markets_supported,
  risk_warning, spec(JSONB)

spec 포맷은 `api/routes/screener.py` 의 `/api/screener/run` 이 받는 UI 포맷과 동일 —
사용자가 카드 클릭 → spec 그대로 SpecBuilder.toDOM(spec) → 즉시 실행 가능.

펀더 v1(PER/PBR/EPS/배당률)만 활용. ROE/부채/성장률 등은 펀더 v2 에서 복원 예정.
v41 시드는 'filters' 배열 포맷이었으나 v43 에서 UI 포맷으로 통일 (UPSERT 멱등).
Spec: _docs/20260427055258_sprint1-design.md §7.2
"""
from __future__ import annotations
import json
from typing import Any


SCREENER_SEED_PRESETS: list[dict[str, Any]] = [
    # ── 거장 5 ─────────────────────────────────────────
    {
        "strategy_key": "buffett",
        "name": "Warren Buffett — 가치 + 수익성",
        "description": "PER ≤ 15, PBR ≤ 2.5, 적자 종목 제외. ROE·부채비율 조건은 펀더 v2 에서 복원.",
        "persona": "Warren Buffett",
        "persona_summary": "장기적 경제적 해자(moat)와 높은 수익성을 가진 우량주를 합리적 가격에 매수.",
        "markets_supported": ["KOSPI", "KOSDAQ", "NASDAQ", "NYSE"],
        "risk_warning": "단기 모멘텀 무시 — 시장 침체기에는 바닥 매수 비용 동반. 거시 악재 시 큰 drawdown 가능.",
        "spec": {
            "max_per": 15.0,
            "max_pbr": 2.5,
            "exclude_negative_eps": True,
            "sort": "market_cap_desc",
        },
    },
    {
        "strategy_key": "lynch",
        "name": "Peter Lynch — 성장 + 밸류",
        "description": "PER ≤ 20 + 1년 수익률 ≥ 15% (성장 모멘텀 근사). PEG 정확 계산은 펀더 v2 에서 복원.",
        "persona": "Peter Lynch",
        "persona_summary": "성장률 대비 저평가된 종목을 찾는 GARP(Growth at Reasonable Price) 전략.",
        "markets_supported": ["KOSPI", "KOSDAQ", "NASDAQ", "NYSE"],
        "risk_warning": "1년 수익률만 보면 단기 과열 종목 혼입 가능. 펀더와 교차 확인 필수.",
        "spec": {
            "max_per": 20.0,
            "exclude_negative_eps": True,
            "return_ranges": {"1y": {"min": 15.0}},
            "sort": "r1y_desc",
        },
    },
    {
        "strategy_key": "graham",
        "name": "Benjamin Graham — 안전마진 (딥밸류)",
        "description": "PBR ≤ 1.5, PER ≤ 12, 배당률 ≥ 2%. 유동비율 조건은 펀더 v2.",
        "persona": "Benjamin Graham",
        "persona_summary": "내재가치 대비 충분한 안전마진을 확보한 깊은 가치주(deep value) 전략.",
        "markets_supported": ["KOSPI", "KOSDAQ", "NASDAQ", "NYSE"],
        "risk_warning": "value trap 위험 — 저PBR 이 정당한 이유(쇠퇴 산업·경영 부실)일 수 있음.",
        "spec": {
            "max_per": 12.0,
            "max_pbr": 1.5,
            "min_dividend_yield_pct": 2.0,
            "exclude_negative_eps": True,
            "sort": "market_cap_desc",
        },
    },
    {
        "strategy_key": "oneil",
        "name": "William O'Neil — CAN SLIM 모멘텀",
        "description": "3개월 수익률 ≥ 20% + 52주 고점 90% 이상. EPS 성장률·거래량 급증은 펀더 v2.",
        "persona": "William O'Neil",
        "persona_summary": "수급·차트·실적 모두 강한 momentum leader 종목 추격 매수.",
        "markets_supported": ["KOSPI", "KOSDAQ", "NASDAQ", "NYSE"],
        "risk_warning": "추세 추종 — 고점 매수 위험. 시장 반락 시 빠른 이탈 규율 필수.",
        "spec": {
            "return_ranges": {"3m": {"min": 20.0}},
            "high_52w_proximity_min": 0.90,
            "sort": "r3m_desc",
        },
    },
    {
        "strategy_key": "greenblatt",
        "name": "Joel Greenblatt — Magic Formula",
        "description": "PER ≤ 11 (earnings yield ≥ 9%) + 시총 1000억 이상. ROIC 조건은 펀더 v2.",
        "persona": "Joel Greenblatt",
        "persona_summary": "고수익률·고자본수익률 종목을 단순 룰로 선별하는 시스템 트레이딩.",
        "markets_supported": ["KOSPI", "KOSDAQ", "NASDAQ", "NYSE"],
        "risk_warning": "단순 정량 룰 — 산업·테마 편향 발생 가능. 분산 투자 필수.",
        "spec": {
            "max_per": 11.0,
            "exclude_negative_eps": True,
            "market_cap_krw": {"min": 100_000_000_000},
            "sort": "market_cap_desc",
        },
    },
    # ── 운영 자동 5 ─────────────────────────────────────
    {
        "strategy_key": "auto_52w_high",
        "name": "52주 신고가 돌파",
        "description": "52주 최고가 대비 98% 이상. 신고가 돌파 모멘텀 추적.",
        "persona": "운영 자동",
        "persona_summary": "최근 52주 고점 근접 종목 — 돌파 매매 후보.",
        "markets_supported": ["KOSPI", "KOSDAQ", "NASDAQ", "NYSE"],
        "risk_warning": "고점 대비 매수 — 단기 반락 시 손실 빠름.",
        "spec": {
            "high_52w_proximity_min": 0.98,
            "sort": "r1m_desc",
        },
    },
    {
        "strategy_key": "auto_volume_spike",
        "name": "거래량 급증",
        "description": "최근 20일 거래량이 60일 평균 대비 3배 이상.",
        "persona": "운영 자동",
        "persona_summary": "갑작스런 수급 변화 — 재료 발생 신호 가능성.",
        "markets_supported": ["KOSPI", "KOSDAQ", "NASDAQ", "NYSE"],
        "risk_warning": "노이즈 다수 — 뉴스·공시와 교차 확인 없으면 휘둘릴 수 있음.",
        "spec": {
            "volume_ratio_min": 3.0,
            "sort": "volume_surge_desc",
        },
    },
    {
        "strategy_key": "auto_foreign_streak",
        "name": "외국인 연속 순매수 (KR)",
        "description": "외국인 수급 streak 데이터는 별 페이지에서 제공. 본 카드는 KR 시장 전체 진입점.",
        "persona": "운영 자동",
        "persona_summary": "외국인 매수 우위 시장 (KR) — 수급 우위 신호.",
        "markets_supported": ["KOSPI", "KOSDAQ"],
        "risk_warning": "외국인 수급 데이터는 별도 페이지에서 확인. 본 카드는 KR 시장 필터만 적용됨.",
        "spec": {
            "markets": ["KOSPI", "KOSDAQ"],
            "sort": "market_cap_desc",
        },
    },
    {
        "strategy_key": "auto_momentum",
        "name": "모멘텀 강세 (1m + 3m)",
        "description": "1개월 ≥ 10%, 3개월 ≥ 20% 동시 만족. 단·중기 모멘텀 동시 강세.",
        "persona": "운영 자동",
        "persona_summary": "단·중기 모멘텀 동시 강세 — 트렌드 follow.",
        "markets_supported": ["KOSPI", "KOSDAQ", "NASDAQ", "NYSE"],
        "risk_warning": "고점 매수 위험. 추세 반전 시 손실 큰 편.",
        "spec": {
            "return_ranges": {"1m": {"min": 10.0}, "3m": {"min": 20.0}},
            "sort": "r3m_desc",
        },
    },
    {
        "strategy_key": "auto_value_yield",
        "name": "저밸류 + 고배당",
        "description": "PBR ≤ 1, 배당률 ≥ 4%. 디펜시브 인컴 전략.",
        "persona": "운영 자동",
        "persona_summary": "방어적 배당주 — 약세장 buffer.",
        "markets_supported": ["KOSPI", "KOSDAQ", "NASDAQ", "NYSE"],
        "risk_warning": "저PBR 이 value trap 일 수 있음. 배당 지속성 확인 필요.",
        "spec": {
            "max_pbr": 1.0,
            "min_dividend_yield_pct": 4.0,
            "sort": "market_cap_desc",
        },
    },
]


def seed_to_sql_values() -> list[tuple]:
    """시드를 INSERT 용 8-tuple list 로 변환.

    각 tuple: (strategy_key, name, description, persona, persona_summary,
              markets_supported, risk_warning, spec_json)

    `is_seed=TRUE` 와 `user_id=NULL` 은 caller(마이그레이션) 가 추가한다 — 이 함수는
    시드 본 데이터만 직렬화하고, INSERT 메타필드는 caller 의 책임으로 분리해
    Sprint 2+ 에서 동일 시드를 다른 컨텍스트(e.g. UI 미리보기) 에서 재사용 가능.
    """
    rows = []
    for s in SCREENER_SEED_PRESETS:
        rows.append((
            s["strategy_key"],
            s["name"],
            s["description"],
            s["persona"],
            s["persona_summary"],
            s["markets_supported"],
            s["risk_warning"],
            json.dumps(s["spec"], ensure_ascii=False),
        ))
    return rows
