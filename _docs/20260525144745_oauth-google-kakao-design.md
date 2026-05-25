# OAuth 소셜 로그인 (Google + Kakao) 도입 설계

- **작성일**: 2026-05-25
- **대상 브랜치**: dev → main
- **선행 의존**: 없음 (기존 JWT/쿠키/`users` 스키마 그대로 재사용)
- **목적**: 신규 사용자 가입 마찰을 줄이고 국내·해외 유입 채널을 다양화. 기존 local 이메일/비밀번호 가입은 그대로 유지.

---

## 1. 결정 사항 요약

| 항목 | 결정 | 비고 |
|---|---|---|
| 1차 스코프 | Google + Kakao 동시 출시 | |
| 이메일 충돌 정책 | 자동 연결 | provider 이메일 검증 신뢰 (`email_verified=true` 필수) |
| Kakao 이메일 미동의 | 가입 거부 | Kakao Developers 콘솔에서 이메일을 **필수 동의항목**으로 설정 |
| DB 구조 | `user_oauth_accounts` 별도 테이블 (1:N) | 한 user 가 Google + Kakao + local 동시 보유 가능 |
| 닉네임 | provider 값 그대로 (`users.nickname` UNIQUE 아님 → suffix 불필요) | |
| 운영 노출 | Cloudflare Tunnel + HTTPS | raw IP 는 Google/Kakao 양쪽 모두 사실상 거부 |
| 계정 연결 메뉴 | v1 프로필 페이지에 포함 | 연결/해제 + 마지막 로그인 수단 보호 |
| 인앱 브라우저 대응 | v1 제외 | User-Agent 감지 + 안내 페이지는 v2 |

### 검증된 외부 제약 (2026-05 기준)

| 환경 | Google | Kakao |
|---|---|---|
| 로컬 (`http://localhost:8000`) | ✓ localhost 예외 | ✓ localhost 예외 |
| 운영 (raw IP) | ✗ "Hosts cannot be raw IP addresses" | ✗ 공식 답변 없음 + 실제 401 사례 |
| 운영 (DDNS HTTPS) | ✓ DuckDNS 사용 사례 다수 | ✓ "도메인 형식" 허용 |
| 운영 (HTTP) | ✗ localhost 만 예외 | ⚠ "특별한 이유 없는 한 HTTPS" |

→ **DDNS 도메인 + HTTPS 가 사실상 강제**. Cloudflare Tunnel 로 해결 (포트포워딩·인증서 자동).

---

## 2. 사전 조건 (코드 작업 전 완료)

1. **Cloudflare 계정 + 도메인** — 기존 도메인 등록 또는 무료 도메인 발급
2. **라파에 `cloudflared` 설치** → `tunnel login` → 터널 생성 → `~/.cloudflared/config.yml` 에 `<domain> → http://localhost:8000` 매핑 → `cloudflared service install`
3. **Google Cloud Console** — OAuth 2.0 Client 생성, redirect URI 등록:
   - `http://localhost:8000/auth/oauth/google/callback`
   - `https://<domain>/auth/oauth/google/callback`
4. **Kakao Developers** — 앱 생성, 카카오 로그인 활성화, Redirect URI 동일 2개 등록, **동의항목 "카카오계정(이메일)" 필수 동의 설정**, **비즈앱 신청** (개인 비즈앱 가능, 심사 1~3 영업일)
5. `_docs/raspberry-pi-setup.md` 에 cloudflared 설치 절 추가
6. `_docs/YYYYMMDDHHMMSS_oauth-setup.md` 작성 (콘솔 등록 절차 + 트러블슈팅 + 환경변수 예시)

---

## 3. 아키텍처 개요

