"""스크리너 시드 프리셋 — 거장 5 + 운영 자동 5.

마이그레이션 v41 에서 INSERT. ON CONFLICT (strategy_key) WHERE is_seed=TRUE DO UPDATE
로 멱등 보장 — 마이그레이션 재실행 시 시드 갱신 가능.

각 dict 는 screener_presets 테이블 컬럼과 1:1 매핑:
  strategy_key, name, description, persona, persona_summary, markets_supported,
  risk_warning, spec(JSONB)

spec 의 inline filter 형식은 analyzer/screener.py 가 해석.
펀더 v39 한계로 일부 거장 본 조건은 단순화 — Sprint 2+ 펀더 v2 에서 복원.
Spec: _docs/20260427055258_sprint1-design.md §7.2
"""
from __future__ import annotations
import json
from typing import Any


SCREENER_SEED_PRESETS: list[dict[str, Any]] = [
    # ── 거장 5 (초기 컷 단순화) ──────────────────────
    {
        "strategy_key": "buffett",
        "name": "Warren Buffett — 저밸류 + 수익성",
        "description": "장기 보유 가치주. PER 15 이하 + PBR 2.5 이하. ROE/부채비율 조건은 펀더 v2 에서 복원 예정.",
        "persona": "Warren Buffett",
        "persona_summary": "장기적 경제적 해자(moat) 와 높은 수익성을 가진 우량주를 합리적 가격에 매수.",
        "markets_supported": ["KOSPI", "KOSDAQ", "NASDAQ", "NYSE"],
        "risk_warning": "단기 모멘텀 무시 — 시장 침체기에는 바닥 매수 비용 동반. 거시 악재 시 큰 drawdown 가능.",
        "spec": {"filters": [
            {"field": "per", "op": "<=", "value": 15},
            {"field": "pbr", "op": "<=", "value": 2.5},
        ]},
    },
    {
        "strategy_key": "lynch",
        "name": "Peter Lynch — 성장 + 밸류",
        "description": "PEG 대신 PER 20 이하 + 1년 수익률 백분위 70 이상. PEG/EPS 성장률 조건은 펀더 v2 에서 복원.",
        "persona": "Peter Lynch",
        "persona_summary": "성장률 대비 저평가된 종목을 찾는 GARP(Growth at Reasonable Price) 전략.",
        "markets_supported": ["KOSPI", "KOSDAQ", "NASDAQ", "NYSE"],
        "risk_warning": "1년 수익률만 보면 모멘텀 단기 과열 종목 혼입 가능. 펀더와 교차 확인 필수.",
        "spec": {"filters": [
            {"field": "per", "op": "<=", "value": 20},
            {"field": "r1y_pctile", "op": ">=", "value": 0.70},
        ]},
    },
    {
        "strategy_key": "graham",
        "name": "Benjamin Graham — 안전마진",
        "description": "PBR 1.5 이하 + PER 12 이하 + 배당률 2% 이상. 유동비율 조건은 펀더 v2.",
        "persona": "Benjamin Graham",
        "persona_summary": "내재가치 대비 충분한 안전마진을 확보한 깊은 가치주(deep value) 전략.",
        "markets_supported": ["KOSPI", "KOSDAQ", "NASDAQ", "NYSE"],
        "risk_warning": "value trap 위험 — 저PBR 이 정당한 이유(쇠퇴 산업·경영 부실)일 수 있음.",
        "spec": {"filters": [
            {"field": "pbr", "op": "<=", "value": 1.5},
            {"field": "per", "op": "<=", "value": 12},
            {"field": "dividend_yield", "op": ">=", "value": 2.0},
        ]},
    },
    {
        "strategy_key": "oneil",
        "name": "William O'Neil — CAN SLIM 모멘텀",
        "description": "3개월 수익률 백분위 80 이상 + 52주 고점 90% 이상. EPS 성장률·거래량 급증은 펀더 v2.",
        "persona": "William O'Neil",
        "persona_summary": "수급·차트·실적 모두 강한 momentum leader 종목 추격 매수.",
        "markets_supported": ["KOSPI", "KOSDAQ", "NASDAQ", "NYSE"],
        "risk_warning": "추세 추종 — 고점 매수 위험. 시장 반락 시 빠른 이탈 규율 필수.",
        "spec": {"filters": [
            {"field": "r3m_pctile", "op": ">=", "value": 0.80},
            {"field": "price_to_52w_high_ratio", "op": ">=", "value": 0.90},
        ]},
    },
    {
        "strategy_key": "greenblatt",
        "name": "Joel Greenblatt — Magic Formula",
        "description": "Earnings yield (1/PER) 상위 10% + 시총 필터. ROIC 조건은 펀더 v2 에서 복원.",
        "persona": "Joel Greenblatt",
        "persona_summary": "고수익률·고자본수익률 종목을 단순 룰로 선별하는 시스템 트레이딩.",
        "markets_supported": ["KOSPI", "KOSDAQ", "NASDAQ", "NYSE"],
        "risk_warning": "단순 정량 룰 — 산업·테마 편향 발생 가능. 분산 투자 필수.",
        "spec": {"filters": [
            {"field": "earnings_yield_pctile", "op": ">=", "value": 0.90},
            {"field": "market_cap", "op": ">=", "value": 100_000_000_000},  # 1000억 이상
        ]},
    },
    # ── 운영 자동 5 ───────────────────────────────────
    {
        "strategy_key": "auto_52w_high",
        "name": "52주 신고가",
        "description": "52주 최고가 대비 98% 이상. 신고가 돌파 모멘텀 추적.",
        "persona": "운영 자동",
        "persona_summary": "최근 52주 고점 근접 종목 — 돌파 매매 후보.",
        "markets_supported": ["KOSPI", "KOSDAQ", "NASDAQ", "NYSE"],
        "risk_warning": "고점 대비 매수 — 단기 반락 시 손실 빠름.",
        "spec": {"filters": [
            {"field": "price_to_52w_high_ratio", "op": ">=", "value": 0.98},
        ]},
    },
    {
        "strategy_key": "auto_volume_spike",
        "name": "거래량 급증",
        "description": "최근 5일 거래량이 60일 평균 대비 3배 이상.",
        "persona": "운영 자동",
        "persona_summary": "갑작스런 수급 변화 — 재료 발생 신호 가능성.",
        "markets_supported": ["KOSPI", "KOSDAQ", "NASDAQ", "NYSE"],
        "risk_warning": "노이즈 다수 — 뉴스·공시와 교차 확인 없으면 휘둘릴 수 있음.",
        "spec": {"filters": [
            {"field": "volume_ratio_5d_vs_60d", "op": ">=", "value": 3.0},
        ]},
    },
    {
        "strategy_key": "auto_foreign_streak",
        "name": "외국인 5일 연속 순매수 (KR)",
        "description": "krx_investor_flow_daily 기반 외국인 연속 순매수 5일 이상.",
        "persona": "운영 자동",
        "persona_summary": "외국인 매수 streak — 수급 우위 신호.",
        "markets_supported": ["KOSPI", "KOSDAQ"],
        "risk_warning": "외국인 청산 시 빠른 반대 흐름. 시총 작은 종목은 변동성 ↑.",
        "spec": {"filters": [
            {"field": "foreign_streak", "op": ">=", "value": 5},
        ]},
    },
    {
        "strategy_key": "auto_momentum",
        "name": "모멘텀 강세",
        "description": "1개월·3개월 수익률 백분위 모두 80 이상.",
        "persona": "운영 자동",
        "persona_summary": "단·중기 모멘텀 동시 강세 — 트렌드 follow.",
        "markets_supported": ["KOSPI", "KOSDAQ", "NASDAQ", "NYSE"],
        "risk_warning": "고점 매수 위험. 추세 반전 시 손실 큰 편.",
        "spec": {"filters": [
            {"field": "r1m_pctile", "op": ">=", "value": 0.80},
            {"field": "r3m_pctile", "op": ">=", "value": 0.80},
        ]},
    },
    {
        "strategy_key": "auto_value_yield",
        "name": "저밸류 + 고배당",
        "description": "PBR 1 이하 + 배당률 4% 이상. 디펜시브 인컴 전략.",
        "persona": "운영 자동",
        "persona_summary": "방어적 배당주 — 약세장 buffer.",
        "markets_supported": ["KOSPI", "KOSDAQ", "NASDAQ", "NYSE"],
        "risk_warning": "저PBR 이 value trap 일 수 있음. 배당 지속성 확인 필요.",
        "spec": {"filters": [
            {"field": "pbr", "op": "<=", "value": 1.0},
            {"field": "dividend_yield", "op": ">=", "value": 4.0},
        ]},
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
