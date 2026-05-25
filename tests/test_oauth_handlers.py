"""OAuth handlers — DB helper 함수 단위 테스트."""
from unittest.mock import MagicMock

import pytest


def _make_cursor(fetchone_value=None, fetchall_value=None):
    cur = MagicMock()
    cur.fetchone.return_value = fetchone_value
    cur.fetchall.return_value = fetchall_value or []
    cur.__enter__.return_value = cur
    cur.__exit__.return_value = False
    return cur


def _make_conn(cur):
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn


def test_find_oauth_account_returns_row_when_exists():
    from api.auth.oauth_handlers import _find_oauth_account
    cur = _make_cursor(fetchone_value={"id": 1, "user_id": 42, "provider": "google",
                                        "provider_user_id": "sub-123"})
    conn = _make_conn(cur)
    result = _find_oauth_account(conn, "google", "sub-123")
    assert result == {"id": 1, "user_id": 42, "provider": "google", "provider_user_id": "sub-123"}


def test_find_oauth_account_returns_none_when_missing():
    from api.auth.oauth_handlers import _find_oauth_account
    cur = _make_cursor(fetchone_value=None)
    conn = _make_conn(cur)
    result = _find_oauth_account(conn, "google", "sub-999")
    assert result is None


def test_find_user_by_email_lowercases():
    from api.auth.oauth_handlers import _find_user_by_email
    cur = _make_cursor(fetchone_value={"id": 7, "email": "foo@bar.com", "is_active": True,
                                        "password_hash": None, "role": "user"})
    conn = _make_conn(cur)
    result = _find_user_by_email(conn, "FOO@BAR.COM")
    assert result is not None
    assert result["id"] == 7
    # execute 호출 인자에 lower 된 이메일 들어갔는지
    args, _ = cur.execute.call_args
    assert "foo@bar.com" in args[1]


def test_insert_oauth_account_runs_insert():
    from api.auth.oauth_handlers import _insert_oauth_account
    cur = _make_cursor()
    conn = _make_conn(cur)
    _insert_oauth_account(conn, user_id=10, provider="google", userinfo={
        "provider_user_id": "sub-x",
        "email": "x@y.com",
        "name": "X Y",
    })
    sql = cur.execute.call_args[0][0]
    assert "INSERT INTO user_oauth_accounts" in sql


def test_create_user_from_oauth_returns_new_id():
    from api.auth.oauth_handlers import _create_user_from_oauth
    cur = _make_cursor(fetchone_value={"id": 99})
    conn = _make_conn(cur)
    new_id = _create_user_from_oauth(conn, userinfo={
        "email": "new@user.com",
        "name": "New User",
    })
    assert new_id == 99
    sql = cur.execute.call_args[0][0]
    assert "INSERT INTO users" in sql
    # role='user' / tier='free' 하드코딩 확인
    assert "'user'" in sql or "%s, %s, 'user'" in sql or "user" in str(cur.execute.call_args[0][1])


def test_can_unlink_returns_true_when_password_exists():
    from api.auth.oauth_handlers import _can_unlink
    cur = MagicMock()
    cur.__enter__.return_value = cur
    cur.__exit__.return_value = False
    # 첫 호출: user 조회 (password_hash 있음)
    # 두 번째 호출: 다른 oauth 카운트 (0)
    cur.fetchone.side_effect = [
        {"password_hash": "bcrypt-hash"},
        {"count": 0},
    ]
    conn = MagicMock()
    conn.cursor.return_value = cur
    assert _can_unlink(conn, user_id=1, provider="google") is True


def test_can_unlink_returns_true_when_other_oauth_exists():
    from api.auth.oauth_handlers import _can_unlink
    cur = MagicMock()
    cur.__enter__.return_value = cur
    cur.__exit__.return_value = False
    cur.fetchone.side_effect = [
        {"password_hash": None},
        {"count": 1},  # Kakao 가 추가로 연결되어 있음
    ]
    conn = MagicMock()
    conn.cursor.return_value = cur
    assert _can_unlink(conn, user_id=1, provider="google") is True


