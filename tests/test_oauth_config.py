"""OAuth 환경변수 → AuthConfig 매핑 검증."""
import os
import pytest
from unittest.mock import patch

from shared.config import AuthConfig


def test_oauth_disabled_by_default():
    """OAUTH_ENABLED 미설정 시 false (회귀 차단)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("OAUTH_") and not k.startswith("GOOGLE_") and not k.startswith("KAKAO_")}
    with patch.dict(os.environ, env, clear=True):
        cfg = AuthConfig()
        assert cfg.oauth_enabled is False


def test_oauth_enabled_from_env():
    with patch.dict(os.environ, {"OAUTH_ENABLED": "true"}, clear=False):
        cfg = AuthConfig()
        assert cfg.oauth_enabled is True


def test_google_credentials_loaded():
    with patch.dict(os.environ, {
        "GOOGLE_CLIENT_ID": "test-google-id",
        "GOOGLE_CLIENT_SECRET": "test-google-secret",
        "GOOGLE_REDIRECT_URI": "http://localhost:8000/auth/oauth/google/callback",
    }, clear=False):
        cfg = AuthConfig()
        assert cfg.google_client_id == "test-google-id"
        assert cfg.google_client_secret == "test-google-secret"
        assert cfg.google_redirect_uri == "http://localhost:8000/auth/oauth/google/callback"


def test_kakao_credentials_loaded():
    with patch.dict(os.environ, {
        "KAKAO_CLIENT_ID": "test-kakao-id",
        "KAKAO_CLIENT_SECRET": "",
        "KAKAO_REDIRECT_URI": "http://localhost:8000/auth/oauth/kakao/callback",
    }, clear=False):
        cfg = AuthConfig()
        assert cfg.kakao_client_id == "test-kakao-id"
        assert cfg.kakao_client_secret == ""
        assert cfg.kakao_redirect_uri == "http://localhost:8000/auth/oauth/kakao/callback"


def test_provider_active_only_when_client_id_set():
    """*_CLIENT_ID 가 비어있으면 해당 provider 비활성."""
    with patch.dict(os.environ, {"OAUTH_ENABLED": "true", "GOOGLE_CLIENT_ID": "", "KAKAO_CLIENT_ID": "kakao-id"}, clear=False):
        cfg = AuthConfig()
        assert cfg.google_active is False
        assert cfg.kakao_active is True


def test_session_secret_required_when_oauth_enabled():
    """OAUTH_ENABLED=true 인데 OAUTH_SESSION_SECRET 비어있으면 validate 가 RuntimeError."""
    with patch.dict(os.environ, {
        "OAUTH_ENABLED": "true",
        "OAUTH_SESSION_SECRET": "",
    }, clear=False):
        cfg = AuthConfig()
        with pytest.raises(RuntimeError, match="OAUTH_SESSION_SECRET"):
            cfg.validate_oauth()


def test_session_secret_present_passes_validation():
    with patch.dict(os.environ, {
        "OAUTH_ENABLED": "true",
        "OAUTH_SESSION_SECRET": "a" * 64,
    }, clear=False):
        cfg = AuthConfig()
        cfg.validate_oauth()  # raises if invalid
