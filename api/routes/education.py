"""투자 교육 API — 토픽 조회 + AI 튜터 채팅 CRUD + 교육 페이지 라우트"""
import json as _json
from typing import Optional
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from shared.tier_limits import get_edu_chat_daily_limit, is_unlimited
from psycopg2.extras import RealDictCursor
from api.serialization import serialize_row as _serialize_row
from api.education_engine import build_topic_context, query_edu_chat_sync, query_edu_chat_stream
from api.auth.dependencies import get_current_user_required, quota_exceeded_detail
from api.auth.models import UserInDB
from api.templates_provider import templates
from api.deps import get_db_conn, make_page_ctx
from api.chat_stream_broker import broker, ChannelAlreadyActive
from shared.config import DatabaseConfig
from shared.db import get_connection

_KST = timezone(timedelta(hours=9))

router = APIRouter(prefix="/education", tags=["교육"])

pages_router = APIRouter(prefix="/pages/education", tags=["교육 페이지"])

_EDU_CATEGORIES = {
    "basics": "기초 개념",
    "analysis": "분석 기법",
    "risk": "리스크 관리",
    "macro": "매크로 경제",
    "practical": "실전 활용",
    "stories": "투자 이야기",
    "tools": "도구·시스템 가이드",
}


class CreateEduSessionRequest(BaseModel):
    topic_id: int


class EduMessageRequest(BaseModel):
    content: str


# ── 토픽 조회 ──────────────────────────────────────


@router.get("/topics")
def list_topics(conn=Depends(get_db_conn), category: str | None = None):
    """교육 토픽 목록 조회 (카테고리 필터 가능)"""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        query = "SELECT id, category, slug, title, summary, difficulty, sort_order FROM education_topics"
        params = []
        if category:
            query += " WHERE category = %s"
            params.append(category)
        query += " ORDER BY sort_order, id"
        cur.execute(query, params)
        topics = cur.fetchall()
    return [_serialize_row(t) for t in topics]


@router.get("/topics/{slug}")
def get_topic(slug: str, conn=Depends(get_db_conn)):
    """교육 토픽 상세 조회 (slug 기반)"""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM education_topics WHERE slug = %s", (slug,))
        topic = cur.fetchone()
        if not topic:
            raise HTTPException(status_code=404, detail="토픽을 찾을 수 없습니다")
    return _serialize_row(topic)


# ── AI 튜터 채팅 세션 CRUD ─────────────────────────


@router.post("/sessions")
def create_edu_session(body: CreateEduSessionRequest, conn=Depends(get_db_conn), user: Optional[UserInDB] = Depends(get_current_user_required)):
    """교육 AI 튜터 채팅 세션 생성"""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, title FROM education_topics WHERE id = %s", (body.topic_id,))
        topic = cur.fetchone()
        if not topic:
            raise HTTPException(status_code=404, detail="토픽을 찾을 수 없습니다")

        user_id = user.id if user else None
        cur.execute(
            """INSERT INTO education_chat_sessions (topic_id, title, user_id)
               VALUES (%s, %s, %s) RETURNING id, topic_id, title, created_at, updated_at""",
            (body.topic_id, f"{topic['title']} 학습", user_id)
        )
        session = cur.fetchone()
    conn.commit()
    return _serialize_row(session)


@router.get("/sessions")
def list_edu_sessions(conn=Depends(get_db_conn), user: Optional[UserInDB] = Depends(get_current_user_required)):
    """AI 튜터 채팅 세션 목록 — 본인 세션만 (Admin은 전체)"""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        query = """
            SELECT es.*, et.title AS topic_title, et.category,
                   (SELECT COUNT(*) FROM education_chat_messages m
                    WHERE m.chat_session_id = es.id) AS message_count
            FROM education_chat_sessions es
            LEFT JOIN education_topics et ON es.topic_id = et.id
        """
        conditions = []
        params = []

        if user and user.role != "admin":
            conditions.append("es.user_id = %s")
            params.append(user.id)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY es.updated_at DESC"
        cur.execute(query, params)
        sessions = cur.fetchall()
    return [_serialize_row(s) for s in sessions]


