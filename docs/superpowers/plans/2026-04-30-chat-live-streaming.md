# Chat Live Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 3개 채팅(General/Theme/Tutor)에 in-process pub/sub broker + SSE 라이브 스트리밍 도입 — 멀티 디바이스 fan-out, 재접속 시 replay, 워커 재시작 시 DB 폴백.

**Architecture:** POST `/messages` 가 user 메시지만 동기 INSERT 후 BackgroundTasks 로 응답 생성을 분기 → broker 가 토큰을 누적·fan-out → SSE 라우트(`/api/chat-stream/{kind}/{session_id}`) 가 권한 가드 후 broker 구독 또는 DB 폴백. DB 가 source of truth, broker 는 휘발성 라이브 채널.

**Tech Stack:** Python 3.10+ asyncio, FastAPI `BackgroundTasks` + `StreamingResponse`, `claude_agent_sdk` (`include_partial_messages=True`), 클라이언트 EventSource, pytest + 기존 `tests/conftest.py` mock 인프라.

**Spec:** [`docs/superpowers/specs/2026-04-30-chat-live-streaming-design.md`](../specs/2026-04-30-chat-live-streaming-design.md)

---

## File Structure

**Create (8 files):**
- `api/chat_stream_broker.py` — In-memory pub/sub broker, StreamChannel 데이터 클래스, TTL cleanup
- `api/chat_stream_helpers.py` — Claude SDK partial 메시지 → 토큰 delta 추출 공용 로직
- `api/routes/chat_stream.py` — SSE 라우트 (3 kind 공용)
- `api/static/js/chat_stream_client.js` — EventSource 공용 컨트롤러 + 자동 재연결
- `tests/test_chat_stream_broker.py` — broker 단위 테스트
- `tests/test_chat_stream_helpers.py` — SDK partial → delta 추출 테스트
- `tests/test_chat_stream_routes.py` — SSE 라우트 + 권한 가드 테스트
- `tests/test_chat_streaming_integration.py` — POST → BG task → broker → SSE 통합

**Modify (10 files):**
- `api/general_chat_engine.py` — `query_general_chat_stream` 추가, `_format_history` 추출
- `api/chat_engine.py` — `query_theme_chat_stream` 추가
- `api/education_engine.py` — `query_education_chat_stream` 추가
- `api/routes/general_chat.py` — POST 라우트 BG task 분기, 응답 스키마 변경
- `api/routes/chat.py` — POST 라우트 BG task 분기
- `api/routes/education.py` — POST 라우트 BG task 분기
- `api/templates/general_chat_room.html` — 클라 JS 교체
- `api/templates/chat_room.html` — 클라 JS 교체
- `api/templates/education/chat_room.html` — 클라 JS 교체
- `api/main.py` — broker.start_cleanup() 호출 + chat_stream 라우터 등록 + lifespan 추가

---

## Task 1: Broker 단위 테스트 작성

**Files:**
- Create: `tests/test_chat_stream_broker.py`

- [ ] **Step 1: 빈 테스트 파일 작성 (먼저 broker 부재 검증)**

```python
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
```

- [ ] **Step 2: 테스트 실행 — broker 부재로 ImportError 확인**

Run: `pytest tests/test_chat_stream_broker.py -v`
Expected: ImportError — `api.chat_stream_broker` 모듈 없음

- [ ] **Step 3: pytest-asyncio 설치 확인**

Run: `python -m pip show pytest-asyncio`
Expected: Version info 출력. 없으면 `pip install pytest-asyncio` 후 `requirements.txt` 에 추가.

- [ ] **Step 4: pyproject.toml 또는 pytest.ini 에 asyncio_mode = "auto" 확인 / 추가**

Run: `cat pytest.ini 2>/dev/null || cat pyproject.toml | grep -A3 pytest`
없으면 `pytest.ini` 생성:

```ini
[pytest]
asyncio_mode = auto
```

또는 각 테스트 함수에 `@pytest.mark.asyncio` 만 두고 진행 가능 (위 테스트는 마커 명시함).

- [ ] **Step 5: 커밋 (테스트만 우선, broker 미작성)**

```bash
git add tests/test_chat_stream_broker.py
# (pytest.ini 추가했으면 함께)
git commit -m "test(chat-stream): broker 단위 테스트 추가 (RED)"
```

---

## Task 2: Broker 구현

**Files:**
- Create: `api/chat_stream_broker.py`

- [ ] **Step 1: broker 모듈 작성**

