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


def test_upsert_executes_correct_sql(monkeypatch):
    """단일 row UPSERT — execute_values 호출됨 (sanity check)."""
    captured = {}
    def fake_execute_values(cur, sql, rows, **kw):
        captured["called"] = True
        captured["row_count"] = len(list(rows))
    monkeypatch.setattr("analyzer.fundamentals_sync.execute_values", fake_execute_values)
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
    assert captured["rows"][0] == (
        "AAPL", "NASDAQ", _date(2026, 4, 25),
        25.4, 8.1, 6.13, 19.2, 0.96, 0.58, "yfinance_info",
    )


def test_sync_kr_market_iterates_tickers(monkeypatch):
    """KR 종목 리스트 → 각 종목 fetch → upsert 호출."""
    captured_rows = []

    def fake_fetch(ticker, snap_date):
        return {
            "per": 10.0, "pbr": 1.0, "eps": 100, "bps": 1000,
            "dps": 50, "dividend_yield": 2.0, "data_source": "pykrx",
        }

    def fake_upsert(cur, rows):
        captured_rows.extend(rows)

    monkeypatch.setattr("analyzer.fundamentals_sync.fetch_kr_fundamental", fake_fetch)
    monkeypatch.setattr("analyzer.fundamentals_sync.upsert_fundamentals", fake_upsert)

    cur = MagicMock()
    from analyzer.fundamentals_sync import sync_market_fundamentals
    n = sync_market_fundamentals(
        cur, market="KOSPI",
        tickers=["005930", "000660", "035420"],
        snapshot_date=_date(2026, 4, 25),
    )
    assert n == 3
    assert len(captured_rows) == 3
    assert all(r["snapshot_date"] == _date(2026, 4, 25) for r in captured_rows)
    assert all(r["market"] == "KOSPI" for r in captured_rows)
    assert {r["ticker"] for r in captured_rows} == {"005930", "000660", "035420"}


def test_sync_skips_missing_tickers(monkeypatch):
    """fetch가 None 반환한 종목은 upsert에 포함되지 않음."""
    captured_rows = []
    monkeypatch.setattr(
        "analyzer.fundamentals_sync.fetch_kr_fundamental",
        lambda t, d: None if t == "BAD" else {
            "per": 10, "pbr": 1, "eps": 100, "bps": 1000,
            "dps": 50, "dividend_yield": 2.0, "data_source": "pykrx",
        },
    )
    monkeypatch.setattr(
        "analyzer.fundamentals_sync.upsert_fundamentals",
        lambda cur, rows: captured_rows.extend(rows),
    )
    cur = MagicMock()
    from analyzer.fundamentals_sync import sync_market_fundamentals
    n = sync_market_fundamentals(cur, "KOSPI", ["005930", "BAD"], _date(2026, 4, 25))
    assert n == 1
    assert {r["ticker"] for r in captured_rows} == {"005930"}


def test_sync_us_market_uses_yfinance(monkeypatch):
    """market이 NASDAQ/NYSE면 fetch_us_fundamental 사용."""
    captured = []
    monkeypatch.setattr(
        "analyzer.fundamentals_sync.fetch_us_fundamental",
        lambda t: {
            "per": 25, "pbr": 8, "eps": 6, "bps": 19,
            "dps": 1, "dividend_yield": 0.58, "data_source": "yfinance_info",
        },
    )
    monkeypatch.setattr(
        "analyzer.fundamentals_sync.upsert_fundamentals",
        lambda cur, rows: captured.extend(rows),
    )
    # KR fetcher가 호출되면 안 됨
    monkeypatch.setattr(
        "analyzer.fundamentals_sync.fetch_kr_fundamental",
        lambda *a, **kw: pytest.fail("KR fetcher should NOT be called for US market"),
    )
    from analyzer.fundamentals_sync import sync_market_fundamentals
    n = sync_market_fundamentals(MagicMock(), "NASDAQ", ["AAPL", "MSFT"], _date(2026, 4, 25))
    assert n == 2
    assert all(r["data_source"] == "yfinance_info" for r in captured)


