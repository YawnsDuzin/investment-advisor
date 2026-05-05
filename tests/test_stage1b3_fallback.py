"""Stage 1-B3 폴백 단위 테스트.

`_screener_candidates_to_fallback_proposals` 의 출력 필드와
`stage1b_universe_first` 의 폴백 트리거 조건을 검증한다.

async 함수는 anyio.run 으로 sync 래핑 — 환경에 pytest-asyncio 미설치 가정.
"""
from __future__ import annotations

from unittest.mock import patch, AsyncMock

import anyio
import pytest


# ── helper: 헬퍼 함수 import는 지연 (conftest.py mock 후) ──

def _import_helper():
    from analyzer.analyzer import _screener_candidates_to_fallback_proposals
    return _screener_candidates_to_fallback_proposals


def _import_pipeline():
    from analyzer.analyzer import stage1b_universe_first
    return stage1b_universe_first


def _sample_candidates():
    return [
        {
            "ticker": "005930",
            "market": "KOSPI",
            "asset_name": "삼성전자",
            "sector_norm": "semiconductors",
            "screener_match_reason": "반도체,HBM",
        },
        {
            "ticker": "AAPL",
            "market": "NASDAQ",
            "asset_name": "Apple Inc",
            "sector_norm": "consumer_electronics",
            "screener_match_reason": "sector/cap_only",
        },
        {
            "ticker": "",  # 빈 ticker → 스킵돼야 함
            "market": "KOSPI",
            "asset_name": "잘못된 행",
            "sector_norm": "x",
        },
    ]


def _sample_theme():
    return {
        "theme_name": "AI 반도체 수요",
        "theme_key": "ai_semiconductor_demand",
        "description": "HBM 공급부족이 지속.",
    }


def _sample_spec():
    return {
        "theme_key": "ai_semiconductor_demand",
        "thesis": "HBM 공급부족 — 후공정 패키징 수혜",
        "expected_catalyst_window_months": 6,
        "required_keywords": ["HBM"],
        "sector_norm": ["semiconductors"],
    }


# ── 헬퍼 함수 ──

def test_helper_skips_empty_ticker():
    fn = _import_helper()
    out = fn(_sample_candidates(), _sample_theme(), _sample_spec(), top_n=10)
    tickers = [p["ticker"] for p in out]
    assert "005930" in tickers
    assert "AAPL" in tickers
    assert "" not in tickers
    assert len(out) == 2


def test_helper_respects_top_n():
    fn = _import_helper()
    out = fn(_sample_candidates(), _sample_theme(), _sample_spec(), top_n=1)
    assert len(out) == 1
    assert out[0]["ticker"] == "005930"


def test_helper_marks_fallback_fields():
    """폴백 proposal 의 필수 필드 확인 — recommender·UI 가 이 태그로 구분."""
    fn = _import_helper()
    out = fn(_sample_candidates(), _sample_theme(), _sample_spec())
    for p in out:
        assert p["asset_type"] == "stock"
        assert p["action"] == "watch"
        assert p["conviction"] == "low"
        assert p["discovery_type"] == "screener_fallback"
        assert p["is_fallback"] is True
        assert p["current_price"] is None  # 실시간 가격은 후속 momentum batch 가 채움
        assert p["target_price_low"] is None
        assert p["upside_pct"] is None
        assert p["spec_snapshot"]["theme_key"] == "ai_semiconductor_demand"
        assert p["fallback_reason"] in ("stage1b3_failed", "stage1b3_empty", "stage1b3_exception")


def test_helper_currency_inferred_from_market():
    fn = _import_helper()
    out = fn(_sample_candidates(), _sample_theme(), _sample_spec())
    by_ticker = {p["ticker"]: p for p in out}
    assert by_ticker["005930"]["currency"] == "KRW"
    assert by_ticker["AAPL"]["currency"] == "USD"


def test_helper_rationale_contains_match_reason():
    fn = _import_helper()
    out = fn(_sample_candidates(), _sample_theme(), _sample_spec())
    by_ticker = {p["ticker"]: p for p in out}
    # 매칭 키워드 인용
    assert "반도체,HBM" in by_ticker["005930"]["rationale"]
    # 테마 가설 인용
    assert "HBM 공급부족" in by_ticker["005930"]["rationale"]
    # 카탈리스트 창
    assert "6" in by_ticker["005930"]["rationale"]


def test_helper_returns_empty_for_empty_candidates():
    fn = _import_helper()
    assert fn([], _sample_theme(), _sample_spec()) == []