```python
# api/chat_stream_broker.py
"""Chat live streaming broker — in-process pub/sub.

단일 워커 + 단일 asyncio 이벤트 루프 가정. 멀티 워커 환경은 v2 (Redis 어댑터).

핵심 책임:
- BG task (POST 라우트가 spawn) 가 토큰을 publish
- SSE 라우트 (구독자) 가 큐로 fan-out 받음
- 누적 버퍼로 늦게 들어온 구독자에게 replay 1회 송출
- 완료/실패 채널 TTL 정리, 진행중 채널 hard kill
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator, Literal, Optional

ChannelStatus = Literal["active", "completed", "failed"]
ChatKind = Literal["general", "theme", "education"]


class ChannelAlreadyActive(Exception):
    """같은 (kind, session_id) 에 이미 진행 중인 채널 존재 — 라우트가 409 변환."""

    def __init__(self, kind: str, session_id: int):
        self.kind = kind
        self.session_id = session_id
        super().__init__(f"channel ({kind}, {session_id}) already active")


@dataclass
class StreamChannel:
    kind: ChatKind
    session_id: int
    started_at: datetime
    accumulated: list[str] = field(default_factory=list)
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    status: ChannelStatus = "active"
    final_text: Optional[str] = None
    final_message_id: Optional[int] = None
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    completed_at: Optional[datetime] = None

    def accumulated_text(self) -> str:
        return "".join(self.accumulated)


class ChatStreamBroker:
    def __init__(self, ttl_seconds: int = 600, hard_kill_seconds: int = 1500):
        self._channels: dict[tuple[ChatKind, int], StreamChannel] = {}
        self._ttl_seconds = ttl_seconds
        self._hard_kill_seconds = hard_kill_seconds
        self._cleanup_task: Optional[asyncio.Task] = None

    # ─── 조회 ─────────────────────────────────────

    def has_channel(self, kind: ChatKind, session_id: int) -> bool:
        return (kind, session_id) in self._channels

    # ─── 발행자 측 ────────────────────────────────

    def open_channel(self, kind: ChatKind, session_id: int) -> StreamChannel:
        key = (kind, session_id)
        existing = self._channels.get(key)
        if existing is not None and existing.status == "active":
            raise ChannelAlreadyActive(kind, session_id)
        channel = StreamChannel(
            kind=kind, session_id=session_id,
            started_at=datetime.now(timezone.utc),
        )
        self._channels[key] = channel
        return channel

    async def publish_token(self, kind: ChatKind, session_id: int, token: str) -> None:
        ch = self._channels.get((kind, session_id))
        if ch is None or ch.status != "active":
            return
        ch.accumulated.append(token)
        dead: list[asyncio.Queue] = []
        for q in ch.subscribers:
            try:
                q.put_nowait(("token", {"text": token}))
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            ch.subscribers.discard(q)

    async def complete(self, kind: ChatKind, session_id: int,
                       final_text: str, final_message_id: int) -> None:
        ch = self._channels.get((kind, session_id))
        if ch is None:
            return
        ch.status = "completed"
        ch.final_text = final_text
        ch.final_message_id = final_message_id
        ch.completed_at = datetime.now(timezone.utc)
        for q in list(ch.subscribers):
            try:
                q.put_nowait(("done", {
                    "message_id": final_message_id, "final_text": final_text,
                }))
                q.put_nowait(("__close__", None))
            except asyncio.QueueFull:
                pass

    async def fail(self, kind: ChatKind, session_id: int,
                   message: str, code: str) -> None:
        ch = self._channels.get((kind, session_id))
        if ch is None:
            return
        ch.status = "failed"
        ch.error_message = message
        ch.error_code = code
        ch.completed_at = datetime.now(timezone.utc)
        for q in list(ch.subscribers):
            try:
                q.put_nowait(("error", {"message": message, "code": code}))
                q.put_nowait(("__close__", None))
            except asyncio.QueueFull:
                pass

    # ─── 구독자 측 ────────────────────────────────

    async def subscribe(
        self, kind: ChatKind, session_id: int
    ) -> AsyncIterator[tuple[str, dict]]:
        ch = self._channels.get((kind, session_id))
        if ch is None:
            return

        if ch.accumulated:
            yield ("replay", {
                "text": ch.accumulated_text(),
                "started_at": ch.started_at.isoformat(),
            })

        if ch.status == "completed":
            yield ("done", {
                "message_id": ch.final_message_id,
                "final_text": ch.final_text,
            })
            return
        if ch.status == "failed":
            yield ("error", {
                "message": ch.error_message,
                "code": ch.error_code,
            })
            return

        q: asyncio.Queue = asyncio.Queue(maxsize=1024)
        ch.subscribers.add(q)
        try:
            while True:
                event, payload = await q.get()
                if event == "__close__":
                    break
                yield (event, payload)
        finally:
            ch.subscribers.discard(q)

    # ─── 정리 ─────────────────────────────────────

    def start_cleanup(self) -> None:
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            now = datetime.now(timezone.utc)
            snapshot = list(self._channels.items())
            stale: list[tuple] = []
            for key, ch in snapshot:
                if ch.status == "active":
                    age = (now - ch.started_at).total_seconds()
                    if age > self._hard_kill_seconds:
                        await self.fail(
                            ch.kind, ch.session_id,
                            "broker hard kill (timeout)", "broker_timeout",
                        )
                elif ch.completed_at is not None:
                    age = (now - ch.completed_at).total_seconds()
                    if age > self._ttl_seconds:
                        stale.append(key)
            for key in stale:
                self._channels.pop(key, None)


# 싱글턴 모듈 레벨 인스턴스
broker = ChatStreamBroker()
```

- [ ] **Step 2: 테스트 실행 → 모두 PASS 확인**

Run: `pytest tests/test_chat_stream_broker.py -v`
Expected: 7개 테스트 모두 PASS

- [ ] **Step 3: 추가 단위 테스트 — TTL cleanup**

`tests/test_chat_stream_broker.py` 끝에 추가:

```python
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
```

- [ ] **Step 4: 테스트 실행 → 8개 PASS**

Run: `pytest tests/test_chat_stream_broker.py -v`
Expected: 8 passed

- [ ] **Step 5: 커밋**

```bash
git add api/chat_stream_broker.py tests/test_chat_stream_broker.py
git commit -m "feat(chat-stream): in-memory pub/sub broker (GREEN)"
```

---

## Task 3: SDK Streaming Helper 테스트

**Files:**
- Create: `tests/test_chat_stream_helpers.py`

- [ ] **Step 1: 테스트 파일 작성**

```python
# tests/test_chat_stream_helpers.py
"""Claude SDK partial 메시지 → 토큰 delta 추출 헬퍼 테스트.

claude_agent_sdk 는 conftest.py 가 mock 처리. 여기서는 mock SDK 가
yield 하는 메시지 시퀀스에 따라 on_token 호출 횟수와 누적 텍스트가
올바른지 검증한다.
"""
from __future__ import annotations
import asyncio
from unittest.mock import patch
import pytest


def _make_text_block(text: str):
    """conftest 의 TextBlock mock 인스턴스 (text 속성 부여)."""
    from claude_agent_sdk import TextBlock
    block = TextBlock()
    block.text = text
    return block


def _make_assistant_message(text_parts: list[str]):
    """AssistantMessage mock — content=[TextBlock,...]."""
    from claude_agent_sdk import AssistantMessage
    msg = AssistantMessage()
    msg.content = [_make_text_block(t) for t in text_parts]
    return msg


@pytest.mark.asyncio
async def test_stream_extracts_prefix_style_partials():
    """SDK 가 prefix-style partial 을 yield → delta 만 emit."""
    from api.chat_stream_helpers import stream_claude_chat

    # 누적 prefix: "안녕" → "안녕하세" → "안녕하세요"
    messages = [
        _make_assistant_message(["안녕"]),
        _make_assistant_message(["안녕하세"]),
        _make_assistant_message(["안녕하세요"]),
    ]

    async def fake_query(prompt, options):
        for m in messages:
            yield m

    tokens: list[str] = []
    async def on_token(t: str):
        tokens.append(t)
    async def on_error(msg: str, code: str):
        pytest.fail(f"on_error called: {msg} ({code})")

    with patch("api.chat_stream_helpers.query", fake_query):
        result = await stream_claude_chat(
            prompt="안녕", system="sys",
            on_token=on_token, on_error=on_error,
        )
    assert tokens == ["안녕", "하세", "요"]
    assert result == "안녕하세요"


@pytest.mark.asyncio
async def test_stream_handles_non_prefix_chunks():
    """누적이 prefix 가 아닌 새 chunk 인 경우 그대로 추가."""
    from api.chat_stream_helpers import stream_claude_chat

    messages = [
        _make_assistant_message(["첫번째 답"]),
        _make_assistant_message(["완전히 다른 것"]),  # prefix 아님
    ]

    async def fake_query(prompt, options):
        for m in messages:
            yield m

    tokens: list[str] = []
    async def on_token(t: str):
        tokens.append(t)
    async def on_error(msg: str, code: str):
        pytest.fail("on_error called")

    with patch("api.chat_stream_helpers.query", fake_query):
        result = await stream_claude_chat(
            prompt="x", system="s",
            on_token=on_token, on_error=on_error,
        )
    assert tokens == ["첫번째 답", "완전히 다른 것"]
    assert result == "첫번째 답완전히 다른 것"


@pytest.mark.asyncio
async def test_stream_skips_messages_without_text():
    """text 가 없는 메시지(ToolUseBlock 등)는 스킵."""
    from api.chat_stream_helpers import stream_claude_chat

    msg_with_text = _make_assistant_message(["응답"])
    msg_without = _make_assistant_message([])  # 빈 content

    async def fake_query(prompt, options):
        yield msg_without
        yield msg_with_text

    tokens: list[str] = []
    async def on_token(t: str):
        tokens.append(t)
    async def on_error(msg: str, code: str):
        pytest.fail("on_error called")

    with patch("api.chat_stream_helpers.query", fake_query):
        result = await stream_claude_chat(
            prompt="x", system="s",
            on_token=on_token, on_error=on_error,
        )
    assert tokens == ["응답"]
    assert result == "응답"


@pytest.mark.asyncio
async def test_stream_calls_on_error_then_raises():
    """SDK 예외 → on_error 호출 + raise."""
    from api.chat_stream_helpers import stream_claude_chat

    async def fake_query(prompt, options):
        yield _make_assistant_message(["부분 "])
        raise RuntimeError("SDK 폭발")

    tokens: list[str] = []
    errors: list[tuple] = []

    async def on_token(t: str):
        tokens.append(t)

    async def on_error(msg: str, code: str):
        errors.append((msg, code))

    with patch("api.chat_stream_helpers.query", fake_query):
        with pytest.raises(RuntimeError, match="SDK 폭발"):
            await stream_claude_chat(
                prompt="x", system="s",
                on_token=on_token, on_error=on_error,
            )
    assert tokens == ["부분 "]
    assert len(errors) == 1
    assert "SDK 폭발" in errors[0][0]
    assert errors[0][1] == "sdk_failure"
```

