"""고객 문의/개선요청 API — 게시판 CRUD + 답변 + 페이지 라우트"""
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from shared.config import AuthConfig
from psycopg2.extras import RealDictCursor
from api.serialization import serialize_row as _serialize_row
from api.page_context import base_ctx as _base_ctx
from api.auth.dependencies import get_current_user, get_current_user_required, require_role, _get_auth_cfg
from api.auth.models import UserInDB
from api.templates_provider import templates
from api.deps import get_db_conn

router = APIRouter(prefix="/inquiry", tags=["문의"])
pages_router = APIRouter(prefix="/pages/inquiry", tags=["문의 페이지"])

VALID_CATEGORIES = ("general", "bug", "feature")
VALID_STATUSES = ("open", "answered", "closed")


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
    conn=Depends(get_db_conn),
    user: Optional[UserInDB] = Depends(get_current_user_required),
):
    """문의 작성 — 로그인 필수"""
    if body.category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"유효하지 않은 카테고리: {body.category}")

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


@router.get("")
def list_inquiries(
    conn=Depends(get_db_conn),
    category: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: Optional[UserInDB] = Depends(get_current_user_required),
):
    """문의 목록 — 공개 문의는 모두, 비공개는 작성자/Admin/Moderator만"""
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


@router.get("/{inquiry_id}")
def get_inquiry(
    inquiry_id: int,
    conn=Depends(get_db_conn),
    user: Optional[UserInDB] = Depends(get_current_user_required),
):
    """문의 상세 + 답변 목록"""
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


@router.delete("/{inquiry_id}")
def delete_inquiry(
    inquiry_id: int,
    conn=Depends(get_db_conn),
    _admin: Optional[UserInDB] = Depends(require_role("admin")),
):
    """문의 삭제 — Admin만"""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM inquiries WHERE id = %s RETURNING id", (inquiry_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="문의를 찾을 수 없습니다")
    conn.commit()
    return {"ok": True}


# ── 답변/코멘트 ──────────────────────────────


@router.post("/{inquiry_id}/replies")
def create_reply(
    inquiry_id: int,
    body: CreateReplyRequest,
    conn=Depends(get_db_conn),
    user: Optional[UserInDB] = Depends(get_current_user_required),
):
    """답변 작성 — Admin/Moderator는 답변, 작성자는 추가 코멘트"""
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


# ── 상태 변경 ──────────────────────────────


@router.patch("/{inquiry_id}/status")
def update_inquiry_status(
    inquiry_id: int,
    body: UpdateStatusRequest,
    conn=Depends(get_db_conn),
    _staff: Optional[UserInDB] = Depends(require_role("admin", "moderator")),
):
    """문의 상태 변경 — Admin/Moderator"""
    if body.status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"유효하지 않은 상태: {body.status}")

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE inquiries SET status = %s, updated_at = NOW() WHERE id = %s RETURNING id",
            (body.status, inquiry_id),
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="문의를 찾을 수 없습니다")
    conn.commit()
    return {"ok": True, "status": body.status}


# ── 문의 페이지 라우트 ──────────────────────────────


@pages_router.get("")
def inquiry_list_page(
    request: Request,
    conn=Depends(get_db_conn),
    category: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    user: Optional[UserInDB] = Depends(get_current_user),
    auth_cfg: AuthConfig = Depends(_get_auth_cfg),
):
    """문의 게시판 목록 페이지"""
    # 유효하지 않은 필터값 무시
    if category and category not in ("general", "bug", "feature"):
        category = None
    if status and status not in ("open", "answered", "closed"):
        status = None

    ctx = _base_ctx(request, "inquiry", user, auth_cfg)

    per_page = 20
    offset = (page - 1) * per_page
    can_view_private = user is not None and user.role in ("admin", "moderator")

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        conditions = []
        params = []

        if not can_view_private:
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
            (*params, per_page, offset),
        )
        inquiries = [_serialize_row(dict(r)) for r in cur.fetchall()]

        cur.execute(f"SELECT COUNT(*) FROM inquiries i {where}", tuple(params))
        total = cur.fetchone()["count"]

    total_pages = max(1, (total + per_page - 1) // per_page)

    return templates.TemplateResponse(request=request, name="inquiry_list.html", context={
        **ctx,
        "inquiries": inquiries,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "selected_category": category,
        "selected_status": status,
    })


@pages_router.get("/new")
def inquiry_new_page(
    request: Request,
    user: Optional[UserInDB] = Depends(get_current_user),
    auth_cfg: AuthConfig = Depends(_get_auth_cfg),
):
    """문의 작성 페이지 — 로그인 필수"""
    if auth_cfg.enabled and not user:
        return RedirectResponse(url="/auth/login?next=/pages/inquiry/new", status_code=302)
    ctx = _base_ctx(request, "inquiry", user, auth_cfg)
    return templates.TemplateResponse(request=request, name="inquiry_new.html", context=ctx)


@pages_router.get("/{inquiry_id}")
def inquiry_detail_page(
    request: Request,
    inquiry_id: int,
    conn=Depends(get_db_conn),
    user: Optional[UserInDB] = Depends(get_current_user),
    auth_cfg: AuthConfig = Depends(_get_auth_cfg),
):
    """문의 상세 페이지"""
    ctx = _base_ctx(request, "inquiry", user, auth_cfg)

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
            return RedirectResponse(url="/pages/inquiry", status_code=302)

        # 비공개 접근 제어
        if inquiry["is_private"]:
            is_author = user and inquiry["user_id"] == user.id
            can_view = user and user.role in ("admin", "moderator")
            if not is_author and not can_view:
                return RedirectResponse(url="/pages/inquiry", status_code=302)

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

    inquiry = _serialize_row(dict(inquiry))
    is_author = user and inquiry.get("user_id") == user.id
    is_staff = user and user.role in ("admin", "moderator")

    return templates.TemplateResponse(request=request, name="inquiry_detail.html", context={
        **ctx,
        "inquiry": inquiry,
        "replies": replies,
        "is_author": is_author,
        "is_staff": is_staff,
    })