# ── stage1b_universe_first 폴백 통합 ──

def _make_mock_cfg():
    """AnalyzerConfig + ScreenerConfig stand-ins."""
    class _AnalyzerCfg:
        max_turns = 1
        model_analysis = "claude-sonnet-4-6"

    class _ScreenerCfg:
        stage1b3_top_n = 20
        b3_fallback_enabled = True
        b3_fallback_top_n = 3

    return _AnalyzerCfg(), _ScreenerCfg()


def _run_pipeline_sync(pipeline, theme, screener_cfg, analyzer_cfg):
    return anyio.run(
        pipeline, theme, None, "2026-05-05", analyzer_cfg, screener_cfg,
    )


def test_universe_first_falls_back_on_empty_b3():
    """1-B3 가 빈 리스트 반환 시 폴백 proposals 가 그 자리를 채워야 함."""
    pipeline = _import_pipeline()
    analyzer_cfg, screener_cfg = _make_mock_cfg()

    with patch("analyzer.analyzer.stage1b1_generate_spec",
               new=AsyncMock(return_value=_sample_spec())), \
         patch("analyzer.analyzer.stage1b2_screen_candidates",
               return_value=_sample_candidates()), \
         patch("analyzer.analyzer.stage1b3_analyze_candidates",
               new=AsyncMock(return_value=[])):
        proposals = _run_pipeline_sync(pipeline, _sample_theme(), screener_cfg, analyzer_cfg)

    assert len(proposals) == 2  # 빈 ticker 1건 제외
    assert all(p["is_fallback"] for p in proposals)
    assert all(p["fallback_reason"] == "stage1b3_empty" for p in proposals)


def test_universe_first_falls_back_on_b3_exception():
    """1-B3 가 예외 발생 시에도 폴백 발동."""
    pipeline = _import_pipeline()
    analyzer_cfg, screener_cfg = _make_mock_cfg()

    async def _raise(*a, **kw):
        raise RuntimeError("SDK timeout")

    with patch("analyzer.analyzer.stage1b1_generate_spec",
               new=AsyncMock(return_value=_sample_spec())), \
         patch("analyzer.analyzer.stage1b2_screen_candidates",
               return_value=_sample_candidates()), \
         patch("analyzer.analyzer.stage1b3_analyze_candidates",
               side_effect=_raise):
        proposals = _run_pipeline_sync(pipeline, _sample_theme(), screener_cfg, analyzer_cfg)

    assert len(proposals) == 2
    assert all(p["fallback_reason"] == "stage1b3_exception" for p in proposals)


def test_universe_first_no_fallback_when_disabled():
    """b3_fallback_enabled=False 면 폴백 발동 안 함 — 빈 리스트 반환 (기존 동작)."""
    pipeline = _import_pipeline()
    analyzer_cfg, screener_cfg = _make_mock_cfg()
    screener_cfg.b3_fallback_enabled = False

    with patch("analyzer.analyzer.stage1b1_generate_spec",
               new=AsyncMock(return_value=_sample_spec())), \
         patch("analyzer.analyzer.stage1b2_screen_candidates",
               return_value=_sample_candidates()), \
         patch("analyzer.analyzer.stage1b3_analyze_candidates",
               new=AsyncMock(return_value=[])):
        proposals = _run_pipeline_sync(pipeline, _sample_theme(), screener_cfg, analyzer_cfg)

    assert proposals == []


def test_universe_first_passes_through_normal_b3():
    """1-B3 가 정상 응답 시 폴백 미발동 — AI 결과 그대로 반환."""
    pipeline = _import_pipeline()
    analyzer_cfg, screener_cfg = _make_mock_cfg()

    ai_proposals = [{
        "ticker": "005930",
        "market": "KOSPI",
        "action": "buy",
        "conviction": "high",
        "discovery_type": "early_signal",
    }]
    with patch("analyzer.analyzer.stage1b1_generate_spec",
               new=AsyncMock(return_value=_sample_spec())), \
         patch("analyzer.analyzer.stage1b2_screen_candidates",
               return_value=_sample_candidates()), \
         patch("analyzer.analyzer.stage1b3_analyze_candidates",
               new=AsyncMock(return_value=ai_proposals)):
        proposals = _run_pipeline_sync(pipeline, _sample_theme(), screener_cfg, analyzer_cfg)

    assert proposals == ai_proposals
    assert "is_fallback" not in proposals[0]