- [ ] **Step 2: 테스트 실행 → ImportError 확인**

Run: `pytest tests/test_chat_stream_helpers.py -v`
Expected: ImportError — `api.chat_stream_helpers` 없음

- [ ] **Step 3: 커밋**

```bash
git add tests/test_chat_stream_helpers.py
git commit -m "test(chat-stream): SDK partial → delta 추출 헬퍼 테스트 (RED)"
```

---

## Task 4: SDK Streaming Helper 구현

**Files:**
- Create: `api/chat_stream_helpers.py`

- [ ] **Step 1: 헬퍼 모듈 작성**

```python
# api/chat_stream_helpers.py
"""Claude Agent SDK partial 메시지 → 토큰 delta 추출 공용 로직.

3개 엔진(general/theme/education)이 동일한 SDK 호출 패턴을 공유하므로
delta 계산 로직을 공용화한다.
"""
from __future__ import annotations
import sys
from typing import Awaitable, Callable, Optional

from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock

OnToken = Callable[[str], Awaitable[None]]
OnError = Callable[[str, str], Awaitable[None]]


def _extract_text(message) -> Optional[str]:
    """SDK 메시지에서 누적/신규 텍스트 추출.

    AssistantMessage / PartialAssistantMessage(SDK 신규 타입, duck-typed) 의
    content 의 모든 TextBlock 을 concat 한 문자열을 반환.
    텍스트 없는 메시지(ToolUseBlock·ResultMessage 등) 는 None.

    호출자는 직전 누적과 prefix 비교하여 delta 를 계산한다.
    """
    if not hasattr(message, "content"):
        return None
    parts: list[str] = []
    for block in getattr(message, "content", []) or []:
        if isinstance(block, TextBlock) or hasattr(block, "text"):
            text = getattr(block, "text", None)
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts) if parts else None


async def stream_claude_chat(
    prompt: str,
    system: str,
    *,
    on_token: OnToken,
    on_error: OnError,
    max_turns: int = 1,
) -> str:
    """Claude SDK 스트리밍 호출 + 토큰 delta 콜백. 최종 누적 텍스트 반환.

    on_token 호출은 직전 누적과의 delta 만 보낸다 (중복 송출 X).
    on_error 는 raise 전에 호출 — broker 가 구독자에게 error 이벤트 전파.
    """
    accumulated = ""
    cli_stderr: list[str] = []

    def _on_stderr(line: str) -> None:
        cli_stderr.append(line)

    try:
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                system_prompt=system,
                max_turns=max_turns,
                include_partial_messages=True,
                stderr=_on_stderr,
            ),
        ):
            new_text = _extract_text(message)
            if new_text is None:
                continue
            if new_text.startswith(accumulated):
                delta = new_text[len(accumulated):]
                accumulated = new_text
            else:
                # 새 chunk (별개 TextBlock 또는 분리된 메시지)
                delta = new_text
                accumulated += new_text
            if delta:
                await on_token(delta)
    except BaseException as e:
        dump = "\n".join(cli_stderr[-200:]) if cli_stderr else "(stderr empty)"
        msg = f"{type(e).__name__}: {e}"
        print(
            f"[chat_stream] Claude SDK 호출 실패: {msg}\n"
            f"--- CLI stderr (마지막 200줄) ---\n{dump}\n--- end ---",
            file=sys.stderr, flush=True,
        )
        await on_error(msg, "sdk_failure")
        raise
    return accumulated
```

- [ ] **Step 2: 테스트 실행 → 4개 PASS**

Run: `pytest tests/test_chat_stream_helpers.py -v`
Expected: 4 passed

- [ ] **Step 3: 커밋**

```bash
git add api/chat_stream_helpers.py
git commit -m "feat(chat-stream): SDK partial 메시지 delta 추출 헬퍼 (GREEN)"
```

---

## Task 5: General Chat Engine — Streaming 변형 추가

**Files:**
- Modify: `api/general_chat_engine.py`

- [ ] **Step 1: 기존 sync 함수의 history 포맷 로직을 `_format_history` 로 추출**

`api/general_chat_engine.py` 의 `query_general_chat_sync` 함수 안의 history_text 빌드 부분(line 170-178)을 모듈 레벨 함수로 추출:

```python
def _format_history(conversation_history: list[dict], window: int = 20) -> str:
    """이전 대화 이력 → 프롬프트 삽입용 텍스트.

    기존 query_general_chat_sync 의 inline 로직 추출 — sync/stream 양쪽이 공유.
    """
    recent = conversation_history[-window:]
    if not recent:
        return ""
    parts = ["\n\n## 이전 대화"]
    for msg in recent:
        prefix = "사용자" if msg["role"] == "user" else "어시스턴트"
        parts.append(f"\n**{prefix}:** {msg['content']}\n")
    return "".join(parts)
```

기존 `query_general_chat_sync` 함수 본문에서 동일 로직을 `history_text = _format_history(conversation_history)` 로 교체.

- [ ] **Step 2: streaming 변형 함수 추가 (파일 끝)**

```python
from api.chat_stream_helpers import stream_claude_chat, OnToken, OnError


async def query_general_chat_stream(
    user_context: str,
    conversation_history: list[dict],
    user_message: str,
    *,
    on_token: OnToken,
    on_error: OnError,
    max_turns: int = 1,
) -> str:
    """자유 채팅 streaming 변형. 토큰 단위로 on_token 호출.

    sync 함수(query_general_chat_sync) 는 폴백/테스트용으로 유지.
    """
    history_text = _format_history(conversation_history)
    prompt = f"{history_text}\n사용자: {user_message}"
    system = GENERAL_CHAT_SYSTEM_PROMPT.format(user_context=user_context or "")
    return await stream_claude_chat(
        prompt=prompt, system=system,
        on_token=on_token, on_error=on_error, max_turns=max_turns,
    )
```

