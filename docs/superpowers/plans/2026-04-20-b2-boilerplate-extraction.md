# B2: 페이지/라우트 보일러플레이트 추출 — 구현 계획서

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 14개 라우트 파일에 중복된 `Jinja2Templates` 인스턴스화·`_register_filters` 호출·`_get_cfg` 정의를 단일 소스(`api/templates_provider.py`, `api/deps.py`)로 통합한다.

**Architecture:** 두 개의 작은 공용 모듈을 신설해 한 번의 import로 모든 라우트가 공유하도록 만든다. 라우트 파일 헤더만 교체하고 함수 본문은 무변경. B1의 baseline diff 도구(`scripts/route_baseline.py diff 00-before-v2 <label>`)로 회귀 0을 단계별 검증.

**Tech Stack:** Python 3.10+, FastAPI, Jinja2, `scripts/route_baseline.py`, httpx.

**Spec:** [docs/superpowers/specs/2026-04-20-b2-boilerplate-extraction-design.md](../specs/2026-04-20-b2-boilerplate-extraction-design.md)

**Reference baseline:** `00-before-v2` (B1 post-Task-5 stable baseline).

---

## 사전 준비

- 모든 Task가 동일 패턴을 공유한다:
  1. 헤더 수정 (라우트 파일의 import 블록 + 보일러플레이트 정의 영역만)
  2. `_templates` 변수명을 쓰는 파일이면 본문의 `_templates.`을 `templates.`으로 치환
  3. `python -c "import api.main"` 부팅 테스트
  4. 서버 기동 (background) → `scripts/route_baseline.py capture --label <stage>` → `diff 00-before-v2 <stage>` = 0
  5. 서버 종료
  6. commit

- 서버 기동 명령어 (공통):
  ```bash
  PYTHONIOENCODING=utf-8 AUTH_ENABLED=false uvicorn api.main:app --host 0.0.0.0 --port 8000
  ```
  until-loop 대기:
  ```bash
  until curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/pages/landing | grep -qE "^(200|302|404|500)$"; do sleep 1; done
  ```
- 포트 정리 (Task 시작 시):
  ```bash
  netstat -ano | grep ':8000 ' | awk '{print $5}' | sort -u | xargs -I {} taskkill //F //PID {} 2>/dev/null || true
  sleep 2
  ```

---

## Task 1: 공용 모듈 2개 신설 (`api/deps.py`, `api/templates_provider.py`)

**Files:**
- Create: `api/deps.py`
- Create: `api/templates_provider.py`

- [ ] **Step 1: `api/deps.py` 신규 작성**

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

- [ ] **Step 2: `api/templates_provider.py` 신규 작성**

```python
"""단일 Jinja2Templates 인스턴스 — 모든 라우트가 공유 (B2)."""
from fastapi.templating import Jinja2Templates
from api.template_filters import register

templates = Jinja2Templates(directory="api/templates")
register(templates.env)
```

- [ ] **Step 3: import 검증**

```bash
python -c "from api.deps import get_db_cfg; from api.templates_provider import templates; print('OK')"
```
Expected: `OK`

- [ ] **Step 4: 서버 부팅 smoke test**

```bash
python -c "import api.main; print('OK')"
```
Expected: `OK` (기존 라우트에 영향 없음)

- [ ] **Step 5: baseline capture + diff**

```bash
# 포트 정리 후 서버 기동 (위 '공통' 명령어)
python scripts/route_baseline.py capture --label b2-01-modules
python scripts/route_baseline.py diff 00-before-v2 b2-01-modules
# 서버 종료
```
Expected: `[OK] diff 없음`

- [ ] **Step 6: 커밋**

```bash
git add api/deps.py api/templates_provider.py
git commit -m "feat(refactor): B2 — 공용 의존성 모듈 신설 (api/deps.py, api/templates_provider.py)"
```

---

## Task 2: `api/page_context.py` 갱신

**Files:**
- Modify: `api/page_context.py`

- [ ] **Step 1: `_get_cfg` 정의를 import로 교체**

현재 파일 상단 (줄 14-22 부근):
```python
from api.auth.models import UserInDB


def _get_cfg() -> DatabaseConfig:
    return DatabaseConfig()
```

