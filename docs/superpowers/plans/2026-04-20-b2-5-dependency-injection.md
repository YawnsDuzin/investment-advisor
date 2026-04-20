# B2.5: Context·Connection FastAPI 의존성 주입 — 구현 계획서

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** FastAPI dependency injection으로 DB 연결 try/finally(~75회)와 `_base_ctx` 호출 보일러플레이트(~24회)를 라우트 함수 본문 밖으로 밀어낸다. B2 리뷰의 Minor 2건(user_admin alias, admin 직접 DatabaseConfig)도 함께 해결한다.

**Architecture:** `api/deps.py`에 `get_db_conn`(yield dependency)과 `make_page_ctx`(page context factory)를 추가한다. 라우트 함수 시그니처에 `Depends(get_db_conn)` / `Depends(make_page_ctx("name"))`를 선언하면 FastAPI가 lifecycle을 관리한다. B1/B2와 동일하게 `scripts/route_baseline.py diff 00-before-v2 <label>` = 0으로 회귀 검증.

**Tech Stack:** Python 3.10+, FastAPI (Depends + yield generator), psycopg2, httpx (baseline).

**Spec:** [docs/superpowers/specs/2026-04-20-b2-5-dependency-injection-design.md](../specs/2026-04-20-b2-5-dependency-injection-design.md)

**Reference baseline:** `00-before-v2`.

---

## 사전 준비 (공통)

**포트 8000 정리 루틴** (각 Task 시작 시):
```bash
netstat -ano | grep ':8000 ' | awk '{print $5}' | sort -u | while read pid; do taskkill //F //PID "$pid" 2>&1 | head -1; done
taskkill //F //IM uvicorn.exe 2>&1 | head -1 || true
tasklist | grep "python.exe" | awk '{print $2}' | while read pid; do taskkill //F //PID "$pid" 2>&1 | head -1; done 2>/dev/null
sleep 2
netstat -ano | grep ':8000 ' | grep LISTENING && echo "STILL OCCUPIED" || echo "port free"
```

**서버 기동** (background):
```bash
PYTHONIOENCODING=utf-8 AUTH_ENABLED=false uvicorn api.main:app --host 0.0.0.0 --port 8000
```

**대기**:
```bash
until curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/pages/landing 2>/dev/null | grep -qE "^(200|302|404|500)$"; do sleep 1; done
```

---

## Task 1: `api/deps.py` 확장 (`get_db_conn`, `make_page_ctx` 추가)

**Files:**
- Modify: `api/deps.py`

- [ ] **Step 1: 현재 `api/deps.py` 읽기**

현재 내용 (B2에서 생성):
```python
"""공통 FastAPI dependency 팩토리 (B2)."""
from shared.config import DatabaseConfig


def get_db_cfg() -> DatabaseConfig:
    """DatabaseConfig 인스턴스 반환 — 라우트 기존 `_get_cfg()` 대체."""
    return DatabaseConfig()
```

- [ ] **Step 2: 전체 파일을 다음으로 교체**

```python
"""공통 FastAPI dependency 팩토리 (B2 + B2.5)."""
from typing import Iterator, Optional

from fastapi import Depends, Request
from shared.config import DatabaseConfig, AuthConfig
from shared.db import get_connection
from api.auth.dependencies import get_current_user, _get_auth_cfg
from api.auth.models import UserInDB
from api.page_context import base_ctx


def get_db_cfg() -> DatabaseConfig:
    """DatabaseConfig 인스턴스 반환 — 라우트 기존 `_get_cfg()` 대체."""
    return DatabaseConfig()


def get_db_conn(cfg: DatabaseConfig = Depends(get_db_cfg)) -> Iterator:
    """DB 연결을 FastAPI dependency lifecycle로 관리 (B2.5).

    사용: `def route(conn = Depends(get_db_conn))`.
    FastAPI가 라우트 종료 시 yield 이후(finally) 블록을 실행해 close 보장.
    """
    conn = get_connection(cfg)
    try:
        yield conn
    finally:
        conn.close()


def make_page_ctx(active_page: str):
    """페이지별 컨텍스트 빌더 dependency 팩토리 (B2.5).

    사용: `def route(ctx: dict = Depends(make_page_ctx("dashboard")))`.

    반환 dict:
    - base_ctx가 채우는 모든 키 (current_user, auth_enabled, tier, unread_notifications 등)
    - 편의 키: `ctx["_user"]` (UserInDB|None), `ctx["_auth_cfg"]` (AuthConfig)
      - `ctx["request"]`는 base_ctx가 이미 넣음
    """
    def _dep(
        request: Request,
        user: Optional[UserInDB] = Depends(get_current_user),
        auth_cfg: AuthConfig = Depends(_get_auth_cfg),
    ) -> dict:
        ctx = base_ctx(request, active_page, user, auth_cfg)
        ctx["_user"] = user
        ctx["_auth_cfg"] = auth_cfg
        return ctx
    return _dep
```

