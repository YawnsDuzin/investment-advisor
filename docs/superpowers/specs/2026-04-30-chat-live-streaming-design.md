# Chat Live Streaming — 멀티 디바이스 SSE 라이브 스트리밍 (General/Theme/Tutor 동시)

작성일: 2026-04-30
관련 작업 폴더: `api/general_chat_engine.py`, `api/chat_engine.py`, `api/education_engine.py`, `api/routes/general_chat.py`, `api/routes/chat.py`, `api/routes/education.py`, `api/templates/general_chat_room.html`, `api/templates/chat_room.html`, `api/templates/education/chat_room.html`, `api/static/js/`, `tests/`
관련 기존 자산: `api/static/js/sse_log_viewer.js` (admin SSE 로그 — 패턴 참조)
연관 이슈/대화: `_docs/_prompts/20260430_prompt.md` ("채팅 했을때 중간에 다른 화면가거나 종료해도, 다시 접속했을때, 실시간으로 확인가능하게")

---

## 1. 배경 / 동기

현행 3개 채팅 (`general_chat`, `theme_chat`(`chat`), `education_chat`) 은 모두 다음 동기 패턴을 공유한다.

```
POST /<...>/sessions/{id}/messages
  → user 메시지 INSERT (DB)
  → query_*_chat_sync(...)            # anyio.run, 응답 완료까지 블로킹 (수~수십 초)
  → assistant 메시지 INSERT (DB)
  → 200 OK {user_message, assistant_message}
```

문제:
1. **HTTP 요청 1개에 응답 생성 전 과정이 묶여있다**. 클라이언트가 도중에 페이지를 떠나면 `fetch().then()` 핸들러가 수신하지 못해 클라이언트 UI 에 표시되지 않는다(서버는 끝까지 돌아 DB 에 저장은 됨).
2. **다시 접속해도 "응답이 도착했다"는 신호가 즉시 오지 않는다** — 페이지 로드 시 DB 조회로 가장 최근 메시지를 가져올 뿐, 진행 중인 응답을 라이브로 볼 방법이 없다.
3. **다른 디바이스로 핸드오프 불가** — PC 에서 보낸 질문을 모바일에서 이어보는 시나리오 0%.
4. **"응답 생성 중"의 시각적 피드백이 빈약** — 단순 spinner 만 노출. 토큰 단위 라이브 스트리밍 대비 체감 차가 크다.

→ 응답 생성을 백그라운드 task 로 분리하고, **in-process pub/sub broker + SSE** 로 라이브 스트림 채널을 노출한다. **DB 가 정답**(source of truth), broker 는 진행 중인 토큰을 fan-out 할 뿐이다.

## 2. 목표 / 비-목표

### 2.1 목표 (in-scope)

- 3개 채팅 (General / Theme / Tutor) **동시 적용**. 한 PR 으로 통합.
- POST `/messages` 응답 스키마 변경: 즉시 `{user_message, stream}` 반환 → 응답 본문은 SSE 로 전달 (BC break).
- 신규 SSE 라우트 `GET /api/chat-stream/{kind}/{session_id}` — 권한 가드 → broker 구독 → broker 없을 시 DB 폴백.
- **Resume semantics = (b) Full replay + (c) DB 폴백**:
  - SSE 연결 시점에 진행 중인 broker 채널이 있으면 **누적 버퍼를 1회 flush(`event: replay`)** → 이후 도착하는 토큰 라이브 송출(`event: token`).
  - broker 채널이 없으면 DB 에서 마지막 메시지 조회 → assistant 면 즉시 `event: done` 단발, user 면 `event: idle` (응답 생성이 끊긴 케이스).
- **멀티 디바이스 fan-out** — 같은 `(kind, session_id)` 에 N 구독자 동시 접속 가능. 같은 user_id 의 PC + 모바일 + 태블릿이 동시에 라이브 스트림을 받음.
- Claude Agent SDK Python 의 `include_partial_messages=True` 옵션을 활용한 **진짜 토큰 단위 스트리밍** (fake-streaming 폴백 불필요).
- 클라이언트 공용 컨트롤러 `static/js/chat_stream_client.js` — `EventSource` 래핑 + 자동 재연결 + replay/token/done/error/idle 이벤트 핸들링.

### 2.2 비-목표 (YAGNI / out-of-scope, v1 제외)

