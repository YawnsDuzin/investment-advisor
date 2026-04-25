"""Stock Cockpit API + 페이지 단위 테스트.

psycopg2가 conftest에서 mock되므로 get_connection → cursor → fetch 체인을
가짜 객체로 꾸민다.
"""
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest


def _fake_conn(fetch_sequence):
    """fetchone/fetchall 호출 순서대로 값 반환하는 가짜 커넥션."""
    cur = MagicMock()
    idx = {"n": 0}

    def _next():
        v = fetch_sequence[idx["n"]]
        idx["n"] += 1
        return v

    cur.fetchone.side_effect = _next
    cur.fetchall.side_effect = _next

    @contextmanager
    def _cursor(**kwargs):
        yield cur

    conn = MagicMock()
    conn.cursor = _cursor
    return conn


class TestStockOverviewAPI:
    """GET /api/stocks/{ticker}/overview"""

    def test_overview_returns_hero_payload(self):
        from api.routes.stocks import get_stock_overview

        # fetch 순서: meta(stock_universe), latest 2 rows(ohlcv), prop_stats, factor_snapshot
        meta_row = {
            "name": "Texas Instruments",
            "sector": "Technology",
            "industry": "Semiconductors",
            "currency": "USD",
            "market": "NASDAQ",
        }
        latest_rows = [
            {"trade_date": date(2026, 4, 24), "close": Decimal("277.14"), "volume": 9240450},
            {"trade_date": date(2026, 4, 23), "close": Decimal("282.21"), "volume": 8800000},
        ]
        stats_row = {
            "proposal_count": 4,
            "avg_post_return_3m_pct": Decimal("12.4"),
            "avg_alpha_vs_benchmark_pct": Decimal("5.1"),
            "latest_consensus": "BUY",
        }
        factor_row = {
            "factor_snapshot": {
                "r1m_pctile": 0.7, "r3m_pctile": 0.8, "r6m_pctile": 0.85, "r12m_pctile": 0.78,
            },
        }

        conn = _fake_conn([meta_row, latest_rows, stats_row, factor_row])

        with patch("api.routes.stocks.get_connection", return_value=conn):
            result = get_stock_overview(ticker="TXN", market="NASDAQ")

        assert result["ticker"] == "TXN"
        assert result["name"] == "Texas Instruments"
        assert result["latest"]["close"] == 277.14
        # 변동률 = (277.14 - 282.21) / 282.21 * 100 ≈ -1.80
        assert round(result["latest"]["change_pct"], 2) == -1.80
        assert result["stats"]["proposal_count"] == 4
        assert result["stats"]["avg_post_return_3m_pct"] == 12.4
        assert result["stats"]["alpha_vs_benchmark_pct"] == 5.1
        # AI 점수 산식 검증
        # factor_score = (0.7+0.8+0.85+0.78)/4 = 0.7825
        # hist_score = clamp(12.4/30, 0, 1) ≈ 0.4133
        # consensus_score = BUY → 0.75
        # score = 100*(0.5*0.7825 + 0.3*0.4133 + 0.2*0.75) ≈ 66.5 → round → 67
        assert result["stats"]["ai_score"] == 67
        assert result["score_breakdown"]["weights"] == {"factor": 0.5, "hist": 0.3, "consensus": 0.2}

    def test_overview_zero_proposals_uses_neutral_score(self):
        from api.routes.stocks import get_stock_overview

        meta_row = {"name": "Foo", "sector": None, "industry": None,
                    "currency": "USD", "market": "NASDAQ"}
        latest_rows = []
        stats_row = {
            "proposal_count": 0, "avg_post_return_3m_pct": None,
            "avg_alpha_vs_benchmark_pct": None, "latest_consensus": None,
        }
        factor_row = {}

        conn = _fake_conn([meta_row, latest_rows, stats_row, factor_row])

        with patch("api.routes.stocks.get_connection", return_value=conn):
            result = get_stock_overview(ticker="FOO", market="NASDAQ")

        # 모든 컴포넌트 중립 0.5 → ai_score = 50
        assert result["stats"]["ai_score"] == 50
        assert result["latest"] is None
        assert result["stats"]["proposal_count"] == 0
        assert result["stats"]["avg_post_return_3m_pct"] is None


class TestComputeAiScore:
    """_compute_ai_score 순수 함수 단위 테스트."""

    def _fn(self, *args, **kwargs):
        from api.routes.stocks import _compute_ai_score
        return _compute_ai_score(*args, **kwargs)

    def test_all_present_full_factor_snapshot(self):
        """factor_snapshot 4개 키 모두 존재, avg 있음, BUY consensus."""
        snapshot = {
            "r1m_pctile": 0.7, "r3m_pctile": 0.8,
            "r6m_pctile": 0.85, "r12m_pctile": 0.78,
        }
        result = self._fn(snapshot, 12.4, "BUY")
        # factor_score = (0.7+0.8+0.85+0.78)/4 = 0.7825
        # hist_score = clamp(12.4/30) ≈ 0.4133
        # consensus_score = 0.75
        # score = 0.5*0.7825 + 0.3*0.4133 + 0.2*0.75 ≈ 0.6655 → 67
        assert result["ai_score"] == 67
        assert result["factor_score"] == round(0.7825, 4)
        assert result["hist_score"] == round(0.4133333333333333, 4)
        assert result["consensus_score"] == 0.75

    def test_partial_pctile_keys(self):
        """pctile 키 일부만 있거나 None — 있는 것만 평균."""
        snapshot = {
            "r1m_pctile": 0.6,
            "r3m_pctile": None,   # None → 제외
            "r6m_pctile": 0.4,
            # r12m_pctile 없음 → 제외
        }
        result = self._fn(snapshot, None, "HOLD")
        # factor_score = (0.6+0.4)/2 = 0.5
        # hist_score = 0.5 (avg None)
        # consensus_score = 0.5 (HOLD)
        # score = 0.5*0.5 + 0.3*0.5 + 0.2*0.5 = 0.5 → 50
        assert result["factor_score"] == 0.5
        assert result["hist_score"] == 0.5
        assert result["consensus_score"] == 0.5
        assert result["ai_score"] == 50

    def test_all_none_inputs_returns_neutral(self):
        """factor_snapshot=None, avg=None, consensus=None → 모두 중립 0.5 → ai_score 50."""
        result = self._fn(None, None, None)
        assert result["ai_score"] == 50
        assert result["factor_score"] == 0.5
        assert result["hist_score"] == 0.5
        assert result["consensus_score"] == 0.5

    def test_empty_factor_snapshot_returns_neutral(self):
        """factor_snapshot={} (빈 dict) → factor_score 중립 0.5."""
        result = self._fn({}, None, None)
        assert result["factor_score"] == 0.5
        assert result["ai_score"] == 50

    def test_returns_four_keys(self):
        """반환 dict에 반드시 4개 키가 존재한다."""
        result = self._fn(None, None, None)
        assert set(result.keys()) == {"ai_score", "factor_score", "hist_score", "consensus_score"}