@router.delete("/sessions/{session_id}")
def delete_edu_session(session_id: int, conn=Depends(get_db_conn), user: Optional[UserInDB] = Depends(get_current_user_required)):
    """AI 튜터 채팅 세션 삭제"""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, user_id FROM education_chat_sessions WHERE id = %s", (session_id,))
        session = cur.fetchone()
        if not session:
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다")

        if user and user.role != "admin" and session.get("user_id") != user.id:
            raise HTTPException(status_code=403, detail="본인의 학습 세션만 삭제할 수 있습니다")

        cur.execute("DELETE FROM education_chat_sessions WHERE id = %s", (session_id,))
    conn.commit()
    return {"message": "삭제 완료"}


# ── 메시지 전송 + Claude 응답 ──────────────────────


@router.post("/sessions/{session_id}/messages")
async def send_edu_message(
    session_id: int,
    body: EduMessageRequest,
    bg: BackgroundTasks,
    conn=Depends(get_db_conn),
    user: Optional[UserInDB] = Depends(get_current_user_required),
):
    """user 메시지 INSERT 후 BG task 로 응답 생성 분기.
    응답 본문은 SSE (`/api/chat-stream/education/{session_id}`) 로 전달.
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # 1) 세션 + 토픽 정보 조회
        cur.execute("""
            SELECT es.id, es.topic_id, es.user_id
            FROM education_chat_sessions es
            WHERE es.id = %s
        """, (session_id,))
        session = cur.fetchone()
        if not session:
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다")

        if user and user.role != "admin" and session.get("user_id") != user.id:
            raise HTTPException(status_code=403, detail="본인의 학습 세션에만 메시지를 보낼 수 있습니다")

        # 일일 턴 한도 체크 (기존 로직 그대로)
        if user and user.role not in ("admin", "moderator"):
            tier = user.effective_tier()
            daily_limit = get_edu_chat_daily_limit(tier)
            if not is_unlimited(daily_limit):
                today_kst_start = datetime.now(_KST).replace(hour=0, minute=0, second=0, microsecond=0)
                cur.execute(
                    """SELECT COUNT(*) AS c FROM education_chat_messages m
                       JOIN education_chat_sessions s ON m.chat_session_id = s.id
                       WHERE s.user_id = %s AND m.role = 'user'
                         AND m.created_at >= %s""",
                    (user.id, today_kst_start),
                )
                today_count = cur.fetchone()["c"]
                if today_count >= (daily_limit or 0):
                    raise HTTPException(
                        status_code=402,
                        detail=quota_exceeded_detail(
                            feature="education",
                            current_tier=tier,
                            usage=today_count,
                            limit=daily_limit,
                            message="오늘 AI 튜터 질문 횟수를 모두 사용했습니다.",
                        ),
                    )

        # 2) 토픽 컨텍스트 구성
        topic_id = session["topic_id"]
        cur.execute("SELECT * FROM education_topics WHERE id = %s", (topic_id,))
        topic = cur.fetchone()
        topic_context = build_topic_context(dict(topic)) if topic else "일반 투자 교육"

        # 3) 대화 이력
        cur.execute("""
            SELECT role, content FROM education_chat_messages
            WHERE chat_session_id = %s ORDER BY created_at
        """, (session_id,))
        history = [dict(row) for row in cur.fetchall()]

        # 4) user 메시지 INSERT
        cur.execute(
            """INSERT INTO education_chat_messages (chat_session_id, role, content)
               VALUES (%s, 'user', %s) RETURNING id, role, content, created_at""",
            (session_id, body.content),
        )
        user_msg = cur.fetchone()
    conn.commit()

    # 5) broker 채널 open
    try:
        channel = broker.open_channel("education", session_id)
    except ChannelAlreadyActive:
        raise HTTPException(
            status_code=409,
            detail="이전 응답이 아직 생성 중입니다. 잠시만 기다려주세요.",
        )

    # 6) BG task spawn
    async def _runner():
        async def _on_token(t: str):
            await broker.publish_token("education", session_id, t)

        async def _on_error(msg: str, code: str):
            await broker.fail("education", session_id, msg, code)

        try:
            final_text = await query_edu_chat_stream(
                topic_context=topic_context,
                conversation_history=history,
                user_message=body.content,
                on_token=_on_token,
                on_error=_on_error,
            )
            db_conn = get_connection(DatabaseConfig())
            try:
                with db_conn.cursor(cursor_factory=RealDictCursor) as cur2:
                    cur2.execute(
                        """INSERT INTO education_chat_messages
                               (chat_session_id, role, content)
                           VALUES (%s, 'assistant', %s)
                           RETURNING id""",
                        (session_id, final_text),
                    )
                    msg_id = cur2.fetchone()["id"]
                    cur2.execute(
                        "SELECT COUNT(*) AS c FROM education_chat_messages WHERE chat_session_id = %s",
                        (session_id,),
                    )
                    if cur2.fetchone()["c"] <= 2:
                        title = body.content[:50] + ("..." if len(body.content) > 50 else "")
                        cur2.execute(
                            "UPDATE education_chat_sessions SET title=%s, updated_at=NOW() WHERE id=%s",
                            (title, session_id),
                        )
                    else:
                        cur2.execute(
                            "UPDATE education_chat_sessions SET updated_at=NOW() WHERE id=%s",
                            (session_id,),
                        )
                db_conn.commit()
            finally:
                db_conn.close()
            await broker.complete("education", session_id, final_text, msg_id)
        except Exception as e:
            await broker.fail("education", session_id, str(e), "runtime")

    bg.add_task(_runner)

    return {
        "user_message": _serialize_row(user_msg),
        "stream": {
            "kind": "education",
            "session_id": session_id,
            "started_at": channel.started_at.isoformat(),
        },
    }


# ── 교육 페이지 라우트 (/pages/education/*) ────────────


@pages_router.get("")
def education_page(ctx: dict = Depends(make_page_ctx("education")), conn=Depends(get_db_conn), category: str | None = None):
    """투자 교육 토픽 목록 페이지"""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        query = "SELECT id, category, slug, title, summary, difficulty, sort_order FROM education_topics"
        params = []
        if category:
            query += " WHERE category = %s"
            params.append(category)
        query += " ORDER BY sort_order, id"
        cur.execute(query, params)
        topics = cur.fetchall()

    # 카테고리별 그룹핑
    grouped = {}
    for t in topics:
        cat = t["category"]
        if cat not in grouped:
            grouped[cat] = {"label": _EDU_CATEGORIES.get(cat, cat), "topics": []}
        grouped[cat]["topics"].append(_serialize_row(t))

    return templates.TemplateResponse(request=ctx["request"], name="education.html", context={
        **ctx,
        "grouped_topics": grouped,
        "selected_category": category,
        "categories": _EDU_CATEGORIES,
    })


@pages_router.get("/topic/{slug}")
def education_topic_page(slug: str, ctx: dict = Depends(make_page_ctx("education")), conn=Depends(get_db_conn)):
    """교육 토픽 상세 페이지"""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM education_topics WHERE slug = %s", (slug,))
        topic = cur.fetchone()
        if not topic:
            return RedirectResponse(url="/pages/education", status_code=302)

        # 이전/다음 토픽 네비게이션
        cur.execute(
            """SELECT slug, title FROM education_topics
               WHERE category = %s AND sort_order < %s
               ORDER BY sort_order DESC LIMIT 1""",
            (topic["category"], topic["sort_order"]),
        )
        prev_topic = cur.fetchone()

        cur.execute(
            """SELECT slug, title FROM education_topics
               WHERE category = %s AND sort_order > %s
               ORDER BY sort_order ASC LIMIT 1""",
            (topic["category"], topic["sort_order"]),
        )
        next_topic = cur.fetchone()

    # examples가 JSON 문자열이면 파싱
    topic_data = _serialize_row(topic)
    examples = topic_data.get("examples")
    if isinstance(examples, str):
        try:
            topic_data["examples"] = _json.loads(examples)
        except (ValueError, TypeError):
            topic_data["examples"] = []

    return templates.TemplateResponse(request=ctx["request"], name="education_topic.html", context={
        **ctx,
        "topic": topic_data,
        "category_label": _EDU_CATEGORIES.get(topic["category"], topic["category"]),
        "prev_topic": _serialize_row(prev_topic) if prev_topic else None,
        "next_topic": _serialize_row(next_topic) if next_topic else None,
    })


@pages_router.get("/chat")
def education_chat_list_page(ctx: dict = Depends(make_page_ctx("education_chat")), conn=Depends(get_db_conn)):
    """AI 튜터 채팅 목록 페이지"""
    user = ctx["_user"]
    if ctx["auth_enabled"] and user is None:
        return RedirectResponse("/auth/login?next=/pages/education/chat", status_code=302)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # 세션 목록
        q = """
            SELECT es.*, et.title AS topic_title, et.category, et.slug AS topic_slug,
                   (SELECT COUNT(*) FROM education_chat_messages m
                    WHERE m.chat_session_id = es.id) AS message_count
            FROM education_chat_sessions es
            LEFT JOIN education_topics et ON es.topic_id = et.id
        """
        params = []
        if user and user.role != "admin":
            q += " WHERE es.user_id = %s"
            params.append(user.id)
        q += " ORDER BY es.updated_at DESC"
        cur.execute(q, params)
        sessions = cur.fetchall()

        # 토픽 목록 (새 채팅 생성용)
        cur.execute("SELECT id, title, category FROM education_topics ORDER BY sort_order, id")
        topics = cur.fetchall()

    return templates.TemplateResponse(request=ctx["request"], name="education_chat_list.html", context={
        **ctx,
        "chat_sessions": [_serialize_row(s) for s in sessions],
        "topics": [_serialize_row(t) for t in topics],
    })


@pages_router.get("/chat/new/{topic_id}")
def education_chat_new_redirect(topic_id: int, ctx: dict = Depends(make_page_ctx("education_chat")), conn=Depends(get_db_conn)):
    """AI 튜터 새 채팅 세션 생성 → 채팅방으로 리다이렉트"""
    user = ctx["_user"]
    if ctx["auth_enabled"] and user is None:
        return RedirectResponse(f"/auth/login?next=/pages/education/chat/new/{topic_id}", status_code=302)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, title FROM education_topics WHERE id = %s", (topic_id,))
        topic = cur.fetchone()
        if not topic:
            return RedirectResponse(url="/pages/education/chat", status_code=302)

        user_id = user.id if user else None
        cur.execute(
            """INSERT INTO education_chat_sessions (topic_id, title, user_id)
               VALUES (%s, %s, %s) RETURNING id""",
            (topic_id, f"{topic['title']} 학습", user_id)
        )
        new_id = cur.fetchone()["id"]
    conn.commit()

    return RedirectResponse(url=f"/pages/education/chat/{new_id}", status_code=302)


@pages_router.get("/chat/{session_id}")
def education_chat_room_page(session_id: int, ctx: dict = Depends(make_page_ctx("education_chat")), conn=Depends(get_db_conn)):
    """AI 튜터 채팅방 페이지"""
    user = ctx["_user"]
    if ctx["auth_enabled"] and user is None:
        return RedirectResponse(f"/auth/login?next=/pages/education/chat/{session_id}", status_code=302)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT es.*, et.title AS topic_title, et.category, et.slug AS topic_slug,
                   et.difficulty, et.summary AS topic_summary
            FROM education_chat_sessions es
            LEFT JOIN education_topics et ON es.topic_id = et.id
            WHERE es.id = %s
        """, (session_id,))
        session = cur.fetchone()
        if not session:
            return RedirectResponse(url="/pages/education/chat", status_code=302)

        # 소유권 검증
        if ctx["auth_enabled"] and user and user.role != "admin" and session.get("user_id") != user.id:
            return RedirectResponse(url="/pages/education/chat", status_code=302)

        cur.execute("""
            SELECT id, role, content, created_at
            FROM education_chat_messages
            WHERE chat_session_id = %s
            ORDER BY created_at
        """, (session_id,))
        messages = cur.fetchall()

    return templates.TemplateResponse(request=ctx["request"], name="education_chat_room.html", context={
        **ctx,
        "session": _serialize_row(session),
        "messages": [_serialize_row(m) for m in messages],
        "category_label": _EDU_CATEGORIES.get(session.get("category", ""), ""),
    })
