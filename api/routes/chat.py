"""테마 채팅 API — 대화 세션 CRUD + 메시지 전송"""
from typing import Optional
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from shared.config import DatabaseConfig
from shared.db import get_connection
from shared.tier_limits import get_chat_daily_limit, is_unlimited
from psycopg2.extras import RealDictCursor
from api.routes.sessions import _serialize_row
from api.chat_engine import build_theme_context, query_theme_chat_sync
from api.auth.dependencies import require_role, quota_exceeded_detail
from api.auth.models import UserInDB

# 서비스 운영 타임존 — 일일 한도는 KST 기준으로 리셋
_KST = timezone(timedelta(hours=9))

router = APIRouter(prefix="/chat", tags=["채팅"])


def _get_cfg() -> DatabaseConfig:
    return DatabaseConfig()


class CreateSessionRequest(BaseModel):
    theme_id: int


class ChatMessageRequest(BaseModel):
    content: str


# ── 채팅 세션 CRUD ──────────────────────────────


@router.post("/sessions")
def create_chat_session(body: CreateSessionRequest, user: Optional[UserInDB] = Depends(require_role("admin", "moderator"))):
    """새 채팅 세션 생성"""
    cfg = _get_cfg()
    conn = get_connection(cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 테마 존재 확인
            cur.execute("SELECT id, theme_name FROM investment_themes WHERE id = %s",
                        (body.theme_id,))
            theme = cur.fetchone()
            if not theme:
                raise HTTPException(status_code=404, detail="테마를 찾을 수 없습니다")

            user_id = user.id if user else None
            cur.execute(
                """INSERT INTO theme_chat_sessions (theme_id, title, user_id)
                   VALUES (%s, %s, %s) RETURNING id, theme_id, title, created_at, updated_at""",
                (body.theme_id, f"{theme['theme_name']} 채팅", user_id)
            )
            session = cur.fetchone()
        conn.commit()
        return _serialize_row(session)
    finally:
        conn.close()


@router.get("/sessions")
def list_chat_sessions(theme_id: int | None = None, user: Optional[UserInDB] = Depends(require_role("admin", "moderator"))):
    """채팅 세션 목록 조회 — 본인 세션만 (Admin은 전체)"""
    cfg = _get_cfg()
    conn = get_connection(cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            query = """
                SELECT cs.*, t.theme_name,
                       (SELECT COUNT(*) FROM theme_chat_messages m
                        WHERE m.chat_session_id = cs.id) AS message_count
                FROM theme_chat_sessions cs
                JOIN investment_themes t ON cs.theme_id = t.id
            """
            conditions = []
            params = []

            # Admin이 아니면 본인 세션만
            if user and user.role != "admin":
                conditions.append("cs.user_id = %s")
                params.append(user.id)

            if theme_id is not None:
                conditions.append("cs.theme_id = %s")
                params.append(theme_id)

            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY cs.updated_at DESC"
            cur.execute(query, params)
            sessions = cur.fetchall()
        return [_serialize_row(s) for s in sessions]
    finally:
        conn.close()


@router.get("/sessions/{session_id}")
def get_chat_session(session_id: int, user: Optional[UserInDB] = Depends(require_role("admin", "moderator"))):
    """채팅 세션 상세 + 메시지 이력 — 본인 세션만 (Admin은 전체)"""
    cfg = _get_cfg()
    conn = get_connection(cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT cs.*, t.theme_name
                FROM theme_chat_sessions cs
                JOIN investment_themes t ON cs.theme_id = t.id
                WHERE cs.id = %s
            """, (session_id,))
            session = cur.fetchone()
            if not session:
                raise HTTPException(status_code=404, detail="채팅 세션을 찾을 수 없습니다")

            # 소유권 검증 (Admin은 모든 세션 접근 가능)
            if user and user.role != "admin" and session.get("user_id") != user.id:
                raise HTTPException(status_code=403, detail="본인의 채팅 세션만 조회할 수 있습니다")

            cur.execute("""
                SELECT id, role, content, created_at
                FROM theme_chat_messages
                WHERE chat_session_id = %s
                ORDER BY created_at
            """, (session_id,))
            messages = cur.fetchall()

        result = _serialize_row(session)
        result["messages"] = [_serialize_row(m) for m in messages]
        return result
    finally:
        conn.close()


@router.delete("/sessions/{session_id}")
def delete_chat_session(session_id: int, user: Optional[UserInDB] = Depends(require_role("admin", "moderator"))):
    """채팅 세션 삭제 — 본인 세션만 (Admin은 전체)"""
    cfg = _get_cfg()
    conn = get_connection(cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, user_id FROM theme_chat_sessions WHERE id = %s", (session_id,))
            session = cur.fetchone()
            if not session:
                raise HTTPException(status_code=404, detail="채팅 세션을 찾을 수 없습니다")

            # 소유권 검증 (Admin은 모든 세션 삭제 가능)
            if user and user.role != "admin" and session.get("user_id") != user.id:
                raise HTTPException(status_code=403, detail="본인의 채팅 세션만 삭제할 수 있습니다")

            cur.execute("DELETE FROM theme_chat_sessions WHERE id = %s", (session_id,))
        conn.commit()
        return {"message": "삭제 완료"}
    finally:
        conn.close()


# ── 메시지 전송 + Claude 응답 ──────────────────


@router.post("/sessions/{session_id}/messages")
def send_message(session_id: int, body: ChatMessageRequest, user: Optional[UserInDB] = Depends(require_role("admin", "moderator"))):
    """사용자 메시지 전송 → Claude 응답 생성 → 양쪽 DB 저장

    동기 함수 — FastAPI가 threadpool에서 실행.
    Claude SDK는 anyio.run()으로 별도 이벤트 루프에서 호출하여
    uvicorn 이벤트 루프 충돌 방지.
    """
    cfg = _get_cfg()
    conn = get_connection(cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 1) 세션 + 테마 정보 조회
            cur.execute("""
                SELECT cs.id, cs.theme_id, cs.user_id, t.theme_name
                FROM theme_chat_sessions cs
                JOIN investment_themes t ON cs.theme_id = t.id
                WHERE cs.id = %s
            """, (session_id,))
            session = cur.fetchone()
            if not session:
                raise HTTPException(status_code=404, detail="채팅 세션을 찾을 수 없습니다")

            # 소유권 검증 (Admin은 모든 세션에 메시지 전송 가능)
            if user and user.role != "admin" and session.get("user_id") != user.id:
                raise HTTPException(status_code=403, detail="본인의 채팅 세션에만 메시지를 보낼 수 있습니다")

            # 일일 턴 한도 체크 (admin/moderator는 무제한, 일반 사용자는 티어 기반)
            # 참고: 현재 세션 생성이 require_role("admin","moderator")로 막혀있어 일반 user는 이 경로까지 오지 못한다.
            # 향후 session 생성을 tier 기반으로 완화하면 이 한도 체크가 실질 동작한다.
            if user and user.role == "user":
                tier = user.effective_tier()
                daily_limit = get_chat_daily_limit(tier)
                if not is_unlimited(daily_limit):
                    # KST 기준 '오늘 자정' 이후 카운트 — 서버/DB 타임존에 무관하게 일관
                    today_kst_start = datetime.now(_KST).replace(hour=0, minute=0, second=0, microsecond=0)
                    cur.execute(
                        """SELECT COUNT(*) AS c FROM theme_chat_messages m
                           JOIN theme_chat_sessions s ON m.chat_session_id = s.id
                           WHERE s.user_id = %s AND m.role = 'user'
                             AND m.created_at >= %s""",
                        (user.id, today_kst_start),
                    )
                    today_count = cur.fetchone()["c"]
                    if today_count >= (daily_limit or 0):
                        raise HTTPException(
                            status_code=402,
                            detail=quota_exceeded_detail(
                                feature="chat",
                                current_tier=tier,
                                usage=today_count,
                                limit=daily_limit,
                                message=(
                                    "오늘 채팅 턴 수를 모두 사용했습니다."
                                    if daily_limit and daily_limit > 0
                                    else "AI 채팅은 Pro 이상 플랜에서 이용 가능합니다."
                                ),
                            ),
                        )

            theme_id = session["theme_id"]

            # 2) 테마 컨텍스트 구성
            cur.execute("SELECT * FROM investment_themes WHERE id = %s", (theme_id,))
            theme = cur.fetchone()

            cur.execute("SELECT * FROM theme_scenarios WHERE theme_id = %s ORDER BY probability DESC",
                        (theme_id,))
            scenarios = cur.fetchall()

            cur.execute("SELECT * FROM investment_proposals WHERE theme_id = %s ORDER BY target_allocation DESC",
                        (theme_id,))
            proposals = cur.fetchall()

            cur.execute("SELECT * FROM macro_impacts WHERE theme_id = %s", (theme_id,))
            macro_impacts = cur.fetchall()

            theme_context = build_theme_context(
                dict(theme), [dict(s) for s in scenarios],
                [dict(p) for p in proposals], [dict(m) for m in macro_impacts],
            )

            # 3) 기존 대화 이력 로드
            cur.execute("""
                SELECT role, content FROM theme_chat_messages
                WHERE chat_session_id = %s ORDER BY created_at
            """, (session_id,))
            history = [dict(row) for row in cur.fetchall()]

            # 4) 사용자 메시지 저장
            cur.execute(
                """INSERT INTO theme_chat_messages (chat_session_id, role, content)
                   VALUES (%s, 'user', %s) RETURNING id, role, content, created_at""",
                (session_id, body.content)
            )
            user_msg = cur.fetchone()
        conn.commit()

        # 5) Claude SDK 호출 (별도 이벤트 루프에서 동기 실행)
        assistant_text = query_theme_chat_sync(
            theme_context=theme_context,
            conversation_history=history,
            user_message=body.content,
        )

        # 6) 응답 메시지 저장 + 세션 업데이트
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """INSERT INTO theme_chat_messages (chat_session_id, role, content)
                   VALUES (%s, 'assistant', %s) RETURNING id, role, content, created_at""",
                (session_id, assistant_text)
            )
            assistant_msg = cur.fetchone()

            # 첫 메시지면 제목 자동 설정 (질문 앞 50자)
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM theme_chat_messages WHERE chat_session_id = %s",
                (session_id,)
            )
            if cur.fetchone()["cnt"] <= 2:
                title = body.content[:50] + ("..." if len(body.content) > 50 else "")
                cur.execute(
                    "UPDATE theme_chat_sessions SET title = %s, updated_at = NOW() WHERE id = %s",
                    (title, session_id)
                )
            else:
                cur.execute(
                    "UPDATE theme_chat_sessions SET updated_at = NOW() WHERE id = %s",
                    (session_id,)
                )
        conn.commit()

        return {
            "user_message": _serialize_row(user_msg),
            "assistant_message": _serialize_row(assistant_msg),
        }
    finally:
        conn.close()
