"""고객 문의/개선요청 API — 게시판 CRUD + 답변"""
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from shared.config import DatabaseConfig
from shared.db import get_connection
from psycopg2.extras import RealDictCursor
from api.routes.sessions import _serialize_row
from api.auth.dependencies import get_current_user_required, require_role
from api.auth.models import UserInDB

router = APIRouter(prefix="/inquiry", tags=["문의"])

VALID_CATEGORIES = ("general", "bug", "feature")
VALID_STATUSES = ("open", "answered", "closed")


def _get_cfg() -> DatabaseConfig:
    return DatabaseConfig()


def _can_view_private(user: Optional[UserInDB]) -> bool:
    """비공개 문의 열람 권한: Admin, Moderator"""
    return user is not None and user.role in ("admin", "moderator")


# ── Pydantic 요청 모델 ──────────────────────────────

class CreateInquiryRequest(BaseModel):
    category: str = Field(default="general")
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1)
    is_private: bool = False


class CreateReplyRequest(BaseModel):
    content: str = Field(min_length=1)


class UpdateStatusRequest(BaseModel):
    status: str


# ── 문의 CRUD ──────────────────────────────


@router.post("")
def create_inquiry(
    body: CreateInquiryRequest,
    user: Optional[UserInDB] = Depends(get_current_user_required),
):
    """문의 작성 — 로그인 필수"""
    if body.category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"유효하지 않은 카테고리: {body.category}")

    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO inquiries (user_id, user_email, category, title, content, is_private)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    user.id if user else None,
                    user.email if user else None,
                    body.category,
                    body.title,
                    body.content,
                    body.is_private,
                ),
            )
            row = cur.fetchone()
        conn.commit()
        return _serialize_row(dict(row))
    finally:
        conn.close()


@router.get("")
def list_inquiries(
    category: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: Optional[UserInDB] = Depends(get_current_user_required),
):
    """문의 목록 — 공개 문의는 모두, 비공개는 작성자/Admin/Moderator만"""
    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            conditions = []
            params = []

            # 비공개 필터: Admin/Mod는 전부 보고, 일반 유저는 자기 것 + 공개만
            if not _can_view_private(user):
                if user:
                    conditions.append("(i.is_private = FALSE OR i.user_id = %s)")
                    params.append(user.id)
                else:
                    conditions.append("i.is_private = FALSE")

            if category:
                conditions.append("i.category = %s")
                params.append(category)
            if status:
                conditions.append("i.status = %s")
                params.append(status)

            where = "WHERE " + " AND ".join(conditions) if conditions else ""

            cur.execute(
                f"""
                SELECT i.*, u.nickname AS user_nickname,
                       (SELECT COUNT(*) FROM inquiry_replies r WHERE r.inquiry_id = i.id) AS reply_count
                FROM inquiries i
                LEFT JOIN users u ON u.id = i.user_id
                {where}
                ORDER BY i.created_at DESC
                LIMIT %s OFFSET %s
                """,
                (*params, limit, offset),
            )
            rows = [_serialize_row(dict(r)) for r in cur.fetchall()]

            # 전체 건수
            cur.execute(
                f"SELECT COUNT(*) FROM inquiries i {where}",
                tuple(params),
            )
            total = cur.fetchone()["count"]

        return {"items": rows, "total": total, "limit": limit, "offset": offset}
    finally:
        conn.close()


@router.get("/{inquiry_id}")
def get_inquiry(
    inquiry_id: int,
    user: Optional[UserInDB] = Depends(get_current_user_required),
):
    """문의 상세 + 답변 목록"""
    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT i.*, u.nickname AS user_nickname
                FROM inquiries i
                LEFT JOIN users u ON u.id = i.user_id
                WHERE i.id = %s
                """,
                (inquiry_id,),
            )
            inquiry = cur.fetchone()
            if not inquiry:
                raise HTTPException(status_code=404, detail="문의를 찾을 수 없습니다")

            # 비공개 접근 제어
            if inquiry["is_private"]:
                is_author = user and inquiry["user_id"] == user.id
                if not is_author and not _can_view_private(user):
                    raise HTTPException(status_code=403, detail="비공개 문의입니다")

            # 답변 목록
            cur.execute(
                """
                SELECT r.*, u.nickname AS user_nickname
                FROM inquiry_replies r
                LEFT JOIN users u ON u.id = r.user_id
                WHERE r.inquiry_id = %s
                ORDER BY r.created_at ASC
                """,
                (inquiry_id,),
            )
            replies = [_serialize_row(dict(r)) for r in cur.fetchall()]

        result = _serialize_row(dict(inquiry))
        result["replies"] = replies
        return result
    finally:
        conn.close()


@router.delete("/{inquiry_id}")
def delete_inquiry(
    inquiry_id: int,
    _admin: Optional[UserInDB] = Depends(require_role("admin")),
):
    """문의 삭제 — Admin만"""
    conn = get_connection(_get_cfg())
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM inquiries WHERE id = %s RETURNING id", (inquiry_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="문의를 찾을 수 없습니다")
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ── 답변/코멘트 ──────────────────────────────


@router.post("/{inquiry_id}/replies")
def create_reply(
    inquiry_id: int,
    body: CreateReplyRequest,
    user: Optional[UserInDB] = Depends(get_current_user_required),
):
    """답변 작성 — Admin/Moderator는 답변, 작성자는 추가 코멘트"""
    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 문의 존재 확인
            cur.execute("SELECT id, user_id, is_private FROM inquiries WHERE id = %s", (inquiry_id,))
            inquiry = cur.fetchone()
            if not inquiry:
                raise HTTPException(status_code=404, detail="문의를 찾을 수 없습니다")

            # 권한 체크: AUTH 비활성 시 누구나, 활성 시 Admin/Moderator 또는 작성자만
            is_staff = user and user.role in ("admin", "moderator")
            is_author = user and inquiry["user_id"] is not None and inquiry["user_id"] == user.id
            if user is not None and not is_staff and not is_author:
                raise HTTPException(status_code=403, detail="답변 권한이 없습니다")

            reply_role = user.role if user else "user"

            cur.execute(
                """
                INSERT INTO inquiry_replies (inquiry_id, user_id, user_email, role, content)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING *
                """,
                (inquiry_id, user.id if user else None, user.email if user else None, reply_role, body.content),
            )
            reply = cur.fetchone()

            # 스태프 답변 시 상태를 'answered'로 변경
            if is_staff:
                cur.execute(
                    "UPDATE inquiries SET status = 'answered', updated_at = NOW() WHERE id = %s",
                    (inquiry_id,),
                )
            else:
                cur.execute(
                    "UPDATE inquiries SET updated_at = NOW() WHERE id = %s",
                    (inquiry_id,),
                )

        conn.commit()
        return _serialize_row(dict(reply))
    finally:
        conn.close()


# ── 상태 변경 ──────────────────────────────


@router.patch("/{inquiry_id}/status")
def update_inquiry_status(
    inquiry_id: int,
    body: UpdateStatusRequest,
    _staff: Optional[UserInDB] = Depends(require_role("admin", "moderator")),
):
    """문의 상태 변경 — Admin/Moderator"""
    if body.status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"유효하지 않은 상태: {body.status}")

    conn = get_connection(_get_cfg())
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE inquiries SET status = %s, updated_at = NOW() WHERE id = %s RETURNING id",
                (body.status, inquiry_id),
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="문의를 찾을 수 없습니다")
        conn.commit()
        return {"ok": True, "status": body.status}
    finally:
        conn.close()