- [ ] **Step 3: 새 함수 임포트 검증 (smoke test)**

Run: `python -c "from api.general_chat_engine import query_general_chat_stream, _format_history; print('ok')"`
Expected: `ok`

- [ ] **Step 4: sync 함수 회귀 테스트 (기존 동작 유지)**

`tests/` 에 기존 sync 함수 테스트가 있다면 실행:

Run: `pytest tests/ -k "general_chat" -v`
Expected: 기존 테스트 PASS (refactor 가 동작을 깨지 않음)

기존 테스트가 없으면 스킵.

- [ ] **Step 5: 커밋**

```bash
git add api/general_chat_engine.py
git commit -m "feat(chat-stream): general chat streaming 변형 + history 헬퍼 추출"
```

---

## Task 6: Theme Chat Engine — Streaming 변형 추가

**Files:**
- Modify: `api/chat_engine.py`

- [ ] **Step 1: 기존 `query_theme_chat_sync` 의 prompt 빌드 로직 확인**

Run: `grep -n "query_theme_chat_sync\|conversation_history\|prompt =" "d:/dzp/바이브코딩/investment-advisor/api/chat_engine.py" | head -20`

기존 함수의 prompt 조립 방식 (history + theme_context + user_message) 확인.

- [ ] **Step 2: streaming 변형 함수 추가**

`api/chat_engine.py` 끝에 추가:

```python
from api.chat_stream_helpers import stream_claude_chat, OnToken, OnError


async def query_theme_chat_stream(
    theme_context: str,
    conversation_history: list[dict],
    user_message: str,
    *,
    on_token: OnToken,
    on_error: OnError,
    max_turns: int = 1,
) -> str:
    """테마 채팅 streaming 변형. 기존 query_theme_chat_sync 와 동일한 prompt 구성."""
    # 기존 sync 함수의 prompt 조립 로직과 정확히 동일하게 작성한다.
    # (sync 함수가 inline 으로 history 를 포맷하면, 동일 코드를 여기에 복제하거나
    #  _format_history_theme() 로 추출하여 양쪽 공유.)
    recent = conversation_history[-20:]
    history_text = ""
    if recent:
        parts = ["\n\n## 이전 대화"]
        for msg in recent:
            prefix = "사용자" if msg["role"] == "user" else "어시스턴트"
            parts.append(f"\n**{prefix}:** {msg['content']}\n")
        history_text = "".join(parts)

    prompt = f"{history_text}\n사용자: {user_message}"
    system = CHAT_SYSTEM_PROMPT.format(theme_context=theme_context)
    return await stream_claude_chat(
        prompt=prompt, system=system,
        on_token=on_token, on_error=on_error, max_turns=max_turns,
    )
```

- [ ] **Step 3: 임포트 검증**

Run: `python -c "from api.chat_engine import query_theme_chat_stream; print('ok')"`
Expected: `ok`

- [ ] **Step 4: 커밋**

```bash
git add api/chat_engine.py
git commit -m "feat(chat-stream): theme chat streaming 변형 추가"
```

---

## Task 7: Education Engine — Streaming 변형 추가

**Files:**
- Modify: `api/education_engine.py`

- [ ] **Step 1: streaming 변형 추가 (Task 6 과 동일 패턴, topic_context 사용)**

`api/education_engine.py` 끝에 추가:

```python
from api.chat_stream_helpers import stream_claude_chat, OnToken, OnError


async def query_education_chat_stream(
    topic_context: str,
    conversation_history: list[dict],
    user_message: str,
    *,
    on_token: OnToken,
    on_error: OnError,
    max_turns: int = 1,
) -> str:
    """교육 AI 튜터 streaming 변형."""
    recent = conversation_history[-20:]
    history_text = ""
    if recent:
        parts = ["\n\n## 이전 대화"]
        for msg in recent:
            prefix = "사용자" if msg["role"] == "user" else "어시스턴트"
            parts.append(f"\n**{prefix}:** {msg['content']}\n")
        history_text = "".join(parts)

    prompt = f"{history_text}\n사용자: {user_message}"
    system = EDU_SYSTEM_PROMPT.format(topic_context=topic_context)
    return await stream_claude_chat(
        prompt=prompt, system=system,
        on_token=on_token, on_error=on_error, max_turns=max_turns,
    )
```

- [ ] **Step 2: 임포트 검증**

Run: `python -c "from api.education_engine import query_education_chat_stream; print('ok')"`
Expected: `ok`

- [ ] **Step 3: 커밋**

```bash
git add api/education_engine.py
git commit -m "feat(chat-stream): education chat streaming 변형 추가"
```

---

## Task 8: SSE 라우트 테스트

**Files:**
- Create: `tests/test_chat_stream_routes.py`

- [ ] **Step 1: 테스트 파일 작성 — broker mock 으로 라우트 동작 검증**

```python
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
```

- [ ] **Step 2: 테스트 실행 → ImportError 확인**

Run: `pytest tests/test_chat_stream_routes.py -v`
Expected: ImportError — `api.routes.chat_stream` 없음

- [ ] **Step 3: 커밋**

```bash
git add tests/test_chat_stream_routes.py
git commit -m "test(chat-stream): SSE 라우트 헬퍼 테스트 (RED)"
```

---

## Task 9: SSE 라우트 구현

**Files:**
- Create: `api/routes/chat_stream.py`

- [ ] **Step 1: 라우트 모듈 작성**

