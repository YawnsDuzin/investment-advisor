"""공통 FastAPI dependency 팩토리 (B2 + B2.5)."""
from typing import Any, Iterator, Optional

from fastapi import Depends, Request
from shared.config import DatabaseConfig, AuthConfig
from shared.db import get_connection
from api.auth.dependencies import get_current_user, _get_auth_cfg
from api.auth.models import UserInDB


def get_db_cfg() -> DatabaseConfig:
    """DatabaseConfig 인스턴스 반환 — 라우트 기존 `_get_cfg()` 대체."""
    return DatabaseConfig()


def get_db_conn(cfg: DatabaseConfig = Depends(get_db_cfg)) -> Iterator[Any]:
    """DB 연결을 FastAPI dependency lifecycle로 관리 (B2.5).

    사용: `def route(conn = Depends(get_db_conn))`.
    FastAPI가 라우트 종료 시 yield 이후(finally) 블록을 실행해 close 보장.
    """
    conn = get_connection(cfg)
    try:
        yield conn
    finally:
        conn.close()


def make_page_ctx(active_page: str):
    """페이지별 컨텍스트 빌더 dependency 팩토리 (B2.5).

    사용: `def route(ctx: dict = Depends(make_page_ctx("dashboard")))`.

    반환 dict:
    - base_ctx가 채우는 모든 키 (current_user, auth_enabled, tier, unread_notifications 등)
    - 편의 키: `ctx["_user"]` (UserInDB|None), `ctx["_auth_cfg"]` (AuthConfig)
      - `ctx["request"]`는 base_ctx가 이미 넣음
    """
    def _dep(
        request: Request,
        user: Optional[UserInDB] = Depends(get_current_user),
        auth_cfg: AuthConfig = Depends(_get_auth_cfg),
    ) -> dict:
        # 순환 임포트 회피: 함수 내부에서 import
        from api.page_context import base_ctx

        ctx = base_ctx(request, active_page, user, auth_cfg)
        ctx["_user"] = user
        ctx["_auth_cfg"] = auth_cfg
        return ctx
    return _dep
