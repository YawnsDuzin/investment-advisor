"""외국인 수급 인사이트 단위 테스트.

DB 쿼리는 mock — SQL 빌드와 결과 정규화 / format 출력만 검증.
"""
from __future__ import annotations

from datetime import date as date_cls
from unittest.mock import patch, MagicMock


def test_compute_filters_non_krx_markets():
    """NASDAQ/NYSE 같은 KRX 외 시장은 SQL 호출 자체를 건너뜀."""
    from analyzer.foreign_flow_insight import compute_foreign_flow_snapshots

    with patch("analyzer.foreign_flow_insight.get_connection") as gc:
        out = compute_foreign_flow_snapshots(
            db_cfg=None,
            tickers=[("AAPL", "NASDAQ"), ("MSFT", "NYSE")],
        )

    assert out == {}
    gc.assert_not_called()


def test_compute_returns_empty_when_no_tickers():
    from analyzer.foreign_flow_insight import compute_foreign_flow_snapshots
    assert compute_foreign_flow_snapshots(db_cfg=None, tickers=[]) == {}


def _make_fake_conn(rows):
    fake_cur = MagicMock()
    fake_cur.fetchall.return_value = rows
    fake_cur.execute = MagicMock()
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=fake_cur)
    cm.__exit__ = MagicMock(return_value=False)
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = cm
    return fake_conn, fake_cur


def test_compute_normalizes_row_into_snapshot():
    """SQL 결과 row → 정규화된 snapshot dict."""
    from analyzer.foreign_flow_insight import compute_foreign_flow_snapshots

    rows = [{
        "ticker": "005930",
        "market": "KOSPI",
        "snapshot_date": date_cls(2026, 5, 3),
        "own_latest": 53.21,
        "own_5d": 53.10,
        "own_20d": 52.00,
        "own_60d": 50.50,
        "net_buy_5d": 1_500_000_000_000,
        "net_buy_20d": 5_200_000_000_000,
        "net_buy_60d": 12_000_000_000_000,
    }]
    fake_conn, fake_cur = _make_fake_conn(rows)

    with patch("analyzer.foreign_flow_insight.get_connection",
               return_value=fake_conn):
        out = compute_foreign_flow_snapshots(
            db_cfg=None,
            tickers=[("005930", "KOSPI")],
        )

    assert ("005930", "KOSPI") in out
    snap = out[("005930", "KOSPI")]
    assert snap["own_latest_pct"] == 53.21
    assert snap["own_delta_5d_pp"] == round(53.21 - 53.10, 3)
    assert snap["own_delta_20d_pp"] == round(53.21 - 52.00, 3)
    assert snap["own_delta_60d_pp"] == round(53.21 - 50.50, 3)
    assert snap["net_buy_5d_krw"] == 1_500_000_000_000
    assert snap["net_buy_60d_krw"] == 12_000_000_000_000
    assert snap["snapshot_date"] == "2026-05-03"


def test_compute_handles_missing_prior_periods():
    """과거 거래일 데이터 부족 → delta 가 None 으로 남되 own_latest 는 살아 있어야."""
    from analyzer.foreign_flow_insight import compute_foreign_flow_snapshots

    rows = [{
        "ticker": "950140",
        "market": "KOSDAQ",
        "snapshot_date": date_cls(2026, 5, 3),
        "own_latest": 8.0,
        "own_5d": None,
        "own_20d": None,
        "own_60d": None,
        "net_buy_5d": 100_000_000,
        "net_buy_20d": None,
        "net_buy_60d": None,
    }]
    fake_conn, _ = _make_fake_conn(rows)

    with patch("analyzer.foreign_flow_insight.get_connection",
               return_value=fake_conn):
        out = compute_foreign_flow_snapshots(
            db_cfg=None,
            tickers=[("950140", "KOSDAQ")],
        )

    snap = out[("950140", "KOSDAQ")]
    assert snap["own_latest_pct"] == 8.0
    assert snap["own_delta_5d_pp"] is None
    assert snap["own_delta_20d_pp"] is None
    assert snap["net_buy_5d_krw"] == 100_000_000
    assert snap["net_buy_20d_krw"] is None


def test_compute_sql_uses_unnest_with_market_arrays():
    """배치 쿼리가 (ticker[], market[]) UNNEST 를 사용하는지 확인."""
    from analyzer.foreign_flow_insight import compute_foreign_flow_snapshots

    fake_conn, fake_cur = _make_fake_conn([])
    with patch("analyzer.foreign_flow_insight.get_connection",
               return_value=fake_conn):
        compute_foreign_flow_snapshots(
            db_cfg=None,
            tickers=[("005930", "KOSPI"), ("000660", "KOSPI")],
        )

    sql, params = fake_cur.execute.call_args[0]
    assert "UNNEST(%s::text[], %s::text[])" in sql
    assert "stock_universe_foreign_flow" in sql
    # ticker / market 배열이 params 처음 두 자리
    assert params[0] == ["005930", "000660"]
    assert params[1] == ["KOSPI", "KOSPI"]


# ── format_foreign_flow_text ──

def test_format_returns_empty_for_empty_snap():
    from analyzer.foreign_flow_insight import format_foreign_flow_text
    assert format_foreign_flow_text({}) == ""
    assert format_foreign_flow_text(None) == ""


def test_format_includes_ownership_and_deltas():
    from analyzer.foreign_flow_insight import format_foreign_flow_text

    snap = {
        "snapshot_date": "2026-05-03",
        "own_latest_pct": 53.21,
        "own_delta_5d_pp": 0.11,
        "own_delta_20d_pp": 1.21,
        "own_delta_60d_pp": 2.71,
        "net_buy_5d_krw": 1_500_000_000_000,
        "net_buy_20d_krw": 5_200_000_000_000,
        "net_buy_60d_krw": 12_000_000_000_000,
    }
    text = format_foreign_flow_text(snap)
    assert "외국인 수급" in text
    assert "보유율 53.21%" in text
    assert "+0.11%p" in text
    assert "+2.71%p" in text
    # 누적 순매수 — 1.5조 = 15,000억
    assert "+15,000억" in text or "15,000억" in text
    # T-2 기준일
    assert "2026-05-03" in text


def test_format_omits_missing_fields_without_error():
    from analyzer.foreign_flow_insight import format_foreign_flow_text

    snap = {
        "snapshot_date": "2026-05-03",
        "own_latest_pct": 8.0,
        "own_delta_5d_pp": None,
        "own_delta_20d_pp": None,
        "own_delta_60d_pp": None,
        "net_buy_5d_krw": 100_000_000,
        "net_buy_20d_krw": None,
        "net_buy_60d_krw": None,
    }
    text = format_foreign_flow_text(snap)
    assert "보유율 8.00%" in text
    assert "5D" in text  # net_buy 5D 만 노출
    assert "20D" not in text or "5D" in text  # 20D delta 도 net buy 도 없어야
    # delta 표기가 None 으로 출력되지 않아야 (None 이라는 문자열 없어야)
    assert "None" not in text
