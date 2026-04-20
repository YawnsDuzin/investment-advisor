# B2 Design — 페이지/라우트 보일러플레이트 추출 (1차)

- **작성일**: 2026-04-20
- **작성자**: Claude (brainstorming skill, opus-4-7)
- **상위 컨텍스트**: 전체 리팩토링 분해 → "B → C 병렬 → A → D" 순서 → B의 두 번째 단계 (B2). **B1 완료 후 이어지는 작업**
- **다음 단계**: writing-plans skill 호출 → 구현 계획서 작성

---

## 0. 컨텍스트

B1에서 `pages.py`를 도메인별 파일에 콜로케이션 흡수한 결과, 각 도메인 파일이 동일한 보일러플레이트를 반복한다.

**현재 상태 (2026-04-20 측정):**

| 보일러플레이트 | 출현 횟수 | 영향 파일 수 |
|---|---|---|
| `def _get_cfg() -> DatabaseConfig: return DatabaseConfig()` 정의 | 10곳 | `page_context.py` + 9 라우트 |
| `Jinja2Templates(directory="api/templates")` 인스턴스화 | 14곳 | 14 라우트 (변수명 `templates` vs `_templates` 일관성도 깨짐) |
| `_register_filters(templates.env)` 호출 | 22회 | 모든 `Jinja2Templates` 인스턴스 |
| `Depends(get_current_user) + Depends(_get_auth_cfg) + _base_ctx(...)` 패턴 | ~24곳 | 11 페이지 라우트 파일 |
| `_get_auth_cfg` import | 49회 | 다수 |
| `get_connection(_get_cfg()) + try/finally` | ~75회 | 12 라우트 |

본 spec(B2)은 위 목록 중 **가장 안전한 3종을 추출**한다. 라우트 함수 시그니처 변경을 수반하는 항목은 B2.5로 분리한다. 응답·예외 포맷 표준화는 B3으로 분리한다.

## 1. Goals

본 B2 범위로 한정:

1. **단일 `templates` 인스턴스** — `api/templates_provider.py` 신설, 14개 파일이 import만으로 공유
2. **단일 `_get_cfg`** — 9개 중복 정의 삭제, `api/deps.py::get_db_cfg`로 통합
3. **`_register_filters` 호출 통합** — `templates_provider` 모듈 import 시 1회만 실행

## 2. Non-goals (B2.5 / B3로 이월)

다음 항목은 본 spec에서 **의도적으로 제외**한다. 각각 별도 brainstorming → spec 사이클로 진행한다.

- **B2.5 — `_base_ctx` 의존성화**: `Depends(get_current_user) + Depends(_get_auth_cfg) + ctx = _base_ctx(...)` 패턴을 `Depends(make_page_ctx_dep("page_name"))` 하나로 축약. 라우트 함수 시그니처 변경을 수반하고, 함수 본문에서 `user`/`auth_cfg`를 직접 참조하는 곳들의 수정을 요구함.
- **B2.5 — DB 연결 컨텍스트 매니저화**: `conn = get_connection(_get_cfg()); try: ... finally: conn.close()` 패턴을 `with get_db_conn() as conn:` 으로 단축. ~75회 출현, 함수 본문 수정.
- **B3 — 응답·예외 포맷 통일**: JSON 응답 포맷(`{data, error}` 등), `HTTPException` → 도메인 예외 + 글로벌 핸들러, 상태 코드 정책 정리.

## 3. 신규 모듈

### 3.1 `api/templates_provider.py`

```python
"""단일 Jinja2Templates 인스턴스 — 모든 라우트가 공유 (B2)."""
from fastapi.templating import Jinja2Templates
from api.template_filters import register

templates = Jinja2Templates(directory="api/templates")
register(templates.env)  # 모듈 import 시 1회 실행
```

**계약:**
- `templates` 심볼을 export. 기존 라우트 파일의 `templates` 지역 변수와 동일 용도.
- `register` 호출은 모듈 최초 import 시 딱 1회 실행 (Python 모듈 시스템 보장).
- 모듈 간 공유되는 단일 `Jinja2Environment` — 필터 변경이 모든 라우트에 자동 반영.

### 3.2 `api/deps.py`

```python
"""공통 FastAPI dependency 팩토리 (B2).

B2.5에서 `get_db_conn`(컨텍스트 매니저 dependency), `get_page_context` 등이
여기에 추가될 예정. 본 spec에서는 `_get_cfg` 중복 제거만 담당.
"""
from shared.config import DatabaseConfig


def get_db_cfg() -> DatabaseConfig:
    """DatabaseConfig 인스턴스 반환 — 라우트 기존 `_get_cfg()` 대체."""
    return DatabaseConfig()
```

**계약:**
- `get_db_cfg()` 시그니처 및 반환 타입은 기존 라우트의 `_get_cfg()` 와 동일.
- FastAPI `Depends(get_db_cfg)` 로도 직접 호출 `get_db_cfg()` 로도 사용 가능.

## 4. Migration 패턴

### Before (라우트 파일 헤더):

```python
from fastapi.templating import Jinja2Templates
from api.template_filters import register as _register_filters
from shared.config import DatabaseConfig

templates = Jinja2Templates(directory="api/templates")
_register_filters(templates.env)


def _get_cfg() -> DatabaseConfig:
    return DatabaseConfig()
```