- **멀티 워커(uvicorn `--workers > 1`) 환경 지원** — in-process broker 의 한계. 운영기는 단일 워커 가정. Redis 백엔드 어댑터는 v2 로 분리하되, broker 인터페이스는 처음부터 추상화.
- **서버 재시작 시 진행 채널 자동 복구** — broker 는 in-memory. 재시작 시 진행 중이던 응답은 잃음(BG task 도 함께 종료). DB 에는 user 메시지만 남음. 클라가 5분 timeout 후 사용자에 "다시 보내주세요" 안내.
- **알림 시스템(우측 상단 배지) 연동** — "다른 페이지에 있는 사용자에게 응답 도착 알림" 은 backlog. 본 spec 은 *채팅방 페이지에 있는 동안*의 라이브 스트리밍에 한정.
- **음성 / 이미지 / 비텍스트 청크** — 텍스트 토큰만.
- **동시 메시지 큐잉 / 메시지 히스토리 검색 / 메시지 편집** — 별개 기능, backlog.
- **이전 채팅 기록의 retroactive 스트리밍** — 이미 완료된 메시지는 즉시 `done` 으로 표시. 토큰 단위 재생 안 함.

## 3. § 1 — 아키텍처 개요

### 3.1 컴포넌트 다이어그램

```
┌─────────────────────────────────────────────────────────────┐
│ Client (탭/디바이스 N개 — 같은 user_id)                       │
│   채팅방 페이지 진입 시 즉시 SSE 연결 (idle 라도)              │
│   POST /<...>/messages → {user_message, stream} 즉시 수령    │
│   GET  /api/chat-stream/{kind}/{session_id}                   │
│         ↳ event: replay → event: token... → event: done       │
└─────────────────────────────────────────────────────────────┘
                ↑                          ↑
              SSE                          POST (BG task 분기)
                │                          │
┌─────────────────────────────────────────────────────────────┐
│ FastAPI (uvicorn 단일 워커, asyncio 이벤트 루프 1개)         │
│                                                              │
│   api/routes/chat_stream.py    ← 신규 (SSE 공용 라우트)      │
│   api/routes/general_chat.py   ← POST 변경 (BG task 분기)    │
│   api/routes/chat.py           ← POST 변경                    │
│   api/routes/education.py      ← POST 변경                    │
│                                                              │
│   api/chat_stream_broker.py    ← 신규 (in-memory pub/sub)    │
│       └─ StreamChannel(kind, session_id, accumulated, ...)   │
│       └─ open / publish_token / complete / fail / subscribe  │
│       └─ TTL cleanup (60s 주기)                              │
│                                                              │
│   api/general_chat_engine.py   ← query_*_stream() 추가       │
│   api/chat_engine.py           ← query_*_stream() 추가       │
│   api/education_engine.py      ← query_*_stream() 추가       │
│       └─ ClaudeAgentOptions(include_partial_messages=True)   │
│       └─ async generator → on_token 콜백                     │
│                                                              │
│   shared/db.py                 ← 변경 없음 (assistant INSERT 는 │
│                                  BG task 가 별도 conn 으로 호출)│
└─────────────────────────────────────────────────────────────┘
```

### 3.2 데이터 흐름 (정상 경로)

```
[Client A] 채팅방 페이지 진입
  GET /pages/general-chat/123
    → 페이지 렌더 + 기존 메시지 표시
    → 즉시 attachChatStream("general", 123) → EventSource 연결
       └─ broker.has_active(general, 123) == false → DB 폴백
          → 마지막이 assistant → event: done (이미 완료된 상태) → SSE 유지

[Client A] 메시지 전송
  POST /general-chat/sessions/123/messages {content: "..."}
    → user 메시지 INSERT (sync) → conn.commit()
    → broker.open_channel("general", 123) — 이미 active 면 409 CONFLICT
    → BackgroundTasks.add_task(_runner)
    → return 200 {user_message, stream: {kind, session_id, started_at}}

[BG task _runner]
  query_general_chat_stream(...)
    async for partial in claude_agent_sdk.query(include_partial_messages=True):
      await broker.publish_token("general", 123, delta_text)
        └─ channel.accumulated.append(delta) + fan-out to subscribers
    → broker 가 fan-out 한 토큰을 [Client A] 의 EventSource 가 수신
       → event: token {text: "..."} → DOM append (provisional)

  스트림 완료 → assistant INSERT (별도 conn) → broker.complete(channel, final_text, msg_id)
    → event: done {message_id, final_text} → DOM commit (provisional → 정식 메시지)

[Client B] 같은 user_id, 모바일 진입 (5초 후, 응답 생성 도중)
  GET /pages/general-chat/123 → SSE 연결
    → broker.has_active(general, 123) == true
    → event: replay {text: 누적분} (1회)
    → 이어서 event: token (라이브)
    → event: done
```

### 3.3 데이터 흐름 (장애 경로)

#### 3.3.1 클라이언트 disconnect 도중 (가장 흔한 케이스)
```
[Client A] 메시지 전송 → BG task 시작
[Client A] 페이지 떠남 → EventSource close
  └─ broker.subscribe 의 큐만 제거됨. BG task / channel 은 영향 없음
BG task 계속 진행 → broker.complete → DB INSERT
[Client A] 다시 접속
  → SSE 연결 → broker 채널이 still active 또는 이미 cleanup
    case A: 아직 진행 중 → event: replay → token... → done
    case B: 이미 완료 + TTL 내 → event: replay (전체 final_text) → event: done
    case C: TTL 지난 후 → DB 폴백 → 마지막이 assistant → event: done
```