```
[브라우저]
   │ ① "Google로 시작" 버튼 클릭
   ▼
[GET /auth/oauth/google/start?next=/]
   │ Authlib → state 발급(세션 저장) → 302
   ▼
[Google OAuth 동의 화면]
   │ ② 동의 + code 반환
   ▼
[GET /auth/oauth/google/callback?code=...&state=...]
   │ ③ state 검증 (Authlib 자동) → code → access_token 교환
   │ ④ userinfo 조회 (sub, email, name, email_verified)
   │ ⑤ user_oauth_accounts 조회
   │      ├ 있음 → user_id 로그인 (last_login_at 갱신)
   │      └ 없음 → users 이메일 조회
   │              ├ 있음 + email_verified=true → 자동 연결 (user_oauth_accounts INSERT)
   │              ├ 있음 + email_verified=false → 자동 연결 거부 (수동 연결 안내)
   │              └ 없음 → 신규 users INSERT (password_hash=NULL, role='user', tier='free')
   │                       + user_oauth_accounts INSERT
   │ ⑥ admin_audit_logs 기록 (oauth_signup / oauth_auto_link / oauth_login)
   │ ⑦ _set_auth_cookies() — 기존 access + refresh 발급 경로 재사용
   │ ⑧ refresh_tokens INSERT
   ▼
[302 → ?next= 또는 /]
```

**핵심 원칙**: ⑦부터는 local 로그인과 **완전 동일한 토큰 발급 경로**. OAuth 는 "사용자 신원 확인" 까지만 담당. Kakao 도 동일 흐름, ④의 응답 구조만 다름 (`kakao_account.email` 중첩).

---

## 4. DB 스키마 (마이그레이션 v48)

```sql
CREATE TABLE IF NOT EXISTS user_oauth_accounts (
    id               SERIAL PRIMARY KEY,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider         VARCHAR(20) NOT NULL,           -- 'google' | 'kakao'
    provider_user_id VARCHAR(255) NOT NULL,          -- google sub / kakao id (문자열)
    provider_email   VARCHAR(255),                   -- 최초 연결 시점 provider 이메일
    provider_name    VARCHAR(100),                   -- 최초 연결 시점 provider nickname
    linked_at        TIMESTAMP NOT NULL DEFAULT NOW(),
    last_login_at    TIMESTAMP,
    UNIQUE (provider, provider_user_id),             -- 한 provider 계정은 한 user 에만
    UNIQUE (user_id, provider)                        -- 한 user 는 provider 당 1개만
);
CREATE INDEX idx_user_oauth_accounts_user ON user_oauth_accounts(user_id);
```

- **`users` 테이블 변경 없음** — `password_hash` 이미 NULLABLE.
- FK CASCADE — 사용자 삭제 시 OAuth 연결도 함께 삭제.
- `(provider, provider_user_id)` UNIQUE — 한 Google 계정이 두 user 에 연결 불가.
- `(user_id, provider)` UNIQUE — 한 user 가 같은 provider 에 두 번 연결 불가.
- `provider_email` / `provider_name` 은 감사 + UI 표시용 (최초 1회 기록, 갱신 안 함).

**마이그레이션 함수**: `shared/db/migrations/versions.py` 에 `_migrate_to_v48()` 추가 + `init_db()` 에 `if current < 48: ...` 분기.

---

## 5. 환경변수

`.env` + `.env.example` 동시 갱신:

```ini
# OAuth - Google
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=https://<domain>/auth/oauth/google/callback

# OAuth - Kakao
KAKAO_CLIENT_ID=                    # REST API 키
KAKAO_CLIENT_SECRET=                # 선택 (Kakao 콘솔에서 'ON' 설정 시 필수)
KAKAO_REDIRECT_URI=https://<domain>/auth/oauth/kakao/callback

# OAuth - 공통
OAUTH_ENABLED=true                  # 전체 OAuth on/off 스위치
OAUTH_SESSION_SECRET=               # Starlette SessionMiddleware (state CSRF 저장용)
                                    # 32 bytes hex 권장 (python -c "import secrets; print(secrets.token_hex(32))")
```