### After:

```python
from api.templates_provider import templates
from api.deps import get_db_cfg as _get_cfg
```

- 함수 본문에서 `templates.TemplateResponse(...)` 호출과 `_get_cfg()` 호출은 무변경.
- `_templates` (underscore prefix) 로 된 파일은 `templates`로 통일 (본문에서 `_templates.TemplateResponse` → `templates.TemplateResponse` 치환).
- `from shared.config import DatabaseConfig` 가 **해당 파일에서 다른 용도로 쓰이지 않으면** 삭제. 다른 용도면 유지.
- `from api.template_filters import register` 는 통째로 삭제 (templates_provider가 대신 호출).

## 5. 영향 파일 목록 (15개)

**신규 2개:**
- `api/templates_provider.py`
- `api/deps.py`

**수정 14개 (라우트 헤더):**
- `api/routes/admin.py`
- `api/routes/auth.py`
- `api/routes/chat.py`
- `api/routes/dashboard.py`
- `api/routes/education.py`
- `api/routes/inquiry.py`
- `api/routes/marketing.py`
- `api/routes/proposals.py`
- `api/routes/sessions.py`
- `api/routes/stocks.py`
- `api/routes/themes.py`
- `api/routes/track_record.py`
- `api/routes/user_admin.py`
- `api/routes/watchlist.py`

**수정 1개 (공용 모듈):**
- `api/page_context.py` — 자체적인 `_get_cfg` 정의를 `from api.deps import get_db_cfg as _get_cfg`로 교체. `base_ctx` 함수 내부의 `_get_cfg()` 호출은 무변경.

**변경 없음:**
- `api/main.py` — templates 초기화 로직이 없으므로 수정 불필요 (`templates_provider`는 라우트 파일들이 각자 import).

## 6. 작업 순서

1. **신규 모듈 생성** — `api/deps.py`, `api/templates_provider.py` 신설 (동작 변화 0, 기존 코드 무영향)
2. **`api/page_context.py` 교체** — `_get_cfg` 정의를 `api.deps` import로 전환
3. **저위험 파일부터 라우트 헤더 교체**:
   - `marketing.py`, `stocks.py`, `track_record.py` (소형, 단순)
   - `dashboard.py`, `auth.py`
   - `sessions.py`, `themes.py`, `proposals.py`
   - `chat.py`, `education.py`, `inquiry.py`, `watchlist.py`
   - `admin.py`, `user_admin.py`
4. **baseline diff 검증** (각 단계 후 또는 일괄) — `scripts/route_baseline.py diff 00-before-v2 <stage>`
5. **최종 diff + 스모크** — 전체 완료 후 최종 diff + 수동 5페이지 확인

각 단계 commit으로 분리. 단계별 diff가 0이어야 진행.

## 7. 검증 전략

B1과 동일한 도구 + 전략:

- **자동 baseline diff**: `scripts/route_baseline.py diff 00-before-v2 b2-final` = 0건 회귀
- **부팅 테스트**: `python -c "import api.main"` 각 단계 후 통과
- **수동 스모크**: B1 완료 후 검증한 5개 페이지를 다시 확인 (`/`, `/pages/sessions`, `/pages/themes`, `/pages/proposals`, `/pages/proposals/history/{ticker}`)

## 8. 위험 & 완화

| 위험 | 완화 |
|---|---|
| `templates` 변수 mutability 차이 (지역 vs import) | 라우트 코드는 `templates` 객체 attribute만 사용 — 동작 동일. filter 등록은 templates_provider가 단일 소유 |
| `_register_filters` 누락 → 페이지 500 (TemplateSyntaxError) | templates_provider가 import-time에 1회 호출. 단일 진입점 보장. 테스트는 페이지 응답 baseline diff로 검출 |
| `_get_cfg` 시그니처 차이 | 동일 시그니처·반환 타입 (`() -> DatabaseConfig`) — 호출측 무변경 |
| 변수명 변경 (`_templates` → `templates`) 시 본문 미치환 | grep으로 파일별 `_templates\.` 출현 수집 → 모두 `templates.` 로 치환. import 후 `python -c "import api.main"` 로 즉시 검증 |
| `from shared.config import DatabaseConfig` 삭제 시 다른 용도 누락 | 헤더 수정 후 `grep "DatabaseConfig" <file>` 로 다른 용도 확인. 있으면 import 유지 |

## 9. 산출물

- 신규 파일 2개 (`templates_provider.py`, `deps.py`) — 합계 약 20줄
- 수정 파일 15개 (14 라우트 헤더 + page_context.py)
- **약 80줄 net 감소** (중복 제거)
- `scripts/route_baseline.py diff 00-before-v2 b2-final` = 0
- 수동 스모크 테스트 통과
- 본 spec 문서

## 10. 본 spec의 범위 밖 (재확인)

- **B2.5**: `_base_ctx` dependency 추출, DB 연결 컨텍스트 매니저화
- **B3**: 응답·예외 포맷 통일
- **C**: 템플릿·UX 개선 (병렬 진행 가능)
- **A**: `shared/db.py` 분할
- **D**: `analyzer/` 파이프라인 분해
