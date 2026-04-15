# JWT 인증 + RBAC 구현 계획

> 작성일: 2026-04-15
> 대상: investment-advisor (FastAPI + Jinja2 + PostgreSQL)

---

## 목차

1. [아키텍처 개요](#1-아키텍처-개요)
2. [JWT 기반 인증 시스템](#2-jwt-기반-인증-시스템)
3. [역할 기반 권한 관리 (RBAC)](#3-역할-기반-권한-관리-rbac)
4. [DB 스키마 변경 (v11)](#4-db-스키마-변경-v11)
5. [구현 순서 및 파일 구조](#5-구현-순서-및-파일-구조)
6. [보안 고려사항](#6-보안-고려사항)
7. [참고 라이브러리 및 버전](#7-참고-라이브러리-및-버전)

---

## 1. 아키텍처 개요

### 현재 상태

- 인증/인가 시스템 없음 — 모든 사용자가 전체 기능에 접근 가능
- FastAPI `Depends()` 미사용 — DB 연결은 함수 내부에서 직접 `get_connection()` 호출
- 미들웨어 없음 (`app.add_middleware()` 호출 없음)
- Jinja2 SSR 기반 다크 테마 UI, API 엔드포인트 병행

### 목표 상태

```
[사용자] → [쿠키(JWT)] → [FastAPI Depends()] → [라우트 핸들러]
                              ↓
                    AUTH_ENABLED=false → bypass (기존 동작 유지)
                    AUTH_ENABLED=true  → 토큰 검증 → 역할 확인 → 허용/거부
```

### 핵심 설계 결정

| 항목 | 결정 | 근거 |
|------|------|------|
| 토큰 저장 | httpOnly 쿠키 | Jinja2 SSR 환경. XSS 방어 |
| 인증 방식 | Access Token + Refresh Token | 세션 서버 불필요, 라즈베리파이 경량 운영 |
| 권한 검증 | FastAPI `Depends()` | 미들웨어보다 라우트별 세밀한 제어 가능 |
| 전환 전략 | `AUTH_ENABLED` 환경변수 | false면 기존 동작 100% 유지, 점진적 전환 |
| 가입 방식 | 공개 회원가입 | 즉시 User 역할 부여, Admin 승인 불필요 |
| OAuth | 이번 제외, 확장 설계 반영 | users 테이블에 `oauth_provider` 컬럼 예약 |

---

## 2. JWT 기반 인증 시스템

### 2.1 회원가입

- **경로**: `POST /auth/register` (Form 전송)
- **입력**: 이메일, 비밀번호 (최소 8자), 닉네임
- **처리**: bcrypt 해싱 → users 테이블 INSERT (role=`user`) → 자동 로그인 → `/` 리다이렉트
- **중복 검사**: email UNIQUE 제약으로 DB 레벨 보장

### 2.2 로그인

- **경로**: `POST /auth/login` (Form 전송)
- **흐름**:
  1. email/password 검증 (bcrypt verify)
  2. `last_login_at = NOW()` 업데이트
  3. 기존 refresh_token 폐기 (동일 user의 만료되지 않은 토큰 `revoked_at = NOW()`)
  4. Access Token 발급 → `access_token` 쿠키 설정
  5. Refresh Token 발급 → `refresh_token` 쿠키 설정 + SHA-256 해시 DB 저장
  6. `RedirectResponse("/")` 반환

### 2.3 로그아웃

- **경로**: `POST /auth/logout`
- **처리**: 양쪽 쿠키 삭제 + refresh_token DB 폐기

### 2.4 토큰 갱신

- **경로**: `POST /auth/refresh`
- **Refresh Token Rotation**: 기존 토큰 폐기 → 새 토큰 쌍 발급
- **탈취 감지**: 폐기된 토큰 재사용 시 해당 user의 모든 refresh_token 일괄 폐기

### 2.5 쿠키 설정

```python
# Access Token 쿠키
response.set_cookie(
    key="access_token",
    value=token,
    httponly=True,         # JS 접근 차단 (XSS 방어)
    secure=COOKIE_SECURE,  # 프로덕션: True (HTTPS)
    samesite="lax",        # CSRF 1차 방어
    max_age=3600,          # ACCESS_TOKEN_EXPIRE_MINUTES * 60
    path="/",
)

# Refresh Token 쿠키
response.set_cookie(
    key="refresh_token",
    value=token,
    httponly=True,
    secure=COOKIE_SECURE,
    samesite="lax",
    max_age=2592000,       # REFRESH_TOKEN_EXPIRE_DAYS * 86400
    path="/auth/refresh",  # refresh 엔드포인트에만 전송 (공격 면적 최소화)
)
```

### 2.6 비밀번호 해싱

- **라이브러리**: `passlib[bcrypt]`
- **cost factor**: 기본값 12 (passlib 기본)
- **비밀번호 정책**: 최소 8자 (Pydantic validator 검증)

```python
from passlib.context import CryptContext

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(plain: str) -> str:
    return _pwd_ctx.hash(plain)

def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_ctx.verify(plain, hashed)
```

### 2.7 AUTH_ENABLED=false 동작

- `get_current_user()` Depends가 항상 `None` 반환
- `require_role()` Depends가 검증 없이 `None` 반환
- 기존 라우트 동작에 영향 없음 (Depends 추가되지만 결과가 사용되지 않음)
- 사이드바에 기존 Admin 링크 그대로 표시

---

## 3. 역할 기반 권한 관리 (RBAC)

### 3.1 역할 정의

| 역할 | 설명 | 권한 범위 |
|------|------|-----------|
| **Admin** | 시스템 관리자 | 전체 접근. 분석 실행/중단, 번역, 사용자 관리(전체), 역할 변경(전체) |
| **Moderator** | 운영자 | 사용자 관리 제한적 접근. 목록 조회, User↔Moderator 역할 변경, User 계정 비활성화 |
| **User** | 일반 사용자 | 대시보드/세션/테마/종목 조회, 테마 채팅 (본인 세션만) |

### 3.2 Moderator 권한 제약

- Admin 계정 조회는 가능하나 수정/비활성화 불가
- 역할 변경: `user ↔ moderator`만 가능, `admin` 역할 부여/박탈 불가
- 비활성화: role이 `user`인 계정만 가능

### 3.3 라우트별 접근 권한 매핑

| 라우터 | 경로 | 인증 | 역할 | 비고 |
|--------|------|------|------|------|
| auth | `POST /auth/login` | 불필요 | 공개 | |
| auth | `POST /auth/register` | 불필요 | 공개 | |
| auth | `POST /auth/logout` | 필요 | 전체 | |
| auth | `POST /auth/refresh` | 불필요 | 쿠키 검증 | |
| pages | `GET /` | 선택적 | 전체 | AUTH_ENABLED=true면 로그인 유도 |
| pages | `GET /pages/*` | 선택적 | 전체 | AUTH_ENABLED=true면 로그인 유도 |
| pages | `GET /pages/chat/*` | **필수** | 전체 | 채팅은 로그인 필수 |
| sessions | `GET /sessions*` | 선택적 | 전체 | 조회 API |
| themes | `GET /themes*` | 선택적 | 전체 | 조회 API |
| proposals | `GET /proposals*` | 선택적 | 전체 | 조회 API |
| chat | `POST /chat/sessions` | **필수** | 전체 | user_id를 세션에 연결 |
| chat | `GET /chat/sessions` | **필수** | 전체 | 본인 세션만 조회 |
| chat | `DELETE /chat/sessions/{id}` | **필수** | 전체 | 본인 세션만 삭제 |
| admin | 모든 경로 | **필수** | **Admin** | 분석 실행/중단/번역 |
| user_admin | `GET /admin/users*` | **필수** | **Admin, Moderator** | |
| user_admin | `PATCH /admin/users/*/role` | **필수** | **Admin, Moderator** | Moderator scope 제한 |
| user_admin | `PATCH /admin/users/*/status` | **필수** | **Admin, Moderator** | Moderator scope 제한 |
| user_admin | `POST /admin/users/*/reset-password` | **필수** | **Admin** | |
| user_admin | `DELETE /admin/users/*` | **필수** | **Admin** | |

### 3.4 Depends 설계

```python
# api/auth/dependencies.py

def get_current_user(access_token, auth_cfg, db_cfg) -> Optional[UserInDB]:
    """AUTH_ENABLED=false → None, 쿠키 없음/만료 → None, 유효 → UserInDB"""

def get_current_user_required(user, auth_cfg) -> Optional[UserInDB]:
    """AUTH_ENABLED=true + user=None → 401. SSR 페이지에서는 리다이렉트로 대체"""

def require_role(*roles: str):
    """역할 기반 접근 제어 팩토리. AUTH_ENABLED=false → 통과"""
    # 사용: Depends(require_role("admin"))
    # 사용: Depends(require_role("admin", "moderator"))
```

### 3.5 SSR 페이지 인증 처리

JSON API와 달리, HTML 페이지에서는 401 대신 로그인 페이지로 리다이렉트:

```python
@router.get("/pages/chat")
def chat_list_page(request, user=Depends(get_current_user), auth_cfg=Depends(...)):
    if auth_cfg.enabled and user is None:
        return RedirectResponse("/auth/login?next=/pages/chat", status_code=302)
    return templates.TemplateResponse(..., context={..., "current_user": user})
```

### 3.6 최초 Admin 계정

- v11 마이그레이션 시 자동 생성
- `ADMIN_EMAIL` / `ADMIN_PASSWORD` 환경변수로 지정 (기본: `admin@example.com` / `changeme123`)
- 기본 비밀번호 사용 시 콘솔에 경고 출력

### 3.7 관리자 사용자 관리 기능

**API 경로:**

| 메서드 | 경로 | 설명 | 권한 |
|--------|------|------|------|
| GET | `/admin/users` | 사용자 목록 + 활동 요약 | Admin, Moderator |
| GET | `/admin/users/{id}` | 사용자 상세 | Admin, Moderator |
| PATCH | `/admin/users/{id}/role` | 역할 변경 | Admin (전체), Moderator (제한적) |
| PATCH | `/admin/users/{id}/status` | 활성/비활성화 | Admin (전체), Moderator (User만) |
| POST | `/admin/users/{id}/reset-password` | 임시 비밀번호 발급 | Admin |
| DELETE | `/admin/users/{id}` | 계정 삭제 | Admin (본인 삭제 금지) |

**활동 로그 쿼리:**

```sql
SELECT
    u.id, u.email, u.nickname, u.role, u.is_active,
    u.created_at, u.last_login_at,
    COUNT(DISTINCT tcs.id) AS chat_session_count,
    COUNT(DISTINCT tcm.id) AS chat_message_count
FROM users u
LEFT JOIN theme_chat_sessions tcs ON tcs.user_id = u.id
LEFT JOIN theme_chat_messages tcm ON tcm.chat_session_id = tcs.id
GROUP BY u.id
ORDER BY u.created_at DESC
LIMIT %s OFFSET %s;
```

---

## 4. DB 스키마 변경 (v11)

### 4.1 신규 테이블

```sql
-- users 테이블
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255),              -- OAuth 확장 시 NULL 허용
    nickname VARCHAR(100) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'user'
        CHECK (role IN ('admin', 'moderator', 'user')),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    last_login_at TIMESTAMP,
    oauth_provider VARCHAR(50),              -- 확장용: 'google', 'kakao' 등
    oauth_provider_id VARCHAR(255)           -- 확장용
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);


-- refresh_tokens 테이블
CREATE TABLE IF NOT EXISTS refresh_tokens (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(255) NOT NULL UNIQUE,  -- SHA-256 해시로 저장
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    revoked_at TIMESTAMP                       -- 명시적 폐기 시각
);

CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user
    ON refresh_tokens(user_id, expires_at);
```

### 4.2 기존 테이블 변경

```sql
-- theme_chat_sessions에 user_id FK 추가 (기존 데이터는 NULL)
ALTER TABLE theme_chat_sessions
    ADD COLUMN IF NOT EXISTS user_id INT REFERENCES users(id) ON DELETE SET NULL;
```

### 4.3 마이그레이션 함수

기존 패턴(`_migrate_to_vN()`)을 따라 `shared/db.py`에 추가:

```python
SCHEMA_VERSION = 11  # v11: JWT 인증 (users, refresh_tokens)

def _migrate_to_v11(cur) -> None:
    """v11: JWT 인증 — users, refresh_tokens, chat_sessions.user_id"""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users ( ... );
        CREATE TABLE IF NOT EXISTS refresh_tokens ( ... );
        ALTER TABLE theme_chat_sessions
            ADD COLUMN IF NOT EXISTS user_id INT REFERENCES users(id) ON DELETE SET NULL;
    """)
    # 최초 Admin 시드
    _seed_admin_user(cur)
    cur.execute("""
        INSERT INTO schema_version (version) VALUES (11)
        ON CONFLICT (version) DO NOTHING;
    """)
    print("[DB] v11 마이그레이션 완료 — users + refresh_tokens 생성")

# init_db()에 추가:
if current < 11:
    _migrate_to_v11(cur)
```

### 4.4 테이블 관계도

```
users ──┬──< refresh_tokens (user_id FK, CASCADE)
        │
        └──< theme_chat_sessions (user_id FK, SET NULL)
                └──< theme_chat_messages (기존 CASCADE)

analysis_sessions (기존 — 변경 없음)
    ├──< investment_themes
    │       ├──< theme_scenarios
    │       ├──< macro_impacts
    │       └──< investment_proposals
    │               └──< stock_analyses
    └──< news_articles
```

---

## 5. 구현 순서 및 파일 구조

### 5.1 전체 파일 구조

**신규 생성 파일 (9개):**

```
api/
├── auth/
│   ├── __init__.py              ← get_current_user, require_role 노출
│   ├── models.py                ← UserInDB, TokenPayload, RegisterRequest, LoginRequest
│   ├── jwt_handler.py           ← create_access_token, decode_access_token, create_refresh_token
│   ├── password.py              ← hash_password, verify_password
│   └── dependencies.py          ← get_current_user, get_current_user_required, require_role
├── routes/
│   ├── auth.py                  ← /auth/register, /auth/login, /auth/logout, /auth/refresh
│   └── user_admin.py            ← /admin/users/* (사용자 관리 CRUD + 활동 로그)
└── templates/
    ├── login.html               ← 로그인 폼
    └── register.html            ← 회원가입 폼
```

**수정 대상 기존 파일 (8개):**

| 파일 | 변경 내용 |
|------|-----------|
| `shared/config.py` | `AuthConfig` 데이터클래스 추가, `AppConfig.auth` 필드 |
| `shared/db.py` | `SCHEMA_VERSION=11`, `_migrate_to_v11()`, `init_db()` 확장 |
| `api/main.py` | `auth_router`, `user_admin_router` 등록 |
| `api/routes/pages.py` | `_base_ctx()` 헬퍼, 각 라우트에 Depends 추가, `current_user`/`auth_enabled` 컨텍스트 |
| `api/routes/chat.py` | `get_current_user_required` Depends, `user_id` INSERT 연결 |
| `api/routes/admin.py` | `require_role("admin")` Depends 적용 |
| `api/templates/base.html` | 로그인/로그아웃 버튼, 역할별 메뉴 가시성 |
| `.env.example` | `AUTH_ENABLED`, `JWT_SECRET_KEY` 등 인증 환경변수 |
| `requirements.txt` | `python-jose[cryptography]`, `passlib[bcrypt]` 추가 |

### 5.2 주요 함수/클래스 시그니처

```python
# api/auth/models.py
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str              # @validator: min 8자
    nickname: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class UserInDB(BaseModel):
    id: int
    email: str
    nickname: str
    role: str                  # 'admin' | 'moderator' | 'user'
    is_active: bool
    created_at: datetime
    last_login_at: Optional[datetime]


# api/auth/jwt_handler.py
def create_access_token(user_id: int, role: str, cfg: AuthConfig) -> str
def decode_access_token(token: str, cfg: AuthConfig) -> dict | None
def create_refresh_token() -> str
def hash_token(raw: str) -> str                # SHA-256


# api/auth/password.py
def hash_password(plain: str) -> str            # bcrypt
def verify_password(plain: str, hashed: str) -> bool


# api/auth/dependencies.py
def get_current_user(...) -> Optional[UserInDB]          # 선택적 인증
def get_current_user_required(...) -> Optional[UserInDB]  # 필수 인증
def require_role(*roles: str) -> Callable                  # 역할 검증 팩토리


# shared/config.py
@dataclass
class AuthConfig:
    enabled: bool                           # AUTH_ENABLED
    jwt_secret_key: str                     # JWT_SECRET_KEY
    jwt_algorithm: str                      # JWT_ALGORITHM (HS256)
    access_token_expire_minutes: int        # 기본 60
    refresh_token_expire_days: int          # 기본 30
    admin_email: str                        # 최초 Admin 이메일
```

### 5.3 구현 순서 (Phase별)

#### Phase 1: 기반 모듈 (의존성 없는 순수 모듈)

```
[ ] requirements.txt — python-jose[cryptography], passlib[bcrypt] 추가
[ ] pip install -r requirements.txt
[ ] api/auth/__init__.py — 빈 파일
[ ] api/auth/password.py — hash_password, verify_password
[ ] api/auth/jwt_handler.py — create_access_token, decode_access_token, create_refresh_token, hash_token
[ ] api/auth/models.py — RegisterRequest, LoginRequest, UserInDB, TokenPayload
[ ] shared/config.py — AuthConfig 추가, AppConfig.auth 필드
[ ] .env.example — AUTH 환경변수 추가
```

#### Phase 2: DB 마이그레이션

```
[ ] shared/db.py — SCHEMA_VERSION = 11
[ ] shared/db.py — _migrate_to_v11() 구현 (users, refresh_tokens, chat_sessions.user_id, admin 시드)
[ ] shared/db.py — init_db()에 if current < 11 추가
[ ] 마이그레이션 검증: python -m api.main 실행 후 테이블 확인
```

#### Phase 3: 인증 Depends + 라우트

```
[ ] api/auth/dependencies.py — get_current_user, get_current_user_required, require_role
[ ] api/auth/__init__.py — 외부 노출 정리
[ ] api/routes/auth.py — register/login/logout/refresh 엔드포인트
[ ] api/templates/login.html — 로그인 폼 (다크 테마)
[ ] api/templates/register.html — 회원가입 폼 (다크 테마)
[ ] api/main.py — auth_router 등록
```

#### Phase 4: 기존 라우트 통합

```
[ ] api/routes/pages.py — _base_ctx() 헬퍼, Depends 추가, 컨텍스트 주입
[ ] api/routes/chat.py — get_current_user_required, user_id 연결
[ ] api/routes/admin.py — require_role("admin") 적용
[ ] api/templates/base.html — 로그인/로그아웃 UI, 역할별 메뉴
```

#### Phase 5: 사용자 관리

```
[ ] api/routes/user_admin.py — CRUD + 활동 로그 + 비밀번호 초기화
[ ] api/main.py — user_admin_router 등록
[ ] 관리자 페이지 사용자 관리 UI (admin.html 확장 또는 별도 템플릿)
```

#### Phase 6: 검증

```
[ ] AUTH_ENABLED=false — 기존 기능 전체 회귀 테스트
[ ] AUTH_ENABLED=true — 회원가입/로그인/로그아웃 동작
[ ] AUTH_ENABLED=true — 역할별 접근 제어 (Admin/Moderator/User)
[ ] Refresh Token rotation 동작
[ ] 비밀번호 초기화 시나리오
[ ] 채팅 세션 user_id 분리 동작
```

---

## 6. 보안 고려사항

### 6.1 JWT 시크릿 키 관리

- `.env` 파일의 `JWT_SECRET_KEY`로 관리 (Git 커밋 금지)
- 기본값 `INSECURE_DEFAULT_CHANGE_IN_PRODUCTION` — 프로덕션 배포 전 반드시 변경
- 생성 방법: `python -c "import secrets; print(secrets.token_hex(32))"`
- 기본값 사용 시 서버 시작 로그에 경고 출력

### 6.2 토큰 만료 시간 권장값

| 토큰 | 만료 시간 | 환경변수 | 기본값 |
|------|-----------|----------|--------|
| Access Token | 60분 | `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` |
| Refresh Token | 30일 | `REFRESH_TOKEN_EXPIRE_DAYS` | `30` |

- Access Token은 짧게 유지하여 탈취 피해 최소화
- Refresh Token은 DB에 SHA-256 해시로 저장, 명시적 폐기 가능

### 6.3 CSRF 방어

- **1차 방어**: `samesite=lax` 쿠키 설정 — cross-origin POST 요청에 쿠키 미전송
- **Refresh Token 경로 제한**: `path=/auth/refresh` — 다른 경로에서 refresh_token 쿠키 미전송
- **추후 강화**: 필요 시 CSRF 토큰을 hidden input으로 삽입 가능

### 6.4 비밀번호 정책

- **최소 길이**: 8자 (Pydantic `@validator`에서 검증)
- **해싱**: bcrypt, cost factor 12 (passlib 기본)
- **관리자 초기화**: 임시 비밀번호 발급 → 콘솔 출력 (이메일 전송 미구현)

### 6.5 Refresh Token 보안

- DB에는 SHA-256 해시만 저장 → DB 유출 시에도 토큰 재사용 불가
- Rotation: 갱신 시 기존 토큰 즉시 폐기, 새 토큰 발급
- 탈취 감지: 폐기된 토큰 재사용 시 해당 user의 모든 토큰 일괄 폐기

### 6.6 비활성화 계정 차단

- `get_current_user()`에서 `WHERE is_active = TRUE` 조건
- 기존 JWT가 유효해도 DB 조회 시 필터링 → `None` 반환 → 접근 차단

---

## 7. 참고 라이브러리 및 버전

| 라이브러리 | 버전 | 용도 |
|-----------|------|------|
| `python-jose[cryptography]` | >=3.3.0 | JWT 발급/검증 |
| `passlib[bcrypt]` | >=1.7.4 | 비밀번호 해싱 (bcrypt) |

### 환경변수 전체 목록 (신규)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `AUTH_ENABLED` | `false` | 인증 시스템 활성화 스위치 |
| `JWT_SECRET_KEY` | `INSECURE_DEFAULT_...` | JWT 서명 키 (반드시 변경) |
| `JWT_ALGORITHM` | `HS256` | JWT 알고리즘 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Access Token 만료 (분) |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `30` | Refresh Token 만료 (일) |
| `ADMIN_EMAIL` | `admin@example.com` | 최초 Admin 이메일 |
| `ADMIN_PASSWORD` | `changeme123` | 최초 Admin 비밀번호 (반드시 변경) |
| `COOKIE_SECURE` | `false` | HTTPS 전용 쿠키 (프로덕션: true) |

---

## 부록: OAuth 확장 설계 (이번 미구현)

이번 구현에서 제외하되, 추후 확장이 용이하도록 다음을 반영:

- `users.oauth_provider` — NULL(로컬), `google`, `kakao` 등
- `users.oauth_provider_id` — OAuth 제공자의 사용자 ID
- `users.password_hash` — OAuth 사용자는 NULL 허용 (`verify_password()` 호출 전 체크)
- `api/routes/auth.py`에 `GET /auth/google`, `GET /auth/google/callback` 진입점 예약 (주석)
- `api/auth/oauth.py` 파일 위치 예약 (`authlib` 라이브러리 기반)

---

## 부록: 프론트엔드 변경 상세

### base.html sidebar-footer 변경

```html
<div class="sidebar-footer">
    {% if current_user %}
        <div style="font-size:12px;color:var(--text-muted);padding:4px 0;">
            {{ current_user.nickname }} ({{ current_user.role }})
        </div>
        {% if current_user.role in ('admin', 'moderator') %}
        <a href="/admin/users">사용자 관리</a>
        {% endif %}
        {% if current_user.role == 'admin' %}
        <a href="/admin">Admin</a>
        {% endif %}
        <a href="/docs" target="_blank">API Docs</a>
        <form action="/auth/logout" method="post">
            <button type="submit">로그아웃</button>
        </form>
    {% else %}
        {% if not auth_enabled %}
        <a href="/admin">Admin</a>
        {% endif %}
        <a href="/docs" target="_blank">API Docs</a>
        <a href="/auth/login">로그인</a>
        <a href="/auth/register">회원가입</a>
    {% endif %}
</div>
```

### pages.py 컨텍스트 헬퍼

```python
def _base_ctx(request, active_page, user, auth_cfg) -> dict:
    return {
        "request": request,
        "active_page": active_page,
        "current_user": user,
        "auth_enabled": auth_cfg.enabled,
    }
```