교체:
```python
from api.auth.models import UserInDB
from api.deps import get_db_cfg as _get_cfg
```

> `base_ctx` 함수 본문의 `_get_cfg()` 호출은 그대로 유지 (import alias가 같은 이름 제공).

- [ ] **Step 2: 정리**

파일 상단의 `from shared.config import DatabaseConfig, AuthConfig` 에서 `DatabaseConfig`가 파일 내 다른 곳에서 쓰이는지 확인:
```bash
grep -n "DatabaseConfig" api/page_context.py
```
사용처가 함수 반환 타입 hint만이면 그대로 유지 (`from api.deps import get_db_cfg as _get_cfg`는 타입 hint 안쓰는 alias라서 `DatabaseConfig` 심볼 자체는 더이상 본 파일에서 필요 없음 — 안전하게 import는 유지하되, 안쓰이면 나중에 linter가 잡아줌).

- [ ] **Step 3: 부팅 테스트**

```bash
python -c "import api.main; print('OK')"
```

- [ ] **Step 4: baseline diff**

```bash
python scripts/route_baseline.py capture --label b2-02-page-context
python scripts/route_baseline.py diff 00-before-v2 b2-02-page-context
```
Expected: `[OK] diff 없음`

- [ ] **Step 5: 커밋**

```bash
git add api/page_context.py
git commit -m "refactor(api): B2 — page_context의 _get_cfg를 api.deps import로 교체"
```

---

## Task 3: 소형 파일 3개 일괄 이전 (`marketing.py`, `stocks.py`, `track_record.py`)

**Files:**
- Modify: `api/routes/marketing.py`
- Modify: `api/routes/stocks.py`
- Modify: `api/routes/track_record.py`

각 파일에 공통 패턴 적용.

- [ ] **Step 1: `marketing.py` 헤더 교체**

**Before (현재 상단 약 13-25줄):**
```python
from fastapi.templating import Jinja2Templates
...
from api.template_filters import register as _register_filters
...

templates = Jinja2Templates(directory="api/templates")
_register_filters(templates.env)
```

**After:**
- `from fastapi.templating import Jinja2Templates` → 삭제
- `from api.template_filters import register as _register_filters` → 삭제
- `templates = Jinja2Templates(directory="api/templates")` → 삭제
- `_register_filters(templates.env)` → 삭제
- 대신 다른 import 블록 가까이에 추가: `from api.templates_provider import templates`

**만약 `_get_cfg` 지역 정의가 없으면 (marketing.py는 없음):**
- 추가 조치 불필요.

- [ ] **Step 2: `stocks.py` 헤더 교체**

동일 패턴 적용. `stocks.py`는 `_get_cfg` 지역 정의 **없음** (확인: `grep "def _get_cfg" api/routes/stocks.py` 결과 없으면 skip).

변경:
- `templates = Jinja2Templates(...)` → `from api.templates_provider import templates` 로 교체 (다른 import 근처).
- `_register_filters(templates.env)` 라인 + 관련 import 삭제.

- [ ] **Step 3: `track_record.py` 헤더 교체**

`track_record.py`는 `_get_cfg` 지역 정의 **있음**. 두 가지 변경:
1. templates 블록 교체 (위 패턴)
2. `def _get_cfg() -> DatabaseConfig: return DatabaseConfig()` 정의 삭제
3. 파일 상단에 `from api.deps import get_db_cfg as _get_cfg` 추가

`from shared.config import DatabaseConfig`가 다른 용도로 쓰이는지 grep으로 확인:
```bash
grep -n "DatabaseConfig" api/routes/track_record.py
```
다른 용도 없으면 import 삭제. 있으면 유지.

- [ ] **Step 4: 부팅 테스트**

```bash
python -c "import api.main; print('OK')"
```

- [ ] **Step 5: baseline diff**

```bash
python scripts/route_baseline.py capture --label b2-03-small
python scripts/route_baseline.py diff 00-before-v2 b2-03-small
```
Expected: `[OK] diff 없음`

- [ ] **Step 6: 커밋**

```bash
git add api/routes/marketing.py api/routes/stocks.py api/routes/track_record.py
git commit -m "refactor(api): B2 — marketing/stocks/track_record 라우트의 보일러플레이트 제거"
```