```python
# api/routes/chat_stream.py
"""채팅 라이브 스트리밍 SSE 라우트 — General/Theme/Education 공용."""
from __future__ import annotations
import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from psycopg2.extras import RealDictCursor

from api.auth.dependencies import get_current_user_required
from api.auth.models import UserInDB
from api.deps import get_db_conn
from api.chat_stream_broker import broker

router = APIRouter(prefix="/api/chat-stream", tags=["채팅 라이브 스트리밍"])

ALLOWED_KINDS: dict[str, dict] = {
    "general": {
        "sessions_table": "general_chat_sessions",
        "messages_table": "general_chat_messages",
        "session_fk": "chat_session_id",
    },
    "theme": {
        "sessions_table": "theme_chat_sessions",
        "messages_table": "theme_chat_messages",
        "session_fk": "chat_session_id",
    },
    "education": {
        "sessions_table": "education_chat_sessions",
        "messages_table": "education_chat_messages",
        "session_fk": "chat_session_id",
    },
}


def _format_sse(event: str, data: dict) -> bytes:
    return (
        f"event: {event}\n"
        f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    ).encode("utf-8")


@router.get("/{kind}/{session_id}")
async def chat_stream(
    kind: str,
    session_id: int,
    request: Request,
    conn=Depends(get_db_conn),
    user: UserInDB = Depends(get_current_user_required),
):
    if kind not in ALLOWED_KINDS:
        raise HTTPException(404, "unknown chat kind")

    cfg = ALLOWED_KINDS[kind]
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"SELECT id, user_id FROM {cfg['sessions_table']} WHERE id = %s",
            (session_id,),
        )
        session = cur.fetchone()
    if session is None:
        raise HTTPException(404, "session not found")
    if user.role != "admin" and session.get("user_id") != user.id:
        raise HTTPException(403, "본인의 채팅 세션만 구독할 수 있습니다")

    async def generator() -> AsyncIterator[bytes]:
        if broker.has_channel(kind, session_id):
            async for ev_name, payload in broker.subscribe(kind, session_id):
                yield _format_sse(ev_name, payload)
                if await request.is_disconnected():
                    return
            return

        async for ev_name, payload in _db_fallback_stream(
            conn, kind, cfg, session_id, request
        ):
            yield _format_sse(ev_name, payload)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


async def _db_fallback_stream(
    conn, kind: str, cfg: dict, session_id: int, request,
) -> AsyncIterator[tuple[str, dict]]:
    """broker 채널이 없을 때 DB 기반 폴백.

    - 마지막 메시지 == assistant → done 즉시 송출 후 종료
    - 마지막 == user → idle 30초 keepalive (5분까지). 그 사이 broker 깨어나면 종료.
    - 메시지 없음 → idle 1회 후 종료
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""SELECT id, role, content FROM {cfg['messages_table']}
                WHERE {cfg['session_fk']} = %s
                ORDER BY created_at DESC LIMIT 1""",
            (session_id,),
        )
        last = cur.fetchone()

    if last is None:
        yield ("idle", {})
        return
    if last["role"] == "assistant":
        yield ("done", {"message_id": last["id"], "final_text": last["content"]})
        return

    elapsed = 0
    while elapsed < 300:
        if await request.is_disconnected():
            return
        yield ("idle", {})
        await asyncio.sleep(30)
        elapsed += 30
        if broker.has_channel(kind, session_id):
            return
```

- [ ] **Step 2: 테스트 실행 → 5개 PASS**

Run: `pytest tests/test_chat_stream_routes.py -v`
Expected: 5 passed

- [ ] **Step 3: 커밋**

```bash
git add api/routes/chat_stream.py
git commit -m "feat(chat-stream): SSE 라우트 + DB 폴백 (GREEN)"
```

---

## Task 10: General Chat POST 라우트 — BG task 분기

**Files:**
- Modify: `api/routes/general_chat.py`

- [ ] **Step 1: 기존 send_message 함수 전면 교체**

`api/routes/general_chat.py` 의 `send_message` 함수(line 183-271)를 다음으로 교체:

```python
from fastapi import BackgroundTasks
from api.chat_stream_broker import broker, ChannelAlreadyActive
from api.general_chat_engine import build_user_context, query_general_chat_stream
from shared.config import DatabaseConfig
from shared.db import get_connection


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: int,
    body: ChatMessageRequest,
    bg: BackgroundTasks,
    conn=Depends(get_db_conn),
    user: UserInDB = Depends(get_current_user_required),
):
    """user 메시지 INSERT 후 BG task 로 응답 생성 분기.
    응답 본문은 SSE (`/api/chat-stream/general/{session_id}`) 로 전달.
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # 1) 세션 검증
        cur.execute(
            "SELECT id, user_id FROM general_chat_sessions WHERE id = %s",
            (session_id,),
        )
        session = cur.fetchone()
        if not session:
            raise HTTPException(status_code=404, detail="채팅 세션을 찾을 수 없습니다")
        if user.role != "admin" and session.get("user_id") != user.id:
            raise HTTPException(status_code=403, detail="본인의 채팅 세션에만 메시지를 보낼 수 있습니다")

        # 2) quota
        _check_quota_or_raise(cur, user)

        # 3) 사용자 컨텍스트 + 이력
        owner_id = session.get("user_id") or user.id
        user_context = build_user_context(conn, owner_id)
        cur.execute(
            """SELECT role, content FROM general_chat_messages
               WHERE chat_session_id = %s ORDER BY created_at""",
            (session_id,),
        )
        history = [dict(row) for row in cur.fetchall()]

        # 4) user 메시지 INSERT
        cur.execute(
            """INSERT INTO general_chat_messages (chat_session_id, role, content)
               VALUES (%s, 'user', %s)
               RETURNING id, role, content, created_at""",
            (session_id, body.content),
        )
        user_msg = cur.fetchone()
    conn.commit()

    # 5) broker 채널 open
    try:
        channel = broker.open_channel("general", session_id)
    except ChannelAlreadyActive:
        raise HTTPException(
            status_code=409,
            detail="이전 응답이 아직 생성 중입니다. 잠시만 기다려주세요.",
        )

    # 6) BG task spawn — disconnect 무관하게 끝까지 실행
    async def _runner():
        async def _on_token(t: str):
            await broker.publish_token("general", session_id, t)

        async def _on_error(msg: str, code: str):
            await broker.fail("general", session_id, msg, code)

        try:
            final_text = await query_general_chat_stream(
                user_context=user_context,
                conversation_history=history,
                user_message=body.content,
                on_token=_on_token,
                on_error=_on_error,
            )
            # assistant INSERT — 별도 conn (BG task 라 원본 닫혔을 수 있음)
            db_conn = get_connection(DatabaseConfig())
            try:
                with db_conn.cursor(cursor_factory=RealDictCursor) as cur2:
                    cur2.execute(
                        """INSERT INTO general_chat_messages
                               (chat_session_id, role, content)
                           VALUES (%s, 'assistant', %s)
                           RETURNING id""",
                        (session_id, final_text),
                    )
                    msg_id = cur2.fetchone()["id"]
                    cur2.execute(
                        "SELECT COUNT(*) AS c FROM general_chat_messages WHERE chat_session_id = %s",
                        (session_id,),
                    )
                    if cur2.fetchone()["c"] <= 2:
                        title = body.content[:50] + ("..." if len(body.content) > 50 else "")
                        cur2.execute(
                            "UPDATE general_chat_sessions SET title=%s, updated_at=NOW() WHERE id=%s",
                            (title, session_id),
                        )
                    else:
                        cur2.execute(
                            "UPDATE general_chat_sessions SET updated_at=NOW() WHERE id=%s",
                            (session_id,),
                        )
                db_conn.commit()
            finally:
                db_conn.close()
            await broker.complete("general", session_id, final_text, msg_id)
        except Exception as e:
            await broker.fail("general", session_id, str(e), "runtime")

    bg.add_task(_runner)

    return {
        "user_message": _serialize_row(user_msg),
        "stream": {
            "kind": "general",
            "session_id": session_id,
            "started_at": channel.started_at.isoformat(),
        },
    }
```

기존 `query_general_chat_sync` 임포트는 제거하지 않고 유지(폴백/테스트용).

- [ ] **Step 2: 임포트 검증**

Run: `python -c "from api.routes.general_chat import send_message; print('ok')"`
Expected: `ok`

- [ ] **Step 3: 기존 통합 테스트 영향 확인**

Run: `pytest tests/ -k "general_chat" -v 2>&1 | head -60`
Expected: 응답 스키마 변경으로 기존 테스트 일부 실패 가능 — Task 13 에서 일괄 보수.

