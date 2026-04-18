"""FastAPI Depends — 인증/인가 의존성"""
from typing import Optional, Callable, Dict, Any
from fastapi import Request, HTTPException, Depends
from shared.config import AuthConfig, DatabaseConfig
from shared.db import get_connection
from shared.tier_limits import VALID_TIERS, TIER_FREE, normalize_tier
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
                "SELECT id, email, nickname, role, tier, tier_expires_at, "
                "is_active, created_at, last_login_at "
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


def quota_exceeded_detail(
    feature: str,
    current_tier: str,
    usage: Optional[int] = None,
    limit: Optional[int] = None,
    message: Optional[str] = None,
) -> Dict[str, Any]:
    """402 Payment Required 응답 페이로드 표준화.

    Frontend의 전역 fetch 인터셉터가 이 구조를 보고 업그레이드 모달을 띄운다.
    """
    return {
        "error": "quota_exceeded",
        "feature": feature,  # "watchlist" | "subscription" | "chat" | "stage2" | "history"
        "current_tier": current_tier,
        "usage": usage,
        "limit": limit,
        "message": message or "현재 플랜의 사용 한도를 초과했습니다.",
        "upgrade_url": "/pages/pricing",
    }


def require_tier(*allowed_tiers: str) -> Callable:
    """특정 티어 이상 접근 제어 팩토리. AUTH_ENABLED=false면 통과.

    만료된 유료 티어는 free로 간주. free가 allowed_tiers에 없으면 402.
    """
    # 허용된 티어 유효성 검증 (서버 기동 시점에 잘못된 상수 잡기)
    for t in allowed_tiers:
        if t not in VALID_TIERS:
            raise ValueError(f"알 수 없는 티어: {t}")

    def _dependency(
        user: Optional[UserInDB] = Depends(get_current_user_required),
        auth_cfg: AuthConfig = Depends(_get_auth_cfg),
    ) -> Optional[UserInDB]:
        if not auth_cfg.enabled:
            return None
        if user is None:
            raise HTTPException(status_code=401, detail="로그인이 필요합니다")
        effective = user.effective_tier()
        if effective not in allowed_tiers:
            raise HTTPException(
                status_code=402,
                detail=quota_exceeded_detail(
                    feature="tier",
                    current_tier=effective,
                    message=f"이 기능은 {', '.join(allowed_tiers).upper()} 플랜에서 이용 가능합니다.",
                ),
            )
        return user
    return _dependency