#### 3.3.2 워커 재시작 도중
```
BG task 진행 중 → 워커 SIGTERM
  → BG task 강제 종료 (asyncio cancel)
  → broker dict 휘발 (in-memory)
  → DB 에는 user 메시지만 INSERT 된 상태
[Client] 재접속
  → SSE 연결 → broker 채널 없음 → DB 폴백 → 마지막이 user
  → event: idle → 30s keepalive 반복
  → 클라가 5분 idle 지속 시 "응답 생성이 중단되었습니다. 다시 보내주세요" 안내
  → v1 은 자동 재시도 X (사용자 명시적 재전송)
```

#### 3.3.3 SDK 실패
```
BG task 의 query_*_stream() 이 예외
  → broker.fail(channel, error_msg, code)
  → event: error {message, code} 모든 구독자 fan-out
  → 채널 status="failed" 로 marking + TTL cleanup
  → DB 에는 assistant 미저장 (user 만 남음)
  → 클라 UI: 빨간색 에러 박스 + 재시도 버튼
  → 재시도 = 사용자가 같은 메시지 재전송 (서버측 자동 재시도 X)
```

## 4. § 2 — Broker 모듈 (`api/chat_stream_broker.py`)

### 4.1 객체 모델

```python
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator, Literal, Optional
import asyncio

ChannelStatus = Literal["active", "completed", "failed"]
ChatKind = Literal["general", "theme", "education"]

@dataclass
class StreamChannel:
    kind: ChatKind
    session_id: int
    started_at: datetime
    accumulated: list[str] = field(default_factory=list)   # 누적 토큰
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
    """In-process pub/sub broker. 단일 이벤트 루프 가정.

    스레드 안전성은 asyncio 단일 루프 가정이라 락 불필요.
    멀티 워커 환경은 v2 (Redis 어댑터) 에서 처리.
    """

    def __init__(self, ttl_seconds: int = 600, hard_kill_seconds: int = 1500):
        self._channels: dict[tuple[ChatKind, int], StreamChannel] = {}
        self._ttl_seconds = ttl_seconds
        self._hard_kill_seconds = hard_kill_seconds
        self._cleanup_task: Optional[asyncio.Task] = None

    # ─── 조회 ────────────────────────────────────

    def has_channel(self, kind: ChatKind, session_id: int) -> bool:
        """라우트가 broker 분기 vs DB 폴백 결정에 사용. 활성/완료/실패 모두 True.
        TTL 로 정리된 채널은 False — 클라가 자연스럽게 DB 폴백으로 흐름.
        """
        return (kind, session_id) in self._channels

    # ─── 발행자 측 (BG task) ─────────────────────────

    def open_channel(self, kind: ChatKind, session_id: int) -> StreamChannel:
        """BG task 시작 직전 호출. 이미 active 면 RuntimeError → 라우트가 409 변환."""
        key = (kind, session_id)
        existing = self._channels.get(key)
        if existing is not None and existing.status == "active":
            raise ChannelAlreadyActive(kind, session_id)
        channel = StreamChannel(kind=kind, session_id=session_id,
                                started_at=datetime.now(timezone.utc))
        self._channels[key] = channel
        return channel

    async def publish_token(self, kind: ChatKind, session_id: int, token: str) -> None:
        ch = self._channels.get((kind, session_id))
        if ch is None or ch.status != "active":
            return  # 채널이 사라졌거나 종료된 경우 무시
        ch.accumulated.append(token)
        # 모든 구독자 큐에 fan-out (블로킹 없이 try)
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
            q.put_nowait(("done", {
                "message_id": final_message_id, "final_text": final_text
            }))
            q.put_nowait(("__close__", None))

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
            q.put_nowait(("error", {"message": message, "code": code}))
            q.put_nowait(("__close__", None))

    # ─── 구독자 측 (SSE 라우트) ──────────────────────

    async def subscribe(self, kind: ChatKind, session_id: int
                        ) -> AsyncIterator[tuple[str, dict]]:
        """SSE 라우트가 호출. yield (event_name, data_dict).

        연결 직후 replay 1회 → 이후 token/done/error 이벤트.
        채널이 없으면 즉시 종료 (라우트가 DB 폴백).
        """
        ch = self._channels.get((kind, session_id))
        if ch is None:
            return

        # replay
        if ch.accumulated:
            yield ("replay", {"text": ch.accumulated_text(),
                              "started_at": ch.started_at.isoformat()})

        # 이미 종료된 채널이면 즉시 done/error 후 종료
        if ch.status == "completed":
            yield ("done", {"message_id": ch.final_message_id,
                            "final_text": ch.final_text})
            return
        if ch.status == "failed":
            yield ("error", {"message": ch.error_message, "code": ch.error_code})
            return

        # active — 큐 등록 후 await
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

    # ─── 정리 ────────────────────────────────────

    def start_cleanup(self) -> None:
        """FastAPI startup 이벤트에서 호출."""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            now = datetime.now(timezone.utc)
            # snapshot — await 도중 dict 변경되어도 안전
            snapshot = list(self._channels.items())
            stale: list[tuple] = []
            for key, ch in snapshot:
                if ch.status == "active":
                    age = (now - ch.started_at).total_seconds()
                    if age > self._hard_kill_seconds:
                        await self.fail(ch.kind, ch.session_id,
                                        "broker hard kill (timeout)", "broker_timeout")
                elif ch.completed_at is not None:
                    age = (now - ch.completed_at).total_seconds()
                    if age > self._ttl_seconds:
                        stale.append(key)
            for key in stale:
                self._channels.pop(key, None)


class ChannelAlreadyActive(Exception):
    def __init__(self, kind: str, session_id: int):
        self.kind = kind
        self.session_id = session_id
        super().__init__(f"channel ({kind}, {session_id}) already active")


# 싱글턴 (모듈 레벨)
broker = ChatStreamBroker()
```

