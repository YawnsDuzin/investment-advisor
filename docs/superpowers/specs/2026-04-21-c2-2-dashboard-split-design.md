# C2.2 — `dashboard.html` 분할 (페이지 섹션 partial 추출) 설계 문서

- **작성일**: 2026-04-21
- **트랙**: C2.2 (긴 템플릿 partial 추출)
- **대상**: `api/templates/dashboard.html` (660줄)
- **선행**: C2.1 (`_macros.html` → `_macros/{proposal,theme,common}.html`)
- **후속 관심사**: C2.3+ (admin.html, user_admin.html 등 나머지 대형 템플릿), C3 (인라인 JavaScript 분할)

## Context

C2.1에서 20개 매크로를 3개 도메인 파일로 분리하여 "크로스 페이지 재사용 시각 컴포넌트" 영역을 정리했다. 그러나 단일 페이지 템플릿은 여전히 거대하다. `dashboard.html`은 7개 섹션(Hero Row 1·2, Market Summary, Yield Curve, Themes, Top Picks, News)과 오케스트레이션 로직이 한 파일에 섞여 있어, 한 섹션을 수정하려면 전체 파일을 훑어야 하는 구조다.

C2.2는 `dashboard.html`만을 대상으로 7개 섹션을 `partials/dashboard/` 서브디렉터리로 추출하고, 상위 템플릿을 "오케스트레이터"로 축소한다. 나머지 대형 템플릿(admin 486줄, user_admin 424줄 등)은 이 트랙에서 정립된 패턴을 그대로 적용할 수 있도록 C2.3 이후로 분리한다.

## 목표

- `dashboard.html`을 660줄 → ~80줄의 오케스트레이터로 축소
- 7개 섹션을 `api/templates/partials/dashboard/` 하위로 분리
- 기존 partial(`partials/_ad_slot.html` 등 5개)과 매크로(`_macros/*`)의 역할 경계 명확화
- 렌더 결과 완전 동일 (visual parity)
- 검증: 9개 템플릿 pre-load, `api.main` import, `pytest tests/` 신규 실패 0건, 수동 스모크

## Non-goals

- `admin.html`, `user_admin.html`, `admin_diagnostics.html`, `proposals.html` 등 다른 대형 템플릿 분할 → C2.3 이후
- 인라인 JavaScript 외부 파일 분리 (Market Summary 토글, Track Record 렌더, toggleWatchlist) → C3
- dashboard 페이지의 비즈니스 로직 변경, CSS 조정, 기능 추가/제거
- 매크로 재구성 또는 신규 매크로 추가
- partial 파일에 대한 재사용성(다른 페이지에서 include) 확장

## 설계 결정

### 결정 1: 추출 메커니즘 — `{% include %}` (매크로 아님)

**선택**: 섹션별 `.html` 파일 + `{% include %}`로 조합.

**근거**:
- 기존 `partials/_ad_slot.html` 등 5개가 모두 include 패턴 → 컨벤션 일관성
- dashboard 섹션은 페이지 전용이라 "명시적 인자를 받는 재사용 컴포넌트"의 장점이 없음
- 컨텍스트 변수가 섹션당 5~10개 → 매크로 인자로 모두 받으면 호출부가 가독성을 잃음 (예: `{{ top_picks_section(top_picks, watched_tickers, current_user, tier, ...) }}`)
- Jinja2 `{% include %}`는 부모 컨텍스트를 자동 상속하므로 호출부가 단순해짐

**트레이드오프**:
- include는 partial이 어떤 변수를 참조하는지가 암묵적 → 각 partial 상단에 `{# CONTEXT: ... #}` 주석으로 의존 변수를 명시하여 보완

### 결정 2: 디렉터리 구조 — 페이지별 서브디렉터리

**선택**: `api/templates/partials/dashboard/`

