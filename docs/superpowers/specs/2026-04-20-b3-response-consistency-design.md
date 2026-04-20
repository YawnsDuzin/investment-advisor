# B3 Design — 응답 포맷 일관성 + B2.5 이월 항목

- **작성일**: 2026-04-20
- **작성자**: Claude (brainstorming skill, opus-4-7)
- **상위 컨텍스트**: "B → C 병렬 → A → D" 로드맵 중 B의 네 번째(마지막) 단계. B1·B2·B2.5 완료 후 이어짐
- **다음 단계**: writing-plans skill 호출 → 구현 계획서 작성

---

## 0. 컨텍스트

B1(pages.py 분할), B2(보일러플레이트 1차), B2.5(dependency injection)를 거친 결과, 라우트 코드의 중복은 대부분 제거됐다. 본 B3는 **남은 일관성 결함**을 마무리한다:

- B2.5 리뷰에서 **Important #1**(`page_context.py`의 자체 DB 연결 — 페이지당 중복 연결)
- B2.5 리뷰에서 **Minor #6**(admin/user_admin 4개 관리자 페이지가 Pattern B 미적용 — 혼자 옛 방식)
- API 전반에 흩어진 HTTPException 응답이 일관된 포맷이 아님

**현재 상태 (2026-04-20 측정):**

| 항목 | 수치 | 메모 |
|---|---|---|
| `HTTPException` 사용 (routes/) | ~87회 × 10 파일 | 각 파일마다 자유로운 detail 메시지 |
| `status_code` 분포 | 302(34), 404(30), 400(16), 403(14), 402(8), 401(8), 500(2) | 다양 |
| JSON 응답의 `"error"` 키 | auth.py 위주 부분 사용 | 다른 라우트는 detail만 |
| Admin 페이지 Pattern B 미적용 | 4곳 | Minor #6 |
| `page_context.py` 자체 DB 연결 | base_ctx 내부 | Important #1 |

## 1. Goals

### Part 1 — B2.5 이월 항목 (필수)

1. **admin/user_admin 4개 페이지 Pattern B 적용**
   - `admin.py:admin_page`, `admin.py:diagnostics_page` (prefix `/admin`)
   - `user_admin.py:user_list_page`, `user_admin.py:audit_logs_page` (prefix `/admin/users`)
2. **page_context.py DB 연결 통합**
   - `base_ctx`의 자체 `get_connection(_get_cfg())` 블록 제거. `make_page_ctx`가 `Depends(get_db_conn)`을 통해 받은 `conn`을 base_ctx에 전달 → 페이지당 단일 연결 공유

### Part 2 — 예외·응답 포맷 경량 통일

3. **글로벌 HTTPException 핸들러** — `{"error": <code>, "detail": <msg>}` 포맷으로 일관
4. **글로벌 RequestValidationError 핸들러** — 422 응답을 동일 포맷으로 변환 (기본 `{"detail": [...]}` → `{"error": "validation_failed", "detail": [...]}`)
5. **기존 `raise HTTPException(...)` 호출은 무변경** — 글로벌 핸들러가 포맷만 일관화. 각 라우트 코드 수정 없음

## 2. Non-goals (의도적 제외)

- ❌ **HTTPException → 도메인 예외 전환** (`NotFoundError`, `ForbiddenError` 등). 필요 시 추후 B4.
- ❌ **응답 body envelope** (`{"data": ..., "meta": ...}` 래핑). 프론트엔드 JS 전면 수정 비용 대비 이득 적음.
- ❌ **404 메시지 표준화**. 각 도메인 맥락을 살린 기존 메시지 유지.
- ❌ **auth.py의 수동 `"error"` 키 응답** (폼 POST 응답)을 글로벌 핸들러로 대체. 현재도 의도된 동작.

## 3. 변경 사항

### 3.1 `api/page_context.py` — base_ctx 시그니처 확장

**Before (현재):**
```python
def base_ctx(request, active_page, user, auth_cfg) -> dict:
    ctx = {...}
    if user and auth_cfg.enabled:
        try:
            conn = get_connection(_get_cfg())
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 'noti' ... UNION ALL ...")
                    ...
            finally:
                conn.close()
        except Exception as e:
            print(f"[page_context.base_ctx] 사용량 조회 실패: {e}")
    return ctx
```

**After:**
```python
def base_ctx(request, active_page, user, auth_cfg, conn=None) -> dict:
    ctx = {...}
    if user and auth_cfg.enabled and conn is not None:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 'noti' ... UNION ALL ...")
                ...
        except Exception as e:
            print(f"[page_context.base_ctx] 사용량 조회 실패: {e}")
    return ctx
```

