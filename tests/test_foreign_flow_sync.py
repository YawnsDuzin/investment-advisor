"""foreign_flow_sync — pykrx 호출 + 컬럼 매핑 + 가드 테스트."""
from datetime import date
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
