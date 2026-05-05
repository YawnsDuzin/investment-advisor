"""펀더 시계열 인사이트 단위 테스트.

DB 결과 정규화·percentile 계산·format 출력만 검증.
"""
from __future__ import annotations

from datetime import date as date_cls
from unittest.mock import patch, MagicMock


def _make_fake_conn(rows):
    fake_cur = MagicMock()
    fake_cur.fetchall.return_value = rows
    fake_cur.execute = MagicMock()
    fake_cur.__enter__ = MagicMock(return_value=fake_cur)
    fake_cur.__exit__ = MagicMock(return_value=False)
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cur
    return fake_conn, fake_cur


# ── compute ──

def test_compute_returns_empty_when_no_tickers():
    from analyzer.fundamentals_engine import compute_fundamentals_snapshots
    assert compute_fundamentals_snapshots(db_cfg=None, tickers=[]) == {}


def test_compute_filters_blank_market_pairs():
    from analyzer.fundamentals_engine import compute_fundamentals_snapshots

    with patch("analyzer.fundamentals_engine.get_connection") as gc:
        out = compute_fundamentals_snapshots(
            db_cfg=None,
            tickers=[("AAPL", "")],  # market 빈 값 → 제외
        )
    assert out == {}
    gc.assert_not_called()


def test_compute_normalizes_row_into_snapshot():
    from analyzer.fundamentals_engine import compute_fundamentals_snapshots

    rows = [{
        "ticker": "AAPL",
        "market": "NASDAQ",
        "snapshot_date": date_cls(2026, 5, 3),
        "per_latest": 28.5,
        "per_12m_mean": 32.0,
        "per_latest_pctile": 0.20,  # 분포 하위 20% 위치 = 상위 80%
        "per_n": 200,
        "pbr_latest": 45.0,
        "pbr_12m_mean": 42.0,
        "pbr_latest_pctile": 0.85,  # 분포 상위 15%
        "pbr_n": 200,
        "eps_latest": 6.5,
        "eps_12m_ago": 5.0,
        "dy_latest": 0.45,
        "sample_size": 250,
    }]
    fake_conn, _ = _make_fake_conn(rows)

    with patch("analyzer.fundamentals_engine.get_connection",
               return_value=fake_conn):
        out = compute_fundamentals_snapshots(
            db_cfg=None,
            tickers=[("AAPL", "NASDAQ")],
        )

    snap = out[("AAPL", "NASDAQ")]
    assert snap["per_latest"] == 28.5
    assert snap["per_12m_mean"] == 32.0
    assert snap["per_12m_top_pct"] == 80  # PER 분포 상위 80% = 저평가
    assert snap["pbr_12m_top_pct"] == 15  # PBR 분포 상위 15% = 고평가
    # EPS YoY = (6.5 - 5.0) / 5.0 * 100 = 30%
    assert snap["eps_yoy_pct"] == 30.0
    assert snap["dividend_yield_latest"] == 0.45
    assert snap["snapshot_date"] == "2026-05-03"


def test_compute_drops_pctile_when_sample_too_small():
    """per_n < MIN_SAMPLE_FOR_PCTILE → percentile NULL (latest 는 살아 있음)."""
    from analyzer.fundamentals_engine import compute_fundamentals_snapshots

    rows = [{
        "ticker": "NEW",
        "market": "NASDAQ",
        "snapshot_date": date_cls(2026, 5, 3),
        "per_latest": 15.0,
        "per_12m_mean": 14.0,
        "per_latest_pctile": 0.5,
        "per_n": 10,                  # MIN_SAMPLE_FOR_PCTILE(30) 미만
        "pbr_latest": None,
        "pbr_12m_mean": None,
        "pbr_latest_pctile": None,
        "pbr_n": 0,
        "eps_latest": None,
        "eps_12m_ago": None,
        "dy_latest": None,
        "sample_size": 12,
    }]
    fake_conn, _ = _make_fake_conn(rows)

    with patch("analyzer.fundamentals_engine.get_connection",
               return_value=fake_conn):
        out = compute_fundamentals_snapshots(
            db_cfg=None,
            tickers=[("NEW", "NASDAQ")],
        )

    snap = out[("NEW", "NASDAQ")]
    assert snap["per_latest"] == 15.0
    assert snap["per_12m_pctile"] is None
    assert snap["per_12m_top_pct"] is None
    # PBR 결측은 None 유지
    assert snap["pbr_latest"] is None


