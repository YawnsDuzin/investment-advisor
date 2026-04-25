"""프리마켓 브리핑 단위 테스트 (D1~D7).

다음 항목을 mock DB로 검증:
  - overnight_us 섹터 집계 로직 (compute_us_overnight_summary 결측 fallback)
  - format_us_summary_text / format_kr_candidates_text 출력 형태
  - briefing_main._validate_kr_picks 화이트리스트 강제
  - briefing API serialization
"""
from contextlib import contextmanager
from datetime import date
from unittest.mock import MagicMock, patch

import pytest


# ── 공통 fake connection (test_track_record.py 패턴 차용) ──
def _fake_connection(fetch_sequence):
    cur = MagicMock()
    idx = {"n": 0}

    def _next():
        v = fetch_sequence[idx["n"]]
        idx["n"] += 1
        return v

    cur.fetchone.side_effect = _next
    cur.fetchall.side_effect = _next
    cur.description = []  # 실제로 사용되는 곳에서 patch 필요

    @contextmanager
    def _cursor(**kwargs):
        yield cur

    conn = MagicMock()
    conn.cursor = _cursor
    return conn


# ── overnight_us.format_us_summary_text ──

def test_format_us_summary_text_empty():
    from analyzer.overnight_us import format_us_summary_text
    assert "없음" in format_us_summary_text({})
    assert "없음" in format_us_summary_text(None)  # type: ignore[arg-type]


def test_format_us_summary_text_renders_indices_and_movers():
    from analyzer.overnight_us import format_us_summary_text
    snap = {
        "trade_date": "2026-04-24",
        "universe_size": 600,
        "indices": {
            "SP500": {"trade_date": "2026-04-24", "close": 5500.0, "change_pct": 1.32},
            "NDX100": {"trade_date": "2026-04-24", "close": 19000.0, "change_pct": 1.55},
        },
        "top_movers": [
            {"ticker": "MU", "change_pct": 8.45},
            {"ticker": "AMD", "change_pct": 6.67},
        ],
        "top_losers": [{"ticker": "X", "change_pct": -4.5}],
        "sector_aggregates": [
            {
                "sector_norm": "semiconductors", "label": "반도체",
                "n": 30, "avg_change_pct": 4.2, "median_change_pct": 3.8,
                "max_change_pct": 8.45, "min_change_pct": -1.0,
                "top_stocks": [{"ticker": "MU", "change_pct": 8.45}],
            },
            {
                "sector_norm": "ai_infra_power", "label": "AI 전력·인프라",
                "n": 5, "avg_change_pct": 8.0, "median_change_pct": 7.0,
                "max_change_pct": 13.75, "min_change_pct": 4.0,
                "top_stocks": [{"ticker": "GEV", "change_pct": 13.75}],
            },
        ],
    }
    text = format_us_summary_text(snap)
    assert "S&P 500" in text
    assert "MU +8.45%" in text
    assert "반도체" in text
    assert "AI 전력·인프라" in text


# ── overnight_us.format_kr_candidates_text ──

def test_format_kr_candidates_empty():
    from analyzer.overnight_us import format_kr_candidates_text
    assert "없음" in format_kr_candidates_text({})


def test_format_kr_candidates_renders_market_cap():
    from analyzer.overnight_us import format_kr_candidates_text
    candidates = {
        "semiconductors": [
            {
                "ticker": "005930", "asset_name": "삼성전자", "market": "KOSPI",
                "sector_norm": "semiconductors",
                "market_cap_krw": 500_000_000_000_000,  # 500조
                "last_price": 70000.0, "r1m_pct": 5.2,
            }
        ],
        "ai_infra_power": [],  # 빈 섹터 — 스킵돼야 함
    }
    text = format_kr_candidates_text(candidates)
    assert "삼성전자" in text
    assert "005930" in text
    assert "5,000,000억" in text  # 500조원 = 5,000,000억
    assert "1M +5.2%" in text


# ── briefing_main._validate_kr_picks (화이트리스트 강제) ──

