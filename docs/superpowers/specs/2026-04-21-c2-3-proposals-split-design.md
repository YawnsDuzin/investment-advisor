# C2.3 — `proposals.html` 분할 (페이지 섹션 partial 추출) 설계 문서

- **작성일**: 2026-04-21
- **트랙**: C2.3 (긴 템플릿 partial 추출 — proposals)
- **대상**: `api/templates/proposals.html` (368줄)
- **선행**: C2.2 (`dashboard.html` → `partials/dashboard/*.html` × 7)
- **후속 관심사**: C2.4 (user_admin.html, admin_diagnostics.html 등 HTML-heavy 파일), C3 (인라인 JavaScript 분할 — admin.html, stock_fundamentals.html 등 JS-heavy 파일 우선)

## Context

C2.2에서 `dashboard.html`을 7개 섹션 partial로 분할하여 페이지 전용 partial 패턴(`partials/<page>/`)을 정립했다. C2.3은 이 패턴을 동일하게 `proposals.html`에 적용한다.

**대상 선정 배경**: 본래 C2.3 후보는 `admin.html`(486줄)이었으나, 실제 분석 결과 admin.html은 HTML body가 ~115줄, JavaScript가 ~365줄(75% JS)로 partial 추출 효과가 제한적이었다. JS-heavy 파일은 C3 트랙(인라인 JS 외부화)의 본격 대상이 더 적절하다고 판단하여, HTML 비중이 100%인 `proposals.html`을 C2.3으로 선정한다.

이 결정으로 트랙 경계가 명확해진다 — **C2 = HTML partial 추출**, **C3 = JS 외부화**.

## 목표

- `proposals.html`을 368줄 → ~130줄의 오케스트레이터로 축소
- 2개 섹션을 `api/templates/partials/proposals/` 하위로 분리
- 렌더 결과 완전 동일 (visual parity)
- 검증: 4개 템플릿 pre-load, `api.main` import, `pytest tests/` 신규 실패 0건, 수동 스모크

## Non-goals

- `{% block scripts %}` 내부 ~100줄 JavaScript 외부 파일 분리 → C3 트랙
- 필터 폼 단순화, 테이블 컬럼 조정, detail 패널 재구성 등 기능 변경
- `addWatch{{ p.id }}` 함수의 Jinja loop 기반 생성 패턴 변경 (스코프 외)
- `external_links` 매크로 시그니처 변경 (이동만)

## 설계 결정

### 결정 1: 추출 메커니즘 — `{% include %}` (C2.2와 동일)

C2.2에서 정립한 패턴을 그대로 적용. 컨텍스트 자동 상속으로 호출부 단순화. 페이지 전용 섹션이라 매크로의 명시적 인자 장점이 없음.

### 결정 2: 디렉터리 구조 — `partials/proposals/` (C2.2 패턴 적용)

`partials/dashboard/`와 동일한 페이지별 서브디렉터리 컨벤션. C2.4+ 확장 시에도 일관성 유지.

### 결정 3: partial 세분화 — 2개 (필터 폼 + 결과 테이블)

**선택**: `_filter_form.html` + `_results_table.html`

**근거**:
- proposals.html은 자연 섹션이 2개 (필터 / 결과). 더 잘게 쪼개면 인위적
- 각 partial이 200줄 미만으로 적정 (필터 ~67, 테이블 ~185)
- 3-partial 분할(detail 패널을 별도 추출)은 단일 제안 상세 페이지가 따로 없어 재사용 가설이 가설적임 (YAGNI)
- detail 패널을 별도 partial로 분리하면 부모 컨텍스트(`p`, `loop.index0`, `current_user`, `user_memos`)에 추가 의존이 발생

**트레이드오프**:
- `_results_table.html`이 ~185줄로 다소 큼 — 단일 책임(결과 테이블 렌더)이라 허용 가능

### 결정 4: 매크로 import는 partial 내부에서 자체 선언 (C2.2 원칙)

`external_links` 매크로 import는 `_results_table.html` 상단에서 독립 선언. proposals.html에서는 import 라인 제거.

### 결정 5: JS는 C2.3 범위에서 유지

