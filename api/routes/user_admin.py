"""사용자 관리 API — Admin/Moderator 전용 CRUD + 활동 로그"""
import secrets
from typing import Optional
from fastapi import APIRouter, Request, HTTPException, Depends, Query
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from shared.config import DatabaseConfig, AuthConfig
from shared.db import get_connection
from psycopg2.extras import RealDictCursor
from api.routes.sessions import _serialize_row
from api.auth.dependencies import require_role, get_current_user, _get_auth_cfg
from api.auth.models import UserInDB
from api.auth.password import hash_password

router = APIRouter(prefix="/admin/users", tags=["사용자 관리"])

templates = Jinja2Templates(directory="api/templates")


def _get_db_cfg() -> DatabaseConfig:
    return DatabaseConfig()


# ── 사용자 목록 (페이지 + API) ────────────────


@router.get("")
def user_list_page(
    request: Request,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    user: Optional[UserInDB] = Depends(get_current_user),
    auth_cfg: AuthConfig = Depends(_get_auth_cfg),
):
    """사용자 관리 페이지"""
    if auth_cfg.enabled:
        if user is None:
            return RedirectResponse("/auth/login?next=/admin/users", status_code=302)
        if user.role not in ("admin", "moderator"):
            raise HTTPException(status_code=403, detail="접근 권한이 없습니다")

    db_cfg = _get_db_cfg()
    offset = (page - 1) * limit
    conn = get_connection(db_cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    u.id, u.email, u.nickname, u.role, u.is_active,
                    u.created_at, u.last_login_at,
                    COUNT(DISTINCT tcs.id) AS chat_session_count,
                    COUNT(DISTINCT tcm.id) AS chat_message_count
                FROM users u
                LEFT JOIN theme_chat_sessions tcs ON tcs.user_id = u.id
                LEFT JOIN theme_chat_messages tcm ON tcm.chat_session_id = tcs.id
                GROUP BY u.id
                ORDER BY u.created_at DESC
                LIMIT %s OFFSET %s
            """, (limit, offset))
            users = [_serialize_row(r) for r in cur.fetchall()]

            cur.execute("SELECT COUNT(*) AS cnt FROM users")
            total = cur.fetchone()["cnt"]
    finally:
        conn.close()

    return templates.TemplateResponse(request=request, name="user_admin.html", context={
        "request": request,
        "active_page": "user_admin",
        "current_user": user,
        "auth_enabled": auth_cfg.enabled,
        "users": users,
        "page": page,
        "limit": limit,
        "total": total,
        "total_pages": (total + limit - 1) // limit,
    })


# ── 역할 변경 ────────────────────────────────


@router.patch("/{user_id}/role")
def change_role(
    user_id: int,
    role: str = Query(..., description="새 역할 (admin, moderator, user)"),
    actor: Optional[UserInDB] = Depends(require_role("admin", "moderator")),
    db_cfg: DatabaseConfig = Depends(_get_db_cfg),
):
    """사용자 역할 변경"""
    if role not in ("admin", "moderator", "user"):
        raise HTTPException(status_code=400, detail="유효하지 않은 역할입니다")

    conn = get_connection(db_cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, role FROM users WHERE id = %s", (user_id,))
            target = cur.fetchone()
            if not target:
                raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")

            # Moderator 권한 제약
            if actor and actor.role == "moderator":
                if target["role"] == "admin":
                    raise HTTPException(status_code=403, detail="Admin 계정의 역할을 변경할 수 없습니다")
                if role == "admin":
                    raise HTTPException(status_code=403, detail="Admin 역할을 부여할 수 없습니다")

            cur.execute("UPDATE users SET role = %s WHERE id = %s", (role, user_id))
        conn.commit()
        return {"message": f"역할이 {role}(으)로 변경되었습니다"}
    finally:
        conn.close()


# ── 계정 활성화/비활성화 ──────────────────────


@router.patch("/{user_id}/status")
def change_status(
    user_id: int,
    is_active: bool = Query(..., description="활성화 여부"),
    actor: Optional[UserInDB] = Depends(require_role("admin", "moderator")),
    db_cfg: DatabaseConfig = Depends(_get_db_cfg),
):
    """사용자 활성화/비활성화"""
    conn = get_connection(db_cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, role FROM users WHERE id = %s", (user_id,))
            target = cur.fetchone()
            if not target:
                raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")

            # Moderator 제약: User만 비활성화 가능
            if actor and actor.role == "moderator":
                if target["role"] != "user":
                    raise HTTPException(status_code=403, detail="User 역할의 계정만 비활성화할 수 있습니다")

            # Admin 자기 자신 비활성화 방지
            if actor and actor.id == user_id and not is_active:
                raise HTTPException(status_code=400, detail="자기 자신을 비활성화할 수 없습니다")

            cur.execute("UPDATE users SET is_active = %s WHERE id = %s", (is_active, user_id))
        conn.commit()
        status_text = "활성화" if is_active else "비활성화"
        return {"message": f"계정이 {status_text}되었습니다"}
    finally:
        conn.close()


# ── 임시 비밀번호 발급 ────────────────────────


@router.post("/{user_id}/reset-password")
def reset_password(
    user_id: int,
    _admin: Optional[UserInDB] = Depends(require_role("admin")),
    db_cfg: DatabaseConfig = Depends(_get_db_cfg),
):
    """임시 비밀번호 발급 (Admin 전용)"""
    temp_pw = secrets.token_urlsafe(12)
    pw_hash = hash_password(temp_pw)

    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM users WHERE id = %s", (user_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")

            cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (pw_hash, user_id))
        conn.commit()
        return {"message": "임시 비밀번호가 발급되었습니다", "temp_password": temp_pw}
    finally:
        conn.close()


# ── 계정 삭제 ─────────────────────────────────


@router.delete("/{user_id}")
def delete_user(
    user_id: int,
    actor: Optional[UserInDB] = Depends(require_role("admin")),
    db_cfg: DatabaseConfig = Depends(_get_db_cfg),
):
    """계정 삭제 (Admin 전용, 본인 삭제 금지)"""
    if actor and actor.id == user_id:
        raise HTTPException(status_code=400, detail="자기 자신을 삭제할 수 없습니다")

    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id = %s RETURNING id", (user_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
        conn.commit()
        return {"message": "계정이 삭제되었습니다"}
    finally:
        conn.close()
