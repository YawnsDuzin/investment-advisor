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
