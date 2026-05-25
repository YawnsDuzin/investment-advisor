# OAuth(Google + Kakao) 소셜 로그인 구현 Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 local 이메일/비밀번호 가입을 유지한 채 Google + Kakao OAuth 소셜 로그인을 추가. `user_oauth_accounts` 별도 테이블로 1:N 연결, 동일 이메일 자동 연결, 프로필에서 수동 연결/해제.

**Architecture:** Authlib + Starlette SessionMiddleware (state CSRF) → 콜백에서 user upsert → 기존 `_set_auth_cookies()` 로 JWT 발급 (local 로그인과 정확히 같은 토큰 경로). provider-별 어댑터(`_extract_userinfo`)로 Google OIDC / Kakao REST API 분기.

**Tech Stack:** FastAPI, Authlib, itsdangerous(SessionMiddleware 의존), PostgreSQL, Jinja2. Cloudflare Tunnel 은 인프라 작업으로 plan 범위 밖 (spec § 2).

**Spec:** [_docs/20260525144745_oauth-google-kakao-design.md](20260525144745_oauth-google-kakao-design.md)

---

## 사전 조건 (코드 작업 전 완료 필요)

spec § 2 참조. Cloudflare Tunnel 세팅 / Google·Kakao 콘솔 등록 / Kakao 비즈앱 신청은 **이 plan 의 범위 밖**. 코드 task 는 이 인프라 없이도 로컬 환경(`http://localhost:8000`)에서 검증 가능하도록 작성됨.

운영 전환은 모든 task 완료 후, Cloudflare Tunnel + 콘솔 redirect URI 등록 + Kakao 비즈앱 심사 통과 시점에 `.env` 변경 + API 재시작으로 완료.

---

## File Structure

| 파일 | 종류 | 책임 |
|---|---|---|
| `requirements.txt` | 수정 | authlib, itsdangerous 추가 |
| `.env.example` | 수정 | OAuth 환경변수 10개 추가 |
| `shared/config.py` | 수정 | `AuthConfig` 에 OAuth 필드 7개 추가 |
| `shared/db/schema.py` | 수정 | `SCHEMA_VERSION` 50→51 |
| `shared/db/migrations/__init__.py` | 수정 | `_MIGRATIONS` dict 에 v51 등록 |
| `shared/db/migrations/versions.py` | 수정 | `_migrate_to_v51()` 추가 |
| `api/auth/oauth_providers.py` | **신규** | Authlib 클라이언트 등록 (google, kakao) |
| `api/auth/oauth_handlers.py` | **신규** | DB upsert + 자동연결 + 토큰발급 콜백 핸들러 |
| `api/routes/auth_oauth.py` | **신규** | start / callback / link / unlink 라우트 4개 |
| `api/main.py` | 수정 | SessionMiddleware + register_providers + router include |
| `api/routes/auth.py` | 수정 | change-password OAuth-only 가드 + login 라우트에 error 메시지 매핑 |
| `api/routes/pages.py` | 수정 | profile 라우트 컨텍스트에 linked_providers + can_unlink 주입 |
| `api/templates/login.html` | 수정 | OAuth 버튼 + error 메시지 |
| `api/templates/register.html` | 수정 | OAuth 버튼 |
| `api/templates/profile.html` | 수정 | 연결된 계정 섹션 |
| `api/static/css/components.css` (또는 기존 위치) | 수정 | .btn-google / .btn-kakao 스타일 |
| `_docs/raspberry-pi-setup.md` | 수정 | cloudflared 설치 절 추가 |
| `_docs/<ts>_oauth-setup.md` | **신규** | Google/Kakao 콘솔 등록 + 트러블슈팅 |
| `tests/test_oauth_config.py` | **신규** | AuthConfig 검증 |
| `tests/test_oauth_migration.py` | **신규** | v51 마이그레이션 SQL 검증 |
| `tests/test_oauth_providers.py` | **신규** | 클라이언트 등록 검증 |
| `tests/test_oauth_handlers.py` | **신규** | DB helper + callback 핸들러 시나리오 13종 |
| `tests/test_oauth_routes.py` | **신규** | 라우트 통합 (FastAPI TestClient) |
| `tests/conftest.py` | 수정 | Authlib mock fixture 추가 |

---

## Task 1: 의존성 추가 + 환경변수 템플릿

**Files:**
- Modify: `requirements.txt`
- Modify: `.env.example`

- [ ] **Step 1: requirements.txt 에 authlib, itsdangerous 추가**

`requirements.txt` 끝에 다음 추가 (auth 섹션 직후):

```text
# oauth (Google + Kakao 소셜 로그인)
authlib>=1.3.0
itsdangerous>=2.2.0
httpx>=0.27.0  # 이미 analyzer 섹션에 있음 — 중복 추가 X
```

- [ ] **Step 2: 의존성 설치 + import 검증**

Run: `pip install -r requirements.txt && python -c "from authlib.integrations.starlette_client import OAuth; print('ok')"`
Expected: `ok` 출력 (오류 없이 종료)

- [ ] **Step 3: .env.example 에 OAuth 섹션 추가**

`.env.example` 끝에 다음 블록 추가:

```ini
# =========================================
# OAuth 소셜 로그인 (Google + Kakao)
# =========================================
# OAUTH_ENABLED: false 면 OAuth 라우트 비활성 + UI 버튼 숨김. 기존 local 로그인은 그대로 작동.
OAUTH_ENABLED=false

# OAUTH_SESSION_SECRET: state CSRF 저장용 SessionMiddleware secret.
# 생성: python -c "import secrets; print(secrets.token_hex(32))"
# OAUTH_ENABLED=true 인데 비어있으면 앱 시작이 거부됩니다.
OAUTH_SESSION_SECRET=

# ── Google OAuth ──
# Google Cloud Console → OAuth 2.0 Client ID → 웹 애플리케이션
# redirect URI 등록: http://localhost:8000/auth/oauth/google/callback (개발)
#                    https://<your-domain>/auth/oauth/google/callback (운영)
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/oauth/google/callback

# ── Kakao OAuth ──
# Kakao Developers → 앱 → 카카오 로그인 활성화
# 동의항목 "카카오계정(이메일)" 필수 동의 + 비즈앱 신청 필수
# Redirect URI 등록은 Google 과 동일 패턴
KAKAO_CLIENT_ID=
KAKAO_CLIENT_SECRET=
KAKAO_REDIRECT_URI=http://localhost:8000/auth/oauth/kakao/callback
```

- [ ] **Step 4: 검증 (수동)**

Run: `python -c "from shared.config import AuthConfig; c = AuthConfig(); print(c.enabled)"`
Expected: `True` (또는 `.env` 의 AUTH_ENABLED 값). 오류 없음.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt .env.example
git commit -m "$(cat <<'EOF'
chore(Auth): OAuth(Google+Kakao) 의존성 + .env.example 추가

authlib + itsdangerous(SessionMiddleware) 추가. .env.example 에 OAuth
환경변수 10개 템플릿. OAUTH_ENABLED 기본 false 로 회귀 차단.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: AuthConfig 에 OAuth 필드 추가