---

## Task 4: 중형 파일 2개 이전 (`dashboard.py`, `auth.py`)

**Files:**
- Modify: `api/routes/dashboard.py`
- Modify: `api/routes/auth.py`

- [ ] **Step 1: `dashboard.py` 헤더 교체**

`dashboard.py`는 `_get_cfg` 지역 정의 **있음**. 3가지 변경:
1. `templates = Jinja2Templates(...)` + `_register_filters(templates.env)` 블록 제거
2. `def _get_cfg() -> DatabaseConfig: return DatabaseConfig()` 정의 제거
3. `from api.templates_provider import templates` 추가
4. `from api.deps import get_db_cfg as _get_cfg` 추가
5. `from fastapi.templating import Jinja2Templates` 삭제
6. `from api.template_filters import register as _register_filters` 삭제
7. `from shared.config import DatabaseConfig, AuthConfig` → 파일 내 다른 사용처 확인 후, `DatabaseConfig`가 안 쓰이면 `from shared.config import AuthConfig`로 축약

- [ ] **Step 2: `auth.py` 헤더 교체**

`auth.py`는 `_get_cfg` 지역 정의 **없음** (JSON API 위주). templates만 교체:
- `templates = Jinja2Templates(...)` 제거
- `_register_filters(templates.env)` 있으면 제거, 관련 import도 제거
- `from api.templates_provider import templates` 추가

auth.py는 templates 사용 횟수 10회로 가장 많은 편 — 함수 본문의 `templates.TemplateResponse(...)` 호출은 무변경.

- [ ] **Step 3: 부팅 테스트**

```bash
python -c "import api.main; print('OK')"
```

- [ ] **Step 4: baseline diff**

```bash
python scripts/route_baseline.py capture --label b2-04-medium
python scripts/route_baseline.py diff 00-before-v2 b2-04-medium
```
Expected: `[OK] diff 없음`

- [ ] **Step 5: 커밋**

```bash
git add api/routes/dashboard.py api/routes/auth.py
git commit -m "refactor(api): B2 — dashboard/auth 라우트의 보일러플레이트 제거"
```

---

## Task 5: `_templates` prefix 파일 3개 이전 (`sessions.py`, `themes.py`, `proposals.py`)

**Files:**
- Modify: `api/routes/sessions.py`
- Modify: `api/routes/themes.py`
- Modify: `api/routes/proposals.py`

**특이사항:** 이 3개 파일은 `_templates` (underscore prefix) 변수명을 사용 중. 변수명 통일도 함께 진행.

- [ ] **Step 1: `sessions.py` 헤더 + 변수명 통일**

변경 순서:
1. `_templates = Jinja2Templates(directory="api/templates")` → 제거
2. `_register_filters(_templates.env)` 제거
3. `from fastapi.templating import Jinja2Templates` 제거
4. `from api.template_filters import register as _register_filters` 제거
5. `def _get_cfg() -> DatabaseConfig: ...` 제거
6. 신규 import 추가: `from api.templates_provider import templates`, `from api.deps import get_db_cfg as _get_cfg`
7. 본문의 모든 `_templates.`을 `templates.`으로 치환 (Edit tool의 `replace_all: true` 사용)
8. `from shared.config import DatabaseConfig`가 다른 곳에서 쓰이는지 grep으로 확인, 안 쓰이면 제거

치환 검증:
```bash
grep -c "_templates\." api/routes/sessions.py
```
Expected: `0`

- [ ] **Step 2: `themes.py` 동일 패턴 적용**

위와 동일. `_templates.` → `templates.` 일괄 치환 포함.

- [ ] **Step 3: `proposals.py` 동일 패턴 적용**

proposals.py는 세 라우터(`router`, `api_router`, `pages_router`)를 가짐 — 이 구조는 건드리지 않음. `_templates` 치환과 `_get_cfg` 제거만 수행.

- [ ] **Step 4: 부팅 테스트**

```bash
python -c "import api.main; print('OK')"
```

- [ ] **Step 5: 잔존 `_templates` 참조 검증**

```bash
grep -n "_templates" api/routes/sessions.py api/routes/themes.py api/routes/proposals.py
```
Expected: 출력 없음 (모두 `templates`로 통일됨)

