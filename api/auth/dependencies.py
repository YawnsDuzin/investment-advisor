"""FastAPI Depends — 인증/인가 의존성"""
from typing import Optional, Callable
from fastapi import Request, HTTPException, Depends
from shared.config import AuthConfig, DatabaseConfig
from shared.db import get_connection
from psycopg2.extras import RealDictCursor
from api.auth.jwt_handler import decode_access_token
from api.auth.models import UserInDB


def _get_auth_cfg() -> AuthConfig:
    return AuthConfig()


def _get_db_cfg() -> DatabaseConfig:
    return DatabaseConfig()


def get_current_user(
    request: Request,
    auth_cfg: AuthConfig = Depends(_get_auth_cfg),
    db_cfg: DatabaseConfig = Depends(_get_db_cfg),
) -> Optional[UserInDB]:
    """선택적 인증 — AUTH_ENABLED=false면 None, 쿠키 없음/만료면 None, 유효하면 UserInDB"""
    if not auth_cfg.enabled:
        return None

    token = request.cookies.get("access_token")
    if not token:
        return None

    payload = decode_access_token(token, auth_cfg.jwt_secret_key, auth_cfg.jwt_algorithm)
    if not payload:
        return None

    user_id = int(payload["sub"])
    conn = get_connection(db_cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, email, nickname, role, is_active, created_at, last_login_at "
                "FROM users WHERE id = %s AND is_active = TRUE",
                (user_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return None
    return UserInDB(**row)


def get_current_user_required(
    request: Request,
    user: Optional[UserInDB] = Depends(get_current_user),
    auth_cfg: AuthConfig = Depends(_get_auth_cfg),
) -> Optional[UserInDB]:
    """필수 인증 — AUTH_ENABLED=false면 None (통과), true + 미인증이면 401"""
    if not auth_cfg.enabled:
        return None
    if user is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")
    return user


def require_role(*roles: str) -> Callable:
    """역할 기반 접근 제어 팩토리. AUTH_ENABLED=false면 통과"""
    def _dependency(
        user: Optional[UserInDB] = Depends(get_current_user_required),
        auth_cfg: AuthConfig = Depends(_get_auth_cfg),
    ) -> Optional[UserInDB]:
        if not auth_cfg.enabled:
            return None
        if user is None:
            raise HTTPException(status_code=401, detail="로그인이 필요합니다")
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="접근 권한이 없습니다")
        return user
    return _dependency