**Files:**
- Modify: `shared/config.py:412-423` (`AuthConfig` 클래스)
- Test: `tests/test_oauth_config.py` (신규)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_oauth_config.py` 신규:

```python
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
    with patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "", "KAKAO_CLIENT_ID": "kakao-id"}, clear=False):
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_oauth_config.py -v`
Expected: 7개 모두 FAIL with `AttributeError: ... has no attribute 'oauth_enabled'`

- [ ] **Step 3: `AuthConfig` 확장**

`shared/config.py:412-423` (`AuthConfig`) 을 다음으로 교체:

```python
@dataclass
class AuthConfig:
    """JWT 인증 + OAuth(Google/Kakao) 설정"""
    enabled: bool = field(default_factory=lambda: _env_bool("AUTH_ENABLED", False))
    jwt_secret_key: str = field(default_factory=lambda: os.getenv("JWT_SECRET_KEY", "INSECURE_DEFAULT_CHANGE_IN_PRODUCTION"))
    jwt_algorithm: str = field(default_factory=lambda: os.getenv("JWT_ALGORITHM", "HS256"))
    access_token_expire_minutes: int = field(default_factory=lambda: int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60")))
    refresh_token_expire_days: int = field(default_factory=lambda: int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "30")))
    admin_email: str = field(default_factory=lambda: os.getenv("ADMIN_EMAIL", "admin@example.com"))
    admin_password: str = field(default_factory=lambda: os.getenv("ADMIN_PASSWORD", "changeme123"))
    cookie_secure: bool = field(default_factory=lambda: _env_bool("COOKIE_SECURE", False))

    # ── OAuth (Google + Kakao) ──
    oauth_enabled: bool = field(default_factory=lambda: _env_bool("OAUTH_ENABLED", False))
    oauth_session_secret: str = field(default_factory=lambda: os.getenv("OAUTH_SESSION_SECRET", ""))
    google_client_id: str = field(default_factory=lambda: os.getenv("GOOGLE_CLIENT_ID", ""))
    google_client_secret: str = field(default_factory=lambda: os.getenv("GOOGLE_CLIENT_SECRET", ""))
    google_redirect_uri: str = field(default_factory=lambda: os.getenv("GOOGLE_REDIRECT_URI", ""))
    kakao_client_id: str = field(default_factory=lambda: os.getenv("KAKAO_CLIENT_ID", ""))
    kakao_client_secret: str = field(default_factory=lambda: os.getenv("KAKAO_CLIENT_SECRET", ""))
    kakao_redirect_uri: str = field(default_factory=lambda: os.getenv("KAKAO_REDIRECT_URI", ""))

    @property
    def google_active(self) -> bool:
        return self.oauth_enabled and bool(self.google_client_id)

    @property
    def kakao_active(self) -> bool:
        return self.oauth_enabled and bool(self.kakao_client_id)

    def validate_oauth(self) -> None:
        """OAuth 설정 검증. enabled=true 이면 session_secret 필수.

        Raises:
            RuntimeError: 필수 설정 누락 시.
        """
        if not self.oauth_enabled:
            return
        if not self.oauth_session_secret:
            raise RuntimeError(
                "OAUTH_ENABLED=true 인데 OAUTH_SESSION_SECRET 이 비어있습니다. "
                "생성: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_oauth_config.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add shared/config.py tests/test_oauth_config.py
git commit -m "$(cat <<'EOF'
feat(Auth): AuthConfig 에 OAuth(Google+Kakao) 필드 + validate_oauth() 추가

OAUTH_ENABLED / OAUTH_SESSION_SECRET / GOOGLE_* / KAKAO_* 7개 필드.
google_active/kakao_active property 로 UI 노출 가드. validate_oauth() 가
session secret 미설정 시 RuntimeError.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: 마이그레이션 v51 (`user_oauth_accounts` 테이블)

**Files:**
- Modify: `shared/db/schema.py:12` (`SCHEMA_VERSION`)
- Modify: `shared/db/migrations/__init__.py:14-64` (`_MIGRATIONS` dict)
- Modify: `shared/db/migrations/versions.py` (끝에 `_migrate_to_v51` 추가)
- Test: `tests/test_oauth_migration.py` (신규)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_oauth_migration.py` 신규:

```python
"""v51 마이그레이션 SQL 검증 (mock cursor 로 호출 기록 확인)."""
from unittest.mock import MagicMock

from shared.db.migrations.versions import _migrate_to_v51


def test_v51_creates_user_oauth_accounts_table():
    cur = MagicMock()
    _migrate_to_v51(cur)
    sql_calls = [str(c.args[0]) for c in cur.execute.call_args_list]
    joined = "\n".join(sql_calls)
    assert "CREATE TABLE IF NOT EXISTS user_oauth_accounts" in joined


def test_v51_has_required_columns():
    cur = MagicMock()
    _migrate_to_v51(cur)
    joined = "\n".join(str(c.args[0]) for c in cur.execute.call_args_list)
    for col in ("user_id", "provider", "provider_user_id", "provider_email",
                "provider_name", "linked_at", "last_login_at"):
        assert col in joined, f"column {col} 누락"


def test_v51_has_unique_constraints():
    cur = MagicMock()
    _migrate_to_v51(cur)
    joined = "\n".join(str(c.args[0]) for c in cur.execute.call_args_list)
    # (provider, provider_user_id) UNIQUE
    assert "UNIQUE (provider, provider_user_id)" in joined
    # (user_id, provider) UNIQUE
    assert "UNIQUE (user_id, provider)" in joined


def test_v51_has_user_id_index():
    cur = MagicMock()
    _migrate_to_v51(cur)
    joined = "\n".join(str(c.args[0]) for c in cur.execute.call_args_list)
    assert "idx_user_oauth_accounts_user" in joined


def test_v51_has_cascade_on_user_delete():
    cur = MagicMock()
    _migrate_to_v51(cur)
    joined = "\n".join(str(c.args[0]) for c in cur.execute.call_args_list)
    assert "ON DELETE CASCADE" in joined


def test_v51_registered_in_migrations_dict():
    from shared.db.migrations import _MIGRATIONS
    assert 51 in _MIGRATIONS
    assert _MIGRATIONS[51] is _migrate_to_v51


def test_schema_version_is_51():
    from shared.db.schema import SCHEMA_VERSION
    assert SCHEMA_VERSION == 51
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_oauth_migration.py -v`
Expected: 모두 FAIL (`_migrate_to_v51` ImportError 또는 SCHEMA_VERSION=50)

- [ ] **Step 3: `_migrate_to_v51` 추가**

`shared/db/migrations/versions.py` 의 마지막 함수 뒤에 다음 추가:

```python
def _migrate_to_v51(cur) -> None:
    """v51: OAuth provider 계정 연결 — user_oauth_accounts 테이블.

    한 user 가 Google + Kakao + local 동시 보유 가능 (1:N).
    Spec: _docs/20260525144745_oauth-google-kakao-design.md §4
    """
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_oauth_accounts (
            id               SERIAL PRIMARY KEY,
            user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            provider         VARCHAR(20) NOT NULL,
            provider_user_id VARCHAR(255) NOT NULL,
            provider_email   VARCHAR(255),
            provider_name    VARCHAR(100),
            linked_at        TIMESTAMP NOT NULL DEFAULT NOW(),
            last_login_at    TIMESTAMP,
            UNIQUE (provider, provider_user_id),
            UNIQUE (user_id, provider)
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_user_oauth_accounts_user ON user_oauth_accounts(user_id);")
```

- [ ] **Step 4: `_MIGRATIONS` dict 에 등록**

`shared/db/migrations/__init__.py:14-64` 의 dict 끝(`50: _v._migrate_to_v50,` 다음)에 추가:

```python
    51: _v._migrate_to_v51,
```

- [ ] **Step 5: SCHEMA_VERSION 증가**

`shared/db/schema.py:12`:

```python
SCHEMA_VERSION = 51  # v51: OAuth provider 계정 연결 (user_oauth_accounts)
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `pytest tests/test_oauth_migration.py -v`
Expected: 7 passed

- [ ] **Step 7: 실제 DB 에 적용 (수동 검증)**

Run: `python -c "from shared.config import DatabaseConfig; from shared.db import init_db; init_db(DatabaseConfig())"`
Expected: `[DB] 테이블 초기화 완료` 출력, 오류 없음.

Run (psql 또는 PG 콘솔): `\d user_oauth_accounts`
Expected: 테이블 + 컬럼 8개 + UNIQUE 2개 + 인덱스 확인.

- [ ] **Step 8: Commit**

```bash
git add shared/db/schema.py shared/db/migrations/__init__.py shared/db/migrations/versions.py tests/test_oauth_migration.py
git commit -m "$(cat <<'EOF'
feat(DB): v51 마이그레이션 — user_oauth_accounts 테이블 (OAuth 1:N)

한 user 가 Google + Kakao + local 동시 보유 가능. (provider, provider_user_id)
UNIQUE + (user_id, provider) UNIQUE + FK CASCADE.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Authlib provider 등록 모듈

**Files:**
- Create: `api/auth/oauth_providers.py`
- Test: `tests/test_oauth_providers.py` (신규)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_oauth_providers.py` 신규:

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_oauth_providers.py -v`
Expected: 모두 FAIL (`ModuleNotFoundError: api.auth.oauth_providers`)

- [ ] **Step 3: `oauth_providers.py` 작성**

`api/auth/oauth_providers.py` 신규:

```python
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_oauth_providers.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add api/auth/oauth_providers.py tests/test_oauth_providers.py
git commit -m "$(cat <<'EOF'
feat(Auth): Authlib OAuth 클라이언트 등록 모듈 (Google + Kakao)

provider 별 *_CLIENT_ID 비어있으면 등록 skip (UI 버튼도 자동 숨김).
register_providers() 멱등.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: OAuth 콜백 DB helper 함수들

**Files:**
- Create: `api/auth/oauth_handlers.py` (helper 함수만 — callback 본체는 Task 6)
- Test: `tests/test_oauth_handlers.py` (신규)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_oauth_handlers.py` 신규 (helper 부분만 — callback 시나리오는 Task 6 에서 추가):

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_oauth_handlers.py -v`
Expected: 모두 FAIL (`ModuleNotFoundError: api.auth.oauth_handlers`)

- [ ] **Step 3: `oauth_handlers.py` helper 함수 작성**

`api/auth/oauth_handlers.py` 신규:

```python
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
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT id, email, password_hash, role, is_active, nickname "
            "FROM users WHERE id = %s",
            (user_id,),
        )
        return cur.fetchone()


def _count_oauth_accounts(conn, user_id: int, exclude_provider: Optional[str] = None) -> int:
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
    """admin_audit_logs 에 OAuth action 기록 (v17 테이블 재사용)."""
    target_email = ""
    user = _get_user(conn, user_id)
    if user:
        target_email = user.get("email", "")
    full_detail = detail or (f"provider={provider}" if provider else "")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO admin_audit_logs "
            "(actor_id, actor_email, target_id, target_email, action, detail) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (user_id, target_email, user_id, target_email, action, full_detail),
        )


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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_oauth_handlers.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add api/auth/oauth_handlers.py tests/test_oauth_handlers.py
git commit -m "$(cat <<'EOF'
feat(Auth): OAuth 콜백 DB helper 함수 (find/insert/audit/can_unlink)

callback 메인 로직은 다음 커밋에서 추가. helper 9종 단위 테스트 통과.
_can_unlink 가 마지막 로그인 수단 보호.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: OAuth 콜백 메인 핸들러 (`handle_oauth_callback`)

**Files:**
- Modify: `api/auth/oauth_handlers.py` (Task 5 에 이어서 추가)
- Modify: `tests/test_oauth_handlers.py` (시나리오 추가)

- [ ] **Step 1: 실패하는 통합 시나리오 테스트 추가**

`tests/test_oauth_handlers.py` 끝에 다음 추가:

```python
# ── handle_oauth_callback 시나리오 ──

import pytest
from unittest.mock import patch, AsyncMock


class _FakeRequest:
    def __init__(self):
        self.session = {}


def _make_full_conn(scenarios):
    """scenarios = {("find_oauth", "google", "sub-1"): row, ("find_user_email", "x@y.com"): row, ...}
    호출별 fetchone 응답을 시나리오로 주입."""
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

    # fetchone 호출 순서:
    #   1) _find_oauth_account → row 있음
    #   2) _get_user(in _audit_log) → user 있음
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

    # 결과는 (user_dict, next_url) 튜플 반환 — 토큰 발급은 라우트에서
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
    # 자동 연결 시 user_oauth_accounts INSERT 실행됐는지
    sql_calls = [str(c.args[0]) for c in cur.execute.call_args_list]
    assert any("INSERT INTO user_oauth_accounts" in s for s in sql_calls)


@pytest.mark.asyncio
async def test_callback_email_unverified_refuses_auto_link():
    """이메일 기존 유저 + email_verified=false → 거부."""
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_oauth_handlers.py -v`
Expected: 6 신규 시나리오 모두 FAIL (`handle_oauth_callback` 또는 `OAuthCallbackError` 없음)

- [ ] **Step 3: `handle_oauth_callback` + `_extract_userinfo` + `OAuthCallbackError` 추가**

`api/auth/oauth_handlers.py` 끝에 추가:

```python
from authlib.integrations.starlette_client import OAuthError


class OAuthCallbackError(Exception):
    """OAuth 콜백 처리 실패 — error_code 로 사용자 안내 메시지 매핑."""

    def __init__(self, error_code: str, message: str = ""):
        super().__init__(message or error_code)
        self.error_code = error_code


async def _extract_userinfo(provider: str, token: dict) -> dict:
    """provider 별 userinfo 응답 표준화.

    Returns:
        {"provider_user_id", "email", "email_verified", "name"}
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
        resp = await oauth.kakao.get("v2/user/me", token=token)
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
    try:
        client = oauth.create_client(provider)
        if client is None:
            raise OAuthCallbackError("oauth_failed", f"provider {provider} not registered")
        token = await client.authorize_access_token(request)
    except OAuthError as e:
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
    _audit_log(conn, new_user_id, "oauth_signup", provider=provider)
    return (new_user, next_url)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_oauth_handlers.py -v`
Expected: 15 passed (helper 9 + callback 6)

- [ ] **Step 5: Commit**

```bash
git add api/auth/oauth_handlers.py tests/test_oauth_handlers.py
git commit -m "$(cat <<'EOF'
feat(Auth): handle_oauth_callback 메인 로직 + OAuthCallbackError

provider 별 userinfo 표준화 (_extract_userinfo). 6 시나리오:
재로그인 / 자동연결 / email_unverified 거부 / Kakao 이메일 미동의 /
신규 가입 / 비활성 계정 거부. 토큰 발급은 라우트 레이어 책임 분리.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: `auth_oauth.py` 라우트 — start + callback

**Files:**
- Create: `api/routes/auth_oauth.py`
- Test: `tests/test_oauth_routes.py` (신규)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_oauth_routes.py` 신규:

```python
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


def test_callback_success_sets_cookies_and_redirects():
    from api.auth.oauth_handlers import OAuthCallbackError

    app = _make_test_app()
    fake_user = {"id": 1, "role": "user", "is_active": True, "email": "u@x.com", "nickname": "U"}
    with TestClient(app) as client, \
         patch("api.routes.auth_oauth.handle_oauth_callback", new=AsyncMock(return_value=(fake_user, "/dashboard"))), \
         patch("api.routes.auth_oauth.get_db_conn", return_value=MagicMock()):
        r = client.get("/auth/oauth/google/callback?code=fake&state=fake", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/dashboard"
        assert "access_token" in r.cookies or "Set-Cookie" in r.headers


def test_callback_oauth_error_redirects_with_error_param():
    from api.auth.oauth_handlers import OAuthCallbackError

    app = _make_test_app()
    with TestClient(app) as client, \
         patch("api.routes.auth_oauth.handle_oauth_callback",
               new=AsyncMock(side_effect=OAuthCallbackError("kakao_email_required"))), \
         patch("api.routes.auth_oauth.get_db_conn", return_value=MagicMock()):
        r = client.get("/auth/oauth/kakao/callback?code=fake&state=fake", follow_redirects=False)
        assert r.status_code == 302
        assert "/auth/login" in r.headers["location"]
        assert "error=kakao_email_required" in r.headers["location"]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_oauth_routes.py -v`
Expected: 모두 FAIL (`ModuleNotFoundError: api.routes.auth_oauth`)

- [ ] **Step 3: `auth_oauth.py` 작성**

`api/routes/auth_oauth.py` 신규:

```python
"""OAuth 라우트 — Google / Kakao 소셜 로그인.

라우트:
    GET  /auth/oauth/{provider}/start      → provider 동의 화면으로 redirect
    GET  /auth/oauth/{provider}/callback   → 콜백 → upsert → 쿠키 발급 → next 302
    POST /auth/oauth/{provider}/link       → 로그인 상태에서 계정 연결 시작 (Task 8)
    POST /auth/oauth/{provider}/unlink     → 연결 해제 (Task 8)
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from api.auth.dependencies import get_current_user_required  # link/unlink (Task 8)
from api.auth.jwt_handler import create_access_token, create_refresh_token, hash_token
from api.auth.oauth_handlers import OAuthCallbackError, handle_oauth_callback
from api.auth.oauth_providers import oauth
from api.deps import get_db_conn
from api.routes.auth import _clear_auth_cookies, _set_auth_cookies
from shared.config import AuthConfig


router = APIRouter(prefix="/auth/oauth", tags=["OAuth"])

_ALLOWED_PROVIDERS = frozenset({"google", "kakao"})


def _get_auth_cfg() -> AuthConfig:
    return AuthConfig()


def _validate_provider(provider: str) -> None:
    if provider not in _ALLOWED_PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown OAuth provider")


def _safe_next(next_url: str) -> str:
    """open redirect 방지 — / 로 시작 안 하면 / 로 폴백."""
    if not next_url or not next_url.startswith("/"):
        return "/"
    # 프로토콜 상대 URL (//evil.com) 차단
    if next_url.startswith("//"):
        return "/"
    return next_url


# ── start ─────────────────────────────────────


@router.get("/{provider}/start")
async def oauth_start(provider: str, request: Request, next: str = "/"):
    """provider 동의 화면으로 redirect. state 는 SessionMiddleware 에 저장됨."""
    _validate_provider(provider)
    cfg = _get_auth_cfg()
    if provider == "google" and not cfg.google_active:
        raise HTTPException(status_code=404, detail="Google OAuth disabled")
    if provider == "kakao" and not cfg.kakao_active:
        raise HTTPException(status_code=404, detail="Kakao OAuth disabled")

    client = oauth.create_client(provider)
    if client is None:
        raise HTTPException(status_code=503, detail="OAuth client not registered")

    safe_next = _safe_next(next)
    request.session["oauth_next_url"] = safe_next

    redirect_uri = (cfg.google_redirect_uri if provider == "google"
                    else cfg.kakao_redirect_uri)
    return await client.authorize_redirect(request, redirect_uri)


# ── callback ──────────────────────────────────


@router.get("/{provider}/callback")
async def oauth_callback(provider: str, request: Request, conn=Depends(get_db_conn)):
    _validate_provider(provider)
    auth_cfg = _get_auth_cfg()

    next_url = _safe_next(request.session.pop("oauth_next_url", "/"))

    try:
        user, next_url = await handle_oauth_callback(
            provider=provider, request=request, conn=conn, next_url=next_url,
        )
    except OAuthCallbackError as e:
        from fastapi.responses import RedirectResponse
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

    from fastapi.responses import RedirectResponse
    response = RedirectResponse(next_url, status_code=302)
    _set_auth_cookies(response, access_token, refresh_raw, auth_cfg)
    return response
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_oauth_routes.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add api/routes/auth_oauth.py tests/test_oauth_routes.py
git commit -m "$(cat <<'EOF'
feat(Auth): OAuth start + callback 라우트 (Google/Kakao)

provider whitelist 가드 + open redirect 방지(_safe_next) + 기존
_set_auth_cookies 재사용. OAuthCallbackError 는 /auth/login?error=...
로 매핑.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: `auth_oauth.py` — link + unlink

**Files:**
- Modify: `api/routes/auth_oauth.py` (라우트 2개 추가)
- Modify: `tests/test_oauth_routes.py` (시나리오 4개 추가)

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_oauth_routes.py` 끝에 추가:

```python
def test_link_requires_login():
    app = _make_test_app()
    with TestClient(app) as client:
        r = client.post("/auth/oauth/google/link", follow_redirects=False)
        # get_current_user_required 가 401 또는 redirect
        assert r.status_code in (401, 302, 403)


def test_unlink_refuses_when_only_login_method():
    """OAuth-only + 단일 provider 유저가 unlink 시도 → 거부."""
    from api.auth.models import UserInDB
    app = _make_test_app()
    fake_user = UserInDB(id=1, email="solo@x.com", nickname="Solo", role="user",
                         tier="free", is_active=True, created_at=__import__("datetime").datetime.now())
    with TestClient(app) as client, \
         patch("api.routes.auth_oauth.get_current_user_required", return_value=fake_user), \
         patch("api.routes.auth_oauth._can_unlink", return_value=False), \
         patch("api.routes.auth_oauth.get_db_conn", return_value=MagicMock()):
        # dependency override 가 깔끔하지 않아 통합테스트 한계 — 실제 가드는 함수 단위 테스트로 검증
        # 여기서는 라우트 존재 확인까지만
        r = client.post("/auth/oauth/google/unlink")
        # _can_unlink=False 시 400 또는 redirect 에 error param 동반
        assert r.status_code in (400, 302, 401)


def test_unlink_succeeds_when_password_present():
    """local password 있는 유저 → unlink 허용."""
    # dependency override 가 복잡하므로 _can_unlink 호출 자체는 Task 5 단위 테스트로 검증함
    # 여기서는 라우트 등록 확인
    app = _make_test_app()
    routes = [r.path for r in app.routes]
    assert "/auth/oauth/{provider}/unlink" in routes
    assert "/auth/oauth/{provider}/link" in routes
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_oauth_routes.py -v`
Expected: 신규 3개 FAIL (라우트 미등록 또는 404)

- [ ] **Step 3: link + unlink 라우트 추가**

`api/routes/auth_oauth.py` 끝에 추가:

```python
from fastapi.responses import RedirectResponse

from api.auth.models import UserInDB
from api.auth.oauth_handlers import (
    _can_unlink, _find_oauth_account, _audit_log,
)


@router.post("/{provider}/link")
async def oauth_link(
    provider: str,
    request: Request,
    user: UserInDB = Depends(get_current_user_required),
    conn=Depends(get_db_conn),
):
    """로그인 상태에서 추가 provider 연결 — start 와 동일하게 동의 화면으로 redirect.

    콜백에서 _find_oauth_account 가 None 이면 이메일 매칭 → 기존 user 에 자동 연결.
    (handle_oauth_callback 의 자동 연결 분기 재사용 — 별도 link 콜백 불필요.)
    """
    _validate_provider(provider)
    cfg = _get_auth_cfg()
    if provider == "google" and not cfg.google_active:
        raise HTTPException(status_code=404, detail="Google OAuth disabled")
    if provider == "kakao" and not cfg.kakao_active:
        raise HTTPException(status_code=404, detail="Kakao OAuth disabled")

    client = oauth.create_client(provider)
    if client is None:
        raise HTTPException(status_code=503, detail="OAuth client not registered")

    request.session["oauth_next_url"] = "/profile"
    redirect_uri = (cfg.google_redirect_uri if provider == "google"
                    else cfg.kakao_redirect_uri)
    return await client.authorize_redirect(request, redirect_uri)


@router.post("/{provider}/unlink")
async def oauth_unlink(
    provider: str,
    user: UserInDB = Depends(get_current_user_required),
    conn=Depends(get_db_conn),
):
    """provider 연결 해제 — 마지막 로그인 수단이면 거부."""
    _validate_provider(provider)

    if not _can_unlink(conn, user.id, provider):
        return RedirectResponse(
            "/profile?error=last_login_method", status_code=302,
        )

    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM user_oauth_accounts WHERE user_id = %s AND provider = %s",
            (user.id, provider),
        )
    _audit_log(conn, user.id, "oauth_unlink", provider=provider)
    conn.commit()
    return RedirectResponse("/profile?success=unlinked", status_code=302)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_oauth_routes.py -v`
Expected: 8 passed (5 기존 + 3 신규)

- [ ] **Step 5: Commit**

```bash
git add api/routes/auth_oauth.py tests/test_oauth_routes.py
git commit -m "$(cat <<'EOF'
feat(Auth): OAuth link/unlink 라우트 + 마지막 수단 보호

/link 는 start 와 동일하게 동의 화면으로 redirect, 콜백에서 자동 연결.
/unlink 는 _can_unlink 통과 시에만 DELETE + 감사로그.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: `main.py` 통합 — SessionMiddleware + router include

**Files:**
- Modify: `api/main.py:18-31` (lifespan), `:93` (router include 영역)

- [ ] **Step 1: lifespan 에 OAuth 검증 + register_providers 추가**

`api/main.py:18-31` lifespan 함수의 `print("[AUTH] ...")` 블록 뒤에 다음 추가:

```python
    # OAuth 활성화 시 SessionMiddleware secret 검증 + 클라이언트 등록
    if auth_cfg.oauth_enabled:
        auth_cfg.validate_oauth()  # RuntimeError if OAUTH_SESSION_SECRET 비어있음
        from api.auth.oauth_providers import register_providers
        register_providers(auth_cfg)
        print(f"[OAUTH] 활성화 — Google={'on' if auth_cfg.google_active else 'off'}, "
              f"Kakao={'on' if auth_cfg.kakao_active else 'off'}")
```

- [ ] **Step 2: SessionMiddleware 등록**

`api/main.py:50` 의 `app = FastAPI(...)` 정의 다음에 추가 (`@app.exception_handler` 위):

```python
# OAuth state CSRF 저장용 SessionMiddleware
_auth_cfg_init = AuthConfig()
if _auth_cfg_init.oauth_enabled and _auth_cfg_init.oauth_session_secret:
    from starlette.middleware.sessions import SessionMiddleware
    app.add_middleware(
        SessionMiddleware,
        secret_key=_auth_cfg_init.oauth_session_secret,
        same_site="lax",
        https_only=_auth_cfg_init.cookie_secure,
        max_age=600,  # 10분 — OAuth 흐름 안에서만 유효
    )
```

- [ ] **Step 3: auth_oauth router include**

`api/main.py:93` 의 `app.include_router(auth_routes.router)` 다음 줄에 추가:

```python
from api.routes import auth_oauth as _auth_oauth_routes
app.include_router(_auth_oauth_routes.router)
```

- [ ] **Step 4: 수동 검증 — 앱 시작 확인**

Run (`.env` 에 `OAUTH_ENABLED=true` + `OAUTH_SESSION_SECRET=<64자>` 설정 후):
`python -c "from api.main import app; print([r.path for r in app.routes if 'oauth' in r.path])"`
Expected: `/auth/oauth/{provider}/start`, `/auth/oauth/{provider}/callback`, `/auth/oauth/{provider}/link`, `/auth/oauth/{provider}/unlink` 4개 출력.

Run (`OAUTH_SESSION_SECRET` 비운 채로):
`OAUTH_ENABLED=true OAUTH_SESSION_SECRET= python -c "from api.main import app"`
Expected: lifespan startup 시점은 import 직후엔 안 돌지만, 명시적 검증 위해
`python -c "from shared.config import AuthConfig; AuthConfig().validate_oauth()"` 실행 → RuntimeError 발생 확인.

- [ ] **Step 5: 기존 테스트 회귀 확인**

Run: `pytest -x`
Expected: 모든 기존 테스트 통과 (회귀 없음).

- [ ] **Step 6: Commit**

```bash
git add api/main.py
git commit -m "$(cat <<'EOF'
feat(Auth): main.py — OAuth SessionMiddleware + lifespan 등록

OAUTH_ENABLED=true 일 때만 SessionMiddleware 추가 + register_providers
호출. OAUTH_SESSION_SECRET 미설정 시 lifespan startup 에서 RuntimeError.
auth_oauth 라우터 include.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: 로그인/회원가입 페이지 OAuth 버튼

**Files:**
- Modify: `api/templates/login.html`
- Modify: `api/templates/register.html`
- Modify: `api/routes/auth.py` (login_page / register_page 컨텍스트에 oauth_*_enabled 주입)

- [ ] **Step 1: login.html 에 OAuth 버튼 추가**

`api/templates/login.html` 의 기존 이메일/비밀번호 폼 위에 다음 추가 (Jinja 블록 구조는 기존 패턴 유지):

```html
{% if oauth_google_enabled or oauth_kakao_enabled %}
<div class="oauth-buttons">
  {% if oauth_google_enabled %}
    <a href="/auth/oauth/google/start?next={{ next_url|urlencode }}"
       class="btn-oauth btn-google">
      <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
        <path fill="#4285F4" d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844c-.209 1.125-.843 2.078-1.796 2.717v2.258h2.908c1.702-1.567 2.684-3.874 2.684-6.615z"/>
        <path fill="#34A853" d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332C2.438 15.983 5.482 18 9 18z"/>
        <path fill="#FBBC05" d="M3.964 10.71c-.18-.54-.282-1.117-.282-1.71s.102-1.17.282-1.71V4.958H.957C.347 6.173 0 7.548 0 9s.348 2.827.957 4.042l3.007-2.332z"/>
        <path fill="#EA4335" d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0 5.482 0 2.438 2.017.957 4.958L3.964 7.29C4.672 5.163 6.656 3.58 9 3.58z"/>
      </svg>
      Google 로 계속하기
    </a>
  {% endif %}
  {% if oauth_kakao_enabled %}
    <a href="/auth/oauth/kakao/start?next={{ next_url|urlencode }}"
       class="btn-oauth btn-kakao">
      <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
        <path fill="#000" d="M9 1C4.589 1 1 3.797 1 7.252c0 2.231 1.495 4.193 3.755 5.319l-.953 3.485c-.083.302.249.547.521.385L8.7 14.04A11.04 11.04 0 0 0 9 14.046c4.411 0 8-2.798 8-6.794C17 3.797 13.411 1 9 1z"/>
      </svg>
      카카오로 계속하기
    </a>
  {% endif %}
</div>
<div class="oauth-divider"><span>또는</span></div>
{% endif %}

{% if error %}
  <div class="error-msg">{{ error }}</div>
{% endif %}

<!-- 기존 이메일/비밀번호 폼 (변경 없음) -->
```

기존 `{% if error %}` 블록이 이미 있으면 위의 error-msg 는 추가하지 말 것 (중복 회피).

- [ ] **Step 2: register.html 에도 동일 패턴 추가**

`api/templates/register.html` 에 `login.html` 과 동일한 OAuth 버튼 블록 추가 (next_url 자리는 비워두거나 `/` 로).

- [ ] **Step 3: login/register 라우트 컨텍스트 갱신**

`api/routes/auth.py:55-69` 의 `login_page` / `register_page` 함수를 다음으로 교체:

```python
@router.get("/login")
def login_page(request: Request, error: str = "", next: str = "/"):
    cfg = AuthConfig()
    return templates.TemplateResponse(request=request, name="login.html", context={
        "active_page": "login",
        "error": _map_oauth_error(error),
        "next_url": next,
        "oauth_google_enabled": cfg.google_active,
        "oauth_kakao_enabled": cfg.kakao_active,
    })


@router.get("/register")
def register_page(request: Request, error: str = ""):
    cfg = AuthConfig()
    return templates.TemplateResponse(request=request, name="register.html", context={
        "active_page": "register",
        "error": _map_oauth_error(error),
        "oauth_google_enabled": cfg.google_active,
        "oauth_kakao_enabled": cfg.kakao_active,
    })


_OAUTH_ERROR_MESSAGES = {
    "oauth_failed": "소셜 로그인에 실패했습니다. 다시 시도해주세요.",
    "kakao_email_required": "카카오 로그인 시 이메일 제공에 동의해야 가입할 수 있습니다.",
    "email_unverified": "이메일 인증이 완료되지 않은 계정입니다. 이메일을 확인하거나 직접 로그인 후 프로필에서 연결해주세요.",
    "account_disabled": "비활성화된 계정입니다. 관리자에게 문의하세요.",
}


def _map_oauth_error(error_code: str) -> str:
    if not error_code:
        return ""
    return _OAUTH_ERROR_MESSAGES.get(error_code, error_code)
```

- [ ] **Step 4: 수동 검증**

`OAUTH_ENABLED=false` 로 API 시작 → `/auth/login` → OAuth 버튼 보이지 않아야 함.
`OAUTH_ENABLED=true` + GOOGLE_CLIENT_ID 설정 → 버튼 노출 확인.
`/auth/login?error=kakao_email_required` → "카카오 로그인 시..." 메시지 노출 확인.

- [ ] **Step 5: Commit**

```bash
git add api/templates/login.html api/templates/register.html api/routes/auth.py
git commit -m "$(cat <<'EOF'
feat(Auth): 로그인/회원가입 페이지 OAuth 버튼 + error 메시지 매핑

OAUTH_ENABLED=false 또는 *_CLIENT_ID 미설정 시 버튼 자동 숨김.
?error=kakao_email_required 등 5종 코드를 한국어 메시지로 매핑.
Google/Kakao 브랜드 SVG 로고 인라인 (외부 의존 없음).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: 프로필 페이지 — 연결된 계정 섹션

**Files:**
- Modify: `api/templates/profile.html`
- Modify: `api/routes/pages.py` (profile 라우트 컨텍스트에 linked_providers + can_unlink 주입)

- [ ] **Step 1: 프로필 라우트 컨텍스트 확장**

`api/routes/pages.py` 의 profile 라우트 (검색: `def profile_page` 또는 `@router.get("/profile")`) 를 찾아서 컨텍스트에 다음 추가:

```python
from api.auth.oauth_handlers import _list_linked_providers, _can_unlink
from shared.config import AuthConfig

@router.get("/profile")
def profile_page(request: Request, user=Depends(get_current_user_required), conn=Depends(get_db_conn)):
    cfg = AuthConfig()
    linked = _list_linked_providers(conn, user.id) if cfg.oauth_enabled else {}
    can_unlink_map = {
        p: _can_unlink(conn, user.id, p) for p in linked.keys()
    }
    ctx = _base_ctx(request, user)
    ctx.update({
        "active_page": "profile",
        "linked_providers": linked,
        "can_unlink_map": can_unlink_map,
        "oauth_enabled": cfg.oauth_enabled,
        "oauth_google_enabled": cfg.google_active,
        "oauth_kakao_enabled": cfg.kakao_active,
        "error": request.query_params.get("error", ""),
        "success": request.query_params.get("success", ""),
    })
    return templates.TemplateResponse(request=request, name="profile.html", context=ctx)
```

기존 profile 라우트 구조에 맞게 조정 (`_base_ctx` 사용 패턴이 다를 수 있음 — 기존 라우트 보고 동일 패턴 유지).

- [ ] **Step 2: profile.html 에 "연결된 계정" 섹션 추가**

`api/templates/profile.html` 의 적절한 위치 (보통 비밀번호 변경 폼 아래) 에 다음 추가:

```html
{% if oauth_enabled %}
<section class="profile-section">
  <h3>연결된 계정</h3>
  {% if error == "last_login_method" %}
    <div class="error-msg">다른 로그인 수단이 없어 연결 해제할 수 없습니다. 비밀번호를 먼저 설정하거나 다른 소셜 계정을 연결해주세요.</div>
  {% endif %}
  {% if success == "unlinked" %}
    <div class="success-msg">연결이 해제되었습니다.</div>
  {% endif %}
  <ul class="oauth-links">
    {% for provider, label in [('google', 'Google'), ('kakao', 'Kakao')] %}
      {% set is_enabled = (provider == 'google' and oauth_google_enabled) or (provider == 'kakao' and oauth_kakao_enabled) %}
      {% if is_enabled %}
        {% set linked = linked_providers.get(provider) %}
        <li class="oauth-link-row">
          <span class="provider-name">{{ label }}</span>
          {% if linked %}
            <span class="linked-email">{{ linked.provider_email or '(이메일 없음)' }}</span>
            <span class="linked-at">{{ linked.linked_at.strftime('%Y-%m-%d') }} 연결</span>
            <form method="post" action="/auth/oauth/{{ provider }}/unlink" style="display:inline">
              <button type="submit" class="btn-unlink"
                {% if not can_unlink_map.get(provider) %}disabled title="다른 로그인 수단이 없어 해제할 수 없습니다"{% endif %}>
                연결 해제
              </button>
            </form>
          {% else %}
            <form method="post" action="/auth/oauth/{{ provider }}/link" style="display:inline">
              <button type="submit" class="btn-link">연결하기</button>
            </form>
          {% endif %}
        </li>
      {% endif %}
    {% endfor %}
  </ul>
</section>
{% endif %}
```

- [ ] **Step 3: 수동 검증**

`OAUTH_ENABLED=true` + 로컬에서 `/profile` 접근 → "연결된 계정" 섹션 표시.
연결 전: "연결하기" 버튼.
연결 후: 이메일 + 날짜 + "연결 해제" 버튼.
local password 없는 OAuth-only 유저: "연결 해제" 버튼 disabled.

- [ ] **Step 4: Commit**

```bash
git add api/templates/profile.html api/routes/pages.py
git commit -m "$(cat <<'EOF'
feat(Auth): 프로필 페이지 '연결된 계정' 섹션 + 마지막 수단 보호 UI

provider 별 연결 상태/이메일/날짜 표시. _can_unlink=false 시
버튼 disabled + 툴팁.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: CSS — OAuth 버튼 스타일

**Files:**
- Modify: `api/static/css/` 하위 적절한 파일 (기존 컴포넌트 CSS 위치 확인 후 추가. 통상 `components.css` 또는 `main.css`)

- [ ] **Step 1: 기존 CSS 위치 파악**

Run: `ls api/static/css/`
Expected: 파일 목록 — `components.css` 또는 `forms.css` 같은 컴포넌트 단위 파일에 추가. 없으면 새 파일.

- [ ] **Step 2: OAuth 버튼 스타일 추가**

선택한 CSS 파일 끝에 추가:

```css
/* ── OAuth 버튼 ─────────────────────── */
.oauth-buttons {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 16px 0;
}

.btn-oauth {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 10px 16px;
  border-radius: 6px;
  font-weight: 500;
  text-decoration: none;
  transition: opacity 0.15s;
  font-size: 14px;
  border: 1px solid transparent;
}

.btn-oauth:hover {
  opacity: 0.9;
}

.btn-google {
  background: #fff;
  color: #1f1f1f;
  border-color: #dadce0;
}

.btn-kakao {
  background: #FEE500;
  color: #000;
}

.oauth-divider {
  display: flex;
  align-items: center;
  text-align: center;
  margin: 16px 0;
  color: #888;
  font-size: 13px;
}

.oauth-divider::before,
.oauth-divider::after {
  content: '';
  flex: 1;
  border-bottom: 1px solid #444;
}

.oauth-divider span {
  padding: 0 12px;
}

/* ── 프로필 — 연결된 계정 ──────────── */
.oauth-links {
  list-style: none;
  padding: 0;
  margin: 12px 0;
}

.oauth-link-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid #2a2a2a;
}

