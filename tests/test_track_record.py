"""트랙레코드 API 단위 테스트 (A-001)

psycopg2가 conftest에서 mock되므로, get_connection → cursor → fetchone/fetchall
체인을 수동으로 꾸며 반환 JSON 구조만 검증한다.
"""
from contextlib import contextmanager
from unittest.mock import patch, MagicMock
from decimal import Decimal
from datetime import date

import pytest


def _fake_connection(fetch_sequence):
    """fetchone/fetchall 호출 순서대로 값 반환하는 가짜 커넥션."""
    cur = MagicMock()

    idx = {"n": 0}

    def _fetchone():
        v = fetch_sequence[idx["n"]]
        idx["n"] += 1
        return v

    def _fetchall():
        v = fetch_sequence[idx["n"]]
        idx["n"] += 1
        return v

    cur.fetchone.side_effect = _fetchone
    cur.fetchall.side_effect = _fetchall

    @contextmanager
    def _cursor(**kwargs):
        yield cur

    conn = MagicMock()
    conn.cursor = _cursor
    return conn


class TestTrackRecordSummary:
    """GET /api/track-record/summary 의 JSON 형태 검증"""

    def test_summary_returns_expected_keys(self):
        from api.routes.track_record import get_track_record_summary
        from shared.config import DatabaseConfig

        # 쿼리 4개 순서: overview, by_type(여러 행), top_picks(여러 행), meta
        overview_row = {
            "n_1m": 10, "win_1m": 6, "avg_1m": Decimal("3.45"),
            "n_3m": 8, "win_3m": 5, "avg_3m": Decimal("7.2"),
            "n_6m": 5, "win_6m": 3, "avg_6m": Decimal("12.0"),
            "n_1y": 3, "win_1y": 2, "avg_1y": Decimal("18.5"),
            "total_proposals": 12,
        }
        by_type_rows = [
            {
                "discovery_type": "early_signal",
                "n": 5, "wins": 4,
                "avg_1m": Decimal("8.1"), "avg_3m": Decimal("12.5"), "avg_1y": Decimal("25.0"),
            },
            {
                "discovery_type": "consensus",
                "n": 3, "wins": 2,
                "avg_1m": Decimal("2.2"), "avg_3m": Decimal("5.5"), "avg_1y": Decimal("10.0"),
            },
        ]
        top_picks_rows = [
            {
                "analysis_date": date(2026, 4, 15), "rank": 1, "score_final": Decimal("87.5"),
                "ticker": "AAPL", "asset_name": "Apple", "discovery_type": "early_signal",
                "conviction": "high", "current_price": Decimal("210.50"),
                "upside_pct": Decimal("12.3"), "return_1m_pct": Decimal("5.2"),
                "return_3m_pct": Decimal("9.1"), "theme_name": "AI 인프라",
                "rationale_text": "미 반영", "source": "rule",
            },
        ]
        meta_row = {"earliest": date(2026, 1, 1), "latest": date(2026, 4, 15)}

        conn = _fake_connection([overview_row, by_type_rows, top_picks_rows, meta_row])

        with patch("api.routes.track_record.get_connection", return_value=conn):
            result = get_track_record_summary(cfg=DatabaseConfig())

        # 최상위 구조
        assert set(result.keys()) >= {
            "overview", "by_discovery_type", "recent_top_picks", "meta", "disclaimer", "generated_at",
        }
        assert "추천 당일" in result["disclaimer"]  # 주의 문구 포함
        assert result["generated_at"] is not None
        # ISO 8601 포맷 (T 포함)
        assert "T" in result["generated_at"]

        # overview
        periods = result["overview"]["periods"]
        assert set(periods.keys()) == {"1m", "3m", "6m", "1y"}
        assert periods["1m"]["n"] == 10
        assert periods["1m"]["wins"] == 6
        assert periods["1m"]["win_rate_pct"] == 60.0  # 6/10*100
        assert periods["1m"]["avg_return_pct"] == 3.45

        # by_discovery_type
        assert len(result["by_discovery_type"]) == 2
        first = result["by_discovery_type"][0]
        assert first["discovery_type"] == "early_signal"
        assert first["win_rate_pct"] == 80.0  # 4/5

        # top_picks
        assert len(result["recent_top_picks"]) == 1
        pick = result["recent_top_picks"][0]
        assert pick["ticker"] == "AAPL"
        assert pick["analysis_date"] == "2026-04-15"
        assert pick["score_final"] == 87.5

        # meta
        assert result["meta"]["earliest_date"] == "2026-01-01"
        assert result["meta"]["latest_date"] == "2026-04-15"

    def test_summary_handles_zero_samples(self):
        """아무 제안도 없을 때 NULL/0 안전 처리"""
        from api.routes.track_record import get_track_record_summary
        from shared.config import DatabaseConfig

        overview_row = {
            "n_1m": 0, "win_1m": 0, "avg_1m": None,
            "n_3m": 0, "win_3m": 0, "avg_3m": None,
            "n_6m": 0, "win_6m": 0, "avg_6m": None,
            "n_1y": 0, "win_1y": 0, "avg_1y": None,
            "total_proposals": 0,
        }
        conn = _fake_connection([overview_row, [], [], {"earliest": None, "latest": None}])

        with patch("api.routes.track_record.get_connection", return_value=conn):
            result = get_track_record_summary(cfg=DatabaseConfig())

        # 승률 / 평균은 None 반환 허용
        assert result["overview"]["periods"]["1m"]["win_rate_pct"] is None
        assert result["overview"]["periods"]["1m"]["avg_return_pct"] is None
        assert result["by_discovery_type"] == []
        assert result["recent_top_picks"] == []
        assert result["meta"]["earliest_date"] is None