- [ ] **Step 3: import 검증**

```bash
python -c "from api.deps import get_db_cfg, get_db_conn, make_page_ctx; dep = make_page_ctx('test'); print('OK')"
```
Expected: `OK`

- [ ] **Step 4: 부팅 smoke test**

```bash
python -c "import api.main; print('OK')"
```
Expected: `OK` (기존 라우트에 영향 없음 — 새 함수는 아직 호출되지 않음)

- [ ] **Step 5: baseline 확인 (선택)**

```bash
# 포트 정리 + 서버 기동 + 대기 후:
python scripts/route_baseline.py capture --label b25-01-deps
python scripts/route_baseline.py diff 00-before-v2 b25-01-deps
```
Expected: `[OK] diff 없음`

- [ ] **Step 6: 커밋**

```bash
git add api/deps.py
git commit -m "feat(refactor): B2.5 — deps.py에 get_db_conn/make_page_ctx 추가"
```

---

## Task 2: Pattern D — `user_admin.py` alias 통일

**Files:**
- Modify: `api/routes/user_admin.py`

- [ ] **Step 1: alias import 교체**

현재 `api/routes/user_admin.py:18`:
```python
from api.deps import get_db_cfg as _get_db_cfg
```

교체:
```python
from api.deps import get_db_cfg as _get_cfg
```

- [ ] **Step 2: 본문 `_get_db_cfg` → `_get_cfg` 일괄 치환**

Edit 도구로 `replace_all: true` 사용:
- `old_string`: `_get_db_cfg`
- `new_string`: `_get_cfg`

치환 확인:
```bash
grep -n "_get_db_cfg" api/routes/user_admin.py
```
Expected: 출력 없음.

`db_cfg` 지역 변수명은 그대로 유지 (별개의 지역 변수명).

- [ ] **Step 3: 부팅 테스트**

```bash
python -c "import api.main; print('OK')"
```

- [ ] **Step 4: baseline diff**

서버 기동 (포트 정리 + AUTH_ENABLED=false) → 캡처 → diff:
```bash
python scripts/route_baseline.py capture --label b25-02-user-admin-alias
python scripts/route_baseline.py diff 00-before-v2 b25-02-user-admin-alias
```
Expected: `[OK] diff 없음`

- [ ] **Step 5: 서버 종료 + 커밋**

```bash
# 서버 종료 (포트 정리 루틴 재사용)
git add api/routes/user_admin.py
git commit -m "refactor(api): B2.5 — user_admin의 _get_db_cfg alias를 _get_cfg로 통일"
```

---

## Task 3: Pattern C — `admin.py` 직접 `DatabaseConfig()` 정리

**Files:**
- Modify: `api/routes/admin.py`

11곳에서 `cfg = DatabaseConfig()` 또는 `local_cfg = DatabaseConfig()` → `cfg = _get_cfg()` 치환.

- [ ] **Step 1: import 추가**

`api/routes/admin.py` 상단에 추가:
```python
from api.deps import get_db_cfg as _get_cfg
```

(이미 있으면 skip. 없으면 다른 `from api...` import 근처에 추가.)

- [ ] **Step 2: 11곳 위치별 치환**

Edit 도구로 한 곳씩 처리 (`replace_all: true` 금지 — 각 줄의 context가 다를 수 있음). 패턴:

`cfg = DatabaseConfig()` → `cfg = _get_cfg()`