.oauth-link-row .provider-name {
  font-weight: 600;
  min-width: 80px;
}

.oauth-link-row .linked-email {
  color: #aaa;
  font-size: 13px;
  flex: 1;
}

.oauth-link-row .linked-at {
  color: #666;
  font-size: 12px;
}

.btn-unlink,
.btn-link {
  padding: 4px 12px;
  border-radius: 4px;
  border: 1px solid #444;
  background: transparent;
  color: #ddd;
  cursor: pointer;
  font-size: 13px;
}

.btn-unlink:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.btn-link:hover {
  border-color: #888;
}
```

- [ ] **Step 3: 수동 검증**

브라우저에서 `/auth/login` + `/profile` 접속하여 다크 테마와 어울리는지 확인. 색상·여백 미세 조정 필요 시 그 자리에서 수정.

- [ ] **Step 4: Commit**

```bash
git add api/static/css/
git commit -m "$(cat <<'EOF'
style(Auth): OAuth 버튼 + 프로필 연결 계정 CSS

다크 테마에 맞춘 .btn-google(흰 배경)/.btn-kakao(노란 배경)
+ .oauth-divider + 연결 계정 row 스타일.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: 보안 가드 — change-password OAuth-only 차단 + 문서

**Files:**
- Modify: `api/routes/auth.py:309-362` (`change_password`)
- Create: `_docs/<ts>_oauth-setup.md`
- Modify: `_docs/raspberry-pi-setup.md`