**근거**:
- 기존 `partials/` 5개(`_ad_slot`, `_bottom_tabbar`, `_common_modal`, `_disclaimer_banner`, `_upgrade_modal`)는 모두 "크로스 페이지 재사용"
- 페이지 전용 섹션을 같은 레벨에 두면 "재사용 목적"과 "페이지 전용" 구분이 흐려짐
- 서브디렉터리 방식은 C2.3(admin), C2.4(user_admin) 등 확장 시에도 `partials/admin/`처럼 일관된 패턴 유지
- 새로운 최상위 디렉터리(`_sections/` 등) 신설은 YAGNI (기존 `partials/`와 역할이 겹치지 않음)

**트레이드오프**:
- 디렉터리 깊이가 1단계 증가 (`partials/` → `partials/dashboard/`)

### 결정 3: 매크로와 partial의 역할 분리

- **매크로(`_macros/*`)** — 여러 페이지에서 재사용되는 시각 원자 컴포넌트 (proposal_card, grade_badge, sparkline, external_links 등)
- **partial(`partials/`)** — 크로스 페이지 재사용 블록 (광고, 탭바, 모달)
- **페이지 partial(`partials/<page>/`)** — 특정 페이지의 논리적 섹션

C2.2는 세 번째 범주를 최초로 도입한다.

### 결정 4: 각 partial의 매크로 import는 파일 내부에서 독립 선언

**선택**: partial이 사용하는 매크로는 각 partial 상단에서 `{% from "_macros/..." import ... %}`로 import.

**근거**:
- dashboard.html이 상속시켜도 `{% include %}`한 partial에서 매크로 이름이 보이지만, 파일 독립성(partial만 열어봐도 의존을 파악) 확보가 우선
- C2.1의 `_macros/theme.html`이 `grade_badge`를 독립 import한 것과 동일 원칙

**트레이드오프**:
- dashboard.html과 partial 양쪽에 동일 import가 일부 중복될 수 있음 — 허용 가능

### 결정 5: 인라인 JS는 C2.2 범위에서 유지

**선택**: 섹션 내부의 인라인 JS(Track Record 렌더 `<script>` 블록, Market Summary 모바일 토글 JS, toggleWatchlist JS)는 그대로 partial에 이식. 외부 파일로 분리하지 않음.

**근거**:
- JS 외부화는 C3 트랙의 범위
- C2.2에서 JS까지 손대면 변경 범위가 커져 렌더 parity 검증이 어려움
- Track Record JS는 `_hero_row1.html` 내부, toggleWatchlist와 Market Summary 모바일 JS는 dashboard.html `{% block scripts %}` 내부 원 위치 유지

## 파일 구성

### 신규 파일 (7개)

**`api/templates/partials/dashboard/_hero_row1.html`** (~155줄)

- 원본 라인: 14-88 (KPI 6개 strip + Track Record 위젯 HTML), 148-224 (Track Record 렌더 JS)
- 매크로 의존: `change_indicator` (`_macros/proposal.html`)
- 컨텍스트: `issue_count`, `issue_delta`, `theme_count`, `theme_delta`, `buy_count`, `buy_delta`, `high_conviction_count`, `early_signal_count`, `total_alloc`, `avg_confidence`
- 주의: 내부 `<script>` 블록은 위젯과 함께 이동 (DOM id `dash-tr-*` 참조)

**`api/templates/partials/dashboard/_hero_row2.html`** (~60줄)

- 원본 라인: 90-147 (워치리스트 / 발굴 유형 분포 / 주요 섹터 3-col)
- 매크로 의존: `risk_gauge` (`_macros/proposal.html`), `discovery_stackbar`, `sector_chips` (`_macros/theme.html`)
- 컨텍스트: `current_user`, `watched_in_today`, `risk_pct`, `session`, `discovery_counts`, `top_sectors`
- 주의: `current_user` 분기(로그인 vs 비로그인)로 워치리스트 또는 리스크 게이지 표시