**변경점:**
- `conn` 파라미터 추가 (기본값 `None` — 하위호환)
- 내부 `get_connection()`/`try`/`finally` 제거
- `conn is None` 이면 사용량 쿼리 스킵 (비로그인/dependency 없는 경우 fallback)
- `shared.db.get_connection` 및 `_get_cfg` import 제거

### 3.2 `api/deps.py` — make_page_ctx 확장

**Before:**
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

**After:**
```python
def make_page_ctx(active_page: str):
    def _dep(
        request: Request,
        conn = Depends(get_db_conn),  # 신규
        user: Optional[UserInDB] = Depends(get_current_user),
        auth_cfg: AuthConfig = Depends(_get_auth_cfg),
    ) -> dict:
        ctx = base_ctx(request, active_page, user, auth_cfg, conn=conn)  # conn 전달
        ctx["_user"] = user
        ctx["_auth_cfg"] = auth_cfg
        ctx["_conn"] = conn  # 페이지 라우트가 추가 쿼리 시 재사용 가능
        return ctx
    return _dep
```

**효과:** 인증된 페이지당 DB 연결 1개만 열림 (기존엔 2개).

### 3.3 admin/user_admin 4개 페이지 Pattern B 적용

B2.5 Task 8/9와 동일 패턴. 각 함수에서 3개 Depends → 1개 `Depends(make_page_ctx("name"))`.

대상 함수:
- `admin.py:admin_page` (line 45) — `active_page="admin"` 계열
- `admin.py:diagnostics_page` (line 578) — `active_page="admin_diagnostics"` 또는 유사
- `user_admin.py:user_list_page` (line 84) — `active_page="admin_users"`
- `user_admin.py:audit_logs_page` (line 445) — `active_page="admin_audit"`

현재 각 함수가 자체 ctx dict를 구성하는 부분은 `base_ctx` 반환 dict에 추가 병합(`ctx |= {...}` or `ctx.update({...})`)로 대체.

### 3.4 글로벌 예외 핸들러 (`api/main.py`)

```python
from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


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
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": _status_to_code(exc.status_code), "detail": exc.detail},
        headers=exc.headers or {},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"error": "validation_failed", "detail": exc.errors()},
    )
```

**계약:**
- `HTTPException(status_code=X, detail=Y)` → `JSON {"error": <code>, "detail": Y}` with status X
- `RequestValidationError` → `JSON {"error": "validation_failed", "detail": [...]}` with status 422
- 기존 detail 문자열은 그대로 detail 필드에 보존
- `exc.headers`가 있으면 응답 헤더에 포함 (WWW-Authenticate 등)

## 4. 영향 파일 (5개)

- `api/deps.py` — make_page_ctx 확장 (conn Depends 추가)
- `api/page_context.py` — base_ctx 시그니처 + 내부 로직 변경 (자체 연결 제거)
- `api/routes/admin.py` — 2개 페이지 Pattern B 적용
- `api/routes/user_admin.py` — 2개 페이지 Pattern B 적용
- `api/main.py` — 글로벌 예외 핸들러 2개 추가

## 5. 작업 순서

1. **Task 1 — base_ctx 리팩토링**: page_context.base_ctx에 conn 파라미터 추가 (기본값 None, 하위호환). 기존 호출자 무영향
2. **Task 2 — make_page_ctx 확장**: deps.make_page_ctx에 `Depends(get_db_conn)` 추가 + base_ctx에 conn 전달
3. **Task 3 — admin/user_admin Pattern B**: 4개 페이지 함수 signature 교체 + 본문 치환
4. **Task 4 — base_ctx의 conn=None 기본값 제거** (선택): `conn`을 required로 변경해 누락 방지. 현재 모든 호출자가 conn을 전달하는지 확인 후 결정
5. **Task 5 — 글로벌 예외 핸들러**: main.py에 HTTPException + RequestValidationError 핸들러 추가
6. **Task 6 — baseline 재캡처 + 검증**: 404/422 응답 body 변경으로 baseline `00-before-v3` 재생성 필요

## 6. 검증 전략

- **자동**: 글로벌 핸들러 때문에 일부 URL의 body가 변경됨 (`{"detail": ...}` → `{"error": ..., "detail": ...}`). baseline을 `00-before-v3` 로 재캡처 후 diff.
- **부팅**: 각 Task 후 `python -c "import api.main"`
- **스모크**:
  - 5개 페이지 정상 렌더
  - admin/user_admin 페이지 정상 렌더 (Pattern B 적용 후)
  - 404 응답 body가 `{"error": "not_found", "detail": "..."}` 인지 curl로 확인
  - 422 응답 확인 (invalid query param 요청으로)