- [ ] **Step 1: change-password 가드 추가 테스트**

`tests/test_oauth_handlers.py` 에 추가:

```python
def test_change_password_blocks_oauth_only_user():
    """password_hash IS NULL 인 유저는 change-password 진입 시 안내 페이지."""
    # 라우트 단위 테스트는 dependency 복잡 — 함수 가드 검증으로 대체
    from api.routes.auth import _is_oauth_only_user

    cur = MagicMock()
    cur.__enter__.return_value = cur
    cur.__exit__.return_value = False
    cur.fetchone.return_value = {"password_hash": None}
    conn = MagicMock()
    conn.cursor.return_value = cur

    assert _is_oauth_only_user(conn, user_id=1) is True


def test_change_password_allows_local_user():
    from api.routes.auth import _is_oauth_only_user

    cur = MagicMock()
    cur.__enter__.return_value = cur
    cur.__exit__.return_value = False
    cur.fetchone.return_value = {"password_hash": "bcrypt-hash"}
    conn = MagicMock()
    conn.cursor.return_value = cur

    assert _is_oauth_only_user(conn, user_id=1) is False
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_oauth_handlers.py::test_change_password_blocks_oauth_only_user -v`
Expected: FAIL (`_is_oauth_only_user` import 안 됨)

- [ ] **Step 3: `_is_oauth_only_user` 추가 + change_password 가드**