**`api/templates/partials/dashboard/_market_summary.html`** (~50줄)

- 원본 라인: 230-277 (Market Summary 접이식)
- 매크로 의존: `risk_gauge` (`_macros/proposal.html`)
- 컨텍스트: `session`, `current_user`, `risk_pct`
- 주의: 토글 버튼의 `onclick` 인라인 JS(`document.getElementById('market-summary-block')...`) 유지. DOM id `market-summary-block` 그대로

**`api/templates/partials/dashboard/_yield_curve.html`** (~30줄)

- 원본 라인: 279-307
- 매크로 의존: `yield_curve` (`_macros/common.html`)
- 컨텍스트: `bond_yields`
- 주의: `{% if bond_yields %}` 가드 partial 내부에 유지

**`api/templates/partials/dashboard/_themes_list.html`** (~66줄)

- 원본 라인: 309-374 (투자 테마 + 소멸 테마 details)
- 매크로 의존: `theme_header`, `indicator_tags` (`_macros/theme.html`)
- 컨텍스트: `themes`, `active_tracking`, `tier`, `theme_view_limit`, `watched_tickers`, `session`, `disappeared_themes`
- 주의: `is_free` / `locked` 계산 로직 partial 내부로 이동

**`api/templates/partials/dashboard/_top_picks.html`** (~165줄)

- 원본 라인: 380-541
- 매크로 의존: `bullet_chart` (`_macros/proposal.html`), `conf_ring` (`_macros/theme.html`), `external_links` (`_macros/common.html`)
- 컨텍스트: `top_picks`, `current_user`
- 주의: `{% if top_picks %}` 가드 partial 내부에 유지. `onclick="toggleWatchlist(this)"` 호출부는 유지(함수는 dashboard.html `{% block scripts %}`에 잔류)

**`api/templates/partials/dashboard/_news_by_category.html`** (~50줄)

- 원본 라인: 543-593
- 매크로 의존: 없음
- 컨텍스트: `news_by_category`, `session`

### 변경 파일

**`api/templates/dashboard.html`** (660 → ~80줄)

변경 후 구조:
```jinja
{% extends "base.html" %}
{% from "partials/_ad_slot.html" import ad_slot with context %}

{% block title %}Dashboard — AlphaSignal{% endblock %}
{% block page_title %}Dashboard{% endblock %}

{% block content %}
{{ ad_slot('banner') }}

{% if session %}
  {% include "partials/dashboard/_hero_row1.html" %}
  {% include "partials/dashboard/_hero_row2.html" %}
  {% include "partials/dashboard/_market_summary.html" %}
  {% if bond_yields %}{% include "partials/dashboard/_yield_curve.html" %}{% endif %}
  {% include "partials/dashboard/_themes_list.html" %}
  {% if top_picks %}{% include "partials/dashboard/_top_picks.html" %}{% endif %}
  {% if news_by_category %}{% include "partials/dashboard/_news_by_category.html" %}{% endif %}
{% else %}
  <div class="empty-state">
    <h3>분석 데이터 없음</h3>
    <p>아직 분석이 실행되지 않았습니다. <code>python -m analyzer.main</code>을 실행하세요.</p>
  </div>
{% endif %}
{% endblock %}

{% block scripts %}
<script>
// 모바일: Market Summary 기본 접힘
// 관심 종목 토글 (추가/제거)
...
</script>
{% endblock %}
```

- 매크로 import 중 `dashboard.html`에서 직접 사용되는 것은 없음 → 모든 `{% from "_macros/..." import ... %}` 제거 (각 partial이 자체 import)
- `partials/_ad_slot.html`의 `ad_slot`은 dashboard.html에서 직접 호출하므로 import 유지
- `{% block scripts %}` 내부 JS 2개(mobile collapse, toggleWatchlist)는 그대로 유지

### 컨텍스트 주석 컨벤션

각 partial 상단은 다음 포맷을 따른다:

