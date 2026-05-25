"""Authlib OAuth 클라이언트 등록 검증."""
from unittest.mock import patch, MagicMock

import pytest


def test_register_google_when_client_id_set():
    from shared.config import AuthConfig
    from api.auth import oauth_providers

    # 리셋
    oauth_providers.oauth = oauth_providers._build_oauth()

    cfg = AuthConfig()
    object.__setattr__(cfg, "oauth_enabled", True)
    object.__setattr__(cfg, "google_client_id", "test-google-id")
    object.__setattr__(cfg, "google_client_secret", "test-google-secret")
    object.__setattr__(cfg, "google_redirect_uri", "http://localhost:8000/auth/oauth/google/callback")
    object.__setattr__(cfg, "kakao_client_id", "")

    oauth_providers.register_providers(cfg)
    assert oauth_providers.oauth.create_client("google") is not None


def test_skip_google_when_client_id_empty():
    from shared.config import AuthConfig
    from api.auth import oauth_providers

    oauth_providers.oauth = oauth_providers._build_oauth()

    cfg = AuthConfig()
    object.__setattr__(cfg, "oauth_enabled", True)
    object.__setattr__(cfg, "google_client_id", "")
    object.__setattr__(cfg, "kakao_client_id", "")

    oauth_providers.register_providers(cfg)
    assert oauth_providers.oauth.create_client("google") is None


def test_register_kakao_when_client_id_set():
    from shared.config import AuthConfig
    from api.auth import oauth_providers

    oauth_providers.oauth = oauth_providers._build_oauth()

    cfg = AuthConfig()
    object.__setattr__(cfg, "oauth_enabled", True)
    object.__setattr__(cfg, "google_client_id", "")
    object.__setattr__(cfg, "kakao_client_id", "test-kakao-id")
    object.__setattr__(cfg, "kakao_client_secret", "")
    object.__setattr__(cfg, "kakao_redirect_uri", "http://localhost:8000/auth/oauth/kakao/callback")

    oauth_providers.register_providers(cfg)
    assert oauth_providers.oauth.create_client("kakao") is not None


def test_skip_all_when_oauth_disabled():
    from shared.config import AuthConfig
    from api.auth import oauth_providers

    oauth_providers.oauth = oauth_providers._build_oauth()

    cfg = AuthConfig()
    object.__setattr__(cfg, "oauth_enabled", False)
    object.__setattr__(cfg, "google_client_id", "x")
    object.__setattr__(cfg, "kakao_client_id", "y")

    oauth_providers.register_providers(cfg)
    assert oauth_providers.oauth.create_client("google") is None
    assert oauth_providers.oauth.create_client("kakao") is None