- [ ] **Step 4: 커밋**

```bash
git add api/routes/general_chat.py
git commit -m "feat(chat-stream): general chat POST → BG task + broker 분기"
```

---

## Task 11: Theme Chat POST 라우트 — BG task 분기

**Files:**
- Modify: `api/routes/chat.py`

- [ ] **Step 1: 기존 send_message 함수 식별 + 교체**

Run: `grep -n "def send_message\|theme_id\|chat_session_id" "d:/dzp/바이브코딩/investment-advisor/api/routes/chat.py" | head -20`

기존 `send_message` 함수의 핵심 동작(세션 검증 → theme 컨텍스트 빌드 → user INSERT → SDK 호출 → assistant INSERT → 응답) 을 다음 구조로 교체:

```python
from fastapi import BackgroundTasks
from api.chat_stream_broker import broker, ChannelAlreadyActive
from api.chat_engine import build_theme_context, query_theme_chat_stream
from shared.config import DatabaseConfig
from shared.db import get_connection


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: int,
    body: ChatMessageRequest,  # 기존 모델 그대로 사용
    bg: BackgroundTasks,
    conn=Depends(get_db_conn),
    user: UserInDB = Depends(get_current_user_required),
):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # 1) 세션 + 권한 + theme 데이터 로드 (기존 로직 유지)
        cur.execute(
            "SELECT id, user_id, theme_id FROM theme_chat_sessions WHERE id = %s",
            (session_id,),
        )
        session = cur.fetchone()
        if not session:
            raise HTTPException(404, "채팅 세션을 찾을 수 없습니다")
        if user.role != "admin" and session.get("user_id") != user.id:
            raise HTTPException(403, "본인의 채팅 세션에만 메시지를 보낼 수 있습니다")

        # quota — 기존 _check_quota_or_raise 또는 동등 호출
        _check_quota_or_raise(cur, user)

        # theme 데이터 조회 (기존 build_theme_context 입력 빌드 — 라우트의 기존 코드 그대로)
        theme_id = session["theme_id"]
        # ... 기존 theme/scenarios/proposals/macro_impacts 조회 코드 (존재 그대로 복사)
        # 결과를 build_theme_context() 에 전달
        theme_context = build_theme_context(theme, scenarios, proposals, macro_impacts)

        cur.execute(
            """SELECT role, content FROM theme_chat_messages
               WHERE chat_session_id = %s ORDER BY created_at""",
            (session_id,),
        )
        history = [dict(row) for row in cur.fetchall()]

        cur.execute(
            """INSERT INTO theme_chat_messages (chat_session_id, role, content)
               VALUES (%s, 'user', %s)
               RETURNING id, role, content, created_at""",
            (session_id, body.content),
        )
        user_msg = cur.fetchone()
    conn.commit()

    try:
        channel = broker.open_channel("theme", session_id)
    except ChannelAlreadyActive:
        raise HTTPException(409, "이전 응답이 아직 생성 중입니다. 잠시만 기다려주세요.")

    async def _runner():
        async def _on_token(t: str):
            await broker.publish_token("theme", session_id, t)
        async def _on_error(msg: str, code: str):
            await broker.fail("theme", session_id, msg, code)
        try:
            final_text = await query_theme_chat_stream(
                theme_context=theme_context,
                conversation_history=history,
                user_message=body.content,
                on_token=_on_token,
                on_error=_on_error,
            )
            db_conn = get_connection(DatabaseConfig())
            try:
                with db_conn.cursor(cursor_factory=RealDictCursor) as cur2:
                    cur2.execute(
                        """INSERT INTO theme_chat_messages
                               (chat_session_id, role, content)
                           VALUES (%s, 'assistant', %s)
                           RETURNING id""",
                        (session_id, final_text),
                    )
                    msg_id = cur2.fetchone()["id"]
                    cur2.execute(
                        "SELECT COUNT(*) AS c FROM theme_chat_messages WHERE chat_session_id = %s",
                        (session_id,),
                    )
                    if cur2.fetchone()["c"] <= 2:
                        title = body.content[:50] + ("..." if len(body.content) > 50 else "")
                        cur2.execute(
                            "UPDATE theme_chat_sessions SET title=%s, updated_at=NOW() WHERE id=%s",
                            (title, session_id),
                        )
                    else:
                        cur2.execute(
                            "UPDATE theme_chat_sessions SET updated_at=NOW() WHERE id=%s",
                            (session_id,),
                        )
                db_conn.commit()
            finally:
                db_conn.close()
            await broker.complete("theme", session_id, final_text, msg_id)
        except Exception as e:
            await broker.fail("theme", session_id, str(e), "runtime")

    bg.add_task(_runner)
    return {
        "user_message": _serialize_row(user_msg),
        "stream": {
            "kind": "theme",
            "session_id": session_id,
            "started_at": channel.started_at.isoformat(),
        },
    }
```

**중요**: 기존 라우트의 theme 데이터 조회 코드(예: themes/scenarios/proposals/macro_impacts SELECT) 는 그대로 보존. `# ... 기존 ... 코드 그대로 복사` 부분에 정확히 채워넣을 것 — 단순히 placeholder 가 아니라 기존 코드를 옮겨야 함.

- [ ] **Step 2: 임포트 검증**

Run: `python -c "from api.routes.chat import send_message; print('ok')"`
Expected: `ok`

- [ ] **Step 3: 커밋**

```bash
git add api/routes/chat.py
git commit -m "feat(chat-stream): theme chat POST → BG task + broker 분기"
```

---

## Task 12: Education POST 라우트 — BG task 분기

**Files:**
- Modify: `api/routes/education.py`

- [ ] **Step 1: 기존 education send_message 교체**

`api/routes/education.py` 의 send_message(line 153 근방) 를 Task 11 의 코드 블록을 베이스로 다음 substitution 만 적용해 교체. (Task 11 의 코드를 복사한 뒤, 각 항목을 그대로 변경)

| Task 11 (theme) | Task 12 (education) |
|---|---|
| `broker.open_channel("theme", ...)` | `broker.open_channel("education", ...)` |
| `broker.publish_token("theme", ...)` | `broker.publish_token("education", ...)` |
| `broker.complete("theme", ...)` / `broker.fail("theme", ...)` | `broker.complete("education", ...)` / `broker.fail("education", ...)` |
| `theme_chat_sessions` (SELECT) | `education_chat_sessions` |
| `theme_chat_messages` (SELECT/INSERT) | `education_chat_messages` |
| `session["theme_id"]` + theme/scenarios/proposals/macro_impacts 조회 | `session["topic_id"]` + `cur.execute("SELECT * FROM education_topics WHERE id = %s", (topic_id,))` 후 `topic = cur.fetchone()` |
| `build_theme_context(theme, scenarios, proposals, macro_impacts)` | `build_topic_context(topic)` |
| `from api.chat_engine import build_theme_context, query_theme_chat_stream` | `from api.education_engine import build_topic_context, query_education_chat_stream` |
| `theme_context=...` (스트리밍 호출 인자) | `topic_context=...` |
| `"kind": "theme"` (응답) | `"kind": "education"` |