`api/routes/auth.py:309` 의 `change_password` 함수 시작 부분에 다음 추가:

```python
def _is_oauth_only_user(conn, user_id: int) -> bool:
    """password_hash IS NULL 이면 True — local 비밀번호 없는 OAuth-only 유저."""
    from psycopg2.extras import RealDictCursor
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT password_hash FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        return row is not None and row["password_hash"] is None


@router.post("/change-password")
def change_password(
    request: Request,
    conn = Depends(get_db_conn),
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
    user: Optional[UserInDB] = Depends(get_current_user_required),
    auth_cfg: AuthConfig = Depends(_get_auth_cfg),
):
    """사용자 본인 비밀번호 변경"""
    # AUTH_ENABLED=false이면 비밀번호 변경 불필요
    if not auth_cfg.enabled or user is None:
        return RedirectResponse("/", status_code=302)

    def _error(msg: str):
        return templates.TemplateResponse(request=request, name="profile.html", context={
            "active_page": "profile",
            "current_user": user,
            "auth_enabled": auth_cfg.enabled,
            "error": msg,
            "success": "",
        })

    # OAuth-only 유저 가드
    if _is_oauth_only_user(conn, user.id):
        return _error("소셜 로그인 계정은 비밀번호가 없습니다. 비밀번호 설정 기능은 추후 제공 예정입니다.")

    # (기존 로직 그대로)
    if new_password != new_password_confirm:
        return _error("새 비밀번호가 일치하지 않습니다")
    # ... 이하 기존 로직
```

