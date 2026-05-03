"""Market Quotes Bar — _fetch_market_quotes() 단위 테스트.

EOD 데이터(market_indices_ohlcv)를 4개 인덱스 × 21영업일 조회하여
카드 렌더용 dict 구조로 가공하는지 검증.
"""
from datetime import date
from unittest.mock import MagicMock


def _make_cursor(rows):
    """RealDictCursor 시뮬레이션 — fetchall() 반환값 주입."""
    cur = MagicMock()
    cur.fetchall.return_value = rows
    return cur


def _row(index_code, trade_date, close):
    return {"index_code": index_code, "trade_date": trade_date, "close": close}


class TestFetchMarketQuotesHappyPath:
    def test_returns_four_indices_with_21_close_points(self):
        from api.routes.dashboard import _fetch_market_quotes

        # 4개 인덱스 × 21 row, 종가는 단순 증가 (trend=up 보장)
        rows = []
        for code in ("KOSPI", "KOSDAQ", "SP500", "NDX100"):
            base = {"KOSPI": 2500, "KOSDAQ": 800, "SP500": 5700, "NDX100": 20500}[code]
            for i in range(21):
                rows.append(_row(code, date(2026, 4, 2 + i), base + i * 5))
        cur = _make_cursor(rows)

        result = _fetch_market_quotes(cur)

        assert len(result["indices"]) == 4
        codes = [ix["code"] for ix in result["indices"]]
        assert set(codes) == {"KOSPI", "KOSDAQ", "SP500", "NDX100"}

        kospi = next(ix for ix in result["indices"] if ix["code"] == "KOSPI")
        # 21 포인트 (sparkline 전체 윈도우)
        assert len(kospi["spark_points"]) == 21
        # 마지막 = 최신 종가
        assert kospi["close"] == kospi["spark_points"][-1]
        # 등락률 = (last - prev) / prev * 100 = (2600 - 2595) / 2595 * 100
        assert kospi["change_pct"] == round((2600 - 2595) / 2595 * 100, 2)
        # 절대 변화 = 5
        assert kospi["change_abs"] == 5
        # 종가가 단조증가 → trend=up
        assert kospi["trend"] == "up"
        # trade_date = 마지막 row 의 date
        assert kospi["trade_date"] == date(2026, 4, 22)

    def test_meta_splits_kr_and_us_trade_dates(self):
        from api.routes.dashboard import _fetch_market_quotes

        rows = []
        # KR 인덱스: 4/22 까지
        for code in ("KOSPI", "KOSDAQ"):
            for i in range(21):
                rows.append(_row(code, date(2026, 4, 2 + i), 2500 + i))
        # US 인덱스: 4/21 까지 (KR보다 1일 이전 — US 휴장일 가정)
        for code in ("SP500", "NDX100"):
            for i in range(21):
                rows.append(_row(code, date(2026, 4, 1 + i), 5700 + i))
        cur = _make_cursor(rows)

        result = _fetch_market_quotes(cur)

        assert result["meta"]["kr_trade_date"] == date(2026, 4, 22)
        assert result["meta"]["us_trade_date"] == date(2026, 4, 21)


class TestFetchMarketQuotesEdgeCases:
    def test_empty_table_returns_no_indices(self):
        from api.routes.dashboard import _fetch_market_quotes
        cur = _make_cursor([])
        result = _fetch_market_quotes(cur)
        assert result["indices"] == []
        assert result["meta"]["kr_trade_date"] is None
        assert result["meta"]["us_trade_date"] is None

    def test_partial_indices_only_kr_present(self):
        from api.routes.dashboard import _fetch_market_quotes
        rows = []
        for code in ("KOSPI", "KOSDAQ"):
            for i in range(21):
                rows.append(_row(code, date(2026, 4, 2 + i), 2500 + i))
        cur = _make_cursor(rows)

        result = _fetch_market_quotes(cur)

        codes = [ix["code"] for ix in result["indices"]]
        assert set(codes) == {"KOSPI", "KOSDAQ"}
        assert result["meta"]["kr_trade_date"] == date(2026, 4, 22)
        assert result["meta"]["us_trade_date"] is None

    def test_single_row_no_change_pct(self):
        from api.routes.dashboard import _fetch_market_quotes
        rows = [_row("KOSPI", date(2026, 4, 22), 2600.0)]
        cur = _make_cursor(rows)

        result = _fetch_market_quotes(cur)

        kospi = result["indices"][0]
        assert kospi["close"] == 2600.0
        assert kospi["change_pct"] is None
        assert kospi["change_abs"] is None
        assert kospi["spark_points"] == [2600.0]
        assert kospi["trend"] == "flat"

    def test_trend_down_when_change_negative(self):
        from api.routes.dashboard import _fetch_market_quotes
        rows = [
            _row("KOSPI", date(2026, 4, 21), 2700.0),
            _row("KOSPI", date(2026, 4, 22), 2600.0),
        ]
        cur = _make_cursor(rows)
        result = _fetch_market_quotes(cur)
        kospi = result["indices"][0]
        assert kospi["trend"] == "down"
        assert kospi["change_pct"] == round((2600 - 2700) / 2700 * 100, 2)
        assert kospi["change_abs"] == -100.0

    def test_trend_flat_when_change_zero(self):
        from api.routes.dashboard import _fetch_market_quotes
        rows = [
            _row("KOSPI", date(2026, 4, 21), 2600.0),
            _row("KOSPI", date(2026, 4, 22), 2600.0),
        ]
        cur = _make_cursor(rows)
        result = _fetch_market_quotes(cur)
        assert result["indices"][0]["trend"] == "flat"

    def test_sql_execute_arguments(self):
        """helper 가 올바른 SQL 과 파라미터로 cursor.execute 를 호출하는지."""
        from api.routes.dashboard import _fetch_market_quotes
        cur = _make_cursor([])
        _fetch_market_quotes(cur)

        cur.execute.assert_called_once()
        sql, params = cur.execute.call_args[0]
        assert "market_indices_ohlcv" in sql
        assert "ROW_NUMBER() OVER" in sql
        assert "PARTITION BY index_code" in sql
        # 파라미터 3종: codes list / lookback / window
        assert params[0] == ["KOSPI", "KOSDAQ", "SP500", "NDX100"]
        assert params[1] == 60   # _MARKET_QUOTE_LOOKBACK_DAYS
        assert params[2] == 21   # _MARKET_QUOTE_WINDOW
