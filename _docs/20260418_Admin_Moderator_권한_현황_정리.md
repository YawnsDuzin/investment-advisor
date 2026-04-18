# Admin / Moderator 권한 현황 정리

> **작성일**: 2026-04-18
> **대상**: investment-advisor 프로젝트 RBAC (Role-Based Access Control)
> **목적**: Admin, Moderator, User 역할별 권한 범위 명확화 및 티어 기반 접근 제어와의 관계 정리

---

## 1. 역할(Role) vs 티어(Tier) 개념 구분

| 구분 | 역할 (Role) | 티어 (Tier) |
|------|-------------|-------------|
| **목적** | 관리 권한 — 시스템 운영 기능 접근 | 서비스 등급 — 일반 사용자 기능 범위 |
| **값** | `admin`, `moderator`, `user` | `free`, `pro`, `premium` |
| **설정** | Admin이 수동 부여 (`user_admin.py`) | Admin이 수동 부여 또는 향후 결제 연동 |
| **만료** | 없음 (영구) | `tier_expires_at`으로 만료 가능 |
| **기본값** | `user` (회원가입 시) | `free` (회원가입 시) |

- `effective_tier()`: `tier_expires_at`이 지났으면 `free`로 강등 (`api/auth/models.py:48`)
- Admin/Moderator는 역할 자체로 모든 티어 제한을 우회

---

## 2. 역할별 권한 매트릭스

### 2.1 Admin (최고 권한)

#### 분석 파이프라인 관리

| 기능 | 엔드포인트 | 파일 위치 |
|------|-----------|----------|
| 분석 실행 상태 조회 | `GET /admin/status` | `api/routes/admin.py:59` |
| 분석 로그 조회 | `GET /admin/logs` | `admin.py:68` |
| 분석 파이프라인 실행 | `POST /admin/run` | `admin.py:81` |
| 분석 로그 실시간 SSE 스트리밍 | `GET /admin/stream` | `admin.py:168` |
| 분석 중단 | `POST /admin/stop` | `admin.py:205` |
| 미번역 뉴스 현황 조회 | `GET /admin/translate-news/status` | `admin.py:229` |
| 뉴스 한글 번역 실행 | `POST /admin/translate-news` | `admin.py:237` |
| 전체 데이터 삭제 | `POST /admin/reset-all-data` | `admin.py:374` |
| 원격 DB 데이터 복사 | `POST /admin/copy-from-remote` | `admin.py:428` |

> 모두 `require_role("admin")`으로 보호

#### 사용자 관리

| 기능 | 엔드포인트 | 파일 위치 | 비고 |
|------|-----------|----------|------|
| 사용자 목록/검색 | `GET /admin/users` 페이지 | `pages.py:99` | Moderator도 접근 가능 |
| 사용자 역할 변경 | `PATCH /admin/users/{id}/role` | `user_admin.py:151` | Moderator도 가능 (제약 있음) |
| 사용자 활성화/비활성화 | `PATCH /admin/users/{id}/status` | `user_admin.py:206` | Moderator도 가능 (제약 있음) |
| **사용자 티어 수동 변경** | `PATCH /admin/users/{id}/tier` | `user_admin.py:261` | **Admin 전용** |
| **비밀번호 초기화** | `POST /admin/users/{id}/reset-password` | `user_admin.py:346` | **Admin 전용** |
| **사용자 삭제** | `DELETE /admin/users/{id}` | `user_admin.py:386` | **Admin 전용** |
| **감사 로그 조회** | `GET /admin/users/audit-logs` 페이지 | `pages.py:445` | **Admin 전용** |

#### 채팅

- 모든 사용자의 채팅 세션 조회/삭제 가능 (`user.role != "admin"` 체크 우회)
- 일일 턴 한도 없음 (무제한)

#### UI 메뉴 (base.html)

- 관리자 페이지 (`/admin`)
- API Docs (`/docs`)
- 감사 로그 (`/admin/users/audit-logs`)
- 사용자 관리 (`/admin/users`)
- Theme Chat

#### 제약 사항

- 본인 계정 삭제/역할 변경/비활성화/티어 변경 불가 (`user_admin.py`에서 `actor_id == target_id` 체크)

---

### 2.2 Moderator (중간 권한)

#### 사용자 관리 (제한적)

| 기능 | 가능 여부 | 제약 조건 | 파일 위치 |
|------|----------|----------|----------|
| 사용자 목록 조회 | O | — | `pages.py:99` |
| 역할 변경 | **제한적** | Admin 계정 수정 불가, Admin 역할 부여 불가 | `user_admin.py:170-174` |
| 활성화/비활성화 | **제한적** | User 역할 계정만 비활성화 가능 | `user_admin.py:221-224` |
| 티어 변경 | X | Admin 전용 | `user_admin.py:261` |
| PW 초기화 | X | Admin 전용 | `user_admin.py:346` |
| 사용자 삭제 | X | Admin 전용 | `user_admin.py:386` |
| 감사 로그 조회 | X | Admin 전용 | `pages.py:445` |

#### 채팅

- **본인** 세션만 관리 가능 (다른 사용자 세션 접근 불가)
- 일일 턴 한도 없음 (무제한)

#### UI 메뉴 (base.html)

- 사용자 관리 (`/admin/users`)
- Theme Chat
- 관리자 페이지, API Docs, 감사 로그는 **미표시**