해당 줄 위치 (현재 기준, 줄 번호는 편집 중 이동할 수 있음 — grep으로 재확인):
- 234
- 250
- 379
- 443 (`local_cfg = DatabaseConfig()` → `local_cfg = _get_cfg()`)
- 600
- 621
- 677
- 694
- 714
- 735
- 851

위치별 2줄 정도 context를 포함한 Edit로 각각 치환. 예:
```python
# Edit old_string (line 234 근처):
    """관리자 대시보드 페이지"""
    cfg = DatabaseConfig()
# new_string:
    """관리자 대시보드 페이지"""
    cfg = _get_cfg()
```

- [ ] **Step 3: 검증**

```bash
grep -n "DatabaseConfig()" api/routes/admin.py
```
Expected: 출력 없음 (11곳 모두 치환). `DatabaseConfig` 심볼 자체는 타입 힌트에서 여전히 사용 가능하므로 import는 유지.

- [ ] **Step 4: 부팅 테스트**

```bash
python -c "import api.main; print('OK')"
```

- [ ] **Step 5: baseline diff**

서버 기동 후:
```bash
python scripts/route_baseline.py capture --label b25-03-admin-cfg
python scripts/route_baseline.py diff 00-before-v2 b25-03-admin-cfg
```
Expected: `[OK] diff 없음`

- [ ] **Step 6: 서버 종료 + 커밋**

```bash
git add api/routes/admin.py
git commit -m "refactor(api): B2.5 — admin.py 직접 DatabaseConfig() 11곳을 _get_cfg() 호출로 통일"
```

---

## Task 4: Pattern A — 소형 파일 DB 연결 의존성화 (`marketing.py` 제외, `stocks.py`, `track_record.py`)

**Files:**
- Modify: `api/routes/stocks.py`
- Modify: `api/routes/track_record.py`

> `marketing.py`는 DB 연결을 사용하지 않으므로 Pattern A 대상 아님.

### 공통 migration 패턴 (Pattern A)

**Before:**
```python
from api.deps import get_db_cfg as _get_cfg
...

@router.get("/foo")
def foo(_user = Depends(get_current_user_required)):
    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT ...")
            rows = cur.fetchall()
    finally:
        conn.close()
    return rows
```

**After:**
```python
from api.deps import get_db_conn
...

@router.get("/foo")
def foo(conn = Depends(get_db_conn), _user = Depends(get_current_user_required)):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT ...")
        rows = cur.fetchall()
    return rows
```

**Rules:**
- `conn` 파라미터는 맨 앞에 추가 (다른 Depends 파라미터 전).
- `from api.deps import get_db_cfg as _get_cfg` 는 여전히 필요한 경우(Depends(_get_cfg) 타입 힌트) 유지. 아니면 `from api.deps import get_db_conn` 로 교체.
- 함수 본문에서 `conn = get_connection(_get_cfg())`, `try:`, `finally: conn.close()` 3줄 제거.
- 들여쓰기 정리: `try:` 안에 있던 코드를 한 단계 바깥으로 이동.

### Step 1: `stocks.py`

- [ ] Apply Pattern A to the stocks page route if it has DB connection usage.

grep으로 `get_connection` 사용처 확인:
```bash
grep -n "get_connection\|conn.close" api/routes/stocks.py
```

각 함수에 대해 Pattern A 적용.

### Step 2: `track_record.py`

- [ ] Apply Pattern A.

`track_record.py`는 `@router.get("/summary")` 하나의 함수에 `get_connection` 사용. 예시:

**Before** (대략):
```python
@router.get("/summary")
def get_track_record_summary(cfg: DatabaseConfig = Depends(_get_cfg)):
    conn = get_connection(cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            ...
    finally:
        conn.close()
    return result
```

**After**:
```python
@router.get("/summary")
def get_track_record_summary(conn = Depends(get_db_conn)):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        ...
    return result
```

> `cfg` 파라미터가 더이상 필요없어지면 제거. DatabaseConfig 타입 힌트도 이 함수에만 쓰였다면 `DatabaseConfig` import도 제거. 다른 함수에서 쓰면 유지.

### Step 3: 부팅 테스트

```bash
python -c "import api.main; print('OK')"
```

