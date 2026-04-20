# B1 Design — `api/routes/pages.py` 도메인 콜로케이션 분할

- **작성일**: 2026-04-20
- **작성자**: Claude (brainstorming skill, opus-4-7)
- **상위 컨텍스트**: 전체 리팩토링 분해 → "B → C 병렬 → A → D" 순서 → B의 첫 번째 단계 (B1)
- **다음 단계**: writing-plans skill 호출 → 구현 계획서 작성

---

## 0. 컨텍스트

`api/routes/pages.py`는 1,673줄·31개 HTML 페이지 라우트로 구성되어 있다. 같은 도메인의 JSON API는 별도 파일(`sessions.py`, `themes.py`, `proposals.py` 등)에 분리되어 있어, 도메인 한 개를 수정할 때 두 파일을 동시에 편집해야 한다. 또한 `_serialize_row`는 `sessions.py`에 정의되어 있으나 `chat.py`/`education.py`/`inquiry.py`에서 cross-import되고 있어 의존 방향이 비정상적이다.

본 작업은 전체 리팩토링 로드맵의 **Sub-project B (API 라우트 일관성·중복 제거)** 중 **첫 단계 B1**이다. B2(보일러플레이트 추출)·B3(응답·예외 일관성)는 별도 spec 사이클로 진행한다.

## 1. 목표 & 비목표

### 목표
- `pages.py`를 도메인별 파일로 분산해 같은 도메인의 JSON API와 HTML 페이지를 한 파일에 모은다 ("콜로케이션").
- 모든 도메인 파일이 `router` (JSON, prefix=`/<domain>`) + `pages_router` (HTML, prefix=`/pages/<domain>`) 이중 라우터 패턴을 따른다.
- `_serialize_row` cross-import를 제거하고, 공용 유틸을 적절한 위치로 이전한다.
- `pages.py` 파일을 완전히 삭제한다.

### 비목표 (B2/B3로 이월)
- 인증 dependency·DB 연결·예외 처리 보일러플레이트 추출.
- JSON 응답 포맷 통일, HTTPException 표준화.
- HTML 컨텍스트 빌더 추출 (`_base_ctx`는 위치만 옮기고 시그니처·동작은 유지).

### 명시적 보존 사항
- 모든 URL 경로 (path, query, method) 동일.
- 모든 응답 (status code, body, headers, template) 동일.
- 모든 인증·권한 동작 동일 (Depends 시그니처 그대로 복사).
- 라우트 등록 순서 (FastAPI는 등록 순서대로 매칭) 동일.

## 2. 라우터 패턴 표준

기존 `proposals.py`의 `router + api_router` 이중 라우터 패턴을 표준화한다:

```python
# 도메인 파일 공통 헤더
router        = APIRouter(prefix="/sessions",         tags=["세션"])
pages_router  = APIRouter(prefix="/pages/sessions",   tags=["세션 페이지"])
```

`api/main.py`에서 두 라우터 모두 include. 도메인에 JSON API가 없으면(`dashboard`, `marketing`) `pages_router`만 정의한다.

> **예외**: `proposals.py`의 `/proposals/{id}/stock-analysis` 페이지 라우트는 prefix가 `/pages/`로 시작하지 않는다. **결정**: 기존 `router` (prefix=`/proposals`, JSON API) 에 페이지 라우트를 함께 등록한다. `response_class=HTMLResponse` 명시로 JSON과 구분. 별도 라우터 신설하지 않음.

## 3. 파일 분할 매핑

