# B3: 응답 포맷 일관성 + B2.5 이월 항목 — 구현 계획서

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** B2.5 이월 항목 2건(`page_context.py` 자체 DB 연결 제거, admin/user_admin 4개 페이지 Pattern B)을 해결하고, FastAPI 글로벌 예외 핸들러로 HTTPException/422 응답 포맷을 `{"error": <code>, "detail": <msg>}`으로 일관화한다.

**Architecture:** `page_context.base_ctx`가 외부에서 주입받은 `conn`을 재사용하도록 수정하고 `make_page_ctx`가 `Depends(get_db_conn)`을 통해 이를 공급. admin 페이지 4곳은 `make_page_ctx`로 통합. `api/main.py`에 2개 글로벌 핸들러 등록. baseline 응답 포맷이 바뀌므로 `00-before-v3`로 재캡처.

**Tech Stack:** Python 3.10+, FastAPI (exception handlers, yield dependency), Jinja2, `scripts/route_baseline.py`.

**Spec:** [docs/superpowers/specs/2026-04-20-b3-response-consistency-design.md](../specs/2026-04-20-b3-response-consistency-design.md)

**Reference baseline:** `00-before-v2` (Task 4까지), 이후 `00-before-v3` (Task 5에서 재생성)

---

## 사전 준비 (공통)

**포트 8000 정리:**
```bash
netstat -ano | grep ':8000 ' | awk '{print $5}' | sort -u | while read pid; do taskkill //F //PID "$pid" 2>&1 | head -1; done
taskkill //F //IM uvicorn.exe 2>&1 | head -1 || true
tasklist | grep "python.exe" | awk '{print $2}' | while read pid; do taskkill //F //PID "$pid" 2>&1 | head -1; done 2>/dev/null
sleep 2
netstat -ano | grep ':8000 ' | grep LISTENING && echo "STILL OCCUPIED" || echo "port free"
```

**서버 기동:**
```bash
PYTHONIOENCODING=utf-8 AUTH_ENABLED=false uvicorn api.main:app --host 0.0.0.0 --port 8000
# (Bash run_in_background: true)
until curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/pages/landing 2>/dev/null | grep -qE "^(200|302|404|500)$"; do sleep 1; done
```

---

## Task 1: `page_context.base_ctx`에 `conn` 파라미터 추가 (하위호환)

**Files:**
- Modify: `api/page_context.py`

- [ ] **Step 1: 현재 `api/page_context.py` 읽고 구조 파악**

  현재 base_ctx 시그니처: `def base_ctx(request, active_page, user, auth_cfg) -> dict`.
  내부에 `conn = get_connection(_get_cfg()); try/finally conn.close()` 블록이 있음.

- [ ] **Step 2: base_ctx 시그니처 확장 + 내부 로직 변경**

Edit `api/page_context.py` — base_ctx 함수 전체를 다음으로 교체:

```python
def base_ctx(
    request: Request,
    active_page: str,
    user: Optional[UserInDB],
    auth_cfg: AuthConfig,
    conn=None,
) -> dict:
    """모든 템플릿에 공통으로 전달할 컨텍스트.

    tier 정보와 사용량/한도는 업그레이드 CTA/사용량 배지 표시에 쓰인다.

    conn: 재사용할 DB 연결. None이면 사용량 조회 스킵 (비로그인 또는 dep 외 호출).
    """
    effective_tier = user.effective_tier() if user else "free"
    tier_info = TIER_INFO.get(effective_tier)

    ctx = {
        "request": request,
        "active_page": active_page,
        "current_user": user,
        "auth_enabled": auth_cfg.enabled,
        "unread_notifications": 0,
        "tier": effective_tier,
        "tier_label": tier_info.label_ko if tier_info else None,
        "tier_badge_color": tier_info.badge_color if tier_info else "free",
        "watchlist_limit": get_watchlist_limit(effective_tier),
        "subscription_limit": get_subscription_limit(effective_tier),
        "chat_daily_limit": get_chat_daily_limit(effective_tier),
        "watchlist_usage": 0,
        "subscription_usage": 0,
    }
    if user and auth_cfg.enabled and conn is not None:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 'noti'   AS k, COUNT(*) FROM user_notifications
                        WHERE user_id = %s AND is_read = FALSE
                    UNION ALL
                    SELECT 'watch'  AS k, COUNT(*) FROM user_watchlist
                        WHERE user_id = %s
                    UNION ALL
                    SELECT 'sub'    AS k, COUNT(*) FROM user_subscriptions
                        WHERE user_id = %s
                    """,
                    (user.id, user.id, user.id),
                )
                for key, cnt in cur.fetchall():
                    if key == "noti":
                        ctx["unread_notifications"] = cnt
                    elif key == "watch":
                        ctx["watchlist_usage"] = cnt
                    elif key == "sub":
                        ctx["subscription_usage"] = cnt
        except Exception as e:
            print(f"[page_context.base_ctx] 사용량 조회 실패 (user_id={user.id}): {e}")
    return ctx
```

