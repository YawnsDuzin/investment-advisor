"""사용자 관리 API — Admin/Moderator 전용 CRUD + 감사 로그

감사 로그: 모든 권한/상태/티어/비밀번호/삭제 작업을 admin_audit_logs에 기록.
actor·target 이메일을 denormalize하여 계정 삭제 이후에도 이력 조회 가능.
"""
import json
import secrets
from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Request, HTTPException, Depends, Query
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from psycopg2.extras import Json, RealDictCursor

from shared.config import DatabaseConfig, AuthConfig
from shared.db import get_connection
from shared.tier_limits import VALID_TIERS, TIER_INFO, normalize_tier
from api.serialization import serialize_row as _serialize_row
from api.auth.dependencies import require_role, get_current_user, _get_auth_cfg
from api.auth.models import UserInDB
from api.auth.password import hash_password

router = APIRouter(prefix="/admin/users", tags=["사용자 관리"])

templates = Jinja2Templates(directory="api/templates")


def _get_db_cfg() -> DatabaseConfig:
    return DatabaseConfig()


# ── 감사 로그 헬퍼 ────────────────────────────────


def _log_admin_action(
    cur,
    *,
    actor: Optional[UserInDB],
    target_user_id: Optional[int],
    target_email: Optional[str],
    action: str,
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
    reason: Optional[str] = None,
) -> None:
    """admin_audit_logs에 1건 기록. 호출자가 동일 트랜잭션으로 commit 한다."""
    cur.execute(
        """
        INSERT INTO admin_audit_logs
            (actor_id, actor_email, target_user_id, target_email,
             action, before_state, after_state, reason)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            actor.id if actor else None,
            actor.email if actor else None,
            target_user_id,
            target_email,
            action,
            Json(before) if before is not None else None,
            Json(after) if after is not None else None,
            reason,
        ),
    )


def _parse_expires_at(raw: Optional[str]) -> Optional[datetime]:
    """프론트에서 올린 만료일 문자열(ISO8601) 파싱. 빈 값/None은 None 반환."""
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    # "2026-12-31" 혹은 "2026-12-31T23:59" 형태 허용
    try:
        if "T" in s:
            return datetime.fromisoformat(s)
        return datetime.fromisoformat(s + "T00:00:00")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"만료일 형식이 올바르지 않습니다: {exc}")


# ── 사용자 목록 (페이지 + API) ────────────────


@router.get("")
def user_list_page(
    request: Request,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    q: Optional[str] = Query(None, description="이메일/닉네임 검색"),
    role: Optional[str] = Query(None, description="역할 필터"),
    status: Optional[str] = Query(None, description="상태 필터 (active/inactive)"),
    tier: Optional[str] = Query(None, description="티어 필터"),
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

    # 동적 WHERE 절 구성
    where_clauses: list[str] = []
    params: list = []
    if q and q.strip():
        where_clauses.append("(u.email ILIKE %s OR u.nickname ILIKE %s)")
        params.extend([f"%{q.strip()}%", f"%{q.strip()}%"])
    if role and role in ("admin", "moderator", "user"):
        where_clauses.append("u.role = %s")
        params.append(role)
    if status == "active":
        where_clauses.append("u.is_active = true")
    elif status == "inactive":
        where_clauses.append("u.is_active = false")
    if tier and tier in VALID_TIERS:
        where_clauses.append("COALESCE(u.tier, 'free') = %s")
        params.append(tier)
    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    conn = get_connection(db_cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"""
                SELECT
                    u.id, u.email, u.nickname, u.role, u.is_active,
                    u.tier, u.tier_expires_at,
                    u.created_at, u.last_login_at,
                    COUNT(DISTINCT tcs.id) AS chat_session_count,
                    COUNT(DISTINCT tcm.id) AS chat_message_count
                FROM users u
                LEFT JOIN theme_chat_sessions tcs ON tcs.user_id = u.id
                LEFT JOIN theme_chat_messages tcm ON tcm.chat_session_id = tcs.id
                {where_sql}
                GROUP BY u.id
                ORDER BY u.created_at DESC
                LIMIT %s OFFSET %s
            """, params + [limit, offset])
            users = [_serialize_row(r) for r in cur.fetchall()]

            cur.execute(f"SELECT COUNT(*) AS cnt FROM users u {where_sql}", params)
            total = cur.fetchone()["cnt"]
    finally:
        conn.close()

    # 페이지네이션 링크에 필터 파라미터 유지 (URL 인코딩)
    qs_params: Dict[str, Any] = {"limit": limit}
    if q:
        qs_params["q"] = q
    if role:
        qs_params["role"] = role
    if status:
        qs_params["status"] = status
    if tier:
        qs_params["tier"] = tier
    pagination_qs = urlencode(qs_params)

    return templates.TemplateResponse(request=request, name="user_admin.html", context={
        "request": request,
        "active_page": "user_admin",
        "current_user": user,
        "auth_enabled": auth_cfg.enabled,
        "users": users,
        "page": page,
        "limit": limit,
        "total": total,
        "total_pages": max(1, (total + limit - 1) // limit),
        "valid_tiers": VALID_TIERS,
        "tier_info": TIER_INFO,
        "q": q or "",
        "filter_role": role or "",
        "filter_status": status or "",
        "filter_tier": tier or "",
        "pagination_qs": pagination_qs,
    })


# ── 역할 변경 ────────────────────────────────


@router.patch("/{user_id}/role")
def change_role(
    user_id: int,
    role: str = Query(..., description="새 역할 (admin, moderator, user)"),
    reason: Optional[str] = Query(None, description="변경 사유(감사 로그용)"),
    actor: Optional[UserInDB] = Depends(require_role("admin", "moderator")),
    db_cfg: DatabaseConfig = Depends(_get_db_cfg),
):
    """사용자 역할 변경"""
    if role not in ("admin", "moderator", "user"):
        raise HTTPException(status_code=400, detail="유효하지 않은 역할입니다")

    conn = get_connection(db_cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, email, role FROM users WHERE id = %s",
                (user_id,),
            )
            target = cur.fetchone()
            if not target:
                raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")

            # Moderator 권한 제약
            if actor and actor.role == "moderator":
                if target["role"] == "admin":
                    raise HTTPException(status_code=403, detail="Admin 계정의 역할을 변경할 수 없습니다")
                if role == "admin":
                    raise HTTPException(status_code=403, detail="Admin 역할을 부여할 수 없습니다")

            # 본인 역할 변경 금지 (권한 에스컬레이션 방지)
            if actor and actor.id == user_id:
                raise HTTPException(status_code=400, detail="자기 자신의 역할은 변경할 수 없습니다")

            cur.execute("UPDATE users SET role = %s WHERE id = %s", (role, user_id))

            _log_admin_action(
                cur,
                actor=actor,
                target_user_id=user_id,
                target_email=target["email"],
                action="role_change",
                before={"role": target["role"]},
                after={"role": role},
                reason=reason,
            )
        conn.commit()
        return {"message": f"역할이 {role}(으)로 변경되었습니다"}
    finally:
        conn.close()


# ── 계정 활성화/비활성화 ──────────────────────


@router.patch("/{user_id}/status")
def change_status(
    user_id: int,
    is_active: bool = Query(..., description="활성화 여부"),
    reason: Optional[str] = Query(None, description="변경 사유(감사 로그용)"),
    actor: Optional[UserInDB] = Depends(require_role("admin", "moderator")),
    db_cfg: DatabaseConfig = Depends(_get_db_cfg),
):
    """사용자 활성화/비활성화"""
    conn = get_connection(db_cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, email, role, is_active FROM users WHERE id = %s",
                (user_id,),
            )
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

            _log_admin_action(
                cur,
                actor=actor,
                target_user_id=user_id,
                target_email=target["email"],
                action="status_change",
                before={"is_active": target["is_active"]},
                after={"is_active": is_active},
                reason=reason,
            )
        conn.commit()
        status_text = "활성화" if is_active else "비활성화"
        return {"message": f"계정이 {status_text}되었습니다"}
    finally:
        conn.close()


# ── 티어 수동 부여/변경 (Admin 전용) ──────────


@router.patch("/{user_id}/tier")
def change_tier(
    user_id: int,
    tier: str = Query(..., description="새 티어 (free, pro, premium)"),
    expires_at: Optional[str] = Query(
        None,
        description="만료 일시 (ISO 형식, 예: 2026-12-31 또는 2026-12-31T23:59). 빈 값이면 영구/해제.",
    ),
    reason: Optional[str] = Query(None, description="부여 사유(감사 로그용)"),
    actor: Optional[UserInDB] = Depends(require_role("admin")),
    db_cfg: DatabaseConfig = Depends(_get_db_cfg),
):
    """구독 티어 수동 부여/변경 (Admin 전용).

    용도: 베타 테스터·환불 보정·프로모션·QA 테스트.
    PG 결제 연동(A-004) 이전까지 유일한 유료 티어 부여 수단.

    정책:
      - Admin만 변경 가능 (과금 성격 — Moderator 제외)
      - 본인 티어 변경 금지
      - free 티어는 tier_expires_at을 자동으로 NULL 처리
      - 변경 이력은 admin_audit_logs에 기록
    """
    if tier not in VALID_TIERS:
        raise HTTPException(
            status_code=400,
            detail=f"유효하지 않은 티어입니다. 허용: {', '.join(VALID_TIERS)}",
        )

    # 본인 티어 변경 금지 (자가 승격 방지)
    if actor and actor.id == user_id:
        raise HTTPException(status_code=400, detail="자기 자신의 티어는 변경할 수 없습니다")

    # free는 만료일 개념 자체가 없음 → 강제 NULL
    if tier == "free":
        expires_dt: Optional[datetime] = None
    else:
        expires_dt = _parse_expires_at(expires_at)
        if expires_dt is not None and expires_dt <= datetime.utcnow():
            raise HTTPException(status_code=400, detail="만료일은 현재 시각 이후여야 합니다")

    conn = get_connection(db_cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, email, tier, tier_expires_at FROM users WHERE id = %s",
                (user_id,),
            )
            target = cur.fetchone()
            if not target:
                raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")

            cur.execute(
                "UPDATE users SET tier = %s, tier_expires_at = %s WHERE id = %s",
                (tier, expires_dt, user_id),
            )

            before_expires = target["tier_expires_at"]
            _log_admin_action(
                cur,
                actor=actor,
                target_user_id=user_id,
                target_email=target["email"],
                action="tier_change",
                before={
                    "tier": target["tier"],
                    "tier_expires_at": before_expires.isoformat() if before_expires else None,
                },
                after={
                    "tier": tier,
                    "tier_expires_at": expires_dt.isoformat() if expires_dt else None,
                },
                reason=reason,
            )
        conn.commit()

        label = TIER_INFO[normalize_tier(tier)].label_ko
        exp_msg = f" (만료: {expires_dt.date()})" if expires_dt else ""
        return {
            "message": f"티어가 {label}(으)로 변경되었습니다{exp_msg}",
            "tier": tier,
            "tier_expires_at": expires_dt.isoformat() if expires_dt else None,
        }
    finally:
        conn.close()


# ── 임시 비밀번호 발급 ────────────────────────


@router.post("/{user_id}/reset-password")
def reset_password(
    user_id: int,
    reason: Optional[str] = Query(None, description="초기화 사유(감사 로그용)"),
    actor: Optional[UserInDB] = Depends(require_role("admin")),
    db_cfg: DatabaseConfig = Depends(_get_db_cfg),
):
    """임시 비밀번호 발급 (Admin 전용)"""
    temp_pw = secrets.token_urlsafe(12)
    pw_hash = hash_password(temp_pw)

    conn = get_connection(db_cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, email FROM users WHERE id = %s", (user_id,))
            target = cur.fetchone()
            if not target:
                raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")

            cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (pw_hash, user_id))

            _log_admin_action(
                cur,
                actor=actor,
                target_user_id=user_id,
                target_email=target["email"],
                action="password_reset",
                before=None,
                after=None,  # 비밀번호 값은 절대 기록하지 않음
                reason=reason,
            )
        conn.commit()
        return {"message": "임시 비밀번호가 발급되었습니다", "temp_password": temp_pw}
    finally:
        conn.close()


# ── 계정 삭제 ─────────────────────────────────


@router.delete("/{user_id}")
def delete_user(
    user_id: int,
    reason: Optional[str] = Query(None, description="삭제 사유(감사 로그용)"),
    actor: Optional[UserInDB] = Depends(require_role("admin")),
    db_cfg: DatabaseConfig = Depends(_get_db_cfg),
):
    """계정 삭제 (Admin 전용, 본인 삭제 금지)"""
    if actor and actor.id == user_id:
        raise HTTPException(status_code=400, detail="자기 자신을 삭제할 수 없습니다")

    conn = get_connection(db_cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, email, role, tier FROM users WHERE id = %s",
                (user_id,),
            )
            target = cur.fetchone()
            if not target:
                raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")

            # 삭제 전에 감사 로그 먼저 기록 — ON DELETE SET NULL로 target_user_id만 NULL이 됨
            # (target_email은 denormalize되어 유지)
            _log_admin_action(
                cur,
                actor=actor,
                target_user_id=user_id,
                target_email=target["email"],
                action="user_delete",
                before={
                    "email": target["email"],
                    "role": target["role"],
                    "tier": target["tier"],
                },
                after=None,
                reason=reason,
            )

            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()
        return {"message": "계정이 삭제되었습니다"}
    finally:
        conn.close()


# ── 감사 로그 조회 (Admin 전용) ──────────────


@router.get("/audit-logs")
def audit_logs_page(
    request: Request,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=30, ge=1, le=200),
    action: Optional[str] = Query(None),
    target_email: Optional[str] = Query(None),
    user: Optional[UserInDB] = Depends(get_current_user),
    auth_cfg: AuthConfig = Depends(_get_auth_cfg),
):
    """관리자 작업 감사 로그 뷰어 (Admin 전용 페이지)"""
    if auth_cfg.enabled:
        if user is None:
            return RedirectResponse("/auth/login?next=/admin/users/audit-logs", status_code=302)
        if user.role != "admin":
            raise HTTPException(status_code=403, detail="Admin 전용 페이지입니다")

    db_cfg = _get_db_cfg()
    offset = (page - 1) * limit

    where_clauses = []
    params: list = []
    if action:
        where_clauses.append("action = %s")
        params.append(action)
    if target_email:
        where_clauses.append("target_email ILIKE %s")
        params.append(f"%{target_email}%")
    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    conn = get_connection(db_cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT id, actor_id, actor_email, target_user_id, target_email,
                       action, before_state, after_state, reason, created_at
                FROM admin_audit_logs
                {where_sql}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = []
            for r in cur.fetchall():
                row = _serialize_row(r)
                # JSONB 필드는 파이썬 dict로 이미 파싱되어 있지만 템플릿에서 쉽게 쓰도록 문자열도 제공
                row["before_json"] = json.dumps(r["before_state"], ensure_ascii=False) if r["before_state"] else ""
                row["after_json"] = json.dumps(r["after_state"], ensure_ascii=False) if r["after_state"] else ""
                rows.append(row)

            cur.execute(f"SELECT COUNT(*) AS cnt FROM admin_audit_logs {where_sql}", params)
            total = cur.fetchone()["cnt"]
    finally:
        conn.close()

    return templates.TemplateResponse(request=request, name="admin_audit_logs.html", context={
        "request": request,
        "active_page": "user_admin",
        "current_user": user,
        "auth_enabled": auth_cfg.enabled,
        "logs": rows,
        "page": page,
        "limit": limit,
        "total": total,
        "total_pages": (total + limit - 1) // limit,
        "filter_action": action or "",
        "filter_target_email": target_email or "",
    })