def test_can_unlink_returns_false_when_oauth_only_and_solo():
    from api.auth.oauth_handlers import _can_unlink
    cur = MagicMock()
    cur.__enter__.return_value = cur
    cur.__exit__.return_value = False
    cur.fetchone.side_effect = [
        {"password_hash": None},
        {"count": 0},
    ]
    conn = MagicMock()
    conn.cursor.return_value = cur
    assert _can_unlink(conn, user_id=1, provider="google") is False


def test_audit_log_inserts_action():
    from api.auth.oauth_handlers import _audit_log
    cur = _make_cursor()
    conn = _make_conn(cur)
    _audit_log(conn, user_id=5, action="oauth_signup", provider="google")
    sql = cur.execute.call_args[0][0]
    assert "INSERT INTO admin_audit_logs" in sql


# ── handle_oauth_callback 시나리오 ──

import pytest
from unittest.mock import patch, AsyncMock


class _FakeRequest:
    def __init__(self):
        self.session = {}


def _make_full_conn(scenarios):
    """scenarios = {"fetchones": [row1, row2, ...]} — fetchone 응답 순서대로 주입."""
    cur = MagicMock()
    cur.__enter__.return_value = cur
    cur.__exit__.return_value = False
    cur.fetchone.side_effect = scenarios.get("fetchones", [None])
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


@pytest.mark.asyncio
async def test_callback_existing_oauth_account_logs_in():
    """기존 OAuth 가입자 재로그인 → 즉시 토큰 발급, users INSERT 없음."""
    from api.auth import oauth_handlers as h

    fake_userinfo = {"provider_user_id": "sub-1", "email": "a@b.com",
                     "email_verified": True, "name": "Alice"}
    conn, cur = _make_full_conn({
        "fetchones": [
            {"id": 5, "user_id": 42, "provider": "google", "provider_user_id": "sub-1"},
            {"id": 42, "email": "a@b.com", "is_active": True,
             "password_hash": None, "role": "user", "nickname": "Alice"},
            {"id": 42, "email": "a@b.com", "is_active": True,
             "password_hash": None, "role": "user", "nickname": "Alice"},
        ],
    })

    with patch.object(h, "_extract_userinfo", new=AsyncMock(return_value=fake_userinfo)), \
         patch("api.auth.oauth_providers.oauth") as mock_oauth:
        mock_client = MagicMock()
        mock_client.authorize_access_token = AsyncMock(return_value={"userinfo": fake_userinfo})
        mock_oauth.create_client.return_value = mock_client

        result = await h.handle_oauth_callback(
            provider="google",
            request=_FakeRequest(),
            conn=conn,
            next_url="/",
        )

    assert result is not None
    user, next_url = result
    assert user["id"] == 42


@pytest.mark.asyncio
async def test_callback_kakao_email_missing_returns_error():
    from api.auth import oauth_handlers as h
    fake_userinfo = {"provider_user_id": "k-1", "email": None,
                     "email_verified": False, "name": "User"}
    conn, _ = _make_full_conn({"fetchones": []})
    with patch.object(h, "_extract_userinfo", new=AsyncMock(return_value=fake_userinfo)), \
         patch("api.auth.oauth_providers.oauth") as mock_oauth:
        mock_client = MagicMock()
        mock_client.authorize_access_token = AsyncMock(return_value={})
        mock_oauth.create_client.return_value = mock_client

        with pytest.raises(h.OAuthCallbackError) as exc:
            await h.handle_oauth_callback("kakao", _FakeRequest(), conn, next_url="/")
        assert exc.value.error_code == "kakao_email_required"


@pytest.mark.asyncio
async def test_callback_auto_link_existing_user_when_email_verified():
    """이메일 기존 유저 + email_verified=true → 자동 연결."""
    from api.auth import oauth_handlers as h
    fake_userinfo = {"provider_user_id": "sub-2", "email": "exists@x.com",
                     "email_verified": True, "name": "Existing"}
    conn, cur = _make_full_conn({
        "fetchones": [
            None,  # _find_oauth_account → 없음
            {"id": 100, "email": "exists@x.com", "password_hash": "bcrypt", "role": "user",
             "is_active": True, "nickname": "Existing"},  # _find_user_by_email
            {"id": 100, "email": "exists@x.com", "password_hash": "bcrypt", "role": "user",
             "is_active": True, "nickname": "Existing"},  # audit 의 _get_user
        ],
    })
    with patch.object(h, "_extract_userinfo", new=AsyncMock(return_value=fake_userinfo)), \
         patch("api.auth.oauth_providers.oauth") as mock_oauth:
        mock_client = MagicMock()
        mock_client.authorize_access_token = AsyncMock(return_value={})
        mock_oauth.create_client.return_value = mock_client

        result = await h.handle_oauth_callback("google", _FakeRequest(), conn, "/")
    user, _ = result
    assert user["id"] == 100
    sql_calls = [str(c.args[0]) for c in cur.execute.call_args_list]
    assert any("INSERT INTO user_oauth_accounts" in s for s in sql_calls)