- [ ] **Step 3: 불필요해진 import 제거**

파일 상단에서 다음 import들을 **제거**:
- `from shared.db import get_connection` (더이상 직접 호출 안 함)
- `from api.deps import get_db_cfg as _get_cfg` (B2 Task 2에서 추가했던 것; 이제 미사용)

`DatabaseConfig` import는 남아있으면 제거 (base_ctx 안에서 더이상 타입 힌트로도 쓰이지 않음). grep으로 확인:
```bash
grep -n "DatabaseConfig\|get_connection\|_get_cfg" api/page_context.py
```
다른 용도 없으면 제거.

- [ ] **Step 4: 부팅 smoke test**

```bash
python -c "import api.main; print('OK')"
```
Expected: `OK`

- [ ] **Step 5: Server + baseline + diff (Task 1까지는 동작 무변경 예상)**

Port 정리 후 서버 기동. 대기 후:
```bash
python scripts/route_baseline.py capture --label b3-01-base-ctx
python scripts/route_baseline.py diff 00-before-v2 b3-01-base-ctx
```

Expected: `[OK] diff 없음` — Task 1은 base_ctx에 optional 파라미터를 추가했을 뿐, 기존 호출(`make_page_ctx`가 `conn` 미전달)은 `conn=None`으로 들어가 사용량 쿼리 스킵. 인증 비활성(`AUTH_ENABLED=false`)이므로 baseline 상 사용량은 원래부터 0 — 동일 응답.

- [ ] **Step 6: 서버 종료 + 커밋**

```bash
# 포트 정리 (공통 루틴 재실행)
git add api/page_context.py
git commit -m "refactor(api): B3 — base_ctx에 conn 파라미터 추가 (기본값 None, 하위호환)"
```

---

## Task 2: `make_page_ctx`에 `Depends(get_db_conn)` 추가 + base_ctx 호출에 conn 전달

**Files:**
- Modify: `api/deps.py`

- [ ] **Step 1: 현재 `api/deps.py`의 make_page_ctx 읽기**

현재 구조:
```python
def make_page_ctx(active_page: str):
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

- [ ] **Step 2: make_page_ctx 교체**

Edit 전체를 다음으로:
```python
def make_page_ctx(active_page: str):
    """페이지별 컨텍스트 빌더 dependency 팩토리 (B2.5 + B3).

    사용: `def route(ctx: dict = Depends(make_page_ctx("dashboard")))`.

    반환 dict:
    - base_ctx가 채우는 모든 키 (current_user, auth_enabled, tier, unread_notifications 등)
    - 편의 키:
      - `ctx["_user"]`: Optional[UserInDB]
      - `ctx["_auth_cfg"]`: AuthConfig
      - `ctx["_conn"]`: DB 연결 (페이지 라우트가 추가 쿼리 시 재사용 가능)
    - `ctx["request"]`: base_ctx가 이미 넣음
    """
    def _dep(
        request: Request,
        conn = Depends(get_db_conn),
        user: Optional[UserInDB] = Depends(get_current_user),
        auth_cfg: AuthConfig = Depends(_get_auth_cfg),
    ) -> dict:
        ctx = base_ctx(request, active_page, user, auth_cfg, conn=conn)
        ctx["_user"] = user
        ctx["_auth_cfg"] = auth_cfg
        ctx["_conn"] = conn
        return ctx
    return _dep