**`AuthConfig`** (`shared/config.py`) 에 6개 필드 추가:
- `oauth_enabled: bool` (default false)
- `google_client_id: str`, `google_client_secret: str`, `google_redirect_uri: str`
- `kakao_client_id: str`, `kakao_client_secret: str`, `kakao_redirect_uri: str`
- `oauth_session_secret: str`

**활성화 조건**: provider 별로 `*_CLIENT_ID` 가 비어있지 않은 경우만 해당 provider 활성. UI 버튼도 활성 provider 만 노출. → 첫 배포 시 환경변수 미설정 상태로도 회귀 없음.

**`OAUTH_ENABLED=true` 인데 `OAUTH_SESSION_SECRET` 미설정**: 앱 시작 거부 (명시적 에러).

---

## 6. 파일 구조

```
api/
├── auth/
│   ├── oauth_providers.py     ← NEW: Authlib 클라이언트 등록 (google, kakao)
│   ├── oauth_handlers.py      ← NEW: 콜백 공통 로직 (upsert + 자동연결 + 토큰발급)
│   ├── jwt_handler.py         ← 기존 유지
│   ├── password.py            ← 기존 유지
│   ├── dependencies.py        ← 기존 유지
│   └── models.py              ← 기존 유지
├── routes/
│   ├── auth.py                ← 기존 유지
│   └── auth_oauth.py          ← NEW: /auth/oauth/{provider}/[start|callback|link|unlink]
└── main.py                    ← SessionMiddleware 등록 + auth_oauth router include + register_providers() 호출

shared/
├── db/migrations/versions.py  ← _migrate_to_v48() 추가
└── config.py                  ← AuthConfig 에 OAuth 필드 6개 추가

api/templates/
├── login.html                 ← OAuth 버튼 추가
├── register.html              ← OAuth 버튼 추가
└── profile.html               ← "연결된 계정" 섹션 추가

api/static/css/                ← .btn-google / .btn-kakao 추가 (인라인 SVG 로고)

requirements.txt               ← authlib, itsdangerous(SessionMiddleware 의존) 추가
```

---

## 7. 라우트

```
GET  /auth/oauth/{provider}/start?next=/        → Authlib redirect to provider
GET  /auth/oauth/{provider}/callback?code=...   → upsert → 쿠키 발급 → next 302
POST /auth/oauth/{provider}/link                → 로그인 상태에서 계정 연결 시작 (Authlib redirect)
POST /auth/oauth/{provider}/unlink              → 연결 해제 (가드 통과 시)
```

**`provider` whitelist**: `{"google", "kakao"}` 외 값은 404. 라우터 진입점에서 가드 (path traversal 방지).

---

## 8. 콜백 핸들러 (`api/auth/oauth_handlers.py`)