### 4.2 설계 결정

- **싱글턴 모듈 레벨 인스턴스** — DI 복잡도 회피. 테스트는 `broker._channels.clear()` 또는 `monkeypatch` 로 격리.
- **TTL 정책**: 완료 채널 10분 보관(`ttl_seconds=600`) — 늦게 들어온 클라가 `event: done` 받을 수 있게. 진행 중 채널 25분 hard kill(`hard_kill_seconds=1500`) — `QUERY_TIMEOUT=900` 보다 충분히 큼.
- **큐 maxsize=1024** — 토큰은 짧고 SSE 가 빨리 비우니 사실상 무제한. full 발생 시 해당 구독자 drop (다른 구독자는 영향 없음).
- **`__close__` 센티넬** — Queue 기반 종료 신호. async generator close 와 분리.
- **fail 후 채널 status 변경** — replay 시 아직 누적분이 있으면 일부라도 보여주고 error 이벤트 (UX: "여기까지 받았는데 실패했어요").

## 5. § 3 — Engine 스트리밍 변형

### 5.1 공용 헬퍼 (`api/chat_stream_helpers.py` — 신규)

```python
"""Claude Agent SDK partial 메시지 → 토큰 delta 추출 공용 로직.

3개 엔진(general/theme/education)이 동일한 SDK 호출 패턴을 공유하므로
delta 계산 로직을 공용화한다.
"""
from typing import Awaitable, Callable, Optional
import sys

from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock
# 주의: SDK 버전별로 PartialAssistantMessage / StreamEvent 등 추가 타입 존재.
# 구현 시 `from claude_agent_sdk.types import ...` 로 정확히 분류.

OnToken = Callable[[str], Awaitable[None]]
OnError = Callable[[str, str], Awaitable[None]]

def _extract_text(message) -> Optional[str]:
    """SDK 메시지에서 누적 텍스트(prefix-style) 또는 신규 chunk 추출.

    반환값:
    - 누적 prefix (partial 케이스): 호출자가 직전 누적과 비교해 delta 계산
    - 신규 chunk (완성 AssistantMessage 의 새 TextBlock 등): 그대로 추가
    - None: 텍스트 없는 메시지 (ToolUseBlock, ResultMessage 등)
    """
    if isinstance(message, AssistantMessage):
        parts: list[str] = []
        for block in message.content:
            if isinstance(block, TextBlock):
                parts.append(block.text)
        return "".join(parts) if parts else None
    # PartialAssistantMessage 등 SDK 신규 타입은 hasattr 으로 duck-typing
    # 구현 단계에서 `claude_agent_sdk.types` 의 정확한 클래스명 확정
    if hasattr(message, "content") and hasattr(message, "partial"):
        # 가정: partial=True 이면 content 가 점진적으로 자라는 prefix
        parts: list[str] = []
        for block in getattr(message, "content", []):
            if isinstance(block, TextBlock):
                parts.append(block.text)
        return "".join(parts) if parts else None
    return None


async def stream_claude_chat(
    prompt: str,
    system: str,
    *,
    on_token: OnToken,
    on_error: OnError,
    max_turns: int = 1,
) -> str:
    """Claude SDK 스트리밍 호출 + 토큰 delta 콜백. 최종 누적 텍스트 반환.

    on_token 호출은 누적 텍스트와의 delta 만 보낸다 (중복 송출 X).
    on_error 는 raise 전에 반드시 호출 — broker 가 구독자에게 error 이벤트 전파해야 함.
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
                include_partial_messages=True,   # 핵심
                stderr=_on_stderr,
            ),
        ):
            new_text = _extract_text(message)
            if new_text is None:
                continue
            if new_text.startswith(accumulated):
                # prefix-style partial → delta 만 emit
                delta = new_text[len(accumulated):]
                accumulated = new_text
            else:
                # 새 chunk (별개 TextBlock 또는 완성 AssistantMessage 추가분)
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

### 5.2 엔진별 wrapper (예: `api/general_chat_engine.py`)

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
    """기존 query_general_chat_sync 의 streaming 변형.

    sync 함수도 폴백/테스트 용도로 유지 (제거 X).
    """
    # 기존 query_general_chat_sync 의 history 포맷 로직과 동일 — 기존 inline 코드를
    # `_format_history()` 로 추출하여 sync/stream 양쪽이 공유 (refactor)
    history_text = _format_history(conversation_history)
    prompt = f"{history_text}\n사용자: {user_message}"
    system = GENERAL_CHAT_SYSTEM_PROMPT.format(user_context=user_context or "")
    return await stream_claude_chat(
        prompt=prompt, system=system,
        on_token=on_token, on_error=on_error, max_turns=max_turns,
    )
```

