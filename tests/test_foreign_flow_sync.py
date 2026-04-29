"""foreign_flow_sync — pykrx 호출 + 컬럼 매핑 + 가드 테스트."""
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd

from analyzer import foreign_flow_sync


def _ownership_df():
    """get_exhaustion_rates_of_foreign_investment 가 반환할 모양."""
    df = pd.DataFrame(
        {"한도수량": [10, 20], "보유수량": [5, 12], "지분율": [50.0, 60.0]},
        index=pd.to_datetime(["2026-04-28", "2026-04-29"]),
    )
    return df


def _trading_value_df():
    """get_market_trading_value_by_date 가 반환할 모양."""
    df = pd.DataFrame(
        {
            "외국인합계": [100_000_000, -50_000_000],
            "기관합계":   [200_000_000,  30_000_000],
            "개인":       [-300_000_000, 20_000_000],
        },
        index=pd.to_datetime(["2026-04-28", "2026-04-29"]),
    )
    return df


def test_fetch_kr_investor_flow_happy_path():
    """두 API 모두 성공 → 영업일별 row 생성, 모든 컬럼 채워짐."""
    with patch.object(foreign_flow_sync, "_check_pykrx", return_value=True), \
         patch.object(foreign_flow_sync, "pykrx_stock") as mock_pykrx:
        mock_pykrx.get_exhaustion_rates_of_foreign_investment.return_value = _ownership_df()
        mock_pykrx.get_market_trading_value_by_date.return_value = _trading_value_df()

        rows = foreign_flow_sync.fetch_kr_investor_flow(
            "005930", date(2026, 4, 28), date(2026, 4, 29)
        )

    assert len(rows) == 2
    assert rows[0]["snapshot_date"] == date(2026, 4, 28)
    assert rows[0]["foreign_ownership_pct"] == 50.0
    assert rows[0]["foreign_net_buy_value"] == 100_000_000
    assert rows[0]["inst_net_buy_value"]    == 200_000_000
    assert rows[0]["retail_net_buy_value"]  == -300_000_000
    assert rows[0]["data_source"] == "pykrx"


def test_fetch_kr_investor_flow_pykrx_disabled():
    """pykrx 비활성 시 빈 리스트."""
    with patch.object(foreign_flow_sync, "_check_pykrx", return_value=False):
        rows = foreign_flow_sync.fetch_kr_investor_flow(
            "005930", date(2026, 4, 28), date(2026, 4, 29)
        )
    assert rows == []


def test_fetch_kr_investor_flow_partial_success():
    """ownership 만 성공, trading_value 빈 응답 → row 는 생성되지만 net_buy 컬럼 NULL."""
    with patch.object(foreign_flow_sync, "_check_pykrx", return_value=True), \
         patch.object(foreign_flow_sync, "pykrx_stock") as mock_pykrx:
        mock_pykrx.get_exhaustion_rates_of_foreign_investment.return_value = _ownership_df()
        mock_pykrx.get_market_trading_value_by_date.return_value = pd.DataFrame()

        rows = foreign_flow_sync.fetch_kr_investor_flow(
            "005930", date(2026, 4, 28), date(2026, 4, 29)
        )

    assert len(rows) == 2
    assert rows[0]["foreign_ownership_pct"] == 50.0
    assert rows[0]["foreign_net_buy_value"] is None
    assert rows[0]["inst_net_buy_value"]    is None
    assert rows[0]["retail_net_buy_value"]  is None


def test_fetch_kr_investor_flow_both_empty():
    """두 API 모두 빈 응답 → 빈 리스트."""
    with patch.object(foreign_flow_sync, "_check_pykrx", return_value=True), \
         patch.object(foreign_flow_sync, "pykrx_stock") as mock_pykrx:
        mock_pykrx.get_exhaustion_rates_of_foreign_investment.return_value = pd.DataFrame()
        mock_pykrx.get_market_trading_value_by_date.return_value = pd.DataFrame()

        rows = foreign_flow_sync.fetch_kr_investor_flow(
            "005930", date(2026, 4, 28), date(2026, 4, 29)
        )
    assert rows == []


def test_fetch_kr_investor_flow_non_korean_ticker():
    """비-숫자 티커 (US 종목 등) → 빈 리스트, pykrx 호출 안 함."""
    with patch.object(foreign_flow_sync, "_check_pykrx", return_value=True), \
         patch.object(foreign_flow_sync, "pykrx_stock") as mock_pykrx:
        rows = foreign_flow_sync.fetch_kr_investor_flow(
            "AAPL", date(2026, 4, 28), date(2026, 4, 29)
        )
    assert rows == []
    mock_pykrx.get_exhaustion_rates_of_foreign_investment.assert_not_called()