| 대상 파일 | 추가될 페이지 라우트 | 비고 |
|---|---|---|
| `api/routes/sessions.py` | `/pages/sessions`, `/pages/sessions/date/{d}`, `/pages/sessions/{id}` | 기존 JSON과 콜로케이션 |
| `api/routes/themes.py` | `/pages/themes`, `/pages/themes/history/{key}` | 〃 |
| `api/routes/proposals.py` | `/pages/proposals`, `/pages/proposals/history/{ticker}`, `/proposals/{id}/stock-analysis` | 마지막 라우트는 prefix 예외 처리 |
| `api/routes/stocks.py` | `/pages/stocks/{ticker}` | 기존 20줄 → 약 100줄 |
| `api/routes/chat.py` | `/pages/chat`, `/pages/chat/new/{id}`, `/pages/chat/{id}` | 〃 |
| `api/routes/education.py` | `/pages/education`, `/pages/education/topic/{slug}`, `/pages/education/chat`, `/pages/education/chat/new/{id}`, `/pages/education/chat/{id}` | 〃 |
| `api/routes/inquiry.py` | `/pages/inquiry`, `/pages/inquiry/new`, `/pages/inquiry/{id}` | 〃 |
| `api/routes/watchlist.py` | `/pages/watchlist`, `/pages/notifications`, `/pages/profile` | 알림·프로필도 개인화 도메인으로 묶음 |
| `api/routes/track_record.py` | `/pages/track-record` | 〃 |
| **신규** `api/routes/dashboard.py` | `/` (대시보드) | 도메인 없는 메인 페이지 |
| **신규** `api/routes/marketing.py` | `/pages/landing`, `/pages/pricing` | 마케팅/가격 페이지 |

`api/routes/pages.py` → **삭제** (재export shim 두지 않음).

## 4. 공용 유틸 이전

| 현재 위치 | 신규 위치 | 사용처 |
|---|---|---|
| `pages.py::_nl_numbered`, `_fmt_price`, `_markdown_to_html` | `api/template_filters.py` | `api/main.py`에서 `register(env)` 호출로 Jinja2 필터 등록 |
| `pages.py::_base_ctx` | `api/page_context.py` | 모든 페이지 라우트 (시그니처·동작 보존) |
| `pages.py::_get_cfg`, `_get_auth_cfg` | 그대로 (각 도메인 파일에서 재정의) | B2에서 `api/deps.py`로 통합 예정 |
| `sessions.py::_serialize_row` | `api/serialization.py` | sessions/chat/education/inquiry |

> 위치 이전 후 `pages.py`에서의 import는 모두 신규 모듈로 변경. 다른 라우트 파일의 cross-import도 동시에 새 위치로 변경.

## 5. 작업 순서

위험을 최소화하기 위해 작은·독립적인 도메인부터 단계적으로 진행한다. 각 단계는 별도 커밋으로 분리한다.

| 단계 | 작업 | 검증 |
|---|---|---|
| 0 | baseline 스크립트(`scripts/route_baseline.py`) 작성 + 현 상태 캡처 | baseline JSON 생성 확인 |
| 1 | 공용 유틸 신설: `template_filters.py`, `page_context.py`, `serialization.py` + `main.py`에 필터 등록 + 기존 `pages.py`/`sessions.py`의 import만 새 위치로 변경 (라우트 이전 0) | baseline diff = 0 |
| 2 | 저위험 도메인 이전: `marketing.py`(신설), `dashboard.py`(신설), `track_record.py`, `stocks.py` | baseline diff = 0 |
| 3 | 중간 도메인 이전: `watchlist.py`(+notifications/profile), `chat.py`, `education.py`, `inquiry.py` | baseline diff = 0 |
| 4 | 고위험 도메인 이전: `sessions.py`, `themes.py`, `proposals.py` | baseline diff = 0, 수동 5개 페이지 확인 |
| 5 | `pages.py` 삭제 + `main.py`에서 `pages.router` include 제거 + 새 `pages_router`들 include 추가 | 서버 부팅 + 전체 baseline 재실행 |

## 6. 검증 전략

자동 회귀 테스트 부재. 다음 두 가지를 결합한다.

### 6.1 자동 baseline diff (필수)
- **스크립트**: `scripts/route_baseline.py`
- **동작**: 기동 중인 API 서버에 모든 페이지 URL을 `httpx`로 GET 요청 → `(url, status_code, content_length, sha256(html))` 기록 → `_baselines/route_baseline_<timestamp>.json` 저장
- **인증 필요한 페이지**: baseline 캡처 시 한시적으로 `AUTH_ENABLED=false` 환경변수로 서버 기동. 캡처 완료 후 다시 활성화. 스크립트 README에 명시. (이유: 토큰 발급·갱신 로직을 baseline 도구가 흉내 내면 검증 대상이 늘어남)
- **운영**: 단계 0에서 1회 캡처, 각 단계 종료 시 재캡처 후 diff. 차이 발생 시 즉시 원인 분석