### Step 4: baseline diff

서버 기동 후:
```bash
python scripts/route_baseline.py capture --label b25-04-small-conn
python scripts/route_baseline.py diff 00-before-v2 b25-04-small-conn
```
Expected: `[OK] diff 없음`

### Step 5: 서버 종료 + 커밋

```bash
git add api/routes/stocks.py api/routes/track_record.py
git commit -m "refactor(api): B2.5 — stocks/track_record의 DB 연결을 Depends(get_db_conn)으로 이전"
```

---

## Task 5: Pattern A — 중형 파일 DB 연결 의존성화 (`dashboard.py`, `auth.py`, `sessions.py`, `themes.py`)

**Files:**
- Modify: `api/routes/dashboard.py`
- Modify: `api/routes/auth.py`
- Modify: `api/routes/sessions.py`
- Modify: `api/routes/themes.py`

각 파일에 대해 Task 4의 Pattern A 적용. 각 파일의 `get_connection` 사용 함수를 차례로 이전.

### Step 1-4: 4개 파일 각각 Pattern A 적용

각 파일에 대해:
1. `get_connection` 사용 함수 enumeration (grep)
2. 각 함수에 대해 signature에 `conn = Depends(get_db_conn)` 추가
3. 함수 본문에서 `conn = get_connection(...)` + try/finally 제거
4. import 조정 (`get_db_conn` 추가, 더이상 안 쓰이면 `get_connection` 제거)

### Step 5: 부팅 테스트

```bash
python -c "import api.main; print('OK')"
```

### Step 6: baseline diff

```bash
python scripts/route_baseline.py capture --label b25-05-medium-conn
python scripts/route_baseline.py diff 00-before-v2 b25-05-medium-conn
```
Expected: `[OK] diff 없음`

### Step 7: 서버 종료 + 커밋

```bash
git add api/routes/dashboard.py api/routes/auth.py api/routes/sessions.py api/routes/themes.py
git commit -m "refactor(api): B2.5 — dashboard/auth/sessions/themes의 DB 연결을 Depends(get_db_conn)으로 이전"
```

---

## Task 6: Pattern A — 대형 파일 DB 연결 의존성화 (`chat.py`, `education.py`, `inquiry.py`, `proposals.py`, `watchlist.py`)

**Files:**
- Modify: `api/routes/chat.py`
- Modify: `api/routes/education.py`
- Modify: `api/routes/inquiry.py`
- Modify: `api/routes/proposals.py`
- Modify: `api/routes/watchlist.py`

Task 4, 5와 동일 패턴. 각 파일의 모든 `get_connection` 호출 함수를 이전.

### Step 1-5: 5개 파일 각각 Pattern A 적용

주의사항:
- `watchlist.py`는 13곳으로 가장 많음. 한 번에 모든 함수를 이전하지 말고 grep으로 함수별로 하나씩 처리.
- `education.py`는 11곳.
- 각 함수에서 `conn = Depends(get_db_conn)` 위치: 기존 Depends 파라미터 앞.
- 다른 Depends (e.g., `_user = Depends(get_current_user_required)`)는 유지.

### Step 6: 부팅 테스트

```bash
python -c "import api.main; print('OK')"
```

### Step 7: baseline diff

```bash
python scripts/route_baseline.py capture --label b25-06-large-conn
python scripts/route_baseline.py diff 00-before-v2 b25-06-large-conn
```
Expected: `[OK] diff 없음`

### Step 8: 서버 종료 + 커밋

```bash
git add api/routes/chat.py api/routes/education.py api/routes/inquiry.py api/routes/proposals.py api/routes/watchlist.py
git commit -m "refactor(api): B2.5 — chat/education/inquiry/proposals/watchlist의 DB 연결을 Depends(get_db_conn)으로 이전"
```

---

## Task 7: Pattern A — 관리자 파일 DB 연결 의존성화 (`admin.py`, `user_admin.py`)

**Files:**
- Modify: `api/routes/admin.py`
- Modify: `api/routes/user_admin.py`

admin.py는 SSE 스트리밍 라우트가 있으므로 주의. SSE 라우트는 요청이 오래 지속되고 yield로 응답을 스트리밍하는데, `Depends(get_db_conn)`이 이 lifecycle과 충돌할 수 있음.