- [ ] **Step 6: baseline diff**

```bash
python scripts/route_baseline.py capture --label b2-05-prefix
python scripts/route_baseline.py diff 00-before-v2 b2-05-prefix
```
Expected: `[OK] diff 없음`

- [ ] **Step 7: 커밋**

```bash
git add api/routes/sessions.py api/routes/themes.py api/routes/proposals.py
git commit -m "refactor(api): B2 — sessions/themes/proposals 보일러플레이트 제거 + _templates→templates 통일"
```

---

## Task 6: 대형 파일 4개 이전 (`chat.py`, `education.py`, `inquiry.py`, `watchlist.py`)

**Files:**
- Modify: `api/routes/chat.py`
- Modify: `api/routes/education.py` (쓰임: `_templates` prefix)
- Modify: `api/routes/inquiry.py`
- Modify: `api/routes/watchlist.py` (쓰임: `_templates` prefix)

`education.py`와 `watchlist.py`는 `_templates` prefix 사용 — 변수명 통일 포함.

- [ ] **Step 1: `chat.py` 이전**

`chat.py`는 `_get_cfg` 지역 정의 있음, `templates` 변수명 사용. 5가지 변경:
1. templates 인스턴스화 블록 + `_register_filters` 호출 제거
2. `_get_cfg` 정의 제거
3. `from api.templates_provider import templates` 추가
4. `from api.deps import get_db_cfg as _get_cfg` 추가
5. 불필요해진 `from fastapi.templating import Jinja2Templates`, `from api.template_filters import register as _register_filters`, `from shared.config import DatabaseConfig` (다른 용도 없으면) 제거

- [ ] **Step 2: `education.py` 이전 (_templates → templates 포함)**

`education.py`는 `_templates` 사용. Task 5와 동일 패턴:
1. templates 블록 + `_register_filters` 호출 제거
2. `_get_cfg` 정의 제거
3. 신규 import 추가
4. 본문 `_templates.` → `templates.` 전체 치환
5. 불필요 import 제거

치환 검증:
```bash
grep -c "_templates\." api/routes/education.py
```
Expected: `0`

- [ ] **Step 3: `inquiry.py` 이전**

`inquiry.py`는 `templates` 변수명 사용 (prefix 없음). Task 5의 3~5개 변경 사항만 적용, `_templates` 치환 불필요.

- [ ] **Step 4: `watchlist.py` 이전 (_templates → templates 포함)**

`watchlist.py`는 `_templates` 사용. education.py와 동일 패턴. 치환 검증:
```bash
grep -c "_templates\." api/routes/watchlist.py
```
Expected: `0`

- [ ] **Step 5: 부팅 테스트**

```bash
python -c "import api.main; print('OK')"
```

- [ ] **Step 6: baseline diff**

```bash
python scripts/route_baseline.py capture --label b2-06-large
python scripts/route_baseline.py diff 00-before-v2 b2-06-large
```
Expected: `[OK] diff 없음`

- [ ] **Step 7: 커밋**

```bash
git add api/routes/chat.py api/routes/education.py api/routes/inquiry.py api/routes/watchlist.py
git commit -m "refactor(api): B2 — chat/education/inquiry/watchlist 보일러플레이트 제거 + _templates 통일"
```

---

## Task 7: 관리자 파일 2개 이전 (`admin.py`, `user_admin.py`)

**Files:**
- Modify: `api/routes/admin.py`
- Modify: `api/routes/user_admin.py`

둘 다 `_get_cfg` 지역 정의가 **없는** 것으로 예상됨 (사전에 `grep "def _get_cfg" api/routes/admin.py api/routes/user_admin.py` 로 확인). 있으면 Task 3과 동일 패턴, 없으면 templates만 교체.

- [ ] **Step 1: `admin.py` 헤더 교체**

사전 확인:
```bash
grep -n "def _get_cfg\|Jinja2Templates\|_register_filters" api/routes/admin.py
```

교체 항목:
1. `templates = Jinja2Templates(directory="api/templates")` → 제거
2. `_register_filters(templates.env)` 있으면 제거 + 해당 import 제거
3. `from fastapi.templating import Jinja2Templates` 제거
4. `from api.templates_provider import templates` 추가
5. (만약 `_get_cfg` 있으면) Task 3 패턴으로 교체