### 6.2 수동 스모크 테스트
- 단계 4 종료 후 다음 5개 페이지를 브라우저에서 직접 확인:
  - `/` (대시보드 — 데이터·시각화 정상 렌더)
  - `/pages/sessions` (세션 목록)
  - `/pages/themes` (테마 + 필터)
  - `/pages/proposals` (제안 + 페이지네이션)
  - `/pages/proposals/history/<ticker>` (티커 히스토리)
- 핵심 인터랙션 1개씩 (필터 클릭, 링크 이동) 동작 확인

## 7. 위험 & 완화

| 위험 | 완화 |
|---|---|
| URL 중복/누락 (라우트 이전 중 `@router.get` 빠뜨림) | baseline 스크립트가 모든 URL 응답을 캡처 → 누락 시 404로 즉시 발견 |
| Jinja2 필터 등록 누락 → 페이지 500 (TemplateSyntaxError) | 신규 `template_filters.py::register(env)`에 한 곳에 모음, `main.py`에서 1회 호출 |
| 인증 dependency 시그니처 누락 → 권한 우회 | 라우트 이전 시 `Depends(...)` 시그니처를 텍스트 그대로 복사. 코드 리뷰 시 diff로 검증 |
| 라우트 매칭 순서 변경 (`/{id}` 와 `/new` 등 path 충돌) | 기존 `pages.py` 내 등록 순서 보존. FastAPI는 등록 순서대로 매칭 |
| 공용 유틸 이전 시 import 경로 변경 누락 → ImportError | 단계 1에서 한 번에 모든 cross-import 일괄 변경. 테스트는 `python -c "import api.main"` 으로 즉시 검증 |
| baseline에 잡히지 않는 부수효과 (POST 폼, JS 동작) | 본 spec은 GET 페이지 라우트만 대상. POST는 모두 기존 JSON API 라우트(`router`)에 있고 본 작업에서 변경 없음 |

## 8. 산출물

- **코드 변경**: 9개 기존 파일 수정 (`sessions.py`, `themes.py`, `proposals.py`, `stocks.py`, `chat.py`, `education.py`, `inquiry.py`, `watchlist.py`, `track_record.py`), 5개 파일 신설 (`dashboard.py`, `marketing.py`, `template_filters.py`, `page_context.py`, `serialization.py`), 1개 파일 삭제 (`pages.py`), 1개 파일 수정 (`main.py`)
- **검증 도구**: `scripts/route_baseline.py`
- **본 spec 문서**

## 9. 본 spec의 범위 밖 (B2/B3로 이월)

다음 항목은 의도적으로 본 spec에서 제외했다 — B1 완료 후 별도 brainstorming → spec 사이클로 진행:

- **B2 보일러플레이트 추출**: `_get_cfg`/`_get_auth_cfg`를 `api/deps.py`로 통합, `RealDictCursor` + `try/finally` 패턴을 컨텍스트 매니저화, `_base_ctx` 호출을 `Depends`로 자동화
- **B3 응답·예외 일관성**: JSON 응답 포맷 (`{data, error}` 등) 통일, `HTTPException` → 도메인 예외 + 글로벌 핸들러, 상태 코드 정책 정리

## 검증 완료 (2026-04-20)

- **자동**: baseline diff `00-before-v2` vs `99-final` = **0건 회귀** (27개 페이지 응답 byte-동일, 3개 STATUS_ONLY 라우트는 status 일치)
- **수동 스모크**: 5개 핵심 페이지 + 1개 인터랙션 정상 확인 (`/`, `/pages/sessions`, `/pages/themes`, `/pages/proposals`, `/pages/proposals/history/{ticker}`)
- **커밋 수**: 21개 (c0966d1 → 836660b)
- **최종 코드 리뷰**: Critical 0건, Important 5건 모두 pre-existing 또는 dead-import (B2/B3 cleanup 후보)
