"""OAuth 라우트 — Google / Kakao 소셜 로그인.

라우트:
    GET  /auth/oauth/{provider}/start      → provider 동의 화면으로 redirect
    GET  /auth/oauth/{provider}/callback   → 콜백 → upsert → 쿠키 발급 → next 302
    POST /auth/oauth/{provider}/link       → 로그인 상태에서 계정 연결 시작 (Task 8)
    POST /auth/oauth/{provider}/unlink     → 연결 해제 (Task 8)
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

import api.auth.oauth_providers as _oauth_mod

from api.auth.jwt_handler import create_access_token, create_refresh_token, hash_token
from api.auth.oauth_handlers import OAuthCallbackError, handle_oauth_callback
from api.deps import get_db_conn
from api.routes.auth import _set_auth_cookies
from shared.config import AuthConfig


router = APIRouter(prefix="/auth/oauth", tags=["OAuth"])

_ALLOWED_PROVIDERS = frozenset({"google", "kakao"})


def _get_auth_cfg() -> AuthConfig:
    return AuthConfig()


def _validate_provider(provider: str) -> None:
    if provider not in _ALLOWED_PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown OAuth provider")


def _safe_next(next_url: str) -> str:
    """open redirect 방지 — / 로 시작 안 하면 / 로 폴백.

    프로토콜 상대 URL (//evil.com) 도 차단.
    """
    if not next_url or not next_url.startswith("/"):
        return "/"
    if next_url.startswith("//"):
        return "/"
    return next_url


# ── start ─────────────────────────────────────


@router.get("/{provider}/start")
async def oauth_start(provider: str, request: Request, next: str = "/"):
    """provider 동의 화면으로 redirect. state 는 SessionMiddleware 에 저장됨."""
    _validate_provider(provider)
    cfg = _get_auth_cfg()

    client = _oauth_mod.oauth.create_client(provider)
    if client is None:
        # provider 비활성화(CLIENT_ID 미설정) 또는 미등록 — 404 처리
        raise HTTPException(status_code=404, detail=f"{provider} OAuth not configured")

    safe_next = _safe_next(next)
    request.session["oauth_next_url"] = safe_next

    redirect_uri = (cfg.google_redirect_uri if provider == "google"
                    else cfg.kakao_redirect_uri)
    return await client.authorize_redirect(request, redirect_uri)


# ── callback ──────────────────────────────────


@router.get("/{provider}/callback")
async def oauth_callback(provider: str, request: Request):
    """OAuth provider 콜백 — 토큰 교환 → upsert → 쿠키 발급 → next 302."""
    _validate_provider(provider)
    auth_cfg = _get_auth_cfg()

    next_url = _safe_next(request.session.pop("oauth_next_url", "/"))

    # get_db_conn 을 런타임에 호출 — 테스트에서 patch 가능하도록 Depends 대신 직접 호출
    db_gen = get_db_conn()
    conn = next(db_gen)
    try:
        try:
            user, next_url = await handle_oauth_callback(
                provider=provider, request=request, conn=conn, next_url=next_url,
            )
        except OAuthCallbackError as e:
            return RedirectResponse(
                f"/auth/login?error={e.error_code}", status_code=302,
            )

        conn.commit()

        # 기존 _set_auth_cookies 경로 재사용 — local 로그인과 동일한 토큰 형태
        access_token = create_access_token(
            user["id"], user["role"],
            auth_cfg.jwt_secret_key, auth_cfg.jwt_algorithm,
            auth_cfg.access_token_expire_minutes,
        )
        refresh_raw = create_refresh_token()
        expires_at = datetime.now(timezone.utc) + timedelta(days=auth_cfg.refresh_token_expire_days)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO refresh_tokens (user_id, token_hash, expires_at) VALUES (%s, %s, %s)",
                (user["id"], hash_token(refresh_raw), expires_at),
            )
            cur.execute("UPDATE users SET last_login_at = NOW() WHERE id = %s", (user["id"],))
        conn.commit()

        response = RedirectResponse(next_url, status_code=302)
        _set_auth_cookies(response, access_token, refresh_raw, auth_cfg)
        return response
    finally:
        try:
            next(db_gen)
        except StopIteration:
            pass