```jinja
{#
  Dashboard — <섹션 이름>
  출처: api/templates/dashboard.html (C2.2, 2026-04-21)
  CONTEXT:
    - session (object): 분석 세션
    - top_picks (list): 오늘의 Top Picks
    - current_user (optional): 로그인 사용자
  매크로 의존:
    - bullet_chart from _macros/proposal.html
    - conf_ring from _macros/theme.html
#}
{% from "_macros/proposal.html" import bullet_chart %}
{% from "_macros/theme.html" import conf_ring %}
{% from "_macros/common.html" import external_links %}

... (body verbatim) ...
```

## 데이터 흐름

`dashboard.html`은 `routes/pages.py`의 `/pages/dashboard` 핸들러로부터 `session`, `themes`, `top_picks`, `news_by_category`, `current_user`, `tier` 등 모든 변수를 한 번에 받는다. `{% include %}`는 Jinja2 기본 동작으로 부모 컨텍스트를 자동 상속하므로, partial은 별도의 인자 전달 없이 원본과 동일한 변수 이름으로 접근한다.

`current_user`, `session` 등은 `_base_ctx()`에서 주입되며, 이 흐름은 C2.2에서 변경하지 않는다.

## 크로스 파일 의존

```
dashboard.html
├── include partials/_ad_slot.html (기존)
├── include partials/dashboard/_hero_row1.html
│   └── from _macros/proposal.html import change_indicator
├── include partials/dashboard/_hero_row2.html
│   ├── from _macros/proposal.html import risk_gauge
│   └── from _macros/theme.html import discovery_stackbar, sector_chips
├── include partials/dashboard/_market_summary.html
│   └── from _macros/proposal.html import risk_gauge
├── include partials/dashboard/_yield_curve.html
│   └── from _macros/common.html import yield_curve
├── include partials/dashboard/_themes_list.html
│   └── from _macros/theme.html import theme_header, indicator_tags
├── include partials/dashboard/_top_picks.html
│   ├── from _macros/proposal.html import bullet_chart
│   ├── from _macros/theme.html import conf_ring
│   └── from _macros/common.html import external_links
└── include partials/dashboard/_news_by_category.html (매크로 의존 없음)
```

모든 import는 기존 C2.1 결과물인 `_macros/*` 경로를 사용한다.

## 검증 전략

C2.1과 동일 패턴. 각 커밋 직후 아래를 확인한다.

1. **Pre-render 9개 템플릿** — 다음 스크립트로 Jinja2 loader가 구문 오류 없이 파싱하는지 확인:
   ```python
   from jinja2 import Environment, FileSystemLoader
   env = Environment(loader=FileSystemLoader("api/templates"))
   for tpl in ["dashboard.html", "base.html",
               "partials/dashboard/_hero_row1.html",
               "partials/dashboard/_hero_row2.html",
               "partials/dashboard/_market_summary.html",
               "partials/dashboard/_yield_curve.html",
               "partials/dashboard/_themes_list.html",
               "partials/dashboard/_top_picks.html",
               "partials/dashboard/_news_by_category.html"]:
       env.get_template(tpl)
   ```

2. **`api.main` import 성공** — `python -c "import api.main"` 무오류.

3. **`pytest tests/` 신규 실패 0건** — 분할 전 17 failed / 52 passed 기준. 신규 실패가 없어야 함.

4. **`grep -rn "_macros\\.html" api/`** — 구 `_macros.html` 참조는 C2.1에서 이미 제거됨. 활성 참조 0건 재확인.

5. **수동 스모크 테스트** — 로컬에서 `python -m api.main` 기동 후:
   - 로그인 없이 `/pages/dashboard` 접근 → 에러 없이 렌더, 콘솔 에러 0건
   - 로그인 후 `/pages/dashboard` 접근 → 워치리스트/Track Record/Top Picks 정상 표시
   - Market Summary 접기/펼치기 동작
   - Track Record 기간 탭(1M/3M/6M/1Y) 전환 동작
   - Top Picks 카드의 관심 종목 토글 동작

