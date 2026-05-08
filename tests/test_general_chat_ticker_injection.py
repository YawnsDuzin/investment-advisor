"""general_chat_engine 의 메시지 → 티커 추출 + DB 스냅샷 주입 (v1: DB only).

외부 API 호출 없이 stock_universe / stock_universe_ohlcv / investment_proposals
3개 테이블만으로 컨텍스트 합성하는지 검증.
"""
from __future__ import annotations

import importlib
import os
import sys
from datetime import date
from unittest.mock import patch


# conftest.py 가 psycopg2 를 mock 하지만 그건 import 단계용 — 본 테스트는
# fake conn/cursor 를 helper 에 주입한다.


class _FakeCursor:
    """SQL 호출 순서대로 미리 준비된 결과를 돌려주는 페이크 cursor.

    각 execute() 호출 후 fetchall() / fetchone() 이 큐의 다음 항목을 꺼낸다.
    """

    def __init__(self, queued_results):
        self._queue = list(queued_results)
        self._current = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self._current = self._queue.pop(0) if self._queue else None

    def fetchall(self):
        v = self._current
        if isinstance(v, list):
            return v
        if v is None:
            return []
        return []

    def fetchone(self):
        v = self._current
        if isinstance(v, dict):
            return v
        if isinstance(v, list):
            return v[0] if v else None
        return None


class _FakeConn:
    def __init__(self, results, raise_on_execute=False):
        self._cursor = _FakeCursor(results)
        self._raise = raise_on_execute
        self.rolled_back = False

    def cursor(self, cursor_factory=None):
        if self._raise:
            raise RuntimeError("DB 폭발")
        return self._cursor

    def rollback(self):
        self.rolled_back = True


# 모듈을 ticker injection 활성화 상태로 import
def _reload_engine(env: dict):
    with patch.dict(os.environ, env, clear=False):
        if "api.general_chat_engine" in sys.modules:
            del sys.modules["api.general_chat_engine"]
        import api.general_chat_engine  # noqa: F401
        return importlib.import_module("api.general_chat_engine")


def test_extract_direct_korean_ticker():
    """6자리 KRX 티커 직접 입력 — DB validate 후 매칭."""
    engine = _reload_engine({"GENERAL_CHAT_TICKER_INJECTION": "true"})
    direct_match = [
        {
            "ticker": "005930",
            "market": "KOSPI",
            "asset_name": "삼성전자",
            "asset_name_en": "Samsung Electronics",
            "currency": "KRW",
            "market_cap_krw": 500_000_000_000_000,
        }
    ]
    substring_match: list = []
    conn = _FakeConn([direct_match, substring_match])

    out = engine._extract_tickers_from_message(conn, "005930 어떻게 보세요?")

    assert len(out) == 1
    assert out[0]["ticker"] == "005930"
    assert out[0]["market"] == "KOSPI"


def test_extract_direct_us_ticker():
    """대문자 영문 티커 — `AAPL` substring."""
    engine = _reload_engine({"GENERAL_CHAT_TICKER_INJECTION": "true"})
    direct_match = [
        {
            "ticker": "AAPL",
            "market": "NASDAQ",
            "asset_name": "Apple Inc",
            "asset_name_en": "Apple Inc",
            "currency": "USD",
            "market_cap_krw": 3_000_000_000_000_000,
        }
    ]
    conn = _FakeConn([direct_match, []])
    out = engine._extract_tickers_from_message(conn, "AAPL 사도 될까")
    assert len(out) == 1
    assert out[0]["ticker"] == "AAPL"


def test_extract_korean_company_name():
    """한국어 종목명 substring 매칭 — '삼성전자' 가 메시지에 포함.

    한국어만 있는 메시지는 직접 티커 후보가 비어 있어 첫 execute 가 스킵된다.
    → 큐는 substring 매칭 결과 1개만.
    """
    engine = _reload_engine({"GENERAL_CHAT_TICKER_INJECTION": "true"})
    substring_match = [
        {
            "ticker": "005930",
            "market": "KOSPI",
            "asset_name": "삼성전자",
            "asset_name_en": "Samsung Electronics",
            "currency": "KRW",
            "market_cap_krw": 500_000_000_000_000,
        }
    ]
    conn = _FakeConn([substring_match])
    out = engine._extract_tickers_from_message(conn, "삼성전자 좋아 보이는데")
    assert len(out) == 1
    assert out[0]["asset_name"] == "삼성전자"


def test_extract_empty_message_skips_db():
    """빈 메시지 → DB 호출 없이 빈 결과."""
    engine = _reload_engine({"GENERAL_CHAT_TICKER_INJECTION": "true"})
    conn = _FakeConn([])  # 결과 없음 — execute 호출되면 None 받음
    assert engine._extract_tickers_from_message(conn, "") == []
    assert engine._extract_tickers_from_message(conn, "   ") == []