`api/chat_engine.py:query_theme_chat_stream` / `api/education_engine.py:query_education_chat_stream` 도 동일 패턴 (각 시스템 프롬프트 / 컨텍스트 빌더 차이만).

### 5.3 SDK partial 메시지 형태 검증 (구현 단계 액션)

context7 docs 확인 결과 `include_partial_messages=True` 옵션 존재만 확인. 정확한 partial 메시지 클래스(예: `StreamEvent`, `PartialAssistantMessage`) 와 누적/delta 의미는 SDK 버전별로 다를 수 있다. 구현 첫 단계에서 다음 검증:

1. `claude_agent_sdk.types` 모듈 dump → partial 관련 타입 식별.
2. `include_partial_messages=True` 로 짧은 query 실행 → yield 되는 메시지 시퀀스 출력.
3. partial 이 prefix-style (누적) 인지, delta-style (증분) 인지, mixed 인지 확정 → `_extract_text` 분기 보강.

이 검증 결과에 따라 `_extract_text` 가 더 정교해질 수 있으나, **외부 인터페이스(broker / SSE / 클라이언트)는 변동 없음** — delta 계산이 prefix vs incremental 둘 다 같은 결과를 낸다(직전 누적 비교).

## 6. § 4 — SSE 라우트 (`api/routes/chat_stream.py`)

### 6.1 라우트 정의

```python
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
from api.chat_stream_broker import broker, ChatKind

router = APIRouter(prefix="/api/chat-stream", tags=["채팅 라이브 스트리밍"])

ALLOWED_KINDS: dict[str, dict] = {
    "general": {"sessions_table": "general_chat_sessions",
                "messages_table": "general_chat_messages",
                "session_fk": "chat_session_id"},
    "theme":   {"sessions_table": "theme_chat_sessions",
                "messages_table": "theme_chat_messages",
                "session_fk": "chat_session_id"},
    "education": {"sessions_table": "education_chat_sessions",
                  "messages_table": "education_chat_messages",
                  "session_fk": "chat_session_id"},
}


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

    # 권한 가드 — 본인 세션 또는 admin
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
        # ── 1) broker 채널 존재 여부 체크 (해당 분기면 이쪽으로만 흐름)
        if broker.has_channel(kind, session_id):
            async for ev_name, payload in broker.subscribe(kind, session_id):
                yield _format_sse(ev_name, payload)
                if await request.is_disconnected():
                    return
            return  # broker 시퀀스 끝나면 종료 (done/error 이미 송출). DB 폴백 진입 X

        # ── 2) broker 채널 없음 → DB 폴백
        async for ev_name, payload in _db_fallback_stream(
            conn, kind, cfg, session_id, request
        ):
            yield _format_sse(ev_name, payload)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",      # nginx SSE 버퍼링 방지
            "Connection": "keep-alive",
        },
    )


def _format_sse(event: str, data: dict) -> bytes:
    return (f"event: {event}\n"
            f"data: {json.dumps(data, ensure_ascii=False)}\n\n").encode("utf-8")


async def _db_fallback_stream(conn, kind, cfg, session_id, request) -> AsyncIterator:
    """broker 채널이 없을 때:
       - 마지막 메시지가 assistant → event: done (즉시) → 종료
       - 마지막이 user → event: idle 30초 keepalive 반복
       - 5분 후 자동 종료 (클라가 재연결)
       - 그 사이 broker 채널이 깨어나면 즉시 종료 → 클라가 EventSource 재연결로 broker 분기 진입
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

    # user 가 마지막 → 응답 생성 도중 워커 재시작 등 추정
    elapsed = 0
    while elapsed < 300:  # 5분
        if await request.is_disconnected():
            return
        yield ("idle", {})
        await asyncio.sleep(30)
        elapsed += 30
        # 매 30초마다 broker 재확인 — 그 사이 새 메시지 시작했을 수 있음
        if broker.has_channel(kind, session_id):
            # broker 가 깨어났다 → SSE 종료, 클라가 재연결 시 broker 분기로 진입
            return
```