def test_sync_aborts_on_consecutive_failures(monkeypatch):
    """max_consecutive_failures 이상 연속 실패 시 조기 종료."""
    call_log = []

    def fake_fetch(ticker):
        call_log.append(ticker)
        return None  # 모든 종목 실패

    monkeypatch.setattr("analyzer.fundamentals_sync.fetch_us_fundamental", fake_fetch)
    monkeypatch.setattr("analyzer.fundamentals_sync.upsert_fundamentals", lambda cur, rows: None)

    from analyzer.fundamentals_sync import sync_market_fundamentals
    n = sync_market_fundamentals(
        MagicMock(),
        "NASDAQ",
        [f"T{i}" for i in range(100)],
        _date(2026, 4, 25),
        max_consecutive_failures=10,
    )
    assert n == 0
    # 정확히 10건만 호출되고 break (남은 90건은 시도 안 함)
    assert len(call_log) == 10


def test_sync_consecutive_counter_resets_on_success(monkeypatch):
    """성공 사이에 끼인 실패는 누적되지 않음 (counter reset)."""
    def fake_fetch(ticker):
        # 짝수 번째만 성공
        idx = int(ticker[1:])
        if idx % 2 == 0:
            return {
                "per": 10, "pbr": 1, "eps": 100, "bps": 1000,
                "dps": 50, "dividend_yield": 2.0, "data_source": "yfinance_info",
            }
        return None
    monkeypatch.setattr("analyzer.fundamentals_sync.fetch_us_fundamental", fake_fetch)
    monkeypatch.setattr("analyzer.fundamentals_sync.upsert_fundamentals", lambda cur, rows: None)

    from analyzer.fundamentals_sync import sync_market_fundamentals
    # 100 종목 중 절반 실패해도 5건 연속이 안 되므로 끝까지 진행
    n = sync_market_fundamentals(
        MagicMock(),
        "NASDAQ",
        [f"T{i}" for i in range(100)],
        _date(2026, 4, 25),
        max_consecutive_failures=5,
    )
    assert n == 50  # 짝수 50개 성공


def test_run_fundamentals_sync_queries_universe(monkeypatch):
    """run_fundamentals_sync — stock_universe에서 활성 종목 시장별로 묶어 sync_market_fundamentals 호출."""
    fake_conn = MagicMock()
    fake_cur = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cur
    fake_cur.fetchall.return_value = [
        ("005930", "KOSPI"), ("000660", "KOSPI"),
        ("035420", "KOSDAQ"),
        ("AAPL", "NASDAQ"), ("MSFT", "NASDAQ"),
    ]

    monkeypatch.setattr(
        "analyzer.fundamentals_sync.get_connection",
        lambda cfg: fake_conn,
    )

    captured_calls = []
    def fake_sync(cur, market, tickers, snap, **kw):
        captured_calls.append((market, sorted(tickers)))
        return len(tickers)
    monkeypatch.setattr(
        "analyzer.fundamentals_sync.sync_market_fundamentals",
        fake_sync,
    )

    from analyzer.fundamentals_sync import run_fundamentals_sync
    from shared.config import DatabaseConfig, FundamentalsConfig
    total = run_fundamentals_sync(DatabaseConfig(), FundamentalsConfig())
    # 시장별로 한 번씩 호출
    by_market = {m: tk for m, tk in captured_calls}
    assert by_market["KOSPI"] == ["000660", "005930"]
    assert by_market["KOSDAQ"] == ["035420"]
    assert by_market["NASDAQ"] == ["AAPL", "MSFT"]
    assert total == 5


def test_run_fundamentals_sync_respects_disabled_flag(monkeypatch):
    """sync_enabled=False 면 즉시 0 반환 (DB 접속 안 함)."""
    monkeypatch.setattr(
        "analyzer.fundamentals_sync.get_connection",
        lambda cfg: pytest.fail("DB 접속이 호출되면 안 됨"),
    )
    from analyzer.fundamentals_sync import run_fundamentals_sync
    from shared.config import DatabaseConfig, FundamentalsConfig
    cfg = FundamentalsConfig()
    cfg.sync_enabled = False
    assert run_fundamentals_sync(DatabaseConfig(), cfg) == 0