@pytest.mark.asyncio
async def test_callback_email_unverified_refuses_auto_link():
    from api.auth import oauth_handlers as h
    fake_userinfo = {"provider_user_id": "sub-3", "email": "ev@x.com",
                     "email_verified": False, "name": "EV"}
    conn, _ = _make_full_conn({
        "fetchones": [
            None,
            {"id": 200, "email": "ev@x.com", "password_hash": "bcrypt",
             "role": "user", "is_active": True, "nickname": "EV"},
        ],
    })
    with patch.object(h, "_extract_userinfo", new=AsyncMock(return_value=fake_userinfo)), \
         patch("api.auth.oauth_providers.oauth") as mock_oauth:
        mock_client = MagicMock()
        mock_client.authorize_access_token = AsyncMock(return_value={})
        mock_oauth.create_client.return_value = mock_client

        with pytest.raises(h.OAuthCallbackError) as exc:
            await h.handle_oauth_callback("google", _FakeRequest(), conn, "/")
        assert exc.value.error_code == "email_unverified"


@pytest.mark.asyncio
async def test_callback_creates_new_user_when_no_match():
    from api.auth import oauth_handlers as h
    fake_userinfo = {"provider_user_id": "sub-4", "email": "new@u.com",
                     "email_verified": True, "name": "Newbie"}
    conn, cur = _make_full_conn({
        "fetchones": [
            None,                                              # _find_oauth_account
            None,                                              # _find_user_by_email
            {"id": 999},                                       # _create_user_from_oauth RETURNING id
            {"id": 999, "email": "new@u.com", "password_hash": None, "role": "user",
             "is_active": True, "nickname": "Newbie"},          # _get_user (after creation)
            {"id": 999, "email": "new@u.com", "password_hash": None, "role": "user",
             "is_active": True, "nickname": "Newbie"},          # audit 의 _get_user
        ],
    })
    with patch.object(h, "_extract_userinfo", new=AsyncMock(return_value=fake_userinfo)), \
         patch("api.auth.oauth_providers.oauth") as mock_oauth:
        mock_client = MagicMock()
        mock_client.authorize_access_token = AsyncMock(return_value={})
        mock_oauth.create_client.return_value = mock_client

        result = await h.handle_oauth_callback("google", _FakeRequest(), conn, "/")
    user, _ = result
    assert user["id"] == 999
    sql_calls = [str(c.args[0]) for c in cur.execute.call_args_list]
    assert any("INSERT INTO users" in s for s in sql_calls)


@pytest.mark.asyncio
async def test_callback_inactive_account_refuses():
    from api.auth import oauth_handlers as h
    fake_userinfo = {"provider_user_id": "sub-5", "email": "off@x.com",
                     "email_verified": True, "name": "Off"}
    conn, _ = _make_full_conn({
        "fetchones": [
            {"id": 1, "user_id": 300, "provider": "google", "provider_user_id": "sub-5"},
            {"id": 300, "email": "off@x.com", "password_hash": "bcrypt",
             "role": "user", "is_active": False, "nickname": "Off"},
        ],
    })
    with patch.object(h, "_extract_userinfo", new=AsyncMock(return_value=fake_userinfo)), \
         patch("api.auth.oauth_providers.oauth") as mock_oauth:
        mock_client = MagicMock()
        mock_client.authorize_access_token = AsyncMock(return_value={})
        mock_oauth.create_client.return_value = mock_client

        with pytest.raises(h.OAuthCallbackError) as exc:
            await h.handle_oauth_callback("google", _FakeRequest(), conn, "/")
        assert exc.value.error_code == "account_disabled"
