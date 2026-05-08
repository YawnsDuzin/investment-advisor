"""api/chat_citations.py — 답변 텍스트 → 종목·테마 추출 단위 테스트 (Tier 1 #6).

Fake conn/cursor 로 SQL 결과를 흉내. extract_citations / attach_citations_to_messages
양쪽의 그래이스풀 폴백·구조 검증.
"""
from __future__ import annotations

from datetime import date

from api.chat_citations import extract_citations, attach_citations_to_messages


# ── Fake DB primitives ────────────────────────────


class _FakeCursor:
    """SQL 호출 순서대로 미리 준비된 결과를 돌려주는 페이크 cursor."""

    def __init__(self, queued):
        self._queue = list(queued)
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
        return []

    def fetchone(self):
        v = self._current
        if isinstance(v, dict):
            return v
        return None


class _FakeConn:
    def __init__(self, queued, raise_on_cursor=False):
        self._cursor = _FakeCursor(queued)
        self._raise = raise_on_cursor
        self.rolled_back = False

    def cursor(self, cursor_factory=None):
        if self._raise:
            raise RuntimeError("DB 폭발")
        return self._cursor

    def rollback(self):
        self.rolled_back = True


# ── extract_citations ─────────────────────────────


def test_extract_citations_returns_empty_for_empty_text():
    out = extract_citations("", _FakeConn([]))
    assert out == {"tickers": [], "themes": []}


def test_extract_citations_returns_empty_when_conn_none():
    out = extract_citations("삼성전자", None)
    assert out == {"tickers": [], "themes": []}


def test_extract_citations_finds_ticker_via_substring():
    """텍스트에 종목명 등장 → DB substring 매칭으로 ticker 카드 반환."""
    direct_match = []  # 직접 ticker 정규식 결과 없음 (한글 회사명만)
    substring_match = [
        {
            "ticker": "005930", "market": "KOSPI",
            "asset_name": "삼성전자", "sector_norm": "Semiconductors",
            "currency": "KRW", "market_cap_krw": 500_000_000_000_000,
        }
    ]
    theme_match = []  # 테마 매칭 없음
    conn = _FakeConn([direct_match, substring_match, theme_match])

    out = extract_citations(
        "삼성전자 HBM 점유율이 어떻게 되나요?", conn,
    )
    assert len(out["tickers"]) == 1
    assert out["tickers"][0]["ticker"] == "005930"
    assert out["tickers"][0]["asset_name"] == "삼성전자"
    assert out["themes"] == []


def test_extract_citations_finds_direct_us_ticker():
    """미국 ticker 정규식 (AAPL) → DB validate."""
    direct_match = [
        {
            "ticker": "AAPL", "market": "NASDAQ",
            "asset_name": "Apple Inc", "sector_norm": "Technology",
            "currency": "USD", "market_cap_krw": 4_000_000_000_000_000,
        }
    ]
    substring_match = []
    theme_match = []
    conn = _FakeConn([direct_match, substring_match, theme_match])

    out = extract_citations("AAPL 어떻게 보세요", conn)
    assert len(out["tickers"]) == 1
    assert out["tickers"][0]["ticker"] == "AAPL"


def test_extract_citations_dedupes_substring_after_direct():
    """direct + substring 모두 같은 종목 매칭 → 1건만."""
    direct_match = [
        {
            "ticker": "005930", "market": "KOSPI",
            "asset_name": "삼성전자", "sector_norm": "Semiconductors",
            "currency": "KRW", "market_cap_krw": 500_000_000_000_000,
        }
    ]
    substring_match = [
        {
            "ticker": "005930", "market": "KOSPI",
            "asset_name": "삼성전자", "sector_norm": "Semiconductors",
            "currency": "KRW", "market_cap_krw": 500_000_000_000_000,
        }
    ]
    theme_match = []
    conn = _FakeConn([direct_match, substring_match, theme_match])

    out = extract_citations("005930 삼성전자 어떻게 봐요", conn)
    assert len(out["tickers"]) == 1


