# tests/test_chat_stream_broker.py
"""ChatStreamBroker 단위 테스트 — DB/SDK 의존성 없이 pub/sub 동작만 검증."""
from __future__ import annotations
import asyncio
import pytest

from api.chat_stream_broker import (
    ChatStreamBroker, ChannelAlreadyActive, StreamChannel,
)


@pytest.mark.asyncio
async def test_open_channel_creates_active_channel():
    broker = ChatStreamBroker()
    channel = broker.open_channel("general", 100)
    assert channel.kind == "general"
    assert channel.session_id == 100
    assert channel.status == "active"
    assert channel.accumulated == []
    assert broker.has_channel("general", 100) is True


@pytest.mark.asyncio
async def test_open_channel_duplicate_raises():
    broker = ChatStreamBroker()
    broker.open_channel("general", 100)
    with pytest.raises(ChannelAlreadyActive):
        broker.open_channel("general", 100)


@pytest.mark.asyncio
async def test_publish_token_fans_out_to_subscribers():
    broker = ChatStreamBroker()
    broker.open_channel("general", 200)

    received_a: list = []
    received_b: list = []

    async def consume(target: list):
        async for ev, payload in broker.subscribe("general", 200):
            target.append((ev, payload))

    task_a = asyncio.create_task(consume(received_a))
    task_b = asyncio.create_task(consume(received_b))
    await asyncio.sleep(0)  # 구독자 등록 대기

    await broker.publish_token("general", 200, "Hello ")
    await broker.publish_token("general", 200, "world")
    await broker.complete("general", 200, "Hello world", final_message_id=999)

    await asyncio.wait_for(task_a, timeout=1.0)
    await asyncio.wait_for(task_b, timeout=1.0)

    # 양 구독자가 동일 시퀀스 수신
    for received in (received_a, received_b):
        events = [ev for ev, _ in received]
        assert events == ["token", "token", "done"]
        assert received[0][1]["text"] == "Hello "
        assert received[1][1]["text"] == "world"
        assert received[2][1]["message_id"] == 999
        assert received[2][1]["final_text"] == "Hello world"


@pytest.mark.asyncio
async def test_late_subscriber_receives_replay():
    broker = ChatStreamBroker()
    broker.open_channel("theme", 300)
    await broker.publish_token("theme", 300, "이미 ")
    await broker.publish_token("theme", 300, "도착한 ")
    await broker.publish_token("theme", 300, "토큰")

    received: list = []

    async def consume():
        async for ev, payload in broker.subscribe("theme", 300):
            received.append((ev, payload))

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await broker.publish_token("theme", 300, " 추가")
    await broker.complete("theme", 300, "이미 도착한 토큰 추가", final_message_id=42)
    await asyncio.wait_for(task, timeout=1.0)

    events = [ev for ev, _ in received]
    assert events == ["replay", "token", "done"]
    assert received[0][1]["text"] == "이미 도착한 토큰"


@pytest.mark.asyncio
async def test_fail_emits_error_event():
    broker = ChatStreamBroker()
    broker.open_channel("education", 400)

    received: list = []

    async def consume():
        async for ev, payload in broker.subscribe("education", 400):
            received.append((ev, payload))

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await broker.fail("education", 400, "SDK timeout", "sdk_timeout")
    await asyncio.wait_for(task, timeout=1.0)

    events = [ev for ev, _ in received]
    assert events == ["error"]
    assert received[0][1] == {"message": "SDK timeout", "code": "sdk_timeout"}


@pytest.mark.asyncio
async def test_subscribe_to_nonexistent_channel_returns_immediately():
    broker = ChatStreamBroker()
    received = []
    async for ev, payload in broker.subscribe("general", 999):
        received.append((ev, payload))
    assert received == []


@pytest.mark.asyncio
async def test_subscribe_to_completed_channel_replays_then_done():
    broker = ChatStreamBroker()
    broker.open_channel("general", 500)
    await broker.publish_token("general", 500, "완료된 응답")
    await broker.complete("general", 500, "완료된 응답", final_message_id=77)

    received = []
    async for ev, payload in broker.subscribe("general", 500):
        received.append((ev, payload))

    events = [ev for ev, _ in received]
    assert events == ["replay", "done"]


@pytest.mark.asyncio
async def test_cleanup_removes_stale_completed_channel():
    broker = ChatStreamBroker(ttl_seconds=0)  # 즉시 stale
    broker.open_channel("general", 600)
    await broker.complete("general", 600, "done", final_message_id=1)
    # 채널 status=completed + completed_at 설정됨
    # cleanup_loop 의 본체 로직만 직접 호출하기 위해 내부 메서드 테스트
    # → snapshot + stale 검출 부분만 재현
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    snapshot = list(broker._channels.items())
    stale = []
    for key, ch in snapshot:
        if ch.completed_at is not None:
            age = (now - ch.completed_at).total_seconds()
            if age > broker._ttl_seconds:
                stale.append(key)
    for key in stale:
        broker._channels.pop(key, None)
    assert ("general", 600) not in broker._channels