def test_extract_db_failure_returns_empty_and_rolls_back():
    """DB 폭발 시 안전 폴백 — 빈 리스트 + 롤백."""
    engine = _reload_engine({"GENERAL_CHAT_TICKER_INJECTION": "true"})
    conn = _FakeConn([], raise_on_execute=True)
    out = engine._extract_tickers_from_message(conn, "삼성전자 어때")
    assert out == []
    assert conn.rolled_back is True


def test_extract_priority_market_cap_desc():
    """여러 종목 매칭 시 market_cap_krw DESC 정렬, 5개 한도."""
    engine = _reload_engine({"GENERAL_CHAT_TICKER_INJECTION": "true"})
    rows = [
        {"ticker": f"{i:06d}", "market": "KOSPI", "asset_name": f"종목{i}",
         "asset_name_en": None, "currency": "KRW",
         "market_cap_krw": (10 - i) * 1_000_000_000}
        for i in range(8)
    ]
    # 한국어만 있는 메시지 → direct_tickers 비어있음 → 첫 execute 스킵
    conn = _FakeConn([rows])
    out = engine._extract_tickers_from_message(conn, "여러 종목 언급")
    assert len(out) == 5  # _MAX_TICKERS_PER_MESSAGE
    caps = [r["market_cap_krw"] for r in out]
    assert caps == sorted(caps, reverse=True)


def test_fetch_snapshot_formats_price_and_stats():
    """OHLCV 종가 + proposals 통계가 형식대로 라인 포맷되는지."""
    engine = _reload_engine({"GENERAL_CHAT_TICKER_INJECTION": "true"})
    tickers = [
        {
            "ticker": "005930", "market": "KOSPI", "asset_name": "삼성전자",
            "currency": "KRW", "market_cap_krw": 500_000_000_000_000,
        }
    ]
    ohlcv_row = {
        "trade_date": date(2026, 5, 7),
        "close": 78500.0,
        "change_pct": 1.23,
    }
    stats_row = {
        "proposal_count": 3,
        "avg_3m": 5.5,
        "last_recommended": date(2026, 5, 1),
    }
    conn = _FakeConn([ohlcv_row, stats_row])
    out = engine._fetch_ticker_snapshot(conn, tickers)
    assert "삼성전자" in out
    assert "005930" in out
    assert "₩78,500" in out
    assert "+1.23%" in out
    assert "2026-05-07" in out
    assert "누적 추천 3회" in out
    assert "+5.5%" in out


def test_fetch_snapshot_handles_missing_ohlcv():
    """OHLCV 결측 종목 — '데이터 없음' 라인."""
    engine = _reload_engine({"GENERAL_CHAT_TICKER_INJECTION": "true"})
    tickers = [
        {
            "ticker": "999999", "market": "KOSDAQ", "asset_name": "신규상장",
            "currency": "KRW", "market_cap_krw": None,
        }
    ]
    conn = _FakeConn([None, {"proposal_count": 0, "avg_3m": None, "last_recommended": None}])
    out = engine._fetch_ticker_snapshot(conn, tickers)
    assert "데이터 없음" in out


def test_fetch_snapshot_empty_tickers_returns_empty():
    engine = _reload_engine({"GENERAL_CHAT_TICKER_INJECTION": "true"})
    assert engine._fetch_ticker_snapshot(_FakeConn([]), []) == ""


def test_build_user_context_disabled_skips_extraction():
    """환경변수 false 시 메시지가 와도 추출 스킵."""
    engine = _reload_engine({"GENERAL_CHAT_TICKER_INJECTION": "false"})
    # _extract_tickers_from_message 가 호출되면 conn.cursor() 가 호출됨 → False 면 안 함
    called = {"n": 0}

    class _SpyConn(_FakeConn):
        def cursor(self, cursor_factory=None):
            called["n"] += 1
            return super().cursor(cursor_factory)

    conn = _SpyConn([], raise_on_execute=False)
    # user_id=None 이고 ticker injection disabled → 빈 컨텍스트
    out = engine.build_user_context(conn, None, user_message="삼성전자 어때")
    assert out == ""
    assert called["n"] == 0  # cursor 호출 자체가 없어야 함


def test_build_user_context_anonymous_with_ticker_injection():
    """비로그인(user_id=None) + 메시지 언급 종목 → 스냅샷만 주입."""
    engine = _reload_engine({"GENERAL_CHAT_TICKER_INJECTION": "true"})
    direct_match = [
        {
            "ticker": "AAPL", "market": "NASDAQ", "asset_name": "Apple Inc",
            "asset_name_en": "Apple Inc", "currency": "USD",
            "market_cap_krw": 3_000_000_000_000_000,
        }
    ]
    ohlcv_row = {"trade_date": date(2026, 5, 7), "close": 215.50, "change_pct": -0.5}
    stats_row = {"proposal_count": 0, "avg_3m": None, "last_recommended": None}
    conn = _FakeConn([direct_match, [], ohlcv_row, stats_row])
    out = engine.build_user_context(conn, None, user_message="AAPL 사도 되나")
    assert "Apple Inc" in out
    assert "$215.50" in out
    assert "스냅샷" in out
    # 비로그인이라 워치리스트/추천 헤더는 없음
    assert "Watchlist" not in out