```

- [ ] **Step 3: import 검증**

```bash
python -c "from api.deps import make_page_ctx; dep = make_page_ctx('test'); print('OK')"
```
Expected: `OK`

- [ ] **Step 4: 부팅 smoke test**

```bash
python -c "import api.main; print('OK')"
```

- [ ] **Step 5: Server + baseline + diff**

Port 정리 후 서버 기동. 대기 후:
```bash
python scripts/route_baseline.py capture --label b3-02-make-ctx-conn
python scripts/route_baseline.py diff 00-before-v2 b3-02-make-ctx-conn
```

Expected: `[OK] diff 없음`. AUTH_ENABLED=false 상태라 사용량 쿼리는 실행되지 않지만, conn 의존성은 라우트별로 주입·해제됨 (동작 무변).

- [ ] **Step 6: 서버 종료 + 커밋**

```bash
git add api/deps.py
git commit -m "refactor(api): B3 — make_page_ctx에 conn dependency 추가 (페이지당 단일 연결)"
```

---

## Task 3: admin/user_admin 4개 페이지 Pattern B 적용

**Files:**
- Modify: `api/routes/admin.py`
- Modify: `api/routes/user_admin.py`

대상 함수 4곳:
- `admin.py:admin_page` (line ~45, `active_page="admin"`)
- `admin.py:diagnostics_page` (line ~578, `active_page` 이름 결정 필요)
- `user_admin.py:user_list_page` (line ~84, `active_page="admin_users"`)
- `user_admin.py:audit_logs_page` (line ~445, `active_page="admin_audit"`)

### Pattern B (B2.5 Task 8/9와 동일)

**Before:**
```python
def admin_page(request: Request, user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    if auth_cfg.enabled:
        if user is None:
            return RedirectResponse("/auth/login?next=/admin", status_code=302)
        if user.role != "admin":
            raise HTTPException(status_code=403, detail="접근 권한이 없습니다")
    return templates.TemplateResponse(request=request, name="admin.html", context={
        "request": request,
        "active_page": "admin",
        "is_running": _running,
        "current_user": user,
        "auth_enabled": auth_cfg.enabled,
    })
```

**After:**
```python
def admin_page(ctx: dict = Depends(make_page_ctx("admin"))):
    if ctx["auth_enabled"]:
        if ctx["_user"] is None:
            return RedirectResponse("/auth/login?next=/admin", status_code=302)
        if ctx["_user"].role != "admin":
            raise HTTPException(status_code=403, detail="접근 권한이 없습니다")
    ctx["is_running"] = _running  # 기존 컨텍스트에 추가 키 병합
    return templates.TemplateResponse(request=ctx["request"], name="admin.html", context=ctx)
```

### Step 1: `admin.py:admin_page` 마이그레이션

- [ ] Apply Pattern B using the admin_page example above.

### Step 2: `admin.py:diagnostics_page` 마이그레이션

- [ ] 파일의 diagnostics_page 함수를 동일 패턴으로 교체.

현재 함수 구조 확인:
```bash
sed -n '570,620p' api/routes/admin.py
```

- 3개 Depends 파라미터 → 1개 `Depends(make_page_ctx("admin_diagnostics"))`.
- 본문 내 `auth_cfg.enabled` → `ctx["auth_enabled"]`, `user` → `ctx["_user"]`, `request` → `ctx["request"]`.
- 함수가 자체 ctx dict를 만들고 있으면 `ctx.update({...})` 또는 개별 키 할당으로 병합.

### Step 3: `user_admin.py:user_list_page` 마이그레이션

- [ ] **주의:** user_list_page는 B2.5에서 이미 `conn=Depends(get_db_conn)` 가 추가되어 있음. Pattern B 적용 시 이 conn은 `ctx["_conn"]`로 대체되거나, 별도 유지 가능 (두 연결이 되는 문제는 없음 — FastAPI가 같은 요청 내 같은 dependency는 단일 인스턴스만 만들기 때문).

간단 처리: 현재 conn Depends 그대로 두고, page context만 make_page_ctx로 교체. `make_page_ctx` 내부의 `get_db_conn` dependency와 함수 파라미터의 `get_db_conn` dependency는 FastAPI 캐싱으로 같은 객체를 공유함.

또는 깔끔하게 `conn = Depends(get_db_conn)` 파라미터 제거 + 본문에서 `ctx["_conn"]` 사용.

**권장:** `conn = Depends(get_db_conn)` 파라미터 제거, 본문의 `conn` 참조를 `ctx["_conn"]`으로 치환.

**Before:**
```python
def user_list_page(
    request: Request,
    page: int = Query(default=1, ge=1),
    ...
    user: Optional[UserInDB] = Depends(get_current_user),
    auth_cfg: AuthConfig = Depends(_get_auth_cfg),
    conn=Depends(get_db_conn),
):
    if auth_cfg.enabled:
        if user is None:
            return RedirectResponse("/auth/login?next=/admin/users", status_code=302)
        if user.role not in ("admin", "moderator"):
            raise HTTPException(status_code=403, detail="접근 권한이 없습니다")
    # ... 본문에서 conn 사용 (with conn.cursor(...)...)
```

**After:**
```python
def user_list_page(
    page: int = Query(default=1, ge=1),
    ...
    ctx: dict = Depends(make_page_ctx("admin_users")),
):
    if ctx["auth_enabled"]:
        if ctx["_user"] is None:
            return RedirectResponse("/auth/login?next=/admin/users", status_code=302)
        if ctx["_user"].role not in ("admin", "moderator"):
            raise HTTPException(status_code=403, detail="접근 권한이 없습니다")
    # ... 본문에서 ctx["_conn"] 사용
```

### Step 4: `user_admin.py:audit_logs_page` 마이그레이션

- [ ] 동일 패턴 적용.

### Step 5: Import 정리

각 파일에서 이제 미사용된 import 제거 (다른 함수에서 여전히 쓰이면 유지):
- `Request` (fastapi) — 다른 곳에서도 안 쓰면 제거
- `get_current_user`, `_get_auth_cfg` — 비-page 라우트에서 쓰이면 유지
- `UserInDB`, `AuthConfig` — 타입 힌트로 쓰이면 유지

`make_page_ctx` 추가:
```python
from api.deps import make_page_ctx  # 기존 get_db_conn import 근처에 추가
```

### Step 6: 부팅 smoke test

```bash
python -c "import api.main; print('OK')"
```

### Step 7: Server + baseline + diff

```bash
python scripts/route_baseline.py capture --label b3-03-admin-pattern-b
python scripts/route_baseline.py diff 00-before-v2 b3-03-admin-pattern-b
```

**예상 결과:** 일부 admin 페이지 body가 달라질 수 있음 (make_page_ctx가 tier/unread_notifications 같은 기존 admin ctx에 없던 키를 추가 — admin.html 템플릿이 `{% if ... %}` 가드가 있으면 무영향, 없으면 차이 발생).

> admin 페이지는 baseline 27개 URL에 포함되지 않음 (baseline은 공개 + `/pages/*` 위주). admin.py/user_admin.py의 `/admin`, `/admin/users` 경로는 ROUTES에 없으므로 diff 영향 없음.

Expected: `[OK] diff 없음`

### Step 8: 서버 종료 + 커밋

```bash
git add api/routes/admin.py api/routes/user_admin.py
git commit -m "refactor(api): B3 — admin/user_admin 4개 페이지를 Depends(make_page_ctx)로 이전"
```

---

## Task 4: 글로벌 예외 핸들러 + baseline 재캡처

**Files:**
- Modify: `api/main.py`

- [ ] **Step 1: `api/main.py` 읽기**

현재 구조 파악. import 섹션과 `app = FastAPI(...)` 선언 이후 라우터 include 전 사이에 핸들러 등록 추가 예정.

- [ ] **Step 2: 글로벌 핸들러 추가**

`api/main.py`의 import 섹션에 추가 (이미 있으면 skip):
```python
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
```

`app = FastAPI(...)` 선언 직후 (라우터 include 전)에 다음 블록 추가:

```python
# ── 글로벌 예외 핸들러 (B3) ──────────────────────────
_STATUS_CODE_MAP = {
    400: "bad_request",
    401: "unauthorized",
    402: "payment_required",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "validation_failed",
    429: "rate_limited",
    500: "server_error",
}


def _status_to_code(status: int) -> str:
    return _STATUS_CODE_MAP.get(status, f"http_{status}")


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """HTTPException 응답을 {"error": <code>, "detail": <msg>} 포맷으로 통일."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": _status_to_code(exc.status_code), "detail": exc.detail},
        headers=dict(exc.headers) if exc.headers else None,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """FastAPI 기본 422 응답을 {"error": "validation_failed", "detail": [...]} 포맷으로 통일."""
    return JSONResponse(
        status_code=422,
        content={"error": "validation_failed", "detail": exc.errors()},
    )
```

> **주의:** `dict(exc.headers)` — `exc.headers`는 `dict | None`이고 JSONResponse는 `Optional[dict]`를 받음. None이면 None 전달.

- [ ] **Step 3: 부팅 smoke test**

```bash
python -c "import api.main; print('OK')"
```

- [ ] **Step 4: 서버 기동 + 수동 핸들러 동작 확인**

서버 기동 후:
```bash
# 404 응답 확인
curl -s http://localhost:8000/sessions/99999 | head -2
```
Expected: `{"error":"not_found","detail":"세션을 찾을 수 없습니다"}` (JSON)

```bash
# 422 확인 (invalid limit)
curl -s "http://localhost:8000/sessions?limit=abc" | head -2
```
Expected: `{"error":"validation_failed","detail":[...]}` (JSON)

정상 200 응답은 영향 없는지도 확인:
```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/pages/landing
```
Expected: `200`

- [ ] **Step 5: baseline 재캡처 + 새 reference 생성**

응답 포맷이 바뀌므로 baseline `00-before-v3` 를 생성:
```bash
python scripts/route_baseline.py capture --label 00-before-v3
```

현재 상태(Task 4 구현 후)가 새 reference가 됨. 이 baseline 파일은 이제부터 dev의 기준선.

차이 검증 (v2와 v3 비교):
```bash
python scripts/route_baseline.py diff 00-before-v2 00-before-v3 2>&1 | head -30
```

Expected: 404/422/500 응답 라우트의 body가 변함 (`{"detail":...}` → `{"error":..., "detail":...}`). 200/302 라우트는 무변경.

> **해석:** 이 차이는 의도된 변화. `00-before-v3` 가 이제 새 기준선. 후속 작업(C/A/D)은 v3 기준으로 diff.

- [ ] **Step 6: sanity check — v3 vs v3**

도구의 안정성을 다시 확인:
```bash
python scripts/route_baseline.py capture --label b3-04-sanity
python scripts/route_baseline.py diff 00-before-v3 b3-04-sanity
```
Expected: `[OK] diff 없음`.

- [ ] **Step 7: 서버 종료 + 커밋**

```bash
git add api/main.py
git commit -m "feat(api): B3 — 글로벌 예외 핸들러 추가 (HTTPException + RequestValidationError)"
```

---

## Task 5: 최종 검증 + 수동 스모크 + 검증 메모

**Files:** (검증 + spec doc 업데이트)

- [ ] **Step 1: 전체 잔존 중복 확인**

```bash
echo "=== page_context.py의 자체 DB 연결 (0이어야 함) ==="
grep -n "get_connection\|_get_cfg" api/page_context.py

echo "=== admin/user_admin의 _base_ctx 직접 호출 (0이어야 함) ==="
grep -n "_base_ctx(\|base_ctx(" api/routes/admin.py api/routes/user_admin.py

echo "=== admin/user_admin의 Pattern B 적용 상태 (make_page_ctx 사용) ==="
grep -n "make_page_ctx" api/routes/admin.py api/routes/user_admin.py
```

Expected:
- page_context.py 자체 연결 호출 없음
- admin/user_admin에 `_base_ctx` 호출 없음
- admin/user_admin에 `make_page_ctx` 호출 4회 (admin_page, diagnostics_page, user_list_page, audit_logs_page)

- [ ] **Step 2: 최종 baseline 확인**

서버 기동 후:
```bash
python scripts/route_baseline.py capture --label b3-99-final
python scripts/route_baseline.py diff 00-before-v3 b3-99-final
```
Expected: `[OK] diff 없음`.

- [ ] **Step 3: 수동 스모크 (정상 모드 + 로그인)**

서버를 정상 모드(`AUTH_ENABLED=true` 기본)로 기동:
```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

로그인 후 확인:
1. 메인 5개 페이지 (`/`, `/pages/sessions`, `/pages/themes`, `/pages/proposals`, `/pages/proposals/history/<ticker>`) 정상
2. **admin 페이지** (`/admin`): 티어 배지·알림 드롭다운 표시 (Pattern B 적용 효과 — 이전엔 없었음)
3. **admin/users 페이지** (`/admin/users`): 동일 확인
4. 잘못된 URL (`/pages/sessions/99999`) 호출 시 404 + JSON `{"error": "not_found", "detail": "..."}`
5. 알림 배지 숫자가 계정에 따라 정확히 표시되는지 (base_ctx conn 공유 효과)

- [ ] **Step 4: Spec 문서에 검증 메모 추가**

`docs/superpowers/specs/2026-04-20-b3-response-consistency-design.md` 하단에 추가:

```markdown

## 검증 완료 (2026-04-20)

- **자동**: baseline 재캡처 `00-before-v3` 기준 `b3-99-final` = 0건 회귀
- **구현 결과**:
  - `page_context.base_ctx`의 자체 DB 연결 제거 → 페이지당 연결 2→1 (B2.5 Important #1 해결)
  - admin/user_admin 4개 페이지를 `make_page_ctx`로 통합 (B2.5 Minor #6 해결)
  - HTTPException/422 응답 포맷 `{"error": <code>, "detail": <msg>}` 일관화
- **수동 스모크**: 5개 페이지 + 관리자 2개 페이지 + 404/422 JSON 포맷 확인
- **커밋**: Task 1 ~ Task 4 (SHA 목록)
```

- [ ] **Step 5: 검증 메모 커밋**

```bash
git add docs/superpowers/specs/2026-04-20-b3-response-consistency-design.md
git commit -m "docs(refactor): B3 검증 완료 메모"
```

---

## 검증 요약

- **자동**: Task 1-3은 `00-before-v2` 대비 0건. Task 4 이후는 `00-before-v3` 새 baseline 생성 후 비교
- **수동**: Task 5에서 로그인 + 관리자 페이지 2곳 + 404/422 JSON 포맷 확인
- **회귀 위험**:
  - Task 1-2: `conn=None` 기본값 덕에 하위호환. 기존 호출자 무영향
  - Task 3: admin 페이지가 make_page_ctx로 이전되며 새 ctx 키 노출. 템플릿이 `{% if ... %}` 가드가 있으면 무영향
  - Task 4: API 404/422 응답 포맷 변화 — 프론트엔드 JS가 `data.detail`을 쓴다면 그대로 호환 (detail 키 유지). `data.error` 키는 추가될 뿐

## 본 plan의 범위 밖

- **B4 (선택)**: 도메인 예외 클래스 (`NotFoundError`, `ForbiddenError` 등) — 각 HTTPException 호출을 의미 있는 예외로 교체
- **C**: UX/템플릿 통합
- **A**: `shared/db.py` 분할
- **D**: `analyzer/` 파이프라인 분해
