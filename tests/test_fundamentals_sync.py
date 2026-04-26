"""펀더멘털 sync — pykrx/yfinance 분기 fetcher 검증."""
from datetime import date
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest


def test_fetch_kr_returns_normalized_dict(monkeypatch):
    """pykrx 응답 → 표준화된 dict (per/pbr/eps/bps/dps/dividend_yield) 변환."""
    monkeypatch.setattr("analyzer.fundamentals_sync._check_pykrx", lambda: True)
    fake_df = pd.DataFrame({
        "BPS": [50000], "PER": [12.5], "PBR": [0.95],
        "EPS": [4000], "DIV": [3.2], "DPS": [1600],
    }, index=["005930"])
    monkeypatch.setattr(
        "pykrx.stock.get_market_fundamental_by_date",
        lambda from_d, to_d, ticker: fake_df,
    )
    from analyzer.fundamentals_sync import fetch_kr_fundamental
    out = fetch_kr_fundamental("005930", date(2026, 4, 25))
    assert out["per"] == 12.5
    assert out["pbr"] == 0.95
    assert out["eps"] == 4000
    assert out["bps"] == 50000
    assert out["dps"] == 1600
    assert out["dividend_yield"] == 3.2
    assert out["data_source"] == "pykrx"


def test_fetch_kr_handles_empty_dataframe(monkeypatch):
    """pykrx 빈 DataFrame (휴장일/조회 실패) → None."""
    monkeypatch.setattr("analyzer.fundamentals_sync._check_pykrx", lambda: True)
    monkeypatch.setattr(
        "pykrx.stock.get_market_fundamental_by_date",
        lambda from_d, to_d, ticker: pd.DataFrame(),
    )
    from analyzer.fundamentals_sync import fetch_kr_fundamental
    assert fetch_kr_fundamental("000000", date(2026, 4, 25)) is None


def test_fetch_kr_handles_pykrx_exception(monkeypatch):
    """pykrx 예외 → None (sync는 계속 진행)."""
    monkeypatch.setattr("analyzer.fundamentals_sync._check_pykrx", lambda: True)
    def _raise(*a, **kw):
        raise RuntimeError("pykrx network error")
    monkeypatch.setattr("pykrx.stock.get_market_fundamental_by_date", _raise)
    from analyzer.fundamentals_sync import fetch_kr_fundamental
    assert fetch_kr_fundamental("005930", date(2026, 4, 25)) is None


def test_fetch_kr_returns_none_when_pykrx_missing(monkeypatch):
    """pykrx 미설치 → _check_pykrx() False → None."""
    monkeypatch.setattr("analyzer.fundamentals_sync._check_pykrx", lambda: False)
    from analyzer.fundamentals_sync import fetch_kr_fundamental
    assert fetch_kr_fundamental("005930", date(2026, 4, 25)) is None


def test_fetch_kr_disables_pykrx_on_login_failure(monkeypatch):
    """pykrx 인증 실패 → _disable_pykrx 호출됨 (세션 short-circuit)."""
    disable_calls = []
    monkeypatch.setattr("analyzer.fundamentals_sync._check_pykrx", lambda: True)
    monkeypatch.setattr("analyzer.fundamentals_sync._is_login_failure", lambda e: True)
    monkeypatch.setattr(
        "analyzer.fundamentals_sync._disable_pykrx",
        lambda reason: disable_calls.append(reason),
    )
    monkeypatch.setattr(
        "pykrx.stock.get_market_fundamental_by_date",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("login fail")),
    )
    from analyzer.fundamentals_sync import fetch_kr_fundamental
    assert fetch_kr_fundamental("005930", date(2026, 4, 25)) is None
    assert len(disable_calls) == 1
    assert "005930" in disable_calls[0]


def test_fetch_us_returns_normalized_dict(monkeypatch):
    """yfinance 응답 → 표준화된 dict (per/pbr/eps/bps/dps/dividend_yield) 변환."""
    fake_info = {
        "trailingPE": 25.4,
        "priceToBook": 8.1,
        "trailingEps": 6.13,
        "bookValue": 19.2,
        "dividendRate": 0.96,
        "dividendYield": 0.0058,   # yfinance returns ratio (0.58%)
    }
    fake_ticker = MagicMock()
    fake_ticker.info = fake_info
    monkeypatch.setattr("yfinance.Ticker", lambda t: fake_ticker)
    from analyzer.fundamentals_sync import fetch_us_fundamental
    out = fetch_us_fundamental("AAPL")
    assert out["per"] == 25.4
    assert out["pbr"] == 8.1
    assert out["eps"] == 6.13
    assert out["bps"] == 19.2
    assert out["dps"] == 0.96
    # dividend_yield는 % 단위로 정규화 (0.0058 → 0.58)
    assert abs(out["dividend_yield"] - 0.58) < 0.001
    assert out["data_source"] == "yfinance_info"