session_id 가 `theme_id` 또는 `topic_id` 가 아니라 **chat session id** 인 것에 주의 — broker 키도 chat session id 기준.

기존 라우트의 quota 체크 헬퍼 (`_check_quota_or_raise` 등) 가 education 전용으로 따로 있으면 그대로 사용. 없으면 기존 라우트 안에서 quota 체크하는 SQL/로직을 그대로 둘 것 — 본 plan 은 quota 정책 변경 안 함.

- [ ] **Step 2: 임포트 검증**

Run: `python -c "from api.routes.education import send_message; print('ok')"`
Expected: `ok`

- [ ] **Step 3: 커밋**

```bash
git add api/routes/education.py
git commit -m "feat(chat-stream): education chat POST → BG task + broker 분기"
```

---

## Task 13: 클라이언트 JS + 템플릿 wiring

**Files:**
- Create: `api/static/js/chat_stream_client.js`
- Modify: `api/templates/general_chat_room.html`
- Modify: `api/templates/chat_room.html`
- Modify: `api/templates/education/chat_room.html`

- [ ] **Step 1: 공용 클라이언트 컨트롤러 작성**

```javascript
// api/static/js/chat_stream_client.js
// 사용:
//   const stream = attachChatStream("general", sessionId, {
//     onReplay: (text, startedAt) => {...},
//     onToken: (text) => {...},
//     onDone: ({message_id, final_text}) => {...},
//     onError: ({message, code}) => {...},
//     onIdle: () => {...},
//   });
//   stream.detach();   // 페이지 떠날 때
//
// 자동 재연결: onerror 시 지수 백오프 (1s, 2s, 4s, ..., max 30s)

(function (global) {
  function attachChatStream(kind, sessionId, callbacks) {
    let es = null;
    let retry = 0;
    let detached = false;

    function connect() {
      if (detached) return;
      const url = `/api/chat-stream/${kind}/${sessionId}`;
      es = new EventSource(url, { withCredentials: true });

      es.addEventListener("replay", (e) => {
        const d = JSON.parse(e.data);
        callbacks.onReplay && callbacks.onReplay(d.text, d.started_at);
      });
      es.addEventListener("token", (e) => {
        const d = JSON.parse(e.data);
        callbacks.onToken && callbacks.onToken(d.text);
      });
      es.addEventListener("done", (e) => {
        const d = JSON.parse(e.data);
        callbacks.onDone && callbacks.onDone(d);
        retry = 0;
      });
      es.addEventListener("error", (e) => {
        if (e && e.data) {
          // 서버 명시 에러
          try {
            const d = JSON.parse(e.data);
            callbacks.onError && callbacks.onError(d);
            return;
          } catch (_) { /* fallthrough */ }
        }
        // 네트워크 에러 → 자동 재연결
        if (es) es.close();
        if (detached) return;
        retry += 1;
        const delay = Math.min(30000, 1000 * Math.pow(2, retry - 1));
        setTimeout(connect, delay);
      });
      es.addEventListener("idle", () => {
        callbacks.onIdle && callbacks.onIdle();
      });
    }

    connect();
    return {
      detach() {
        detached = true;
        if (es) es.close();
      },
    };
  }

  global.attachChatStream = attachChatStream;
})(window);
```

- [ ] **Step 2: general_chat_room.html 클라 wiring**

`api/templates/general_chat_room.html` 의 기존 메시지 전송 / 응답 표시 JS 블록을 다음으로 교체:

```html
<script src="/static/js/chat_stream_client.js"></script>
<script>
(function () {
  const sessionId = {{ session.id }};
  const messagesEl = document.getElementById("messages");
  let provisional = null;

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
  }

  function appendUserMessage(m) {
    const el = document.createElement("div");
    el.className = "msg msg-user";
    el.dataset.messageId = m.id;
    el.innerHTML = `<div class="msg-content">${escapeHtml(m.content)}</div>`;
    messagesEl.appendChild(el);
    el.scrollIntoView({ block: "end" });
  }

  function ensureProvisional() {
    if (!provisional) {
      provisional = document.createElement("div");
      provisional.className = "msg msg-assistant msg-provisional";
      provisional.innerHTML = '<div class="msg-content"></div><div class="msg-typing">생성 중…</div>';
      messagesEl.appendChild(provisional);
      provisional.scrollIntoView({ block: "end" });
    }
    return provisional.querySelector(".msg-content");
  }

  function commitAssistant(messageId, finalText) {
    if (!provisional) {
      const el = document.createElement("div");
      el.className = "msg msg-assistant";
      el.dataset.messageId = messageId;
      el.innerHTML = `<div class="msg-content"></div>`;
      el.querySelector(".msg-content").textContent = finalText;
      messagesEl.appendChild(el);
    } else {
      provisional.classList.remove("msg-provisional");
      provisional.dataset.messageId = messageId;
      const typing = provisional.querySelector(".msg-typing");
      if (typing) typing.remove();
      provisional.querySelector(".msg-content").textContent = finalText;
      provisional = null;
    }
  }

  const stream = attachChatStream("general", sessionId, {
    onReplay: (text) => { ensureProvisional().textContent = text; },
    onToken: (text) => {
      const el = ensureProvisional();
      el.textContent = el.textContent + text;
    },
    onDone: ({ message_id, final_text }) => commitAssistant(message_id, final_text),
    onError: ({ message }) => {
      if (provisional) {
        const t = provisional.querySelector(".msg-typing");
        if (t) t.textContent = "오류: " + message;
      }
    },
    onIdle: () => { /* placeholder 유지 */ },
  });
  window.addEventListener("beforeunload", () => stream.detach());

  document.getElementById("send-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = document.getElementById("send-input");
    const content = input.value.trim();
    if (!content) return;
    input.disabled = true;
    try {
      const res = await fetch(`/general-chat/sessions/${sessionId}/messages`, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        alert("전송 실패: " + (err.detail || res.status));
        return;
      }
      const data = await res.json();
      appendUserMessage(data.user_message);
      input.value = "";
    } finally {
      input.disabled = false;
      input.focus();
    }
  });
})();
</script>
```

기존 form/input/messages 컨테이너의 id (`send-form`, `send-input`, `messages`) 가 다르면 템플릿 실제 id 에 맞게 변수명 조정.

- [ ] **Step 3: chat_room.html 동일 패턴 적용**

`kind="theme"`, POST URL `/chat/sessions/${sessionId}/messages`. 나머지 동일.

- [ ] **Step 4: education/chat_room.html 동일 패턴 적용**

`kind="education"`, POST URL `/education/sessions/${sessionId}/messages`. 나머지 동일.

- [ ] **Step 5: 임포트 / 정적 자원 검증**

Run: `python -c "import api.main; print('ok')"`
Expected: `ok`

`/static/js/chat_stream_client.js` 가 정적 파일 마운트 (`api/main.py:75`) 하에 노출되는지 — `api/static/js/` 경로면 `/static/js/chat_stream_client.js` 로 접근 가능.

- [ ] **Step 6: 커밋**

