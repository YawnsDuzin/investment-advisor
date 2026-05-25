"""OAuth 라우트 — start + callback."""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from fastapi.testclient import TestClient


def _make_test_app():
    """OAuth 라우트만 마운트한 최소 FastAPI 앱."""
    from fastapi import FastAPI
    from starlette.middleware.sessions import SessionMiddleware
    from api.routes import auth_oauth

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-" + "a" * 32)
    app.include_router(auth_oauth.router)
    return app


def test_unknown_provider_returns_404():
    app = _make_test_app()
    with TestClient(app) as client:
        r = client.get("/auth/oauth/naver/start", follow_redirects=False)
        assert r.status_code == 404


def test_start_redirects_to_provider_authorize_url():
    app = _make_test_app()
    with TestClient(app) as client, \
         patch("api.auth.oauth_providers.oauth") as mock_oauth:
        mock_client = MagicMock()
        async def fake_redirect(request, redirect_uri, **kwargs):
            from starlette.responses import RedirectResponse
            return RedirectResponse("https://accounts.google.com/o/oauth2/auth?...", status_code=302)
        mock_client.authorize_redirect = AsyncMock(side_effect=fake_redirect)
        mock_oauth.create_client.return_value = mock_client

        r = client.get("/auth/oauth/google/start", follow_redirects=False)
        assert r.status_code == 302
        assert "accounts.google.com" in r.headers["location"]


def test_start_rejects_external_next_url():
    """open redirect 방지 — next 가 / 로 시작 안 하면 / 로 폴백."""
    app = _make_test_app()
    with TestClient(app) as client, \
         patch("api.auth.oauth_providers.oauth") as mock_oauth:
        captured = {}
        mock_client = MagicMock()
        async def fake_redirect(request, redirect_uri, **kwargs):
            captured["next"] = request.session.get("oauth_next_url")
            from starlette.responses import RedirectResponse
            return RedirectResponse("https://provider/auth", status_code=302)
        mock_client.authorize_redirect = AsyncMock(side_effect=fake_redirect)
        mock_oauth.create_client.return_value = mock_client

        r = client.get("/auth/oauth/google/start?next=https://evil.com/steal", follow_redirects=False)
        assert r.status_code == 302
        assert captured["next"] == "/"


def _override_db_conn(app, fake_conn=None):
    """FastAPI dependency_overrides 패턴 — get_db_conn 을 fake conn 으로 교체."""
    from api.deps import get_db_conn
    conn = fake_conn if fake_conn is not None else MagicMock()
    app.dependency_overrides[get_db_conn] = lambda: conn
    return conn


def test_callback_success_sets_cookies_and_redirects():
    from api.auth.oauth_handlers import OAuthCallbackError

    app = _make_test_app()
    fake_conn = _override_db_conn(app)
    fake_user = {"id": 1, "role": "user", "is_active": True, "email": "u@x.com", "nickname": "U"}
    with TestClient(app) as client, \
         patch("api.routes.auth_oauth.handle_oauth_callback", new=AsyncMock(return_value=(fake_user, "/dashboard"))):
        r = client.get("/auth/oauth/google/callback?code=fake&state=fake", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/dashboard"
        cookies = r.headers.get("set-cookie", "")
        assert "access_token" in cookies or "Set-Cookie" in r.headers


def test_callback_oauth_error_redirects_with_error_param():
    from api.auth.oauth_handlers import OAuthCallbackError

    app = _make_test_app()
    _override_db_conn(app)
    with TestClient(app) as client, \
         patch("api.routes.auth_oauth.handle_oauth_callback",
               new=AsyncMock(side_effect=OAuthCallbackError("kakao_email_required"))):
        r = client.get("/auth/oauth/kakao/callback?code=fake&state=fake", follow_redirects=False)
        assert r.status_code == 302
        assert "/auth/login" in r.headers["location"]
        assert "error=kakao_email_required" in r.headers["location"]


def test_link_requires_login():
    """로그인 없이 /link 호출 → 401 또는 redirect."""
    app = _make_test_app()
    with TestClient(app) as client:
        r = client.post("/auth/oauth/google/link", follow_redirects=False)
        assert r.status_code in (401, 302, 403)


def test_link_unlink_routes_registered():
    """라우트 등록 자체만 검증 (dependency override 복잡 — 함수 가드는 Task 5 단위 테스트로 검증함)."""
    app = _make_test_app()
    routes = [getattr(r, "path", None) for r in app.routes]
    assert "/auth/oauth/{provider}/unlink" in routes
    assert "/auth/oauth/{provider}/link" in routes


def test_unlink_refuses_when_can_unlink_false(monkeypatch):
    """can_unlink=false → /profile?error=last_login_method 로 redirect."""
    from api.auth.models import UserInDB
    from api.auth.dependencies import get_current_user_required
    from api.deps import get_db_conn
    import api.auth.oauth_handlers as h
    from datetime import datetime

    app = _make_test_app()
    fake_user = UserInDB(id=1, email="solo@x.com", nickname="Solo", role="user",
                         tier="free", is_active=True, created_at=datetime.now())
    fake_conn = MagicMock()
    app.dependency_overrides[get_current_user_required] = lambda: fake_user
    app.dependency_overrides[get_db_conn] = lambda: fake_conn

    monkeypatch.setattr(h, "_can_unlink", lambda conn, uid, provider: False)

    with TestClient(app) as client:
        r = client.post("/auth/oauth/google/unlink", follow_redirects=False)
        assert r.status_code == 302
        assert "/profile" in r.headers["location"]
        assert "error=last_login_method" in r.headers["location"]