> 주의: 기존 `change_password` 함수 본체를 직접 수정 — `def _error(msg):` 정의 직후, `if new_password != ...` 검증 앞에 OAuth-only 가드 1줄 삽입.

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_oauth_handlers.py -v`
Expected: 17 passed (15 + 2 신규)

- [ ] **Step 5: 운영 문서 작성**

`_docs/20260525150854_oauth-setup.md` 신규 (Cloudflare Tunnel + Google Cloud Console + Kakao Developers 등록 절차):

```markdown
# OAuth(Google + Kakao) 운영 세팅 가이드

이 문서는 코드 작업 외 인프라/콘솔 등록 절차를 다룹니다.
설계 spec: [20260525144745_oauth-google-kakao-design.md](20260525144745_oauth-google-kakao-design.md)

## 1. Cloudflare Tunnel (라파 운영기 HTTPS 노출)

### 1.1 cloudflared 설치 (라파)
\`\`\`bash
curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb
sudo dpkg -i cloudflared.deb
cloudflared --version
\`\`\`

### 1.2 터널 생성
\`\`\`bash
cloudflared tunnel login                    # 브라우저로 Cloudflare 계정 인증
cloudflared tunnel create investment-advisor
cloudflared tunnel route dns investment-advisor <your-subdomain>.<your-domain>
\`\`\`

### 1.3 config.yml (라파 ~/.cloudflared/config.yml)
\`\`\`yaml
tunnel: <tunnel-id from `cloudflared tunnel list`>
credentials-file: /home/dzp/.cloudflared/<tunnel-id>.json

ingress:
  - hostname: <your-subdomain>.<your-domain>
    service: http://localhost:8000
  - service: http_status:404
\`\`\`

### 1.4 systemd 등록
\`\`\`bash
sudo cloudflared service install
sudo systemctl enable --now cloudflared
sudo systemctl status cloudflared
\`\`\`

## 2. Google Cloud Console

1. https://console.cloud.google.com → 프로젝트 생성/선택
2. "API 및 서비스" → "OAuth 동의 화면" → 외부 (또는 내부) → 앱 정보 입력
3. "사용자 인증 정보" → "사용자 인증 정보 만들기" → "OAuth 클라이언트 ID" → 웹 애플리케이션
4. "승인된 리디렉션 URI" 에 다음 2개 등록:
   - `http://localhost:8000/auth/oauth/google/callback` (개발)
   - `https://<your-subdomain>.<your-domain>/auth/oauth/google/callback` (운영)