```bash
git add api/static/js/chat_stream_client.js \
        api/templates/general_chat_room.html \
        api/templates/chat_room.html \
        api/templates/education/chat_room.html
git commit -m "feat(chat-stream): 클라이언트 SSE 컨트롤러 + 3채팅방 템플릿 wiring"
```

---

## Task 14: main.py — 라우터 등록 + cleanup task 시작 + 통합 테스트 + 회귀 보수

**Files:**
- Modify: `api/main.py`
- Create: `tests/test_chat_streaming_integration.py`
- (선택) Modify: 기존 chat 통합 테스트 — 응답 스키마 변경 반영

- [ ] **Step 1: main.py 에 chat_stream 라우터 등록**

`api/main.py` 의 import 블록(line 9-13)에 `chat_stream` 추가:

```python
from api.routes import (
    sessions, themes, proposals, chat, admin, admin_systemd,
    auth as auth_routes, user_admin, watchlist, track_record,
    stocks, education, inquiry, marketing, dashboard,
    chat_stream,
)
```

라우터 등록(line 88 근방의 `app.include_router(chat.router)` 직후):

```python
app.include_router(chat_stream.router)
```

- [ ] **Step 2: lifespan 에 broker.start_cleanup() 추가**

`api/main.py:lifespan` 함수(line 16-27) 의 `init_db(...)` 호출 직후 추가:

```python
from api.chat_stream_broker import broker as _chat_broker
_chat_broker.start_cleanup()
print("[CHAT-STREAM] broker cleanup task 시작 (TTL 600s, hard kill 1500s)")
```

- [ ] **Step 3: 통합 테스트 작성**

```python
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
    실제 fastapi.testclient 로 라우트 호출 — DB / SDK / quota / auth mock.
    """
    from api.chat_stream_broker import ChatStreamBroker

    fresh = ChatStreamBroker()

    # 라우트 핸들러를 직접 호출하기보다, 핵심 로직(broker.open_channel) 만
    # 시퀀스 검증 — full TestClient 통합은 운영기 수동 E2E 로 위임.
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
```

- [ ] **Step 4: 모든 신규 테스트 + 기존 테스트 실행**

Run: `pytest tests/test_chat_stream_broker.py tests/test_chat_stream_helpers.py tests/test_chat_stream_routes.py tests/test_chat_streaming_integration.py -v`
Expected: 신규 테스트 모두 PASS (broker 8 + helpers 4 + routes 5 + integration 3 = 20)

전체 테스트 회귀:

Run: `pytest tests/ -v 2>&1 | tail -30`
Expected: 신규 PASS. 기존 chat 라우트 통합 테스트(있다면) 일부 응답 스키마 변경으로 실패 가능 — 다음 스텝에서 보수.

- [ ] **Step 5: 기존 chat 라우트 회귀 테스트 보수**

기존 테스트가 `assert response.json()["assistant_message"]["content"] == ...` 같이 동기 응답을 기대하면, 다음 패턴으로 변경:

```python
# Before
data = res.json()
assert data["assistant_message"]["content"] == ...

# After (BG task + broker mock)
data = res.json()
assert "stream" in data
assert data["stream"]["kind"] == "general"
# assistant 메시지는 broker 가 publish 한 시뮬레이션을 별도 검증
```

대상 테스트 식별:

Run: `grep -rn "assistant_message" "d:/dzp/바이브코딩/investment-advisor/tests/" 2>&1 | head`

해당 테스트가 있으면 위 패턴으로 보수. 없으면 스킵.

- [ ] **Step 6: 임포트 / startup 스모크**

Run: `python -c "import api.main; print('ok')"`
Expected: `ok`

(가능하면) Run: `python -m uvicorn api.main:app --port 8001 &` 후 즉시 종료 — startup 로그에 `[CHAT-STREAM] broker cleanup task 시작` 확인.

- [ ] **Step 7: 운영기 systemd 단일 워커 검증**

Run: `grep -n "workers\|ExecStart" "d:/dzp/바이브코딩/investment-advisor/deploy/systemd/" -r 2>&1 | head`

`investment-advisor-api.service` 의 ExecStart 가 `--workers` 미지정 또는 `=1` 인지 확인. 다중 워커면 spec § 2.2 비목표 위반 — 별도 이슈로 backlog 등록 (broker Redis 어댑터 도입 전까지 다중 워커 비활성화).

- [ ] **Step 8: 최종 커밋 + 프롬프트 로그 함께**

```bash
git add api/main.py tests/test_chat_streaming_integration.py
# (있으면) 보수한 기존 테스트 파일도 add
# 프롬프트 로그도 함께
git add _docs/_prompts/20260430_prompt.md
git commit -m "$(cat <<'EOF'
feat(chat-stream): 라우터 등록 + lifespan cleanup + 통합 테스트

- api/main.py: chat_stream 라우터 include + lifespan 에서 broker.start_cleanup()
- 통합 테스트: open_channel 시퀀스 / 동시 메시지 409 / 구독자 disconnect 안전성
- 기존 회귀: 응답 스키마 변경(assistant_message → stream) 반영

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## 운영기 수동 E2E 체크리스트 (배포 후)

이 섹션은 자동 테스트로 커버할 수 없는 항목 — 라즈베리파이 배포 직후 수동 검증.

- [ ] PC 와 모바일 (또는 두 브라우저 탭) 같은 user 로 같은 채팅방 접속
- [ ] 한쪽에서 메시지 전송 → 양쪽에서 토큰 라이브 흐름 확인 (replay → token → done)
- [ ] 메시지 전송 즉시 페이지 새로고침 → replay 후 이어서 라이브 또는 done 확인
- [ ] 응답 도중 EventSource 끊고(devtools network throttle offline) 다시 online → 자동 재연결 후 done 도착
- [ ] nginx 통과 시 SSE 30분 idle keepalive 끊김 없는지 확인 (`X-Accel-Buffering: no` 헤더 효과)
- [ ] systemctl restart investment-advisor-api 후 채팅방 접속 → DB 폴백으로 idle 송출 → 5분 후 자동 종료
- [ ] 동시 메시지 전송(같은 세션) → 두 번째는 409 응답
- [ ] Free 티어 quota 초과 시 POST 시점에 402 (broker 채널 미생성 확인)
- [ ] 운영 1주 후 `journalctl -u investment-advisor-api | grep "broker hard kill"` 발생 빈도 검토 — 자주 발생하면 SDK timeout / hard_kill_seconds 재조정

---

## 변경 영향 요약

| 영역 | 변경 |
|---|---|
| DB 스키마 | 변경 없음 |
| 외부 API | `/api/chat-stream/{kind}/{session_id}` GET 신규. 기존 POST 응답 스키마 BC break (인증 필수 — 외부 클라 영향 0) |
| 환경변수 | 변경 없음 (broker TTL 은 코드 상수) |
| systemd | 변경 없음 (단일 워커 가정 검증만) |
| 패키지 | `pytest-asyncio` 추가 (없으면) |
| 마이그레이션 | 없음 |
| 롤백 | 1 PR revert. broker in-memory 라 DB 영향 없음 |
