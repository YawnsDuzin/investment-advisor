# B2.5 Design — Context·Connection FastAPI 의존성 주입

- **작성일**: 2026-04-20
- **작성자**: Claude (brainstorming skill, opus-4-7)
- **상위 컨텍스트**: "B → C 병렬 → A → D" 로드맵 중 B의 세 번째 단계 (B1, B2 완료 후 이어짐)
- **다음 단계**: writing-plans skill 호출 → 구현 계획서 작성

---

## 0. 컨텍스트

B1(pages.py 분할), B2(보일러플레이트 1차)를 거친 결과, 라우트 함수 내부에 남은 주요 중복 패턴은 **DB 연결 관리**와 **페이지 컨텍스트 조립**이다. FastAPI의 dependency injection 시스템을 활용해 이를 함수 본문 밖으로 밀어낸다.

**현재 상태 (2026-04-20 측정):**

| 잔존 보일러플레이트 | 출현 | 영향 파일 |
|---|---|---|
| `conn = get_connection(_get_cfg()) + try: ... finally: conn.close()` | ~75회 | 12 라우트 |
| `_base_ctx(request, "name", user, auth_cfg)` 호출 | 24회 | 11 페이지 라우트 |
| `Depends(get_current_user) + Depends(_get_auth_cfg)` 쌍 | 페이지 라우트마다 | 11 파일 |
| `user_admin.py` alias `_get_db_cfg` (B2 Minor) | 1 파일 | 일관성 결함 |
| `admin.py` 직접 `DatabaseConfig()` 인스턴스화 | 11회 | 1 파일 |

## 1. Goals

1. **DB 연결 FastAPI dependency화** — `conn = Depends(get_db_conn)` 로 try/finally 제거
2. **`_base_ctx` 호출 dependency화** — `ctx = Depends(make_page_ctx("page_name"))` 팩토리로 3개 Depends + 1줄 호출 → 1개 Depends
3. **B2 Minor 해결**
   - `user_admin.py` alias `_get_db_cfg` → `_get_cfg` 통일
   - `admin.py` 직접 `DatabaseConfig()` 11회 → `_get_cfg()` 호출 또는 `Depends(get_db_conn)` 이전

## 2. Non-goals (B3 / C / A / D)

- **B3**: JSON 응답 포맷 통일, `HTTPException` → 도메인 예외 + 글로벌 핸들러, 상태 코드 정책 정리
- **C**: 템플릿·UX 개편
- **A**: `shared/db.py` 분할
- **D**: `analyzer/` 파이프라인 분해

## 3. 신규 함수 (`api/deps.py` 확장)

기존 `api/deps.py` (get_db_cfg만 존재)를 확장한다:

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
    """DatabaseConfig 인스턴스 반환 (B2)."""
    return DatabaseConfig()


def get_db_conn(cfg: DatabaseConfig = Depends(get_db_cfg)) -> Iterator:
    """DB 연결을 FastAPI dependency lifecycle로 관리 (B2.5).

    사용: `def route(conn = Depends(get_db_conn))`
    FastAPI가 함수 종료 직후 yield 이후(finally) 블록을 실행해 close 보장.
    """
    conn = get_connection(cfg)
    try:
        yield conn
    finally:
        conn.close()