#### 접근 불가 영역

- 분석 파이프라인 (실행/중단/로그/번역)
- 데이터 관리 (전체 삭제, 원격 복사)
- 감사 로그

---

### 2.3 User (일반 사용자) — 티어 기반 차등

| 기능 | Free | Pro | Premium |
|------|------|-----|---------|
| 대시보드/세션 조회 | O | O | O |
| 테마 상세 열람 | 2건/일 | 무제한 | 무제한 |
| 히스토리 조회 | 7일 | 90일 | 무제한 |
| 워치리스트 | 5개 | 30개 | 무제한 |
| 알림 구독 | 3개 | 30개 | 무제한 |
| Stage 2 심층분석 | 1건/일 | 5건/일 | 무제한 |
| **AI 채팅** | **차단** | **10턴/일** | **무제한** |

> 한도 상수 정의: `shared/tier_limits.py`

---

## 3. 접근 제어 메커니즘

### 3.1 `require_role(*roles)` — 역할 기반 하드 블록

```python
# api/auth/dependencies.py:69
def require_role(*roles: str) -> Callable:
    # AUTH_ENABLED=false → 통과
    # user.role not in roles → 403 Forbidden
```

- Admin 전용 기능 (분석 파이프라인, 데이터 관리 등)에 사용
- Admin+Moderator 공용 기능 (사용자 관리 일부)에 사용

### 3.2 `get_current_user_required` — 로그인 필수

```python
# api/auth/dependencies.py:56
def get_current_user_required(...):
    # AUTH_ENABLED=false → None (통과)
    # AUTH_ENABLED=true + 미인증 → 401
```

- 채팅 API, 개인화 API (워치리스트, 알림 등)에 사용

### 3.3 티어 기반 한도 체크 — 코드 내 직접 검증

```python
# 예: chat.py 세션 생성 시
if user and user.role not in ("admin", "moderator"):
    tier = user.effective_tier()
    daily_limit = get_chat_daily_limit(tier)
    if daily_limit is not None and daily_limit <= 0:
        raise HTTPException(status_code=402, ...)
```

- Admin/Moderator는 한도 체크를 건너뜀
- 일반 사용자는 `effective_tier()`에 따라 402 (Payment Required) 반환

### 3.4 소유권 검증 — 리소스 수준 접근 제어

```python
# 채팅, 워치리스트 등에서 공통 패턴
if user and user.role != "admin" and resource.user_id != user.id:
    raise HTTPException(status_code=403, ...)
```

- Admin은 모든 사용자의 리소스 접근 가능
- Moderator/User는 본인 리소스만 접근

---

## 4. UI 메뉴 표시 조건 (base.html)

| 메뉴 | 표시 조건 | 위치 |
|------|----------|------|
| Theme Chat | `role in ('admin','moderator')` 또는 `effective_tier() in ('pro','premium')` | `base.html:43` |
| 사용자 관리 | `role in ('admin','moderator')` | `base.html:101` |
| 감사 로그 링크 | `role == 'admin'` | `base.html:105`, `user_admin.html:79` |
| 관리자 페이지 | `role == 'admin'` | `base.html:105` |
| API Docs | `role == 'admin'` | `base.html:105` |
| 티어 관리 버튼 | `role == 'admin'` | `user_admin.html:149` |
| PW 초기화/삭제 버튼 | `role == 'admin'` | `user_admin.html:150` |
| Admin 역할 옵션 | `role == 'admin'` | `user_admin.html:136` |

---

## 5. 감사 로그 (Audit Log)

Admin의 모든 권한 작업은 `admin_audit_logs` 테이블에 기록됩니다.

| 액션 | 설명 | 기록 항목 |
|------|------|----------|
| `role_change` | 역할 변경 | actor, target, before → after, reason |
| `status_change` | 활성화/비활성화 | actor, target, before → after, reason |
| `tier_change` | 티어 수동 변경 | actor, target, before → after, reason, expires_at |
| `password_reset` | 임시 비밀번호 발급 | actor, target |
| `user_delete` | 계정 삭제 | actor, target email (삭제 후에도 이력 유지) |

> 감사 로그 구현: `user_admin.py:36-65` (`_write_audit_log()`)

---

## 6. 2026-04-18 변경 이력

### 채팅 접근 제어: 역할(role) 기반 → 티어(tier) 기반 전환

**변경 전 (문제)**:
- 채팅 API 전체가 `require_role("admin", "moderator")`로 보호
- `tier_limits.py`에 티어별 한도를 정의했으나, 일반 user는 역할 체크에서 먼저 차단되어 **죽은 코드**
- Pro/Premium 유료 사용자도 채팅 불가

**변경 후**:
- `require_role` → `get_current_user_required` + 티어 기반 한도 체크
- Free: 402 (채팅 차단) / Pro: 10턴/일 / Premium: 무제한
- Admin/Moderator: 한도 체크 건너뜀 (기존과 동일)
- `base.html` 사이드바: Pro/Premium 사용자에게도 Theme Chat 메뉴 표시

**변경 파일**:
- `api/routes/chat.py` — 5개 엔드포인트 접근 제어 전환
- `api/routes/pages.py` — 3개 페이지 라우트 접근 제어 전환
- `api/chat_engine.py` — `max_turns` 기본값 2 → 1 (토큰 최적화)
- `api/templates/base.html` — 사이드바 메뉴 표시 조건 변경