def test_validate_kr_picks_drops_unknown_tickers():
    from analyzer.briefing_main import _validate_kr_picks
    log = MagicMock()
    candidates = {
        "semiconductors": [
            {"ticker": "005930", "asset_name": "삼성전자", "market": "KOSPI"},
            {"ticker": "000660", "asset_name": "SK하이닉스", "market": "KOSPI"},
        ]
    }
    briefing = {
        "kr_impact": [
            {
                "sector_norm": "semiconductors", "label": "반도체",
                "korean_picks": [
                    {"ticker": "005930", "asset_name": "삼성전자(잘못된이름)", "market": "KOSPI",
                     "rationale": "..."},
                    {"ticker": "FAKE001", "asset_name": "유령종목", "market": "KOSPI",
                     "rationale": "AI 환각"},
                    {"ticker": "000660", "asset_name": "SK하이닉스", "market": "KOSPI",
                     "rationale": "..."},
                ],
            }
        ]
    }
    result = _validate_kr_picks(briefing, candidates, log)
    picks = result["kr_impact"][0]["korean_picks"]
    tickers = [p["ticker"] for p in picks]
    assert "005930" in tickers
    assert "000660" in tickers
    assert "FAKE001" not in tickers
    # asset_name이 후보 풀 값으로 교정됨
    samsung = next(p for p in picks if p["ticker"] == "005930")
    assert samsung["asset_name"] == "삼성전자"
    log.warning.assert_called_once()


def test_validate_kr_picks_no_impact_returns_unchanged():
    from analyzer.briefing_main import _validate_kr_picks
    briefing = {"morning_brief": "오늘은 평온"}
    out = _validate_kr_picks(briefing, {}, MagicMock())
    assert out == briefing


# ── overnight_us 섹터 집계 — 작은 섹터 제거 ──

def test_compute_us_overnight_summary_skips_small_sectors():
    """min_sector_n 이하 섹터는 sector_aggregates에서 제외됨을 검증.

    DB 레이어를 patch하고 핵심 로직만 검증.
    """
    from analyzer import overnight_us as ou

    # 6종목: semiconductors 4건(통과) + biotech 2건(min=3 미달, 제거)
    fake_rows = [
        {"ticker": f"S{i}", "market": "NASDAQ", "asset_name": f"Stock {i}",
         "asset_name_en": f"Stock {i}", "sector_norm": "semiconductors",
         "industry": "x", "close": 100.0, "change_pct": 5.0 + i, "volume": 1000,
         "market_cap_krw": None, "sector_gics": None}
        for i in range(4)
    ] + [
        {"ticker": f"B{i}", "market": "NASDAQ", "asset_name": f"Bio {i}",
         "asset_name_en": f"Bio {i}", "sector_norm": "biotech_pharma",
         "industry": "y", "close": 50.0, "change_pct": 2.0, "volume": 500,
         "market_cap_krw": None, "sector_gics": None}
        for i in range(2)
    ]

    cfg = MagicMock()
    with patch.object(ou, "_get_latest_us_trade_date", return_value=date(2026, 4, 24)), \
         patch.object(ou, "_fetch_us_daily_changes", return_value=fake_rows), \
         patch.object(ou, "_fetch_us_indices", return_value={}):
        snap = ou.compute_us_overnight_summary(cfg, min_sector_n=3)

    sector_keys = [s["sector_norm"] for s in snap["sector_aggregates"]]
    assert "semiconductors" in sector_keys
    assert "biotech_pharma" not in sector_keys
    assert snap["universe_size"] == 6
    # Top movers — 가장 큰 change_pct
    assert snap["top_movers"][0]["ticker"] == "S3"  # change_pct = 8.0


def test_compute_us_overnight_summary_no_data_returns_empty():
    from analyzer import overnight_us as ou

    cfg = MagicMock()
    with patch.object(ou, "_get_latest_us_trade_date", return_value=None):
        assert ou.compute_us_overnight_summary(cfg) == {}


# ── prompts.BRIEFING_PROMPT placeholders ──

def test_briefing_prompt_has_required_placeholders():
    from analyzer.prompts import BRIEFING_PROMPT
    for ph in ("{date}", "{trade_date}", "{regime_section}",
               "{us_summary_section}", "{kr_candidates_section}"):
        assert ph in BRIEFING_PROMPT, f"missing placeholder {ph}"


def test_briefing_system_present():
    from analyzer.prompts import BRIEFING_SYSTEM
    assert "프리마켓" in BRIEFING_SYSTEM
    assert "JSON" in BRIEFING_SYSTEM


# ── DB migration 등록 확인 ──

def test_v34_migration_registered():
    from shared.db.migrations import _MIGRATIONS
    from shared.db.migrations import versions as _v
    assert 34 in _MIGRATIONS
    assert _MIGRATIONS[34] is _v._migrate_to_v34


def test_schema_version_at_least_34():
    from shared.db.schema import SCHEMA_VERSION
    assert SCHEMA_VERSION >= 34