def make_page_ctx(active_page: str):
    """페이지별 컨텍스트 빌더 dependency 팩토리 (B2.5).

    사용: `def route(ctx: dict = Depends(make_page_ctx("dashboard")))`

    반환 dict:
    - base_ctx가 채우는 모든 키 (current_user, auth_enabled, tier, unread_notifications 등)
    - 호출자가 필요 시 직접 접근할 수 있도록 편의 키 추가:
      - `ctx["_user"]`: Optional[UserInDB]
      - `ctx["_auth_cfg"]`: AuthConfig
      - `ctx["request"]`: Request (base_ctx가 이미 넣음)
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

**계약:**
- `get_db_conn`: yield 기반 dependency — 라우트 종료 시 자동 close.
- `make_page_ctx`: factory — `active_page` 문자열을 캡처해 dependency 반환. 각 페이지 라우트는 `Depends(make_page_ctx("unique_name"))` 호출.

## 4. Migration 패턴

### Pattern A — DB 연결 간소화

**Before:**
```python
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
@router.get("/foo")
def foo(conn = Depends(get_db_conn), _user = Depends(get_current_user_required)):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT ...")
        rows = cur.fetchall()
    return rows
```

**효과:** `conn = get_connection(...)`, `try:`, `finally: conn.close()` 3줄 제거. `_get_cfg()` 호출 불필요 (dep 체인이 자동 처리). `_get_cfg`, `get_connection` import 일부 불필요해짐.

### Pattern B — 페이지 컨텍스트 간소화

**Before:**
```python
@pages_router.get("")
def dashboard(
    request: Request,
    user: Optional[UserInDB] = Depends(get_current_user),
    auth_cfg: AuthConfig = Depends(_get_auth_cfg),
):
    if auth_cfg.enabled and user is None:
        return RedirectResponse(url="/pages/landing", status_code=302)
    ctx = _base_ctx(request, "dashboard", user, auth_cfg)
    # ... body uses ctx, user, auth_cfg
    return templates.TemplateResponse(request=request, name="dashboard.html", context=ctx)
```

**After:**
```python
@pages_router.get("")
def dashboard(ctx: dict = Depends(make_page_ctx("dashboard"))):
    if ctx["auth_enabled"] and ctx["current_user"] is None:
        return RedirectResponse(url="/pages/landing", status_code=302)
    # ... body uses ctx
    # 필요 시: ctx["_user"], ctx["_auth_cfg"], ctx["request"]
    return templates.TemplateResponse(
        request=ctx["request"], name="dashboard.html", context=ctx
    )
```

**효과:** 4줄 signature → 1줄. `request`/`user`/`auth_cfg` 직접 참조를 `ctx["..."]` 로 치환. 함수 본문 중 `user.id` 같은 접근은 `ctx["_user"].id` 또는 `ctx["current_user"].id` 로.

### Pattern C — `admin.py` 직접 `DatabaseConfig()` 정리

11곳에서 `get_connection(DatabaseConfig())` → `get_connection(_get_cfg())` 치환. 더 나아가 가능하면 Pattern A로 함께 이전 (라우트별로 판단).

### Pattern D — `user_admin.py` alias 통일

`from api.deps import get_db_cfg as _get_db_cfg` → `from api.deps import get_db_cfg as _get_cfg`. 본문의 `Depends(_get_db_cfg)` → `Depends(_get_cfg)` replace_all.

## 5. 영향 파일 목록

**수정 1개 (dependency module):**
- `api/deps.py` — 확장 (`get_db_conn`, `make_page_ctx` 추가)

**수정 12-14개 (라우트):**
- Pattern A 적용: `admin.py`, `auth.py`, `chat.py`, `dashboard.py`, `education.py`, `inquiry.py`, `proposals.py`, `sessions.py`, `themes.py`, `track_record.py`, `user_admin.py`, `watchlist.py` (12 파일)
- Pattern B 적용: 페이지 라우트가 있는 11 파일 중 해당 함수들 (`dashboard.py`, `marketing.py`, `sessions.py`, `themes.py`, `proposals.py`, `chat.py`, `education.py`, `inquiry.py`, `stocks.py`, `track_record.py`, `watchlist.py`)
- Pattern C: `admin.py` 11곳 추가 정리
- Pattern D: `user_admin.py` alias 통일

**변경 없음:**
- `api/main.py`, `api/templates_provider.py`, `api/page_context.py` (`base_ctx` 로직 재활용)

## 6. 작업 순서

단계별 commit + 각 단계 `baseline diff 00-before-v2 <label>` = 0 검증.

1. **deps.py 확장** — `get_db_conn`, `make_page_ctx` 추가 (독립, 무영향)
2. **Pattern D** — `user_admin.py` alias 통일 (Minor 해결, 1 파일)
3. **Pattern C** — `admin.py` 직접 `DatabaseConfig()` 정리 (Minor 해결, 1 파일)
4. **Pattern A 단계 1** — 작은 파일: `marketing.py`, `stocks.py`, `track_record.py`
5. **Pattern A 단계 2** — 중간 파일: `dashboard.py`, `auth.py`, `sessions.py`, `themes.py`
6. **Pattern A 단계 3** — 대형 파일: `chat.py`, `education.py`, `inquiry.py`, `proposals.py`, `watchlist.py`
7. **Pattern A 단계 4** — 관리자: `admin.py`, `user_admin.py`
8. **Pattern B 단계 1** — 페이지 컨텍스트 이전 (작은 파일 4-5개씩 그룹)
9. **Pattern B 단계 2** — 페이지 컨텍스트 나머지
10. **최종 baseline diff + 수동 스모크**

## 7. 검증 전략

B1/B2와 동일:
- **자동**: `scripts/route_baseline.py diff 00-before-v2 <stage>` = 0
- **부팅**: `python -c "import api.main"` 각 단계 후
- **수동 스모크**: 5개 페이지 + 필터 클릭 (Pattern B 이후 특히 중요 — context가 제대로 주입되는지 확인)

## 8. 위험 & 완화

| 위험 | 완화 |
|---|---|
| Pattern A: 함수 본문에서 `conn` 변수 재사용·shadow — yield dependency는 함수 끝에 close | 한 라우트당 `conn` 1회 사용. 재대입 금지 (linter/grep 확인) |
| Pattern A: 재귀/중첩 라우트 호출로 yield dependency 다중 생성 | FastAPI는 요청당 1회 해소. 라우트 내부에서 다른 라우트 함수 호출 시 conn 인자 명시 전달로 우회 |
| Pattern B: 함수 본문이 `request`, `user`, `auth_cfg` 직접 참조 — 치환 누락 | 각 함수당 grep으로 참조 수집 → `ctx["..."]` 로 치환. 치환 후 `request`, `user`, `auth_cfg` 출현 0 검증 |
| Pattern B: TemplateResponse 호출에서 `request=request` 요구 | `request=ctx["request"]` 로 치환 |
| Pattern B: 리다이렉트 `RedirectResponse(...)` 에서 `request` 불필요 | 그대로 (리다이렉트는 request 안 씀) |
| SSE/stream 라우트 (admin.py): yield dependency가 stream 생명주기와 안 맞을 수 있음 | SSE 라우트는 Pattern A 적용 전 수동 확인. 문제 시 그 라우트만 try/finally 패턴 유지 (단독 예외) |
| admin.py Pattern C: 모든 11곳이 함수 내부 DatabaseConfig()인지 아니면 class-level인지 | grep으로 위치별 확인 후 일괄 치환 |

## 9. 산출물

- `api/deps.py` 확장 (신규 2개 함수, 파일 합계 약 50줄)
- 라우트 파일 12-14개 수정
- **순 코드 감소 ~200줄** (75 try/finally × 3줄 = 225줄 + 24 ctx signature 축약 등)
- baseline diff = 0
- B2 Minor 2건 해결

## 10. 본 spec 범위 밖

- **B3**: 응답·예외 포맷 통일
- **C**: UX/템플릿 통합
- **A**: `shared/db.py` 분할
- **D**: `analyzer/` 파이프라인 분해