### 6.2 헤더 / 프록시 호환성

- `Cache-Control: no-cache` — 중간 캐시 방지.
- `X-Accel-Buffering: no` — nginx 가 SSE 를 청크 buffer 안 하도록.
- `Connection: keep-alive` — 명시.
- 라즈베리파이 직접 노출 시 cloudflare 통과 케이스: SSE 는 cloudflare 기본 100초 idle 후 끊김. 30초 idle keepalive 로 우회.

## 7. § 5 — POST 라우트 변경 (3개 동일 패턴)

### 7.1 변경 전 (예: `general_chat.py:send_message`)

```python
@router.post("/sessions/{session_id}/messages")
def send_message(session_id, body, conn, user):
    # ... user 메시지 INSERT
    # ... query_general_chat_sync(...)  ← 블로킹
    # ... assistant 메시지 INSERT
    return {"user_message": ..., "assistant_message": ...}
```

### 7.2 변경 후

```python
from fastapi import BackgroundTasks
from api.chat_stream_broker import broker, ChannelAlreadyActive

@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: int, body: ChatMessageRequest, bg: BackgroundTasks,
    conn=Depends(get_db_conn), user: UserInDB = Depends(get_current_user_required),
):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # 1) 세션 검증 + 권한 (기존 로직 그대로)
        # 2) quota 체크
        _check_quota_or_raise(cur, user)
        # 3) user_context + history 로드
        owner_id = ...
        user_context = build_user_context(conn, owner_id)
        history = ...
        # 4) user 메시지 INSERT
        cur.execute(
            "INSERT INTO general_chat_messages ... RETURNING ...",
            (session_id, body.content),
        )
        user_msg = cur.fetchone()
    conn.commit()

    # 5) broker 채널 open — 이미 active 면 409
    try:
        channel = broker.open_channel("general", session_id)
    except ChannelAlreadyActive:
        raise HTTPException(409, "이전 응답이 아직 생성 중입니다. 잠시만 기다려주세요.")

    # 6) BG task 분기
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
            from shared.db import get_connection
            from shared.config import DatabaseConfig
            db_conn = get_connection(DatabaseConfig())
            try:
                with db_conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """INSERT INTO general_chat_messages
                           (chat_session_id, role, content)
                           VALUES (%s, 'assistant', %s)
                           RETURNING id""",
                        (session_id, final_text),
                    )
                    msg_id = cur.fetchone()["id"]
                    # 첫 응답이면 title 자동 설정 (기존 로직 보존)
                    cur.execute(
                        "SELECT COUNT(*) AS c FROM general_chat_messages WHERE chat_session_id = %s",
                        (session_id,),
                    )
                    if cur.fetchone()["c"] <= 2:
                        title = body.content[:50] + ("..." if len(body.content) > 50 else "")
                        cur.execute(
                            "UPDATE general_chat_sessions SET title=%s, updated_at=NOW() WHERE id=%s",
                            (title, session_id),
                        )
                    else:
                        cur.execute(
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

`api/routes/chat.py` (theme) / `api/routes/education.py` 도 동일 구조 — kind 문자열, 테이블/컬럼명, 컨텍스트 빌더만 다름.

### 7.3 BC break 영향

- 기존 클라이언트 JS 가 응답에서 `assistant_message` 를 기대하는 곳 → 모두 stream client 사용으로 교체. 외부 API 노출 없음(인증 필수) — 외부 클라 영향 0.
- API 문서(Swagger) — 응답 모델 변경 (Pydantic response_model 업데이트).
- 기존 테스트 (`tests/test_general_chat*.py` 등) — assistant 응답 검증 부분을 broker mock + final state 검증으로 변경.

## 8. § 6 — 클라이언트 (`api/static/js/chat_stream_client.js`)

### 8.1 공용 컨트롤러

```javascript
// chat_stream_client.js
// 사용:
//   const stream = attachChatStream(panel, "general", sessionId, {
//     onReplay: (text, startedAt) => {...},
//     onToken: (text) => {...},
//     onDone: ({message_id, final_text}) => {...},
//     onError: ({message, code}) => {...},
//     onIdle: () => {...},
//   });
//   stream.detach();  // 페이지 떠날 때
//
// 자동 재연결: idle 5분 후 또는 onerror 시 지수 백오프 (1s, 2s, 4s, ..., max 30s)

