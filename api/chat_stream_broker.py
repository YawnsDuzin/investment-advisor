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
            except asyncio.QueueFull:
                pass

    # ─── 구독자 측 ────────────────────────────────

    async def subscribe(
        self, kind: ChatKind, session_id: int
    ) -> AsyncIterator[tuple[str, dict]]:
        """구독자가 채널 큐에 등록되고 이벤트를 yield 받는다.

        IMPORTANT (race condition 회피):
        active 상태 채널의 경우, 큐 등록(`ch.subscribers.add(q)`) 은
        replay yield *이전* 에 동기적으로 완료된다. 따라서 호출자가
        `await asyncio.sleep(0)` 한 번 후 publish 를 호출하면 구독자
        큐에 도달함이 보장된다 (replay 이후 publish 된 토큰 누락 방지).

        완료/실패 채널은 큐 없이 즉시 종료 — replay→done/error 후 generator close.
        """
        ch = self._channels.get((kind, session_id))
        if ch is None:
            return

        # active 채널이면 큐를 replay 이전에 등록 (race 방지)
        active_queue: Optional[asyncio.Queue] = None
        if ch.status == "active":
            active_queue = asyncio.Queue(maxsize=1024)
            ch.subscribers.add(active_queue)

        try:
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

            # status == "active" — active_queue 가 반드시 non-None
            assert active_queue is not None
            while True:
                event, payload = await active_queue.get()
                if event in ("__close__", "done", "error"):
                    if event != "__close__":
                        yield (event, payload)
                    break
                yield (event, payload)
        finally:
            if active_queue is not None:
                ch.subscribers.discard(active_queue)

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