### Step 1: `admin.py` SSE 라우트 식별

```bash
grep -n "StreamingResponse\|SSE\|EventSource\|yield" api/routes/admin.py | head -10
```

SSE 라우트가 있다면 해당 함수는 Pattern A 적용 **대상 외**로 둔다 (여전히 수동 `conn = get_connection(...)` + try/finally 유지). 이유: FastAPI의 yield dependency는 응답 반환 후 close 하므로 스트리밍이 끝날 때 close되어 과도한 연결 유지 가능. SSE 라우트는 스트림 내부에서 필요할 때마다 짧게 연결하는 편이 안전.

일반 라우트 (non-SSE)에 대해서만 Pattern A 적용.

### Step 2: `admin.py` 일반 라우트 Pattern A 적용

Task 4, 5, 6과 동일 패턴을 non-SSE 함수에만 적용. SSE 함수는 이번 작업에서 제외하고 코멘트로 "SSE stream — B2.5 Pattern A 예외" 표시.

### Step 3: `user_admin.py` Pattern A 적용

user_admin.py는 Task 2에서 alias를 `_get_cfg`로 통일함. 이번엔 각 함수에서:
- `db_cfg: DatabaseConfig = Depends(_get_cfg)` → `conn = Depends(get_db_conn)` (db_cfg가 단지 connection 생성용으로만 쓰였다면)
- 본문 `conn = get_connection(db_cfg)` + try/finally 제거

함수에서 `db_cfg`가 connection 외 다른 용도로 쓰이면 그건 유지.

### Step 4: 부팅 테스트

```bash
python -c "import api.main; print('OK')"
```

### Step 5: baseline diff

```bash
python scripts/route_baseline.py capture --label b25-07-admin-conn
python scripts/route_baseline.py diff 00-before-v2 b25-07-admin-conn
```
Expected: `[OK] diff 없음`

### Step 6: 서버 종료 + 커밋

```bash
git add api/routes/admin.py api/routes/user_admin.py
git commit -m "refactor(api): B2.5 — admin/user_admin의 DB 연결을 Depends(get_db_conn)으로 이전 (SSE 라우트 제외)"
```

---

## Task 8: Pattern B — 페이지 컨텍스트 의존성화 (소형/중형 파일)

**Files:**
- Modify: `api/routes/marketing.py`
- Modify: `api/routes/stocks.py`
- Modify: `api/routes/track_record.py`
- Modify: `api/routes/dashboard.py`
- Modify: `api/routes/sessions.py`
- Modify: `api/routes/themes.py`
- Modify: `api/routes/watchlist.py`

### 공통 migration 패턴 (Pattern B)

각 페이지 라우트 함수에 대해:

**Before:**
```python
@pages_router.get("")
def foo_page(
    request: Request,
    user: Optional[UserInDB] = Depends(get_current_user),
    auth_cfg: AuthConfig = Depends(_get_auth_cfg),
):
    if auth_cfg.enabled and user is None:
        return RedirectResponse(url="/pages/landing")
    ctx = _base_ctx(request, "foo", user, auth_cfg)
    # body uses ctx, user, auth_cfg, request
    return templates.TemplateResponse(request=request, name="foo.html", context=ctx)
```

**After:**
```python
@pages_router.get("")
def foo_page(ctx: dict = Depends(make_page_ctx("foo"))):
    if ctx["auth_enabled"] and ctx["current_user"] is None:
        return RedirectResponse(url="/pages/landing")
    # body uses ctx, ctx["_user"], ctx["_auth_cfg"], ctx["request"]
    return templates.TemplateResponse(request=ctx["request"], name="foo.html", context=ctx)
```

### Migration rules (반드시 준수)

1. **Signature 교체**: 3개 Depends 파라미터 (`request`, `user`, `auth_cfg`) → 1개 (`ctx: dict = Depends(make_page_ctx("page_name"))`).
2. **`_base_ctx(...)` 호출 제거**: make_page_ctx가 이미 수행.
3. **본문 치환 매핑**:
   - `user` → `ctx["_user"]` 또는 `ctx["current_user"]` (동일 객체)
   - `auth_cfg.enabled` → `ctx["auth_enabled"]`
   - `auth_cfg` (다른 속성 접근) → `ctx["_auth_cfg"]`
   - `request` → `ctx["request"]`
