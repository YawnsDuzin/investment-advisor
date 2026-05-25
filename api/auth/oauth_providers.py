"""Authlib OAuth 클라이언트 등록 — Google + Kakao.

`register_providers(cfg)` 를 앱 lifespan startup 에서 호출.
provider 별 `*_CLIENT_ID` 가 비어있으면 등록 skip → UI 버튼도 자동 숨김.
"""
from authlib.integrations.starlette_client import OAuth

from shared.config import AuthConfig


def _build_oauth() -> OAuth:
    """새 OAuth 인스턴스 생성 (테스트 격리용)."""
    return OAuth()


# 전역 OAuth 인스턴스 — 라우트에서 import 해서 사용
oauth: OAuth = _build_oauth()


def register_providers(cfg: AuthConfig) -> None:
    """AuthConfig 에 따라 provider 를 oauth 인스턴스에 등록.

    멱등 — 이미 등록된 provider 는 재등록 안 함.
    """
    if not cfg.oauth_enabled:
        return

    if cfg.google_client_id and oauth.create_client("google") is None:
        oauth.register(
            name="google",
            client_id=cfg.google_client_id,
            client_secret=cfg.google_client_secret,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )

    if cfg.kakao_client_id and oauth.create_client("kakao") is None:
        oauth.register(
            name="kakao",
            client_id=cfg.kakao_client_id,
            client_secret=cfg.kakao_client_secret or "",
            access_token_url="https://kauth.kakao.com/oauth/token",
            authorize_url="https://kauth.kakao.com/oauth/authorize",
            api_base_url="https://kapi.kakao.com/",
            client_kwargs={"scope": "account_email profile_nickname"},
        )