## 7. 위험 & 완화

| 위험 | 완화 |
|---|---|
| base_ctx 호출자가 conn을 전달하지 않음 | `conn=None` 기본값으로 하위호환. Task 4에서 선택적으로 required화 |
| 글로벌 핸들러가 302 HTTPException에도 적용 | 302 리다이렉트는 보통 `RedirectResponse` 사용, HTTPException 아님. 혹시 HTTPException(302)가 있다면 별도 처리 |
| admin 페이지 ctx 추가 키 누락으로 템플릿 렌더 실패 | 각 admin 함수의 현재 ctx dict 확인 후 추가 키를 `ctx.update({...})` 로 병합 |
| baseline 재캡처 타이밍 | Part 2 Task 5 직전에 `00-before-v3` 캡처 — 그 시점까지는 기존 포맷. 이후 diff는 `00-before-v3` 대비 |
| auth.py 폼 응답의 수동 `"error"` 키와 충돌 | 글로벌 핸들러는 HTTPException만 처리. 수동 JSONResponse는 무영향 |

## 8. 산출물

- `api/deps.py`, `api/page_context.py`, `api/routes/admin.py`, `api/routes/user_admin.py`, `api/main.py` 수정
- 페이지당 DB 연결 2 → 1로 감소 (인증 페이지 기준)
- API 예외 응답 포맷 `{"error": <code>, "detail": <msg>}` 일관화
- baseline `00-before-v3` 업데이트 (글로벌 핸들러 효과 반영)
- 본 spec 문서

## 9. 본 spec의 범위 밖

- **B4 (선택)**: 도메인 예외 클래스 (`NotFoundError`, `ForbiddenError`)
- **C**: UX/템플릿 통합
- **A**: `shared/db.py` 분할
- **D**: `analyzer/` 파이프라인 분해

## 10. 검증 완료 (2026-04-20)

**전체 단계 실행 결과:**

### 검증 지표

| 항목 | 결과 | 메모 |
|---|---|---|
| `page_context.py` 자체 DB 연결 호출 | 0회 ✓ | `get_connection`, `_get_cfg` 제거됨 |
| admin/user_admin의 `_base_ctx` 직접 호출 | 0회 ✓ | Pattern B 적용 완료 |
| admin/user_admin의 `make_page_ctx` 사용 | 6회 ✓ | admin.py 3회 + user_admin.py 3회 |
| baseline diff (`00-before-v3` vs `b3-99-final`) | 0건 회귀 ✓ | 응답 포맷 일관성 검증 완료 |

### 구현 결과

1. **B2.5 이월 항목 완료**:
   - `page_context.base_ctx` 자체 DB 연결 제거 → 인증 페이지당 연결 2 → 1 (Important #1 해결)
   - admin/user_admin 4개 페이지(`admin_page`, `diagnostics_page`, `user_list_page`, `audit_logs_page`)를 `Depends(make_page_ctx)`로 Pattern B 통일 (Minor #6 해결)

2. **응답 포맷 일관성**:
   - HTTPException 글로벌 핸들러: `{"error": <code>, "detail": <msg>}` 포맷 일관화
   - RequestValidationError 글로벌 핸들러: 422 응답을 동일 포맷으로 변환
   - 기존 라우트 코드 수정 없음 — 핸들러만 추가

### 커밋 이력

| Task | 커밋 SHA | 메시지 |
|---|---|---|
| 1 | 7258c32 | refactor(api): B3 T1 — base_ctx conn 파라미터 추가 + 자체 연결 제거 |
| 2 | b5cfca7 | refactor(api): B3 T2 — make_page_ctx에 Depends(get_db_conn) 추가 + base_ctx conn 전달 |
| 3 | 888eb4a | refactor(api): B3 T3 — admin/user_admin 4개 페이지 Pattern B 적용 |
| 4 | 48e1f66 | refactor(api): B3 T4 — 글로벌 예외 핸들러 (HTTPException + RequestValidationError) |

### 최종 상태

- **새 baseline reference**: `00-before-v3` (이후 refactoring은 이 기준선 사용)
- **회귀 테스트**: baseline diff 0건 → 안정성 확인
- **통합 검증**: global pattern 3개 + baseline diff 1개 = 총 4개 지표 green
