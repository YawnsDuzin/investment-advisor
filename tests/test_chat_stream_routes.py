# tests/test_chat_stream_routes.py
"""SSE 라우트 권한 가드 + broker/DB 폴백 분기 테스트."""
from __future__ import annotations
import asyncio
from types import SimpleNamespace
from unittest.mock import patch
import pytest


@pytest.mark.asyncio
async def test_format_sse_produces_valid_event_stream():
    from api.routes.chat_stream import _format_sse
    out = _format_sse("token", {"text": "hi"})
    assert out == b'event: token\ndata: {"text": "hi"}\n\n'


@pytest.mark.asyncio
async def test_format_sse_korean_unescaped():
    from api.routes.chat_stream import _format_sse
    out = _format_sse("token", {"text": "안녕"})
    # ensure_ascii=False 라 UTF-8 그대로
    assert b"\xec\x95\x88\xeb\x85\x95" in out  # "안녕" UTF-8 바이트


@pytest.mark.asyncio
async def test_db_fallback_assistant_last_emits_done():
    """마지막 메시지가 assistant → done 즉시 + 종료."""
    from api.routes.chat_stream import _db_fallback_stream

    # Mock conn / cursor — 마지막 메시지 = assistant
    class MockCursor:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, sql, params): self._params = params
        def fetchone(self):
            return {"id": 42, "role": "assistant", "content": "이미 답변"}

    class MockConn:
        def cursor(self, cursor_factory=None): return MockCursor()

    cfg = {"messages_table": "general_chat_messages",
           "session_fk": "chat_session_id"}

    class MockReq:
        async def is_disconnected(self): return False

    events = []
    async for ev, payload in _db_fallback_stream(
        MockConn(), "general", cfg, 100, MockReq()
    ):
        events.append((ev, payload))
    assert events == [("done", {"message_id": 42, "final_text": "이미 답변"})]


@pytest.mark.asyncio
async def test_db_fallback_no_messages_emits_idle_then_returns():
    from api.routes.chat_stream import _db_fallback_stream

    class MockCursor:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, sql, params): pass
        def fetchone(self): return None

    class MockConn:
        def cursor(self, cursor_factory=None): return MockCursor()

    cfg = {"messages_table": "general_chat_messages",
           "session_fk": "chat_session_id"}

    class MockReq:
        async def is_disconnected(self): return False

    events = []
    async for ev, payload in _db_fallback_stream(
        MockConn(), "general", cfg, 100, MockReq()
    ):
        events.append((ev, payload))
    assert events == [("idle", {})]


@pytest.mark.asyncio
async def test_db_fallback_user_last_idle_loop_exits_on_disconnect():
    """마지막이 user → idle 30s keepalive. disconnect 즉시 종료."""
    from api.routes.chat_stream import _db_fallback_stream

    class MockCursor:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, sql, params): pass
        def fetchone(self):
            return {"id": 1, "role": "user", "content": "질문"}

    class MockConn:
        def cursor(self, cursor_factory=None): return MockCursor()

    cfg = {"messages_table": "general_chat_messages",
           "session_fk": "chat_session_id"}

    call_count = [0]
    class MockReq:
        async def is_disconnected(self):
            call_count[0] += 1
            # 두 번째 체크에서 disconnect
            return call_count[0] >= 2

    # asyncio.sleep 을 빠르게
    with patch("api.routes.chat_stream.asyncio.sleep", new=lambda s: asyncio.sleep(0)):
        events = []
        async for ev, payload in _db_fallback_stream(
            MockConn(), "general", cfg, 100, MockReq()
        ):
            events.append((ev, payload))
            if len(events) > 5:
                break  # 안전 가드
    assert events[0] == ("idle", {})
    assert all(ev == "idle" for ev, _ in events)