4. **import 정리**: `Request`, `get_current_user`, `_get_auth_cfg`, `UserInDB`, `AuthConfig` 중 다른 라우트에서도 안 쓰이는 것만 삭제.
5. **make_page_ctx 인자**: 기존 `_base_ctx` 호출의 두 번째 인자(active_page 문자열)를 그대로 전달.

### Step 0: Pattern B import 추가

각 파일 상단에:
```python
from api.deps import make_page_ctx
```

### Step 1-7: 7개 파일 각각 Pattern B 적용

각 파일에 대해:
1. `grep -n "_base_ctx(" <file>` 로 호출 위치 enumerate
2. 해당 함수의 signature 교체
3. 함수 본문의 `user`, `auth_cfg`, `request` 참조를 `ctx["..."]` 로 치환
4. 불필요해진 import 정리

**주의:** 본문 치환 시 `user` 문자열이 단어 경계로 변수명일 때만 치환. 문자열 리터럴("user_id") 같은 곳은 건드리지 않음. Edit 도구 사용 시 context 2-3줄 포함한 `old_string`으로 정밀 치환.

**`marketing.py` 특수성:** `landing_page`와 `pricing_page`는 대부분 템플릿 렌더만 한다. `_base_ctx` 호출 후 `ctx`와 `request` 주로 사용. 가장 단순하므로 먼저 적용 권장.

**`dashboard.py` 특수성:** 약 300줄의 큰 함수. 본문에 `user.effective_tier()`, `user.id` 등 사용 많음 — 모든 곳을 `ctx["_user"].effective_tier()`, `ctx["_user"].id` 로 치환.

### Step 8: 부팅 테스트

```bash
python -c "import api.main; print('OK')"
```

### Step 9: 검증 grep

```bash
# 각 파일에서 request, user, auth_cfg 지역 참조 잔존 여부 확인
for f in api/routes/marketing.py api/routes/stocks.py api/routes/track_record.py api/routes/dashboard.py api/routes/sessions.py api/routes/themes.py api/routes/watchlist.py; do
  echo "=== $f ==="
  grep -nE "^\s+(user|auth_cfg|request)\." "$f" | head -5
done
```
Expected: 각 파일에서 이런 라인이 없거나, 다른 용도(예: `auth_cfg = Depends(...)`는 페이지 외 JSON 라우트에서 유지)만 출력.

### Step 10: baseline diff

```bash
python scripts/route_baseline.py capture --label b25-08-page-ctx-a
python scripts/route_baseline.py diff 00-before-v2 b25-08-page-ctx-a
```
Expected: `[OK] diff 없음`

### Step 11: 서버 종료 + 커밋

```bash
git add api/routes/marketing.py api/routes/stocks.py api/routes/track_record.py api/routes/dashboard.py api/routes/sessions.py api/routes/themes.py api/routes/watchlist.py
git commit -m "refactor(api): B2.5 — marketing/stocks/track_record/dashboard/sessions/themes/watchlist 페이지 컨텍스트를 Depends(make_page_ctx)로 이전"
```

---

## Task 9: Pattern B — 페이지 컨텍스트 의존성화 (대형 파일)

**Files:**
- Modify: `api/routes/chat.py`
- Modify: `api/routes/education.py`
- Modify: `api/routes/inquiry.py`
- Modify: `api/routes/proposals.py`

Task 8의 Pattern B를 4개 파일에 적용.

### Step 1-4: 4개 파일 각각 Pattern B 적용

Task 8의 규칙 동일. 각 파일:

- `chat.py`: 2개 _base_ctx 호출
- `education.py`: 4개
- `inquiry.py`: 3개
- `proposals.py`: 3개

### Step 5: 부팅 테스트

```bash
python -c "import api.main; print('OK')"
```

### Step 6: baseline diff

```bash
python scripts/route_baseline.py capture --label b25-09-page-ctx-b
python scripts/route_baseline.py diff 00-before-v2 b25-09-page-ctx-b
```
Expected: `[OK] diff 없음`