def test_upsert_investor_flow_executes_values():
    """upsert_investor_flow 가 execute_values 로 일괄 INSERT ... ON CONFLICT 실행."""
    from analyzer.foreign_flow_sync import upsert_investor_flow

    cur = MagicMock()
    rows = [
        {
            "ticker": "005930", "market": "KOSPI", "snapshot_date": date(2026, 4, 28),
            "foreign_ownership_pct": 51.5, "foreign_net_buy_value": 100,
            "inst_net_buy_value": 200, "retail_net_buy_value": -300,
            "data_source": "pykrx",
        },
        {
            "ticker": "035720", "market": "KOSPI", "snapshot_date": date(2026, 4, 28),
            "foreign_ownership_pct": 30.0, "foreign_net_buy_value": -50,
            "inst_net_buy_value": 0, "retail_net_buy_value": 50,
            "data_source": "pykrx",
        },
    ]
    with patch("analyzer.foreign_flow_sync.execute_values") as mock_exec:
        upsert_investor_flow(cur, rows)
    assert mock_exec.called
    args = mock_exec.call_args
    sql = args[0][1]
    assert "INSERT INTO stock_universe_foreign_flow" in sql
    assert "ON CONFLICT (ticker, market, snapshot_date) DO UPDATE" in sql
    assert "inst_net_buy_value" in sql and "retail_net_buy_value" in sql


def test_upsert_investor_flow_empty_noop():
    from analyzer.foreign_flow_sync import upsert_investor_flow
    cur = MagicMock()
    with patch("analyzer.foreign_flow_sync.execute_values") as mock_exec:
        upsert_investor_flow(cur, [])
    mock_exec.assert_not_called()


def test_sync_market_investor_flow_skips_failed_tickers():
    """fetch 가 빈 리스트 반환하는 종목은 skip, 성공한 종목만 row 누적."""
    from analyzer.foreign_flow_sync import sync_market_investor_flow

    def _fake_fetch(ticker, start, end):
        if ticker == "005930":
            return [{
                "ticker": "005930", "snapshot_date": date(2026, 4, 28),
                "foreign_ownership_pct": 51.5, "foreign_net_buy_value": 100,
                "inst_net_buy_value": 200, "retail_net_buy_value": -300,
                "data_source": "pykrx",
            }]
        return []

    cur = MagicMock()
    with patch("analyzer.foreign_flow_sync.fetch_kr_investor_flow", side_effect=_fake_fetch), \
         patch("analyzer.foreign_flow_sync.execute_values") as mock_exec:
        n = sync_market_investor_flow(
            cur, "KOSPI", ["005930", "FAILED1"], date(2026, 4, 28), date(2026, 4, 28),
            max_workers=2,
        )
    assert n == 1
    # market 이 row 에 채워졌는지 확인
    values = mock_exec.call_args[0][2]
    assert values[0][1] == "KOSPI"


def test_run_foreign_flow_sync_skips_when_disabled():
    from analyzer.foreign_flow_sync import run_foreign_flow_sync
    db_cfg = MagicMock()
    cfg = MagicMock(sync_enabled=False, max_consecutive_failures=0)
    result = run_foreign_flow_sync(db_cfg, cfg=cfg)
    assert result["total"] == 0


def test_run_foreign_flow_sync_calls_market_sync():
    from analyzer.foreign_flow_sync import run_foreign_flow_sync

    db_cfg = MagicMock()
    cfg = MagicMock(sync_enabled=True, max_consecutive_failures=50)

    fake_conn = MagicMock()
    fake_cur = MagicMock()
    fake_cur.fetchall.return_value = [
        ("005930", "KOSPI"), ("035720", "KOSPI"), ("247540", "KOSDAQ"),
    ]
    fake_conn.cursor.return_value.__enter__.return_value = fake_cur

    with patch("analyzer.foreign_flow_sync.get_connection", return_value=fake_conn), \
         patch("analyzer.foreign_flow_sync.sync_market_investor_flow", return_value=3) as mock_sync:
        result = run_foreign_flow_sync(
            db_cfg, cfg=cfg, snapshot_date=date(2026, 4, 28), backfill_days=0
        )

    # KOSPI + KOSDAQ 각각 1번 호출
    assert mock_sync.call_count == 2
    assert result["total"] == 6  # 2 markets × 3 rows


def test_run_foreign_flow_sync_backfill_days_expands_range():
    from analyzer.foreign_flow_sync import run_foreign_flow_sync

    cfg = MagicMock(sync_enabled=True, max_consecutive_failures=0)
    fake_conn = MagicMock()
    fake_cur = MagicMock()
    fake_cur.fetchall.return_value = [("005930", "KOSPI")]
    fake_conn.cursor.return_value.__enter__.return_value = fake_cur

    captured: dict = {}

    def _capture(cur, market, tickers, start, end, **kw):
        captured["start"] = start
        captured["end"] = end
        return 1

    with patch("analyzer.foreign_flow_sync.get_connection", return_value=fake_conn), \
         patch("analyzer.foreign_flow_sync.sync_market_investor_flow", side_effect=_capture):
        run_foreign_flow_sync(
            MagicMock(), cfg=cfg, snapshot_date=date(2026, 4, 28), backfill_days=90
        )
    assert captured["start"] == date(2026, 4, 28) - timedelta(days=90)
    assert captured["end"] == date(2026, 4, 28)