`{% block scripts %}` 내부의 ~100줄 JavaScript(테이블 행 토글, watchlist toggle, 메모 저장/삭제, sticky CTA, 워치 추가 함수 동적 생성)는 그대로 proposals.html에 유지. 외부 파일 분리는 C3 트랙.

특히 `addWatch{{ p.id }}` 함수는 Jinja `{% for p in proposals %}` 루프로 동적 생성되며, 이를 외부 JS로 옮기려면 패턴 자체를 재설계해야 한다 — C3 트랙의 본격 작업.

## 파일 구성

### 신규 파일 (2개)

**`api/templates/partials/proposals/_filter_form.html`** (~67줄)

- 원본 라인: 7-73 (필터 폼 전체)
- 매크로 의존: 없음
- 컨텍스트:
  - `date_from`, `date_to` (str/None): 날짜 범위
  - `action`, `asset_type`, `conviction`, `market`, `sector`, `discovery_type`, `time_horizon`, `ticker`, `sort` (str/None): 현재 필터 값
  - `market_options`, `sector_options`, `discovery_type_options`, `time_horizon_options` (list): 동적 옵션
- 주의: 폼 제출 URL `/pages/proposals`, GET method 유지

**`api/templates/partials/proposals/_results_table.html`** (~185줄)

- 원본 라인: 75-259 (table-wrap + table 전체, tbody loop 포함)
- 매크로 의존: `external_links` (`_macros/common.html`)
- 컨텍스트:
  - `proposals` (list): 제안 목록 (호출부 proposals.html이 `{% if proposals %}`로 가드)
    - 각 p: id, action, asset_name, ticker, market, conviction, target_allocation, current_price, currency, target_price_low/high, upside_pct, quant_score, discovery_type, time_horizon, theme_name, analysis_date, sector, price_momentum_check, supply_chain_position, vendor_tier, sentiment_score, foreign_net_buy_signal, squeeze_risk, index_membership, return_1m/3m/6m/1y_pct, rationale, risk_factors, entry_condition, exit_condition
  - `current_user` (optional): 로그인 시 워치리스트 컬럼 + sticky CTA + 메모 섹션 표시
  - `watched_tickers` (set/list): 관심 종목 ticker 셋
  - `user_memos` (dict): {proposal_id: memo_content}
- 주의:
  - tbody 내부 `{% for p in proposals %}` 루프 유지 (각 p마다 row + detail-row 2개 row 생성)
  - colspan 계산식 `{% if current_user %}14{% else %}13{% endif %}` 유지
  - `addWatch{{ p.id }}` 함수 호출은 partial 안에 그대로 (함수 정의는 `{% block scripts %}`에 잔류)

### 변경 파일

**`api/templates/proposals.html`** (368 → ~130줄)

변경 후 구조:
```jinja
{% extends "base.html" %}
{% block title %}Proposals — AlphaSignal{% endblock %}
{% block page_title %}종목 스크리너{% endblock %}

{% block content %}
{% include "partials/proposals/_filter_form.html" %}

{% if proposals %}
{% include "partials/proposals/_results_table.html" %}
{% else %}
<div class="empty-state">
    <h3>조건에 맞는 제안 없음</h3>
    <p>필터를 조정하거나 분석을 먼저 실행하세요.</p>
</div>
{% endif %}
{% endblock %}

{% block scripts %}
<script>
// (~100줄 기존 JS — 변경 없음)
</script>
{% endblock %}
```

- `{% from "_macros/common.html" import external_links %}` 제거 (`_results_table.html`로 이동)
- 본문 2개 섹션이 `{% include %}` 2개로 축소
- `{% block scripts %}` 내부는 변경 없음

### 컨텍스트 주석 컨벤션 (C2.2와 동일)

각 partial 상단:
```jinja
{#
  Proposals — <섹션 이름>
  출처: api/templates/proposals.html (C2.3, 2026-04-21)
  CONTEXT:
    - var1 (type): 설명
    ...
  매크로 의존:
    - macro_name from _macros/common.html
#}
{% from "_macros/common.html" import macro_name %}

... (body verbatim) ...
```