- [ ] **Step 2: `user_admin.py` 헤더 교체**

동일 패턴.

- [ ] **Step 3: 부팅 테스트**

```bash
python -c "import api.main; print('OK')"
```

- [ ] **Step 4: baseline diff**

```bash
python scripts/route_baseline.py capture --label b2-07-admin
python scripts/route_baseline.py diff 00-before-v2 b2-07-admin
```
Expected: `[OK] diff 없음`

- [ ] **Step 5: 커밋**

```bash
git add api/routes/admin.py api/routes/user_admin.py
git commit -m "refactor(api): B2 — admin/user_admin 보일러플레이트 제거"
```

---

## Task 8: 최종 검증 (baseline + 수동 스모크)

**Files:** (코드 변경 없음 — 검증만)

- [ ] **Step 1: 전체 잔존 중복 확인**

```bash
grep -rn "def _get_cfg" api/routes/ --include="*.py"
```
Expected: 출력 없음 (9개 지역 정의가 모두 제거됨).

```bash
grep -rn "Jinja2Templates(directory" api/routes/ --include="*.py"
```
Expected: 출력 없음 (14개 인스턴스화가 모두 `templates_provider`로 통합됨).

```bash
grep -rn "_register_filters(" api/routes/ --include="*.py"
```
Expected: 출력 없음.

```bash
grep -rn "_templates" api/routes/ --include="*.py"
```
Expected: 출력 없음.

- [ ] **Step 2: 최종 baseline 캡처 + diff**

```bash
python scripts/route_baseline.py capture --label b2-99-final
python scripts/route_baseline.py diff 00-before-v2 b2-99-final
```
Expected: `[OK] diff 없음 (00-before-v2 vs b2-99-final) (STATUS_ONLY로 body 무시: 3개)`

- [ ] **Step 3: 서버 정상 모드 (AUTH_ENABLED 원복) 기동 → 수동 스모크**

`AUTH_ENABLED=true` 또는 기본값으로 서버 기동:
```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

B1과 동일한 5개 페이지 + 1개 인터랙션:
- `http://localhost:8000/` (대시보드)
- `http://localhost:8000/pages/sessions`
- `http://localhost:8000/pages/themes` + 필터 클릭 1회
- `http://localhost:8000/pages/proposals`
- `http://localhost:8000/pages/proposals/history/<실제_ticker>`

우측 상단 사용자 드롭다운 + 알림 배지 정상 표시 확인.

- [ ] **Step 4: spec 문서에 검증 메모 추가**

`docs/superpowers/specs/2026-04-20-b2-boilerplate-extraction-design.md` 하단에 추가:
```markdown
## 검증 완료 (2026-04-20)

- **자동**: baseline diff `00-before-v2` vs `b2-99-final` = 0건 회귀
- **수동 스모크**: 5개 핵심 페이지 + 1개 인터랙션 정상
- **순 코드 감소**: 약 80줄 (중복 제거)
```

- [ ] **Step 5: 검증 메모 커밋**

```bash
git add docs/superpowers/specs/2026-04-20-b2-boilerplate-extraction-design.md
git commit -m "docs(refactor): B2 검증 완료 메모"
```

---

## 검증 요약

- **자동**: 모든 Task 후 `scripts/route_baseline.py diff 00-before-v2 <stage>` = 0건 회귀
- **수동**: Task 8에서 5개 페이지 + 1개 인터랙션
- **회귀 위험**: 라우트 함수 시그니처·본문 무변경, 헤더만 교체 → 위험 낮음. 위험 포인트는 `_templates` → `templates` 치환 누락(Task 5, 6에서 grep 0 검증으로 방어)

## 본 plan의 범위 밖 (B2.5/B3로 이월)

- **B2.5**: `_base_ctx` dependency 추출 (`Depends(make_page_ctx_dep("X"))`), DB 연결 컨텍스트 매니저화 (`with get_db_conn() as conn`)
- **B3**: JSON 응답 포맷 통일, `HTTPException` → 도메인 예외 + 글로벌 핸들러