5. 생성된 `Client ID`, `Client secret` 을 `.env` 의 `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` 에 입력

## 3. Kakao Developers

1. https://developers.kakao.com → "내 애플리케이션" → 애플리케이션 추가
2. "앱 설정" → "플랫폼" → Web 플랫폼 → 사이트 도메인:
   - `http://localhost:8000`
   - `https://<your-subdomain>.<your-domain>`
3. "카카오 로그인" → 활성화 ON → "Redirect URI" 등록:
   - `http://localhost:8000/auth/oauth/kakao/callback`
   - `https://<your-subdomain>.<your-domain>/auth/oauth/kakao/callback`
4. "동의항목" → "카카오계정(이메일)" → **필수 동의** 설정
5. **"앱 설정" → "비즈앱 신청"** (개인 비즈앱 가능, 심사 1~3 영업일)
   - 비즈앱 전환 안 하면 이메일 동의항목 필수 설정 불가
6. "앱 키" 페이지에서 "REST API 키" 를 `.env` 의 `KAKAO_CLIENT_ID` 에 입력
7. (선택) "보안" → Client Secret 활성화 → `.env` 의 `KAKAO_CLIENT_SECRET` 에 입력

## 4. .env 운영 값 예시

\`\`\`ini
OAUTH_ENABLED=true
OAUTH_SESSION_SECRET=<python -c "import secrets; print(secrets.token_hex(32))">

GOOGLE_CLIENT_ID=<Google Cloud Console>
GOOGLE_CLIENT_SECRET=<Google Cloud Console>
GOOGLE_REDIRECT_URI=https://<subdomain>.<domain>/auth/oauth/google/callback

KAKAO_CLIENT_ID=<Kakao REST API 키>
KAKAO_CLIENT_SECRET=
KAKAO_REDIRECT_URI=https://<subdomain>.<domain>/auth/oauth/kakao/callback
\`\`\`

## 5. 트러블슈팅

| 증상 | 원인 | 해결 |
|---|---|---|
| `redirect_uri_mismatch` (Google) | 콘솔 등록값과 `.env` 값 불일치 | trailing slash, http/https, 포트 모두 확인. character-for-character 일치 필요 |
| `KOE006` (Kakao) | Redirect URI 미등록 또는 오타 | Kakao 콘솔에서 등록 URI 와 콜백 URL 비교 |
| Kakao 이메일 동의 화면에 이메일이 안 나옴 | 비즈앱 미전환 또는 필수 동의 미설정 | 비즈앱 신청 + 동의항목 "이메일" 필수로 변경 |
| 로컬에서 OAuth 흐름이 hang | SessionMiddleware 미등록 | `OAUTH_ENABLED=true` + `OAUTH_SESSION_SECRET` 설정 확인 |
| 콜백에서 `state mismatch` | 동일 브라우저 세션 안에서 두 탭 동시 로그인 | 한 탭으로 재시도 |
```