```python
async def handle_oauth_callback(provider: str, request: Request, conn, auth_cfg: AuthConfig):
    # 1. Authlib 으로 토큰 교환 (state 검증 자동) + userinfo 조회
    try:
        token = await oauth.create_client(provider).authorize_access_token(request)
    except OAuthError:
        return RedirectResponse("/auth/login?error=oauth_failed", status_code=302)

    userinfo = await _extract_userinfo(provider, token)
    # → {"provider_user_id": "...", "email": "...", "name": "...", "email_verified": bool}

    # 2. Kakao 이메일 필수 검증
    if provider == "kakao" and not userinfo["email"]:
        return RedirectResponse("/auth/login?error=kakao_email_required", status_code=302)

    # 3. user_oauth_accounts 조회
    existing = _find_oauth_account(conn, provider, userinfo["provider_user_id"])
    if existing:
        user = _get_user(conn, existing["user_id"])
        if not user["is_active"]:
            return RedirectResponse("/auth/login?error=account_disabled", status_code=302)
        _update_oauth_last_login(conn, existing["id"])
        _audit_log(conn, user["id"], "oauth_login", provider=provider)
        return _issue_tokens_and_redirect(user, conn, auth_cfg, next_url)

    # 4. 이메일로 users 조회 → 자동 연결 또는 신규 가입
    user = _find_user_by_email(conn, userinfo["email"])
    if user:
        if not userinfo["email_verified"]:
            # 자동 연결 거부 — 프로필에서 수동 연결 안내
            return RedirectResponse(
                "/auth/login?error=email_unverified&provider=" + provider, status_code=302
            )
        if not user["is_active"]:
            return RedirectResponse("/auth/login?error=account_disabled", status_code=302)
        _insert_oauth_account(conn, user["id"], provider, userinfo)
        _audit_log(conn, user["id"], "oauth_auto_link", provider=provider)
    else:
        user_id = _create_user_from_oauth(conn, userinfo)  # role='user', tier='free' 하드코딩
        _insert_oauth_account(conn, user_id, provider, userinfo)
        user = _get_user(conn, user_id)
        _audit_log(conn, user_id, "oauth_signup", provider=provider)

    return _issue_tokens_and_redirect(user, conn, auth_cfg, next_url)


async def _extract_userinfo(provider: str, token: dict) -> dict:
    if provider == "google":
        ui = token["userinfo"]  # Authlib OIDC 자동 파싱
        return {
            "provider_user_id": ui["sub"],
            "email": ui["email"],
            "email_verified": ui.get("email_verified", False),
            "name": ui.get("name", ""),
        }
    elif provider == "kakao":
        resp = await oauth.kakao.get("v2/user/me", token=token)
        data = resp.json()
        account = data.get("kakao_account", {})
        return {
            "provider_user_id": str(data["id"]),
            "email": account.get("email"),
            "email_verified": account.get("is_email_verified", False),
            "name": account.get("profile", {}).get("nickname", ""),
        }
    raise ValueError(f"Unknown provider: {provider}")
```

**`_issue_tokens_and_redirect()`**: `routes/auth.py:_set_auth_cookies()` 와 `create_access_token()` / `create_refresh_token()` 그대로 호출. local 로그인과 정확히 같은 토큰 형태. → 기존 `decode_access_token()`, refresh rotation, 탈취 감지 로직 모두 자동 적용.

---

## 9. UI 변경

### `login.html` + `register.html`

```jinja2
<div class="oauth-buttons">
  {% if oauth_google_enabled %}
    <a href="/auth/oauth/google/start?next={{ next_url|urlencode }}" class="btn-oauth btn-google">
      <svg>...Google G 로고...</svg> Google 로 계속하기
    </a>
  {% endif %}
  {% if oauth_kakao_enabled %}
    <a href="/auth/oauth/kakao/start?next={{ next_url|urlencode }}" class="btn-oauth btn-kakao">
      <svg>...Kakao 심볼...</svg> 카카오로 계속하기
    </a>
  {% endif %}
</div>
<div class="divider">또는</div>
<!-- 기존 이메일/비밀번호 폼 -->
```

`oauth_google_enabled` / `oauth_kakao_enabled` 컨텍스트는 login/register 라우트에서 직접 주입 (전역 `_base_ctx()` 부담 회피).

### `profile.html` — "연결된 계정" 섹션

```jinja2
<section>
  <h3>연결된 계정</h3>
  <ul class="oauth-links">
    {% for provider in ['google', 'kakao'] %}
      {% set linked = linked_providers.get(provider) %}
      <li>
        <span class="provider-name">{{ provider|capitalize }}</span>
        {% if linked %}
          <span class="linked-email">{{ linked.provider_email }}</span>
          <span class="linked-at">{{ linked.linked_at|fmt_date }}</span>
          <form method="post" action="/auth/oauth/{{ provider }}/unlink">
            <button class="btn-unlink" {% if not can_unlink %}disabled
              title="다른 로그인 수단이 없어 해제할 수 없습니다"{% endif %}>연결 해제</button>
          </form>
        {% else %}
          <a href="/auth/oauth/{{ provider }}/start?next=/profile">연결하기</a>
        {% endif %}
      </li>
    {% endfor %}
  </ul>
</section>
```