(function (global) {
  function attachChatStream(panel, kind, sessionId, callbacks) {
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
        try {
          const d = JSON.parse(e.data);
          callbacks.onError && callbacks.onError(d);
        } catch (_) {
          // 네트워크 에러 (e.data 없음) → 자동 재연결
          if (es && es.readyState !== EventSource.CLOSED) return;
          if (detached) return;
          retry += 1;
          const delay = Math.min(30000, 1000 * Math.pow(2, retry - 1));
          setTimeout(connect, delay);
        }
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

### 8.2 템플릿 wiring (예: `general_chat_room.html`)

```html
<script src="/static/js/chat_stream_client.js"></script>
<script>
  const sessionId = {{ session.id }};
  const messagesEl = document.getElementById("messages");
  let provisional = null;   // 진행 중 assistant 메시지 DOM

  function ensureProvisional() {
    if (!provisional) {
      provisional = document.createElement("div");
      provisional.className = "msg msg-assistant msg-provisional";
      provisional.innerHTML = '<div class="msg-content"></div><div class="msg-typing">생성 중…</div>';
      messagesEl.appendChild(provisional);
      provisional.scrollIntoView();
    }
    return provisional.querySelector(".msg-content");
  }

  function commitAssistant(messageId, finalText) {
    if (!provisional) {
      // replay 없이 done 만 받은 경우 (이미 완료된 상태) — 새 DOM 생성
      const el = document.createElement("div");
      el.className = "msg msg-assistant";
      el.dataset.messageId = messageId;
      el.innerHTML = `<div class="msg-content">${escapeHtml(finalText)}</div>`;
      messagesEl.appendChild(el);
    } else {
      provisional.classList.remove("msg-provisional");
      provisional.dataset.messageId = messageId;
      provisional.querySelector(".msg-typing")?.remove();
      provisional.querySelector(".msg-content").textContent = finalText;
      provisional = null;
    }
  }

  const stream = attachChatStream("general", sessionId, {
    onReplay: (text) => { ensureProvisional().textContent = text; },
    onToken: (text) => { ensureProvisional().textContent += text; },
    onDone: ({ message_id, final_text }) => commitAssistant(message_id, final_text),
    onError: ({ message }) => {
      if (provisional) {
        provisional.querySelector(".msg-typing").textContent = "오류: " + message;
      }
    },
    onIdle: () => { /* placeholder 유지 */ },
  });
  window.addEventListener("beforeunload", () => stream.detach());

  // 메시지 전송
  document.getElementById("send-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = document.getElementById("send-input");
    const content = input.value.trim();
    if (!content) return;
    input.disabled = true;
    const res = await fetch(`/general-chat/sessions/${sessionId}/messages`, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
    if (!res.ok) {
      alert("전송 실패: " + res.status);
      input.disabled = false;
      return;
    }
    const data = await res.json();
    appendUserMessage(data.user_message);
    input.value = "";
    input.disabled = false;
    // SSE 가 자동으로 token 받음
  });
</script>
```

`chat_room.html` (theme) / `education/chat_room.html` 도 동일 패턴 — `kind` 와 sessionId, POST URL 만 차이.

## 9. § 7 — 리스크 / 운영 결정 (요약)

| 리스크 | 대응 | 분류 |
|---|---|---|
| 멀티 워커 환경에서 broker 깨짐 | 운영기 systemd unit `--workers=1` 검증. 다중 워커는 v2 (Redis 어댑터) | 운영 |
| 같은 세션에 동시 user 메시지 | broker.open_channel 이 409 → 클라가 done 까지 send disable | UX |
| BG task 도중 워커 재시작 | DB 에 user 만 남음 → SSE idle → 클라 5분 후 재전송 안내. v1 자동 재시도 X | 한계 |
| broker 메모리 누수 | 완료 채널 10분 TTL, 진행 중 25분 hard kill, 60초 정리 task | 자동 |
| 다중 탭 동시 SSE | 구독자별 큐 독립. 메모리 부담 무시 가능 | OK |
| BC break (POST 응답) | 전 채팅 페이지 한 PR 동시 수정. 외부 API 노출 0 | OK |
| 프록시/CDN SSE 버퍼링 | `X-Accel-Buffering: no` + 30초 idle keepalive | OK |
| Free 티어 무제한 스트림 | quota 는 POST 시점에 체크 (기존). SSE read-only 라 제한 X | OK |
| SDK partial 메시지 형태 미확인 | 구현 첫 단계에서 SDK types dump + 짧은 query 실험. 외부 인터페이스는 변동 없음 | 검증 |
| 교육 채팅의 `topic_id` FK | broker 키는 `(kind, session_id)` 만 — topic_id 는 시스템 프롬프트 빌드 단계에서 처리, broker 와 무관 | OK |

## 10. § 8 — 운영기 검증 항목 (구현 단계)

- [ ] `deploy/systemd/investment-advisor-api.service` 의 ExecStart uvicorn 옵션 확인. `--workers` 미지정 또는 `=1` 인지.
- [ ] nginx/cloudflare 등 reverse proxy 통과 시 `/api/chat-stream/*` 경로에 SSE 호환 설정 (proxy_buffering off, proxy_read_timeout > 600s).
- [ ] 라즈베리파이 단일 워커에서 동시 SSE 100개 부하 테스트 (메모리/이벤트 루프 지연 모니터링).

## 11. § 9 — 테스트 전략

### 11.1 단위 테스트 (DB/SDK 불필요)

- `tests/test_chat_stream_broker.py`
  - open_channel 중복 → ChannelAlreadyActive
  - publish_token → 모든 구독자 큐에 fan-out
  - complete → done 이벤트 + 큐 close
  - fail → error 이벤트 + 큐 close
  - subscribe(replay) → 누적분 1회 송출 후 라이브
  - cleanup_loop → TTL 지난 완료 채널 제거, hard_kill 진행 중 채널 fail 처리
  - 다중 구독자 → 한 명 큐 full 이어도 다른 구독자 영향 없음

- `tests/test_chat_stream_helpers.py`
  - Mock SDK partial 메시지 시퀀스 → on_token 호출 횟수·누적 검증
  - prefix-style partial → delta 계산 정확
  - 새 chunk (별도 TextBlock) → 그대로 추가
  - SDK 예외 → on_error 호출 후 raise

### 11.2 통합 테스트 (TestClient + DB mock)

- `tests/test_chat_stream_routes.py`
  - 인증 없이 GET → 401
  - 다른 user_id 의 session 구독 → 403
  - 진행 중 채널 → replay → token → done 시퀀스
  - 완료된 채널 (TTL 내) → replay (final_text 통째) → done
  - DB 폴백 (broker 없음, 마지막이 assistant) → done 즉시
  - DB 폴백 (마지막이 user) → idle 반복 후 5분 종료

- `tests/test_general_chat_streaming.py` (theme/education 도 동일)
  - POST /messages → user_msg + stream 즉시 반환 (BG task spawn 검증)
  - 같은 세션 연속 POST → 두 번째는 409
  - BG task mock 으로 SDK 응답 시뮬레이트 → broker.complete 호출 + DB INSERT 확인

### 11.3 E2E (수동, 라즈베리파이 운영기)

- 두 브라우저(PC + 모바일) 같은 user 로 같은 채팅방 진입 → 한쪽에서 메시지 → 양쪽에서 토큰 라이브 흐름 확인.
- 메시지 전송 직후 페이지 떠나기 → 다시 진입 → replay 후 이어서 라이브 또는 done 확인.
- nginx 통과 시 SSE 끊김 없는지 30분 idle 검증.

## 12. § 10 — 마이그레이션 / 롤아웃

본 변경은 DB 스키마 변경 없음. 코드 한 PR 으로 다음 순서:

1. `api/chat_stream_broker.py` + `api/chat_stream_helpers.py` 추가 + 단위 테스트
2. `api/routes/chat_stream.py` 추가 + 통합 테스트 (broker mock)
3. 3개 엔진에 `query_*_stream` 추가 (sync 함수는 유지)
4. 3개 POST 라우트 BG task 변경 + 응답 스키마 변경
5. 3개 템플릿 + `chat_stream_client.js` 추가 + wiring
6. `api/main.py` 의 startup 이벤트에 `broker.start_cleanup()` 호출 추가
7. 기존 통합 테스트 업데이트 (assistant_message → broker mock)
8. 운영기 배포 → systemd unit 재시작 (API 만) → 수동 E2E

롤백: PR revert 1번. broker 는 in-memory 라 DB 영향 없음.

## 13. 참고 / 의존성

- Claude Agent SDK Python `include_partial_messages=True` 옵션. context7 docs 확인됨 (`/anthropics/claude-agent-sdk-python`).
- FastAPI `BackgroundTasks` — 응답 전송 후 실행 보장.
- FastAPI `StreamingResponse` + `text/event-stream` — SSE 표준.
- 클라이언트 `EventSource` — 모든 모던 브라우저 지원.
- nginx `proxy_buffering off` (운영기 reverse proxy 사용 시).

---

## 14. 결정 요약

| 결정 사항 | 선택 | 사유 |
|---|---|---|
| 스코프 | 3채팅 동시 적용 (a) | 추상화 공유, 사용자 선택 |
| Resume semantics | (b) Full replay + (c) DB 폴백 | 멀티 디바이스 핸드오프 핵심 + 워커 재시작 안전망 |
| 멀티 워커 | v1 비지원 (단일 워커 가정) | 라즈베리파이 환경, 추후 Redis 어댑터로 확장 |
| Transport | SSE | 단방향 충분, 기존 sse_log_viewer 패턴 재사용, 인증 쿠키 자동 |
| 동시 user 메시지 | 409 거부 | v1 단순화, 큐잉은 backlog |
| sync 함수 폐기 여부 | 유지 (테스트/폴백) | 안전망 |
| 알림 시스템 연동 | v1 미포함 | 별개 기능, backlog |
