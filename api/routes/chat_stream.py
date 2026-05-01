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
