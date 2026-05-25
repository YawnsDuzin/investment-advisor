"""OAuth 콜백 핸들러 — DB helper + callback 메인 로직.

callback 메인 (`handle_oauth_callback`) 는 Task 6 에서 추가됩니다.
이 파일은 우선 DB helper 함수만 정의.
"""
from typing import Optional

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
