"""OAuth 콜백 핸들러 — DB helper + callback 메인 로직.

DB helper 9종 + handle_oauth_callback (5분기) + _extract_userinfo +
OAuthCallbackError. 토큰 발급은 라우트 레이어 책임.

라우트 작성 주의: OAuthCallbackError.message (str(e)) 는 Authlib 내부
상세를 포함할 수 있어 사용자 노출 금지. 로그용으로만 사용하고, 사용자에게는
error_code 기반 안내 메시지를 보여야 한다 (api/routes/auth.py 의
_OAUTH_ERROR_MESSAGES 매핑 참조).
"""
from typing import Optional

from authlib.integrations.starlette_client import OAuthError
from psycopg2.extras import RealDictCursor


def _find_oauth_account(conn, provider: str, provider_user_id: str) -> Optional[dict]:
    """(provider, provider_user_id) 로 user_oauth_accounts row 조회."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT id, user_id, provider, provider_user_id "
            "FROM user_oauth_accounts "
            "WHERE provider = %s AND provider_user_id = %s",
            (provider, provider_user_id),
        )
        return cur.fetchone()


def _find_user_by_email(conn, email: str) -> Optional[dict]:
    """이메일로 users 조회 (대소문자 무관)."""
    normalized = email.lower().strip()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT id, email, password_hash, role, is_active, nickname "
            "FROM users WHERE LOWER(email) = %s",
            (normalized,),
        )
        return cur.fetchone()


def _insert_oauth_account(conn, user_id: int, provider: str, userinfo: dict) -> None:
    """user_oauth_accounts 에 신규 연결 INSERT."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO user_oauth_accounts "
            "(user_id, provider, provider_user_id, provider_email, provider_name, last_login_at) "
            "VALUES (%s, %s, %s, %s, %s, NOW())",
            (
                user_id,
                provider,
                userinfo["provider_user_id"],
                userinfo.get("email"),
                userinfo.get("name", ""),
            ),
        )


def _update_oauth_last_login(conn, oauth_account_id: int) -> None:
    """기존 OAuth 연결의 last_login_at 갱신."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE user_oauth_accounts SET last_login_at = NOW() WHERE id = %s",
            (oauth_account_id,),
        )


def _create_user_from_oauth(conn, userinfo: dict) -> int:
    """OAuth 신규 가입 — users INSERT, password_hash=NULL, role='user', tier='free'.

    role/tier 하드코딩 — 권한 상승 경로 차단.
    """
    nickname = userinfo.get("name") or userinfo["email"].split("@")[0]
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "INSERT INTO users (email, password_hash, nickname, role, tier) "
            "VALUES (%s, NULL, %s, 'user', 'free') RETURNING id",
            (userinfo["email"], nickname),
        )
        row = cur.fetchone()
        return row["id"]


def _get_user(conn, user_id: int) -> Optional[dict]:
    """user_id 로 users row 조회."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT id, email, password_hash, role, is_active, nickname "
            "FROM users WHERE id = %s",
            (user_id,),
        )
        return cur.fetchone()


def _count_oauth_accounts(conn, user_id: int, exclude_provider: Optional[str] = None) -> int:
    """user_id 에 연결된 OAuth 계정 수 (exclude_provider 제외 가능)."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        if exclude_provider:
            cur.execute(
                "SELECT COUNT(*) AS count FROM user_oauth_accounts "
                "WHERE user_id = %s AND provider != %s",
                (user_id, exclude_provider),
            )
        else:
            cur.execute(
                "SELECT COUNT(*) AS count FROM user_oauth_accounts WHERE user_id = %s",
                (user_id,),
            )
        row = cur.fetchone()
        return int(row["count"]) if row else 0


def _can_unlink(conn, user_id: int, provider: str) -> bool:
    """provider 연결 해제 시 다른 로그인 수단이 남는지 확인.

    True = unlink 허용 (local password 또는 다른 provider 1개 이상 있음).
    """
    user = _get_user(conn, user_id)
    if user is None:
        return False
    has_password = user["password_hash"] is not None
    other_count = _count_oauth_accounts(conn, user_id, exclude_provider=provider)
    return has_password or other_count >= 1


def _audit_log(conn, user_id: int, action: str, provider: Optional[str] = None,
               detail: Optional[str] = None) -> None:
    """admin_audit_logs 에 OAuth action 기록 (v17 테이블 재사용).

    user 조회 실패해도 best-effort 로 INSERT 진행. INSERT 자체가 실패하면
    logging.warning 으로만 남기고 호출자로 예외 전파 안 함 (감사 실패가 OAuth
    로그인 자체를 막아선 안 됨).

    v17 컬럼: actor_id / actor_email / target_user_id / target_email / action
    / before_state JSONB / after_state JSONB / reason TEXT. OAuth 는 상태 변경
    추적 의도가 아니므로 before/after 는 비우고 reason 에 provider 정보를 담는다.
    """
    target_email = ""
    user = _get_user(conn, user_id)
    if user:
        target_email = user.get("email", "")
    reason_text = detail or (f"provider={provider}" if provider else "")
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO admin_audit_logs "
                "(actor_id, actor_email, target_user_id, target_email, action, reason) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (user_id, target_email, user_id, target_email, action, reason_text),
            )
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("OAuth _audit_log INSERT failed: %s", exc)


def _list_linked_providers(conn, user_id: int) -> dict:
    """profile 페이지용 — {provider: {provider_email, linked_at, last_login_at}} 맵."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT provider, provider_email, linked_at, last_login_at "
            "FROM user_oauth_accounts WHERE user_id = %s",
            (user_id,),
        )
        rows = cur.fetchall()
    return {row["provider"]: dict(row) for row in rows}