6. **렌더 parity 수동 비교** — 분할 전/후의 페이지 HTML을 diff하여, 공백 외의 차이가 없는지 확인 (`curl -s http://localhost:8000/pages/dashboard > /tmp/after.html`).

## 커밋 구조

1. **Commit 1** — `feat(tpl): C2.2 — partials/dashboard/ 7개 파일 생성 (VERBATIM 이동)`
   - `api/templates/partials/dashboard/` 생성 + 7개 partial 파일 작성
   - `dashboard.html`은 아직 변경하지 않음
   - 검증: 9개 템플릿 pre-load 성공 (기존 dashboard.html + 새 partial 모두 파싱 가능)

2. **Commit 2** — `refactor(tpl): C2.2 — dashboard.html을 include 기반 오케스트레이터로 축소`
   - `dashboard.html`의 섹션 7개를 `{% include %}`로 교체
   - 사용하지 않는 매크로 import 구문 제거
   - 검증: `api.main` import, 수동 스모크, 렌더 parity diff

3. **Commit 3** — `docs(refactor): C2.2 — dashboard.html 분할 검증 완료 메모`
   - `_docs/20260421_C2_2_dashboard_split_검증완료.md` 작성
   - 분할 전/후 라인 수 표, 파일별 담당, 검증 통과 항목, 후속 관심사

계획서 커밋(이 스펙 문서 + 플랜 문서)은 설계 단계에서 별도로 1회 추가 — 총 4개 커밋.

## 리스크 및 완화

| 리스크 | 확률 | 완화책 |
|---|---|---|
| partial 내부 변수명이 누락되어 `UndefinedError` 발생 | 중 | 각 partial 상단 CONTEXT 주석 + 수동 스모크로 로그인/비로그인 2케이스 검증 |
| include 순서 의존성 (A가 B보다 먼저 렌더되어야 할 경우) | 낮음 | Jinja2 include는 단순 텍스트 치환 — 순서 의존 없음. 원본과 동일 순서 유지 |
| `{% block scripts %}` 내부 JS가 partial 내부 DOM id를 참조 | 중 | `market-summary-block`, `dash-tr-donut-*` 등 DOM id는 원래 위치에 그대로 유지. JS 블록은 partial로 옮기지 않음 |
| 렌더 결과 공백/개행 차이로 시각적 변화 | 낮음 | `{% include %}`는 blank line을 추가할 수 있음 — `{%- -%}` 트리밍이 필요한 경우 개별 적용 (대부분의 섹션은 `<div>` 단위라 영향 없음) |
| `_hero_row1.html`의 Track Record JS가 DOM 초기화 시점 의존 | 낮음 | 원본 JS는 `<script>` 블록 내부에서 IIFE로 즉시 실행되며 `fetch` 후 `render()` 호출 — partial 이동 후에도 동일하게 동작 |

## 롤백 전략

각 커밋이 독립적이므로 문제 발생 시 `git revert <commit>` 로 되돌릴 수 있다:
- Commit 2 revert → dashboard.html이 원본으로 복원, partial 파일은 남지만 dead file (다음 커밋에서 삭제 가능)
- Commit 1 revert → partial 파일 전체 삭제

최악의 경우 `git revert HEAD~2..HEAD` 로 Commit 1·2를 한 번에 되돌려 C2.1 직후 상태로 복귀.

## 참조

- **C2.1 스펙**: `docs/superpowers/specs/2026-04-20-c2-1-macros-split-design.md`
- **C2.1 플랜**: `docs/superpowers/plans/2026-04-20-c2-1-macros-split.md`
- **C2.1 검증**: `_docs/20260420_C2_1_macros_split_검증완료.md`
- **기존 partial 예시**: `api/templates/partials/_ad_slot.html`, `_upgrade_modal.html`