### Step 7: 서버 종료 + 커밋

```bash
git add api/routes/chat.py api/routes/education.py api/routes/inquiry.py api/routes/proposals.py
git commit -m "refactor(api): B2.5 — chat/education/inquiry/proposals 페이지 컨텍스트를 Depends(make_page_ctx)로 이전"
```

---

## Task 10: 최종 검증 + 수동 스모크 + 검증 메모 커밋

**Files:** (검증만. 마지막에 spec 문서에 검증 메모 추가)

- [ ] **Step 1: 전체 잔존 패턴 확인**

```bash
echo "=== 잔존 try/finally + conn.close() 패턴 (SSE 예외 외에는 0이어야 함) ==="
grep -rn "conn.close()" api/routes/ --include="*.py"

echo "=== 잔존 _base_ctx() 호출 (0이어야 함 — make_page_ctx 내부에서만) ==="
grep -rn "_base_ctx(request" api/routes/ --include="*.py"

echo "=== DatabaseConfig() 직접 인스턴스화 (0이어야 함) ==="
grep -rn "DatabaseConfig()" api/routes/ --include="*.py"

echo "=== _get_db_cfg alias (0이어야 함 — Task 2에서 통일) ==="
grep -rn "_get_db_cfg" api/routes/ --include="*.py"
```

각 결과 확인. admin.py SSE 라우트만 예외적으로 conn.close가 남아있을 수 있음 (그게 Task 7의 의도).

- [ ] **Step 2: 최종 baseline + diff**

서버 기동 후:
```bash
python scripts/route_baseline.py capture --label b25-99-final
python scripts/route_baseline.py diff 00-before-v2 b25-99-final
```
Expected: `[OK] diff 없음 (00-before-v2 vs b25-99-final) (STATUS_ONLY로 body 무시: 3개)`

- [ ] **Step 3: 서버 정상 모드로 재기동 + 수동 스모크 (사용자 작업)**

`AUTH_ENABLED=true` (기본값)으로 기동:
```bash
# 기존 서버 종료 후
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

B1/B2와 동일한 5개 페이지 + 1개 인터랙션 확인.

- [ ] **Step 4: spec 문서에 검증 메모 추가**

`docs/superpowers/specs/2026-04-20-b2-5-dependency-injection-design.md` 하단에 추가:

```markdown

## 검증 완료 (2026-04-20)

- **자동**: baseline diff `00-before-v2` vs `b25-99-final` = 0건 회귀
- **중복 제거 결과**:
  - `conn = get_connection(...) + try/finally + conn.close()` 블록: ~75회 → SSE 라우트만 잔존 (예상 1-2곳)
  - `_base_ctx(request, ...)` 호출: 24회 → 0회 (모두 `Depends(make_page_ctx(...))`로 통합)
  - `user_admin._get_db_cfg` alias → `_get_cfg` 통일
  - `admin.py` 직접 `DatabaseConfig()` 11곳 → `_get_cfg()` 통일
- **커밋**: Task 1-9 (SHA 목록 추가)
```

- [ ] **Step 5: 검증 메모 커밋**

```bash
git add docs/superpowers/specs/2026-04-20-b2-5-dependency-injection-design.md
git commit -m "docs(refactor): B2.5 검증 완료 메모"
```

---

## 검증 요약

- **자동**: 각 Task 후 `scripts/route_baseline.py diff 00-before-v2 <stage>` = 0건
- **수동**: Task 10에서 5개 페이지 + 1개 인터랙션
- **회귀 위험**:
  - Pattern A (DB 연결): 함수 시그니처에 `conn = Depends(...)` 추가 + try/finally 제거. FastAPI가 lifecycle 관리. SSE 라우트는 예외.
  - Pattern B (page ctx): 함수 시그니처 전면 교체 + 본문 `user`/`auth_cfg`/`request` 참조 치환. 치환 누락이 가장 큰 위험 → grep 기반 검증 필수.

## 본 plan의 범위 밖 (B3로 이월)

- **B3**: JSON 응답 포맷 통일, `HTTPException` → 도메인 예외 + 글로벌 핸들러