def test_fetch_us_handles_missing_keys(monkeypatch):
    """yfinance.info에 키가 일부 누락 → 해당 필드만 None."""
    fake_ticker = MagicMock()
    fake_ticker.info = {"trailingPE": 10.0}  # 나머지 키 없음
    monkeypatch.setattr("yfinance.Ticker", lambda t: fake_ticker)
    from analyzer.fundamentals_sync import fetch_us_fundamental
    out = fetch_us_fundamental("XXX")
    assert out["per"] == 10.0
    assert out["pbr"] is None
    assert out["eps"] is None
    assert out["dividend_yield"] is None
    assert out["data_source"] == "yfinance_info"


def test_fetch_us_handles_yfinance_exception(monkeypatch):
    """yfinance 예외 → None (sync는 계속 진행)."""
    def _raise(t):
        raise RuntimeError("yfinance throttled")
    monkeypatch.setattr("yfinance.Ticker", _raise)
    from analyzer.fundamentals_sync import fetch_us_fundamental
    assert fetch_us_fundamental("AAPL") is None


def test_fetch_us_handles_empty_info(monkeypatch):
    """info가 빈 dict → None (수집할 가치 없음)."""
    fake_ticker = MagicMock()
    fake_ticker.info = {}
    monkeypatch.setattr("yfinance.Ticker", lambda t: fake_ticker)
    from analyzer.fundamentals_sync import fetch_us_fundamental
    assert fetch_us_fundamental("AAPL") is None


def test_fetch_us_returns_none_when_yfinance_missing(monkeypatch):
    """yfinance 미설치 → yf is None → None."""
    import analyzer.fundamentals_sync as mod
    monkeypatch.setattr(mod, "yf", None)
    assert mod.fetch_us_fundamental("AAPL") is None


from datetime import date as _date


def test_upsert_executes_correct_sql():
    """단일 row UPSERT — execute_values 호출됨 (sanity check)."""
    captured = {}
    def fake_execute_values(cur, sql, rows, **kw):
        captured["called"] = True
        captured["row_count"] = len(list(rows))

    # monkeypatch으로 execute_values 교체
    import analyzer.fundamentals_sync as mod
    original = mod.execute_values
    try:
        mod.execute_values = fake_execute_values
        cur = MagicMock()
        rows = [
            {"ticker": "005930", "market": "KOSPI", "snapshot_date": _date(2026, 4, 25),
             "per": 12.5, "pbr": 0.95, "eps": 4000, "bps": 50000,
             "dps": 1600, "dividend_yield": 3.2, "data_source": "pykrx"},
        ]
        from analyzer.fundamentals_sync import upsert_fundamentals
        upsert_fundamentals(cur, rows)
        assert captured["called"]
        assert captured["row_count"] == 1
    finally:
        mod.execute_values = original


def test_upsert_skips_empty_rows():
    """빈 리스트 → execute 호출 없음."""
    cur = MagicMock()
    from analyzer.fundamentals_sync import upsert_fundamentals
    upsert_fundamentals(cur, [])
    assert not cur.execute.called
    assert not cur.executemany.called


def test_upsert_uses_on_conflict(monkeypatch):
    """SQL에 ON CONFLICT (ticker, market, snapshot_date) DO UPDATE 포함 검증."""
    captured = {}
    def fake_execute_values(cur, sql, rows, **kw):
        captured["sql"] = sql
        captured["rows"] = list(rows)
    monkeypatch.setattr(
        "analyzer.fundamentals_sync.execute_values",
        fake_execute_values,
    )
    cur = MagicMock()
    rows = [{
        "ticker": "AAPL", "market": "NASDAQ",
        "snapshot_date": _date(2026, 4, 25),
        "per": 25.4, "pbr": 8.1, "eps": 6.13,
        "bps": 19.2, "dps": 0.96, "dividend_yield": 0.58,
        "data_source": "yfinance_info",
    }]
    from analyzer.fundamentals_sync import upsert_fundamentals
    upsert_fundamentals(cur, rows)
    sql_upper = captured["sql"].upper()
    assert "INSERT INTO STOCK_UNIVERSE_FUNDAMENTALS" in sql_upper
    assert "ON CONFLICT (TICKER, MARKET, SNAPSHOT_DATE) DO UPDATE" in sql_upper
    assert len(captured["rows"]) == 1