def test_extract_citations_finds_theme():
    """investment_themes 매칭."""
    direct_match = []
    substring_match = []
    theme_match = [
        {
            "theme_id": 42, "theme_name": "AI 반도체", "theme_key": "ai_semi",
            "session_id": 100, "confidence_score": 0.85,
            "analysis_date": date(2026, 5, 1),
        }
    ]
    conn = _FakeConn([direct_match, substring_match, theme_match])
    out = extract_citations("AI 반도체 사이클 끝물인가요?", conn)
    assert out["themes"][0]["theme_name"] == "AI 반도체"
    assert out["themes"][0]["theme_key"] == "ai_semi"


def test_extract_citations_swallows_db_errors_silently():
    """DB 폭발 → 채팅은 안 죽음 (빈 결과 반환)."""
    conn = _FakeConn([], raise_on_cursor=True)
    out = extract_citations("삼성전자", conn)
    assert out == {"tickers": [], "themes": []}


def test_extract_citations_caps_tickers_to_max_5():
    """6개 이상 매칭 시 시총 큰 순 5개만.

    텍스트에 직접 ticker 가 없어 direct_match SQL execute 는 skip → execute 2번:
      ① substring SQL → big_caps
      ② theme SQL → []
    """
    big_caps = []
    for i in range(8):
        big_caps.append({
            "ticker": f"00593{i}", "market": "KOSPI",
            "asset_name": f"테스트종목{i}", "sector_norm": "Tech",
            "currency": "KRW", "market_cap_krw": (8 - i) * 1_000_000_000_000,
        })
    conn = _FakeConn([big_caps, []])

    out = extract_citations("테스트종목 후보가 많아요", conn)
    assert len(out["tickers"]) == 5
    # 시총 큰 순 (0번이 가장 큼 8조)
    assert out["tickers"][0]["ticker"] == "005930"


# ── attach_citations_to_messages ──────────────────


def test_attach_citations_only_modifies_assistant_messages():
    """user 메시지는 무변경, assistant 메시지에만 citations 주입."""
    direct_match = []
    sub_match = [{
        "ticker": "005930", "market": "KOSPI", "asset_name": "삼성전자",
        "sector_norm": "Semi", "currency": "KRW", "market_cap_krw": 5e14,
    }]
    theme_match = []
    conn = _FakeConn([direct_match, sub_match, theme_match])

    messages = [
        {"role": "user", "content": "삼성전자 어때?"},
        {"role": "assistant", "content": "삼성전자는 HBM 점유율 1위입니다."},
    ]
    attach_citations_to_messages(messages, conn)

    assert "citations" not in messages[0]
    assert "citations" in messages[1]
    assert messages[1]["citations"]["tickers"][0]["ticker"] == "005930"


def test_attach_citations_skips_when_no_match():
    """매칭 0건 → citations 필드 자체를 안 붙임 (템플릿 if 분기 단순화)."""
    direct_match = []
    sub_match = []
    theme_match = []
    conn = _FakeConn([direct_match, sub_match, theme_match])

    messages = [
        {"role": "assistant", "content": "전반적으로 시장이 횡보하고 있습니다."},
    ]
    attach_citations_to_messages(messages, conn)
    assert "citations" not in messages[0]


def test_attach_citations_handles_empty_input():
    """빈 messages / None conn → 그대로 반환."""
    assert attach_citations_to_messages([], None) == []
    assert attach_citations_to_messages([], _FakeConn([])) == []
    assert attach_citations_to_messages([{"role": "user", "content": "x"}], None) == [
        {"role": "user", "content": "x"}
    ]


def test_attach_citations_robust_to_malformed_messages():
    """msg 가 dict 가 아니거나 content 없음 → 건너뜀."""
    conn = _FakeConn([])
    messages = [
        "not a dict",  # noqa
        {"role": "assistant"},  # content 없음
        {"role": "assistant", "content": ""},
    ]
    # 예외 없이 통과
    out = attach_citations_to_messages(messages, conn)
    assert out is messages
