"""briefing.html 렌더 테스트용 샘플 데이터.

첨부 이미지(2026-04-26 KST)와 동일한 형상 — 2개 섹터:
  - 반도체 (KR picks 있음 → 펼침)
  - 통신·케이블 (KR 매칭 없음 → 접힘)
"""
from __future__ import annotations


def make_briefing(
    *,
    with_headline: bool = True,
    with_morning: bool = True,
    with_kr_only_extra: bool = False,
) -> dict:
    """기본 샘플. 플래그로 부분 케이스 생성."""
    bd: dict = {
        "us_summary": {
            "headline": "INTC +23.60% 이례적 급등이 ARM·AMD·QCOM 두 자릿수 랠리를 견인",
            "groups": [
                {
                    "sector_norm": "semiconductors",
                    "label": "반도체 (SEMICONDUCTORS)",
                    "top_movers": [
                        {"ticker": "INTC", "change_pct": 23.60},
                        {"ticker": "ARM", "change_pct": 14.76},
                        {"ticker": "AMD", "change_pct": 13.91},
                    ],
                    "catalyst": "INTC 단일 종목 +23.60% 급등이 섹터 전반 동조 매수를 촉발.",
                },
                {
                    "sector_norm": "communication_cable",
                    "label": "통신·케이블 — 실적 쇼크 (COMMUNICATION)",
                    "top_movers": [
                        {"ticker": "CHTR", "change_pct": -25.50},
                        {"ticker": "CMCSA", "change_pct": -12.90},
                    ],
                    "catalyst": "케이블TV 가입자 이탈 가속 + 분기 대규모 미스로 급락.",
                },
            ],
        },
        "kr_impact": [
            {
                "sector_norm": "semiconductors",
                "label": "반도체 장비·메모리 — 갭 상승 강력",
                "strength": "gap_up_strong",
                "korean_picks": [
                    {
                        "ticker": "000660", "asset_name": "SK하이닉스", "market": "KOSPI",
                        "rationale": "HBM3E·HBM4 공급 과점 지위로 AMD·QCOM AI 가속기 수요 직접 수혜.",
                        "expected_open_change_pct": "+2~4%",
                    },
                    {
                        "ticker": "042700", "asset_name": "한미반도체", "market": "KOSPI",
                        "rationale": "HBM TC-본딩 장비 독점 공급 지위.",
                        "expected_open_change_pct": "+3~5%",
                    },
                ],
                "catalysts_kr": "오전 외국인 순매수 진입 가능성.",
                "related_etfs": ["KODEX 반도체"],
            },
        ],
        "morning_brief": (
            "오늘 챙겨야 할 핵심은 반도체 메모리 흐름. AI 칩 설계~생산 전 레이어 동반 강세, "
            "케이블TV 실적 쇼크는 극명한 대조."
        ),
    }
    if not with_headline:
        bd["us_summary"]["headline"] = ""
    if not with_morning:
        bd["morning_brief"] = ""
    if with_kr_only_extra:
        bd["kr_impact"].append({
            "sector_norm": "shipbuilding_kr",
            "label": "조선 (KR-only)",
            "strength": "upside_expected",
            "korean_picks": [
                {"ticker": "009540", "asset_name": "HD한국조선해양", "market": "KOSPI",
                 "rationale": "수주 모멘텀 지속.", "expected_open_change_pct": "+1~2%"},
            ],
            "catalysts_kr": "",
            "related_etfs": [],
        })

    return {
        "briefing_date": "2026-04-26",
        "source_trade_date": "2026-04-24",
        "status": "success",
        "generated_at": "2026-04-26T06:36:00",
        "updated_at": "2026-04-26T06:36:00",
        "us_summary": {
            "trade_date": "2026-04-24",
            "universe_size": 600,
            "top_movers": [
                {"ticker": "INTC", "asset_name": "Intel Corporation",
                 "market": "NASDAQ", "sector_norm": "semiconductors", "change_pct": 23.60},
                {"ticker": "ARM", "asset_name": "Arm Holdings",
                 "market": "NASDAQ", "sector_norm": "semiconductors", "change_pct": 14.76},
            ],
            "top_losers": [
                {"ticker": "CHTR", "asset_name": "Charter Communications",
                 "market": "NASDAQ", "sector_norm": "communication_cable", "change_pct": -25.50},
            ],
            "sector_aggregates": [
                {
                    "sector_norm": "semiconductors", "label": "반도체",
                    "n": 30, "avg_change_pct": 4.66, "median_change_pct": 3.11,
                    "max_change_pct": 23.60, "min_change_pct": -1.50,
                    "top_stocks": [
                        {"ticker": "INTC", "asset_name": "Intel Corporation",
                         "market": "NASDAQ", "sector_norm": "semiconductors", "change_pct": 23.60},
                        {"ticker": "AMD", "asset_name": "Advanced Micro Devices",
                         "market": "NASDAQ", "sector_norm": "semiconductors", "change_pct": 13.91},
                    ],
                },
                {
                    "sector_norm": "communication_cable", "label": "통신·케이블",
                    "n": 8, "avg_change_pct": -2.94, "median_change_pct": -3.10,
                    "max_change_pct": 0.50, "min_change_pct": -25.50,
                    "top_stocks": [
                        {"ticker": "CHTR", "asset_name": "Charter Communications",
                         "market": "NASDAQ", "sector_norm": "communication_cable", "change_pct": -25.50},
                        {"ticker": "CMCSA", "asset_name": "Comcast Corporation",
                         "market": "NASDAQ", "sector_norm": "communication_cable", "change_pct": -12.90},
                    ],
                },
            ],
            "indices": {},
        },
        "briefing_data": bd,
        "regime_snapshot": {},
        "error_message": None,
    }
