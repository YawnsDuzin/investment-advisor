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