**`can_unlink` 가드**:
```python
def _can_unlink(conn, user_id, provider):
    user = _get_user(conn, user_id)
    other_oauth_count = _count_oauth_accounts(conn, user_id, exclude_provider=provider)
    has_password = user["password_hash"] is not None
    return has_password or other_oauth_count >= 1
```

### 스타일

`.btn-google` (흰 배경 + Google 컬러 G 로고), `.btn-kakao` (`#FEE500` 배경 + 카카오 심볼). 다크 테마와 대비. 로고는 인라인 SVG (외부 의존 없음).

---

## 10. 보안 체크리스트

1. **state CSRF**: Authlib + Starlette SessionMiddleware 자동 처리. 직접 구현 금지.
2. **provider whitelist**: `{"google", "kakao"}` 외 path 는 라우터 진입점에서 404.
3. **redirect_uri 정확 일치**: `.env` 값을 Authlib 가 자동 전달. 동적 생성·합성 금지.
4. **`role` 강제 'user'**: OAuth 신규 가입 시 하드코딩. admin/moderator 권한 상승 경로 차단.
5. **`tier` 강제 'free'**: 동일.
6. **`email_verified` 신뢰**: Google `email_verified=true` / Kakao `is_email_verified=true` 인 경우만 자동 연결. false 면 거부 + 수동 연결 안내.
7. **OAuth-only 유저 비밀번호 변경 가드**: `/auth/change-password` 진입 시 `password_hash IS NULL` 이면 "OAuth 계정은 비밀번호가 없습니다" 안내 페이지.
8. **`can_unlink` 가드**: 마지막 로그인 수단 해제 거부.
9. **감사 로그**: `oauth_signup` / `oauth_login` / `oauth_auto_link` / `oauth_manual_link` / `oauth_unlink` 5종을 `admin_audit_logs` (v17) 에 기록. 기존 `action` 컬럼에 새 값 추가만 — 스키마 변경 없음.
10. **`OAUTH_SESSION_SECRET`** 미설정 시 앱 시작 거부 (`OAUTH_ENABLED=true` 일 때).
11. **비활성 계정 차단**: OAuth 로그인/연결 시점 모두 `is_active` 검사.
12. **`next` open redirect 방지**: start 라우트에서 받은 `next` 파라미터를 콜백까지 전달할 때, 외부 호스트 URL 차단. `next` 가 `/` 로 시작하지 않으면 `/` 로 폴백. (예: `?next=https://evil.com` 차단)
13. **Authlib `authorize_redirect()` redirect_uri 일치 검증**: 라이브러리 내부에서 콜백 시 자동 검증 — `redirect_uri` 변조 시 토큰 교환 실패.

---

## 11. 테스트 매트릭스 (pytest)

| 시나리오 | 기대 결과 |
|---|---|
| Google 신규 가입 (DB 에 이메일 없음) | `users` INSERT + `user_oauth_accounts` INSERT + 토큰 발급 |
| Google 가입자가 다시 Google 로그인 | 즉시 로그인, `users` INSERT 없음, `last_login_at` 갱신 |
| local 가입자가 동일 이메일로 Google 가입 (`email_verified=true`) | `users` 재사용, `user_oauth_accounts` INSERT (자동 연결), `oauth_auto_link` 감사로그 |
| local 가입자가 동일 이메일로 Google 가입 (`email_verified=false`) | 자동 연결 거부, `/auth/login?error=email_unverified` 302 |
| Kakao 이메일 미동의 콜백 | `/auth/login?error=kakao_email_required` 302 |
| 비활성 계정으로 OAuth 로그인 | `/auth/login?error=account_disabled` 302 |
| OAuth-only 유저가 마지막 provider unlink 시도 | 거부 (`can_unlink=false`) |
| local password + Google 연결된 유저가 Google unlink | 허용 + `oauth_unlink` 감사로그 |
| 로그인 상태에서 Google 연결 (`/auth/oauth/google/link`) | `user_oauth_accounts` INSERT + `oauth_manual_link` 감사로그 |
| provider whitelist 외 path (`/auth/oauth/naver/start`) | 404 |
| state 불일치 콜백 (CSRF 시뮬) | OAuthError → `/auth/login?error=oauth_failed` 302 |
| `OAUTH_ENABLED=false` 일 때 `/auth/oauth/google/start` | 404 |
| `OAUTH_ENABLED=true` + `OAUTH_SESSION_SECRET` 미설정 | 앱 시작 실패 (RuntimeError) |

