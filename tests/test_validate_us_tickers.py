"""US 티커 화이트리스트 검증 단위 테스트.

stock_universe 조회는 DB mock — universe lookup 캐시 빌드 후의 매칭만 확인.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch


def _reset():
    from analyzer.stock_data import _reset_us_lookup
    _reset_us_lookup()


def _make_fake_conn(rows):
    """validate_us_tickers 는 `with conn.cursor() as cur` 패턴을 쓰므로 context manager 필요."""
    fake_cur = MagicMock()
    fake_cur.fetchall.return_value = rows
    fake_cur.execute = MagicMock()
    fake_cur.__enter__ = MagicMock(return_value=fake_cur)
    fake_cur.__exit__ = MagicMock(return_value=False)
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cur
    return fake_conn, fake_cur


def test_validate_skips_when_db_cfg_none():
    _reset()
    from analyzer.stock_data import validate_us_tickers
    out = validate_us_tickers(
        [{"ticker": "AAPL", "market": "NASDAQ", "asset_name": "Apple"}],
        db_cfg=None,
    )
    assert out == {"corrected": 0, "invalid": 0, "details": []}


def test_validate_returns_empty_when_universe_load_fails():
    _reset()
    from analyzer.stock_data import validate_us_tickers

    with patch("shared.db.get_connection", side_effect=RuntimeError("db down")):
        out = validate_us_tickers(
            [{"ticker": "AAPL", "market": "NASDAQ", "asset_name": "Apple"}],
            db_cfg="dummy",
        )
    assert out == {"corrected": 0, "invalid": 0, "details": []}


def test_validate_marks_unregistered_us_ticker_as_invalid():
    _reset()
    from analyzer.stock_data import validate_us_tickers

    fake_conn, _ = _make_fake_conn([("AAPL", "Apple Inc"), ("MSFT", "Microsoft")])
    with patch("shared.db.get_connection", return_value=fake_conn):
        out = validate_us_tickers(
            [
                {"ticker": "AAPL", "market": "NASDAQ", "asset_name": "Apple"},
                {"ticker": "FAKE_HAL", "market": "NASDAQ", "asset_name": "Hallucinated Co"},
            ],
            db_cfg="dummy",
        )

    assert out["corrected"] == 0
    assert out["invalid"] == 1
    assert any("FAKE_HAL" in d for d in out["details"])
    # AAPL 은 화이트리스트 일치 → details 에 안 들어감
    assert not any("AAPL" in d for d in out["details"])


def test_validate_skips_non_us_proposals():
    _reset()
    from analyzer.stock_data import validate_us_tickers

    fake_conn, _ = _make_fake_conn([("AAPL", "Apple Inc")])
    with patch("shared.db.get_connection", return_value=fake_conn):
        out = validate_us_tickers(
            [
                {"ticker": "005930", "market": "KOSPI", "asset_name": "삼성전자"},
                {"ticker": "AAPL", "market": "NASDAQ", "asset_name": "Apple"},
            ],
            db_cfg="dummy",
        )

    assert out["invalid"] == 0  # KOSPI 종목은 무시됨


def test_validate_caches_lookup_across_calls():
    """첫 호출에서 lookup 빌드 → 두 번째 호출은 DB 재조회 없이 캐시 사용."""
    _reset()
    from analyzer.stock_data import validate_us_tickers

    fake_conn, fake_cur = _make_fake_conn([("AAPL", "Apple Inc")])
    with patch("shared.db.get_connection", return_value=fake_conn) as gc:
        validate_us_tickers(
            [{"ticker": "AAPL", "market": "NASDAQ", "asset_name": "Apple"}],
            db_cfg="dummy",
        )
        validate_us_tickers(
            [{"ticker": "MSFT", "market": "NYSE", "asset_name": "Microsoft"}],
            db_cfg="dummy",
        )

    # get_connection 은 한 번만 호출
    assert gc.call_count == 1


def test_validate_handles_amex_market():
    """AMEX 도 US 시장으로 인식돼야 한다."""
    _reset()
    from analyzer.stock_data import validate_us_tickers

    fake_conn, _ = _make_fake_conn([("SPY", "SPDR S&P 500 ETF")])
    with patch("shared.db.get_connection", return_value=fake_conn):
        out = validate_us_tickers(
            [
                {"ticker": "SPY", "market": "AMEX", "asset_name": "SPY ETF"},
                {"ticker": "BOGUS", "market": "AMEX", "asset_name": "Bogus ETF"},
            ],
            db_cfg="dummy",
        )

    assert out["invalid"] == 1
    assert any("BOGUS" in d for d in out["details"])