def test_compute_handles_zero_eps_prev_safely():
    """eps_12m_ago=0 일 때 ZeroDivision 없이 None 으로 떨어져야 함."""
    from analyzer.fundamentals_engine import compute_fundamentals_snapshots

    rows = [{
        "ticker": "X", "market": "NASDAQ",
        "snapshot_date": date_cls(2026, 5, 3),
        "per_latest": 10.0, "per_12m_mean": 10.0,
        "per_latest_pctile": 0.5, "per_n": 200,
        "pbr_latest": None, "pbr_12m_mean": None,
        "pbr_latest_pctile": None, "pbr_n": 0,
        "eps_latest": 1.5, "eps_12m_ago": 0.0,
        "dy_latest": None, "sample_size": 250,
    }]
    fake_conn, _ = _make_fake_conn(rows)

    with patch("analyzer.fundamentals_engine.get_connection",
               return_value=fake_conn):
        out = compute_fundamentals_snapshots(
            db_cfg=None, tickers=[("X", "NASDAQ")],
        )

    assert out[("X", "NASDAQ")]["eps_yoy_pct"] is None


def test_compute_sql_uses_unnest_with_two_arrays():
    from analyzer.fundamentals_engine import compute_fundamentals_snapshots

    fake_conn, fake_cur = _make_fake_conn([])
    with patch("analyzer.fundamentals_engine.get_connection",
               return_value=fake_conn):
        compute_fundamentals_snapshots(
            db_cfg=None,
            tickers=[("AAPL", "NASDAQ"), ("005930", "KOSPI")],
        )

    sql, params = fake_cur.execute.call_args[0]
    assert "UNNEST(%s::text[], %s::text[])" in sql
    assert "stock_universe_fundamentals" in sql
    assert "PERCENT_RANK" in sql
    assert params[0] == ["AAPL", "005930"]
    assert params[1] == ["NASDAQ", "KOSPI"]


# ── format ──

def test_format_returns_empty_when_no_data():
    from analyzer.fundamentals_engine import format_fundamentals_text
    assert format_fundamentals_text({}) == ""
    assert format_fundamentals_text(None) == ""


def test_format_includes_per_pbr_eps_dividend():
    from analyzer.fundamentals_engine import format_fundamentals_text

    snap = {
        "snapshot_date": "2026-05-03",
        "per_latest": 12.0,
        "per_12m_mean": 18.0,
        "per_12m_top_pct": 88,  # 저평가
        "pbr_latest": 1.2,
        "pbr_12m_mean": 1.5,
        "pbr_12m_top_pct": 75,
        "eps_latest": 5.0,
        "eps_12m_ago": 3.5,
        "eps_yoy_pct": 42.86,
        "dividend_yield_latest": 2.1,
        "sample_size": 230,
    }
    text = format_fundamentals_text(snap)
    assert "PER 12.00" in text
    assert "12M 평균 18.00" in text
    assert "분포 상위 88%" in text
    assert "저평가 구간" in text  # top_pct >= 80 → 저평가 라벨
    assert "PBR 1.20" in text
    assert "EPS YoY: +42.86%" in text
    assert "배당수익률 latest: 2.10%" in text
    assert "2026-05-03" in text


def test_format_omits_missing_fields_without_error():
    from analyzer.fundamentals_engine import format_fundamentals_text

    snap = {
        "snapshot_date": "2026-05-03",
        "per_latest": 10.0,
        "per_12m_mean": None,
        "per_12m_top_pct": None,
        "pbr_latest": None,
        "pbr_12m_mean": None,
        "pbr_12m_top_pct": None,
        "eps_latest": None,
        "eps_12m_ago": None,
        "eps_yoy_pct": None,
        "dividend_yield_latest": None,
        "sample_size": 50,
    }
    text = format_fundamentals_text(snap)
    assert "PER 10.00" in text
    assert "None" not in text  # 결측 표기는 None 으로 새지 않아야


def test_format_returns_empty_when_only_header_would_show():
    """per/pbr/eps/dy 모두 결측 → 빈 문자열 (의미 없는 헤더만 출력 방지)."""
    from analyzer.fundamentals_engine import format_fundamentals_text

    snap = {
        "snapshot_date": "2026-05-03",
        "per_latest": None,
        "pbr_latest": None,
        "eps_latest": None,
        "eps_yoy_pct": None,
        "dividend_yield_latest": None,
        "sample_size": 0,
    }
    assert format_fundamentals_text(snap) == ""