**Authlib mock**: `conftest.py` 에 `mock_oauth_provider()` fixture — `authorize_access_token()` 반환값을 시나리오별로 주입. 실제 Google/Kakao 호출 안 함.

**수동 검증** (코드 + 인프라가 진짜 도는지):
1. 로컬 `http://localhost:8000` — Google/Kakao 흐름 전체 (가입 → 로그아웃 → 재로그인 → 연결 → 해제)
2. Cloudflare Tunnel + `https://<domain>` — 동일 흐름
3. Kakao 비즈앱 심사 통과 후 실제 이메일 동의 화면 노출 확인

---

## 12. 롤백 + 운영

**롤백**: `OAUTH_ENABLED=false` → API 재시작 → 버튼 자동 숨김 + OAuth 라우트 404. v48 마이그레이션은 destructive 아니므로 별도 다운그레이드 불필요. 기존 OAuth 가입자는 OAuth 만 가진 경우 로그인 불가 — 운영자가 `users.password_hash` 를 콘솔에서 임시 부여하거나 OAuth 다시 켜야 함.

**모니터링**:
- `admin_audit_logs` 에서 `oauth_*` action 일별 카운트 (대시보드 추가 안 함, ad-hoc 쿼리)
- `app_logs` 에 OAuth 콜백 에러 (Authlib OAuthError, Kakao API 5xx 등) `level='error'` 로 기록

---

## 13. Out of Scope (v1 제외)

- **Naver / Apple / GitHub OAuth** — 동일 패턴으로 확장 가능하지만 v1 에서는 제외.
- **인앱 브라우저 대응** (User-Agent 감지 + 외부 브라우저 유도 페이지) — Kakao톡/Instagram 인앱은 Google OAuth 차단. v2 에서 추가.
- **계정 연결 후 이메일 변경 동기화** — provider 이메일이 바뀌어도 `user_oauth_accounts.provider_email` 은 최초 값 유지. 갱신 안 함.
- **2FA / TOTP** — OAuth 자체는 2FA 보장 안 함. provider 측 2FA 신뢰.
- **OAuth 토큰 영속화** — access_token / refresh_token from provider 를 저장하지 않음. provider API 호출 필요 시 v2 에서 도입.
- **회원 탈퇴 시 provider 연결 해제 (Kakao 연결 끊기 API 호출)** — v2.

---

## 14. 작업 분량 추정

| 단계 | 소요 |
|---|---|
| Cloudflare Tunnel + DDNS + Kakao 비즈앱 신청 | 0.5~3일 (Kakao 심사 변수) |
| v48 마이그레이션 + `AuthConfig` 확장 | 1시간 |
| `oauth_providers.py` + `oauth_handlers.py` + `auth_oauth.py` | 3~4시간 |
| `login.html` / `register.html` / `profile.html` + CSS | 1.5시간 |
| pytest 시나리오 13종 + Authlib mock fixture | 2시간 |
| 로컬 수동 검증 + 운영 도메인 검증 + 문서 (`_docs/...oauth-setup.md`) | 1.5시간 |

**총**: 인프라 제외 ~ 9시간. Kakao 심사 + Cloudflare 세팅 변수 포함 시 ~ 1.5~3일.

---

## 15. 후속 작업 트리거

이 spec 승인 후 → `superpowers:writing-plans` 스킬로 단계별 구현 plan 작성 → `superpowers:executing-plans` 또는 직접 실행.