## 데이터 흐름

`proposals.html`은 `routes/proposals.py`의 `/pages/proposals` 핸들러로부터 모든 변수(필터값, 옵션 리스트, proposals, current_user, watched_tickers, user_memos)를 받는다. `{% include %}`는 부모 컨텍스트를 자동 상속하므로 partial은 별도 인자 없이 동일 변수명으로 접근.

## 크로스 파일 의존

```
proposals.html
├── include partials/proposals/_filter_form.html (매크로 의존 없음)
└── include partials/proposals/_results_table.html
    └── from _macros/common.html import external_links
```

## 검증 전략 (C2.2와 동일 패턴)

1. **Pre-render 4개 템플릿** — Jinja2 loader 구문 오류 0건:
   ```python
   tpls = ["proposals.html", "base.html",
           "partials/proposals/_filter_form.html",
           "partials/proposals/_results_table.html"]
   ```

2. **`api.main` import 성공** — `python -c "import api.main"`

3. **`pytest tests/` 신규 실패 0건** — baseline (10 failed, 59 passed, 6 errors) 유지

4. **proposals.html 매크로 호출 grep** — `external_links` 호출 0건 (모두 partial로 이동)

5. **`include` 호출 grep** — 2건 (`_filter_form.html`, `_results_table.html`)

6. **수동 스모크** — `python -m api.main` 기동 후:
   - 로그인 없이 `/pages/proposals` 접근 → 필터 + 테이블 정상 렌더
   - 로그인 후 동일 페이지 → 워치리스트 별 컬럼 + 메모 섹션 표시
   - 행 클릭 시 detail-row 펼치기/접기 동작
   - 필터 적용 → URL 갱신 + 결과 테이블 갱신
   - 메모 저장/삭제 동작

## 커밋 구조 (3개)

1. **Commit 1** — `feat(tpl): C2.3 — partials/proposals/ 2개 파일 생성 (VERBATIM 이동)`
2. **Commit 2** — `refactor(tpl): C2.3 — proposals.html을 include 기반 오케스트레이터로 축소`
3. **Commit 3** — `docs(refactor): C2.3 — proposals.html 분할 검증 완료 메모`

스펙 + 플랜 커밋은 별도 — 총 5개 커밋 (C2.2와 동일 구조).

## 리스크 및 완화

| 리스크 | 확률 | 완화책 |
|---|---|---|
| `_results_table.html`이 `loop.index0` 사용 — partial 내부 for 루프와 충돌 가능 | 낮음 | 원본의 `loop.index0`은 partial 내부 `{% for p in proposals %}` 루프에서 사용 → 같은 partial 내 루프이므로 정상 |
| `addWatch{{ p.id }}` 함수가 `{% block scripts %}`에서 정의되며, partial에서 호출 — 함수 미정의 우려 | 낮음 | 부모 페이지 렌더 시 scripts 블록도 함께 렌더되므로 함수는 항상 존재. partial로 옮겨도 동일 페이지 내 호출이라 문제 없음 |
| `external_links` 매크로가 `_results_table.html` 내부에서만 호출되는데 import 위치 변경 | 낮음 | C2.2에서 동일 패턴으로 검증된 방식 |
| 폼 제출 시 GET 파라미터 라우트가 partial 위치 변경의 영향을 받을 수 있다는 우려 | 없음 | partial은 단순 HTML 텍스트 치환 — 라우트와 무관 |

## 롤백 전략

각 커밋 독립 — `git revert <commit>`로 단계별 롤백 가능. 최악의 경우 `git revert HEAD~2..HEAD`로 Commit 1·2를 한 번에 되돌려 C2.2 직후 상태로 복귀.

## 참조

- **C2.2 스펙**: `docs/superpowers/specs/2026-04-21-c2-2-dashboard-split-design.md`
- **C2.2 플랜**: `docs/superpowers/plans/2026-04-21-c2-2-dashboard-split.md`
- **C2.2 검증**: `_docs/20260421_C2_2_dashboard_split_검증완료.md`
- **선례 partial 디렉터리**: `api/templates/partials/dashboard/` × 7