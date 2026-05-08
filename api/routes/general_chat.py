"""자유 질문 채팅 API + 페이지 — 테마/토픽 비종속 대화.

차별점:
- Theme Chat = 테마 컨텍스트 필수 (Pro+)
- AI Tutor = 교육 토픽 컨텍스트 필수 (Free OK, 일 5턴)
- General Chat = 컨텍스트 자유, 워치리스트·최근 추천 자동 주입 (Free OK 일 5턴)
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from psycopg2.extras import RealDictCursor

from api.auth.dependencies import get_current_user_required, quota_exceeded_detail
from api.auth.models import UserInDB
from api.chat_stream_broker import broker, ChannelAlreadyActive
from api.deps import get_db_conn, make_page_ctx
from api.general_chat_engine import build_user_context, query_general_chat_sync, query_general_chat_stream
from api.serialization import serialize_row as _serialize_row
from api.templates_provider import templates
from shared.config import DatabaseConfig
from shared.db import get_connection
from shared.tier_limits import get_general_chat_daily_limit, is_unlimited

# 일일 한도 리셋 기준 — KST 자정
_KST = timezone(timedelta(hours=9))

router = APIRouter(prefix="/general-chat", tags=["자유 채팅"])
pages_router = APIRouter(prefix="/pages/general-chat", tags=["자유 채팅 페이지"])


class CreateSessionRequest(BaseModel):
    title: Optional[str] = None


class ChatMessageRequest(BaseModel):
    content: str


# ── 공통 가드 ────────────────────────────────────


def _check_quota_or_raise(cur, user: UserInDB) -> None:
    """일일 턴 수 체크. admin/moderator 면 패스. 초과 시 402."""
    if user.role in ("admin", "moderator"):
        return
    tier = user.effective_tier()
    daily_limit = get_general_chat_daily_limit(tier)
    if is_unlimited(daily_limit):
        return
    today_kst_start = datetime.now(_KST).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    cur.execute(
        """
        SELECT COUNT(*) AS c FROM general_chat_messages m
        JOIN general_chat_sessions s ON m.chat_session_id = s.id
        WHERE s.user_id = %s AND m.role = 'user'
          AND m.created_at >= %s
        """,
        (user.id, today_kst_start),
    )
    today_count = cur.fetchone()["c"]
    if today_count >= (daily_limit or 0):
        raise HTTPException(
            status_code=402,
            detail=quota_exceeded_detail(
                feature="general_chat",
                current_tier=tier,
                usage=today_count,
                limit=daily_limit,
                message="오늘 자유 채팅 턴 수를 모두 사용했습니다. 플랜을 업그레이드하면 더 많이 이용할 수 있습니다.",
            ),
        )


# ── 세션 CRUD ─────────────────────────────────


@router.post("/sessions")
def create_session(
    body: CreateSessionRequest,
    conn=Depends(get_db_conn),
    user: UserInDB = Depends(get_current_user_required),
):
    """새 세션 생성 — 빈 세션, title 옵션."""
    title = (body.title or "").strip() or "새 대화"
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            INSERT INTO general_chat_sessions (user_id, title)
            VALUES (%s, %s)
            RETURNING id, user_id, title, created_at, updated_at
            """,
            (user.id, title),
        )
        session = cur.fetchone()
    conn.commit()
    return _serialize_row(session)


@router.get("/sessions")
def list_sessions(
    conn=Depends(get_db_conn),
    user: UserInDB = Depends(get_current_user_required),
):
    """본인 세션 목록 (Admin 은 전체)."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        sql = """
            SELECT cs.*,
                   (SELECT COUNT(*) FROM general_chat_messages m
                    WHERE m.chat_session_id = cs.id) AS message_count
            FROM general_chat_sessions cs
        """
        params: list = []
        if user.role != "admin":
            sql += " WHERE cs.user_id = %s"
            params.append(user.id)
        sql += " ORDER BY cs.updated_at DESC"
        cur.execute(sql, params)
        sessions = cur.fetchall()
    return [_serialize_row(s) for s in sessions]


@router.get("/sessions/{session_id}")
def get_session(
    session_id: int,
    conn=Depends(get_db_conn),
    user: UserInDB = Depends(get_current_user_required),
):
    """세션 상세 + 메시지 이력."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM general_chat_sessions WHERE id = %s", (session_id,)
        )
        session = cur.fetchone()
        if not session:
            raise HTTPException(status_code=404, detail="채팅 세션을 찾을 수 없습니다")
        if user.role != "admin" and session.get("user_id") != user.id:
            raise HTTPException(status_code=403, detail="본인의 채팅 세션만 조회할 수 있습니다")

        cur.execute(
            """
            SELECT id, role, content, created_at
            FROM general_chat_messages
            WHERE chat_session_id = %s
            ORDER BY created_at
            """,
            (session_id,),
        )
        messages = cur.fetchall()

    out = _serialize_row(session)
    out["messages"] = [_serialize_row(m) for m in messages]
    return out