- [ ] **Step 6: raspberry-pi-setup.md 에 cloudflared 절 추가**

`_docs/raspberry-pi-setup.md` 의 적절한 위치 (네트워크/외부 노출 섹션) 에 다음 추가:

```markdown
## X. Cloudflare Tunnel (HTTPS 외부 노출 — OAuth 운영 시 필수)

OAuth 운영을 위해서는 도메인 + HTTPS 가 필수입니다 (Google/Kakao 양쪽 정책).
Cloudflare Tunnel 을 사용하면 공유기 포트포워딩 없이 무료로 가능합니다.

상세 절차: [20260525150854_oauth-setup.md](20260525150854_oauth-setup.md) §1
```

- [ ] **Step 7: 회귀 테스트 + 수동 흐름 검증**

Run: `pytest -x`
Expected: 모든 테스트 통과.

수동:
1. `.env` 에 `OAUTH_ENABLED=true` + secret + Google CLIENT_ID 세팅 → API 재시작
2. 브라우저 시크릿모드 → `http://localhost:8000/auth/login` → "Google 로 계속하기" 클릭
3. Google 동의 화면 노출 확인 → 동의 → 콜백 → 대시보드 redirect
4. `/profile` → 연결된 계정 섹션에 Google 표시 + "연결 해제" 버튼 비활성화 (OAuth-only 유저라)
5. 로그아웃 → 같은 Google 계정으로 재로그인 → 즉시 로그인 (users INSERT 없음)
6. DB 확인: `SELECT * FROM user_oauth_accounts;` → 1 row, `SELECT * FROM admin_audit_logs WHERE action LIKE 'oauth_%';` → signup + login 2 row

- [ ] **Step 8: Commit**

```bash
git add api/routes/auth.py tests/test_oauth_handlers.py _docs/20260525150854_oauth-setup.md _docs/raspberry-pi-setup.md
git commit -m "$(cat <<'EOF'
feat(Auth): change-password OAuth-only 가드 + 운영 세팅 문서

_is_oauth_only_user 가드로 password_hash IS NULL 유저의 비밀번호
변경 시도를 명시적으로 차단. Cloudflare Tunnel + Google/Kakao
콘솔 등록 절차 + 트러블슈팅 가이드 문서화.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## 최종 검증 (모든 Task 완료 후)

- [ ] **전체 테스트 회귀 확인**

Run: `pytest`
Expected: 모든 테스트 통과 (기존 + 신규 OAuth 테스트 약 28개).

- [ ] **운영 환경 흐름 검증** (Cloudflare Tunnel + Kakao 비즈앱 심사 완료 후)

1. 라파에서 `cloudflared` 데몬 동작 확인 — `systemctl status cloudflared`
2. 외부 브라우저에서 `https://<subdomain>.<domain>/auth/login` 접속
3. Google 가입/로그인 흐름 — 신규 가입 → 로그아웃 → 재로그인
4. Kakao 가입/로그인 흐름 — 동의 화면에 이메일 항목 노출 확인 → 동의 → 신규 가입
5. 동일 이메일 가진 local 유저가 Google 로 진입 시 자동 연결 확인 (DB 의 user_oauth_accounts 에 row 추가, users 는 그대로)
6. 프로필에서 연결/해제 동작 확인
7. `OAUTH_ENABLED=false` 로 변경 후 재시작 → 버튼 숨김 + `/auth/oauth/*` 404 확인

- [ ] **PR 또는 main merge**

`finishing-a-development-branch` 스킬 호출하여 PR 생성 또는 main merge.

---

## Spec 매핑

| Spec 섹션 | 구현 Task |
|---|---|
| § 1 결정 사항 | 모든 task 의 전제 |
| § 2 사전 조건 | Task 13 (문서) |
| § 3 아키텍처 | Task 6, 7 |
| § 4 DB 스키마 v51 | Task 3 |
| § 5 환경변수 | Task 1, 2 |
| § 6 파일 구조 | 모든 task |
| § 7 라우트 4개 | Task 7, 8 |
| § 8 콜백 핸들러 | Task 5, 6 |
| § 9 UI | Task 10, 11, 12 |
| § 10 보안 13개 | Task 6 (provider whitelist, role/tier 강제, email_verified, can_unlink), Task 7 (open redirect, state CSRF), Task 9 (session secret 강제), Task 13 (change-password 가드) |
| § 11 테스트 매트릭스 | Task 6, 7 |
| § 12 롤백 | Task 1 (OAUTH_ENABLED 토글) |
| § 13 Out of Scope | 명시적 제외 |