class OAuthCallbackError(Exception):
    """OAuth 콜백 처리 실패 — error_code 로 사용자 안내 메시지 매핑."""

    def __init__(self, error_code: str, message: str = ""):
        super().__init__(message or error_code)
        self.error_code = error_code


async def _extract_userinfo(provider: str, token: dict) -> dict:
    """provider 별 userinfo 응답 표준화.

    Returns:
        {"provider_user_id", "email", "email_verified", "name"}

    Raises:
        OAuthCallbackError("oauth_failed"): Kakao API 호출 (네트워크/5xx) 실패 시
    """
    from api.auth.oauth_providers import oauth

    if provider == "google":
        ui = token.get("userinfo") or {}
        return {
            "provider_user_id": str(ui.get("sub", "")),
            "email": ui.get("email"),
            "email_verified": bool(ui.get("email_verified", False)),
            "name": ui.get("name", ""),
        }
    elif provider == "kakao":
        client = oauth.create_client("kakao")
        if client is None:
            raise OAuthCallbackError("oauth_failed", "kakao client not registered")
        try:
            resp = await client.get("v2/user/me", token=token)
        except Exception as e:
            raise OAuthCallbackError("oauth_failed", f"kakao userinfo fetch failed: {e}")
        data = resp.json()
        account = data.get("kakao_account", {})
        profile = account.get("profile", {})
        return {
            "provider_user_id": str(data.get("id", "")),
            "email": account.get("email"),
            "email_verified": bool(account.get("is_email_verified", False)),
            "name": profile.get("nickname", ""),
        }
    raise ValueError(f"Unknown provider: {provider}")


async def handle_oauth_callback(provider: str, request, conn, next_url: str) -> tuple:
    """OAuth 콜백 메인 — 토큰 교환 → upsert → audit → (user, next_url) 반환.

    토큰 발급(`_set_auth_cookies`)는 라우트 레이어 책임. 이 함수는 user 식별까지.

    Raises:
        OAuthCallbackError(error_code=...): 사용자 안내 가능한 실패
            - "oauth_failed" — state/code 오류
            - "kakao_email_required" — Kakao 이메일 미동의
            - "email_unverified" — provider 이메일 미검증
            - "account_disabled" — is_active=false
    """
    from api.auth.oauth_providers import oauth

    # 1. Authlib 토큰 교환 (state 검증 자동)
    client = oauth.create_client(provider)
    if client is None:
        raise OAuthCallbackError("oauth_failed", f"provider {provider} not registered")
    try:
        token = await client.authorize_access_token(request)
    except OAuthError as e:
        # state 불일치 / code 만료 등 OAuth 프로토콜 오류만 oauth_failed 로 매핑
        # DB·내부 버그는 그대로 전파 — 라우트에서 500
        raise OAuthCallbackError("oauth_failed", str(e))

    userinfo = await _extract_userinfo(provider, token)

    # 2. Kakao 이메일 필수
    if provider == "kakao" and not userinfo["email"]:
        raise OAuthCallbackError("kakao_email_required")

    # 3. 기존 OAuth 연결 조회 → 즉시 로그인
    existing = _find_oauth_account(conn, provider, userinfo["provider_user_id"])
    if existing:
        user = _get_user(conn, existing["user_id"])
        if user is None:
            raise OAuthCallbackError("oauth_failed", "linked user not found")
        if not user["is_active"]:
            raise OAuthCallbackError("account_disabled")
        _update_oauth_last_login(conn, existing["id"])
        _audit_log(conn, user["id"], "oauth_login", provider=provider)
        return (user, next_url)

    # 4. 이메일 매칭 → 자동 연결 또는 신규 생성
    user = _find_user_by_email(conn, userinfo["email"]) if userinfo["email"] else None
    if user:
        if not userinfo["email_verified"]:
            raise OAuthCallbackError("email_unverified")
        if not user["is_active"]:
            raise OAuthCallbackError("account_disabled")
        _insert_oauth_account(conn, user["id"], provider, userinfo)
        _audit_log(conn, user["id"], "oauth_auto_link", provider=provider)
        return (user, next_url)

    # 5. 신규 가입
    new_user_id = _create_user_from_oauth(conn, userinfo)
    _insert_oauth_account(conn, new_user_id, provider, userinfo)
    new_user = _get_user(conn, new_user_id)
    if new_user is None:
        # INSERT 직후 SELECT 가 None — DB 비정상. 라우트에서 사용자 안내 매핑.
        raise OAuthCallbackError("oauth_failed", "user creation lookup failed")
    _audit_log(conn, new_user_id, "oauth_signup", provider=provider)
    return (new_user, next_url)