@router.delete("/sessions/{session_id}")
def delete_session(
    session_id: int,
    conn=Depends(get_db_conn),
    user: UserInDB = Depends(get_current_user_required),
):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT id, user_id FROM general_chat_sessions WHERE id = %s",
            (session_id,),
        )
        session = cur.fetchone()
        if not session:
            raise HTTPException(status_code=404, detail="채팅 세션을 찾을 수 없습니다")
        if user.role != "admin" and session.get("user_id") != user.id:
            raise HTTPException(status_code=403, detail="본인의 채팅 세션만 삭제할 수 있습니다")
        cur.execute("DELETE FROM general_chat_sessions WHERE id = %s", (session_id,))
    conn.commit()
    return {"message": "삭제 완료"}


# ── 메시지 전송 + Claude 응답 ──────────────────


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
        # user_message 를 함께 전달하면 stock_universe 화이트리스트 기반으로
        # 메시지 언급 종목의 D-1 종가 스냅샷을 컨텍스트에 추가 주입한다 (DB only).
        owner_id = session.get("user_id") or user.id
        user_context = build_user_context(conn, owner_id, user_message=body.content)
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


# ── 페이지 라우트 ────────────────────────────────


def _require_login_or_redirect(ctx: dict, next_path: str):
    """비로그인 사용자는 로그인 페이지로 리다이렉트. 로그인된 경우 None."""
    user = ctx["_user"]
    if ctx["auth_enabled"] and user is None:
        return RedirectResponse(f"/auth/login?next={next_path}", status_code=302)
    return None


@pages_router.get("")
def general_chat_list_page(
    ctx: dict = Depends(make_page_ctx("general_chat")),
    conn=Depends(get_db_conn),
):
    """세션 목록 페이지."""
    redirect = _require_login_or_redirect(ctx, "/pages/general-chat")
    if redirect is not None:
        return redirect

    user = ctx["_user"]
    sessions: list = []
    if user is not None:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            sql = """
                SELECT cs.*,
                       (SELECT COUNT(*) FROM general_chat_messages m
                        WHERE m.chat_session_id = cs.id) AS message_count
                FROM general_chat_sessions cs
            """
            params: list = []
            if user.role != "admin":
                sql += " WHERE cs.user_id = %s"
                params.append(user.id)
            sql += " ORDER BY cs.updated_at DESC"
            cur.execute(sql, params)
            sessions = [_serialize_row(s) for s in cur.fetchall()]

    return templates.TemplateResponse(
        request=ctx["request"],
        name="general_chat_list.html",
        context={**ctx, "chat_sessions": sessions},
    )


@pages_router.get("/new")
def general_chat_new_redirect(
    ctx: dict = Depends(make_page_ctx("general_chat")),
    conn=Depends(get_db_conn),
):
    """새 세션 생성 → 채팅방 리다이렉트."""
    redirect = _require_login_or_redirect(ctx, "/pages/general-chat/new")
    if redirect is not None:
        return redirect

    user = ctx["_user"]
    user_id = user.id if user is not None else None
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            INSERT INTO general_chat_sessions (user_id, title)
            VALUES (%s, %s)
            RETURNING id
            """,
            (user_id, "새 대화"),
        )
        new_id = cur.fetchone()["id"]
    conn.commit()
    return RedirectResponse(url=f"/pages/general-chat/{new_id}", status_code=302)


@pages_router.get("/{session_id}")
def general_chat_room_page(
    session_id: int,
    ctx: dict = Depends(make_page_ctx("general_chat")),
    conn=Depends(get_db_conn),
):
    """채팅방 페이지."""
    redirect = _require_login_or_redirect(ctx, f"/pages/general-chat/{session_id}")
    if redirect is not None:
        return redirect

    user = ctx["_user"]
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM general_chat_sessions WHERE id = %s", (session_id,)
        )
        session = cur.fetchone()
        if not session:
            return RedirectResponse(url="/pages/general-chat", status_code=302)
        if (
            ctx["auth_enabled"]
            and user is not None
            and user.role != "admin"
            and session.get("user_id") != user.id
        ):
            return RedirectResponse(url="/pages/general-chat", status_code=302)

        cur.execute(
            """
            SELECT id, role, content, created_at
            FROM general_chat_messages
            WHERE chat_session_id = %s
            ORDER BY created_at
            """,
            (session_id,),
        )
        messages = cur.fetchall()

    return templates.TemplateResponse(
        request=ctx["request"],
        name="general_chat_room.html",
        context={
            **ctx,
            "session": _serialize_row(session),
            "messages": [_serialize_row(m) for m in messages],
        },
    )
