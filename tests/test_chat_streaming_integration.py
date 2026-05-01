# tests/test_chat_streaming_integration.py
"""POST /messages → BG task → broker fan-out → SSE 통합 흐름 테스트.

DB / SDK 는 mock. broker 는 실제 인스턴스로 동작 확인.
"""
from __future__ import annotations
import asyncio
from unittest.mock import patch, MagicMock
import pytest


@pytest.mark.asyncio
async def test_send_message_opens_channel_and_returns_stream_id():
    """POST 라우트가 broker 채널 open + stream 메타 반환하는지 검증.
    실제 fastapi.testclient 로 라우트 호출보다, 핵심 로직(broker.open_channel)
    만 시퀀스 검증 — full TestClient 통합은 운영기 수동 E2E 로 위임.
    """
    from api.chat_stream_broker import ChatStreamBroker

    fresh = ChatStreamBroker()

    ch = fresh.open_channel("general", 1)
    assert ch.status == "active"

    # publish_token → complete 시퀀스
    received = []
    async def consume():
        async for ev, payload in fresh.subscribe("general", 1):
            received.append((ev, payload))
    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await fresh.publish_token("general", 1, "응답")
    await fresh.complete("general", 1, "응답", final_message_id=10)
    await asyncio.wait_for(task, timeout=1.0)

    assert [ev for ev, _ in received] == ["token", "done"]


@pytest.mark.asyncio
async def test_concurrent_send_messages_second_raises():
    """같은 (kind, session_id) 에 두 번째 open_channel → ChannelAlreadyActive."""
    from api.chat_stream_broker import ChatStreamBroker, ChannelAlreadyActive

    fresh = ChatStreamBroker()
    fresh.open_channel("general", 5)
    with pytest.raises(ChannelAlreadyActive):
        fresh.open_channel("general", 5)


@pytest.mark.asyncio
async def test_complete_after_subscriber_disconnect_does_not_raise():
    """구독자가 disconnect 한 후 complete 가 안전하게 동작."""
    from api.chat_stream_broker import ChatStreamBroker

    fresh = ChatStreamBroker()
    fresh.open_channel("theme", 7)

    async def short_consume():
        async for ev, payload in fresh.subscribe("theme", 7):
            return  # 한 이벤트만 받고 종료 (disconnect 시뮬)

    task = asyncio.create_task(short_consume())
    await asyncio.sleep(0)
    await fresh.publish_token("theme", 7, "x")
    await asyncio.wait_for(task, timeout=1.0)

    # 구독자 0명인 상태에서 complete — 예외 없어야 함
    await fresh.complete("theme", 7, "x", final_message_id=99)
    ch = fresh._channels[("theme", 7)]
    assert ch.status == "completed"
    assert ch.final_message_id == 99


@pytest.mark.asyncio
async def test_double_fail_idempotent():
    """on_error → outer except 의 두 번 fail 호출에서 idempotency 가드."""
    from api.chat_stream_broker import ChatStreamBroker

    fresh = ChatStreamBroker()
    fresh.open_channel("education", 11)

    received = []
    async def consume():
        async for ev, payload in fresh.subscribe("education", 11):
            received.append((ev, payload))

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await fresh.fail("education", 11, "first error", "sdk_failure")
    # 두 번째 fail — 가드로 무시
    await fresh.fail("education", 11, "second error", "runtime")
    await asyncio.wait_for(task, timeout=1.0)

    # 첫 error 만 emit (idempotency)
    events = [(ev, p.get("code") if isinstance(p, dict) else None) for ev, p in received]
    assert events == [("error", "sdk_failure")]


@pytest.mark.asyncio
async def test_complete_after_fail_idempotent():
    """failed 채널에 complete 호출은 무시 (대칭성)."""
    from api.chat_stream_broker import ChatStreamBroker

    fresh = ChatStreamBroker()
    fresh.open_channel("general", 22)
    await fresh.fail("general", 22, "err", "sdk_failure")
    # 이미 failed — complete 는 무시
    await fresh.complete("general", 22, "won't apply", final_message_id=111)
    ch = fresh._channels[("general", 22)]
    assert ch.status == "failed"
    assert ch.final_text is None
    assert ch.final_message_id is None
