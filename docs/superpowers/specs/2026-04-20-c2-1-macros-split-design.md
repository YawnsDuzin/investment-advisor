# C2.1 Design — `_macros.html` 분할 (도메인별 3개 파일)

- **작성일**: 2026-04-20
- **작성자**: Claude (brainstorming skill, opus-4-7)
- **상위 컨텍스트**: C 트랙의 두 번째 서브프로젝트 C2 (템플릿·매크로 분할) 중 첫 단계. C1(CSS 분할) 완료 후 이어짐. C2는 C2.1(매크로) · C2.2(긴 템플릿 partial)로 분할.
- **다음 단계**: writing-plans skill 호출 → 구현 계획서 작성

---

## 0. 컨텍스트

`api/templates/_macros.html`는 **655줄 단일 파일**로 **20개의 Jinja2 매크로**가 섞여 있다:

**현재 매크로 분류:**

| 도메인 | 매크로 (정의 위치 라인) |
|---|---|
| Proposal (6) | `proposal_card_compact(62)`, `proposal_card_full(112)`, `krx_badges(343)`, `change_indicator(408)`, `risk_gauge(419)`, `bullet_chart(444)` |
| Theme (9) | `theme_header(210)`, `scenario_grid(257)`, `indicator_tags(279)`, `macro_impact_table(291)`, `conf_ring(482)`, `discovery_donut(497)`, `sector_bubbles(532)`, `discovery_stackbar(545)`, `sector_chips(574)` |
| Common / Display primitives (5) | `market_summary_body(8)`, `grade_badge(320)`, `external_links(375)`, `sparkline(590)`, `yield_curve(599)` |

**외부 import 사용처 (6개 파일):**

| 파일 | 사용 매크로 | 소속 도메인 |
|---|---|---|
| `dashboard.html` | `theme_header`, `indicator_tags`, `grade_badge`, `external_links`, `change_indicator`, `risk_gauge`, `bullet_chart`, `conf_ring`, `discovery_stackbar`, `sector_chips`, `sparkline`, `yield_curve` | theme(5) + proposal(3) + common(4) |
| `session_detail.html` | `theme_header`, `scenario_grid`, `indicator_tags`, `macro_impact_table`, `external_links`, `market_summary_body` | theme(4) + common(2) |
| `themes.html` | `theme_header`, `indicator_tags`, `scenario_grid`, `macro_impact_table` | theme(4) |
| `proposals.html` | `external_links` | common(1) |
| `stock_analysis.html` | `external_links` | common(1) |
| `ticker_history.html` | `external_links` | common(1) |

**현재 상태의 문제:**

1. 20개 매크로가 한 파일에 섞여 있어 관련 매크로 그룹 탐색 시 file 전체 스크롤 필요
2. 매크로 추가 시 "어디에 두어야 할지" 판단 기준 불명확 (proposal 카드 하단 부분의 bullet_chart가 position 444에 덩그러니 있음)
3. 도메인 경계 불명확 — "chart" 계열은 proposal/theme 양쪽에 분산

## 1. Goals

1. **도메인 분할** — 3개 파일(proposal / theme / common)로 매크로를 의미 단위 분리
2. **명시적 import 경로** — 각 호출부가 사용할 매크로의 도메인을 경로로 선언 (`{% from "_macros/theme.html" import theme_header %}`)
3. **단일 책임** — 매크로 정의 ↔ 사용 관계가 grep으로 쉽게 추적 가능
4. **기능 변경 0** — 렌더 결과 완전 동일, 매크로 시그니처·본문 무수정

## 2. Non-goals (C1 / C2.2 / C3 / D)

- **C1**: 이미 완료 (style.css 분할 + 빌드 번들링)
- **C2.2**: 긴 템플릿 partial 추출 (`dashboard.html` 658줄, `admin.html` 486줄 등) — C2.1 이후 별도 스펙
- **C3**: 인라인 JavaScript 분할 (`base.html`의 fetch 인터셉터 등) — 이후 별도 스펙
- **D**: `analyzer/` 파이프라인 분해
- **매크로 로직 개선**: 성능 최적화, 가독성 리팩토링, API 변경 없음
- **새 매크로 추가·기존 제거**: 현재 20개 그대로
- **Jinja2 include 파셜 추출**: `partials/_ad_slot.html` 등의 include 패턴은 건드리지 않음

## 3. 분할 후 파일 구조

```
api/templates/
└── _macros/
    ├── proposal.html      # 6 macros — 제안·종목 카드/지표/차트
    ├── theme.html         # 9 macros — 테마 헤더·시나리오·매크로·도넛/버블/칩
    └── common.html        # 5 macros — 공통 display primitives
```

**기존 `api/templates/_macros.html`는 T3에서 삭제.**

### 각 파일 내용

**`_macros/proposal.html`** (~200 lines, 6 macros):

- `proposal_card_compact(p)` — 제안 카드 컴팩트 뷰 (라인 62~111 원본)
- `proposal_card_full(p)` — 제안 카드 풀 뷰 (라인 112~209)
- `krx_badges(p)` — KRX 외인매수·숏스퀴즈·지수편입·외인보유 배지 (라인 343~374)
- `change_indicator(delta, suffix='')` — 변화량 인디케이터 (라인 408~418)
- `risk_gauge(risk_pct, risk_label)` — 리스크 게이지 (라인 419~443)
- `bullet_chart(current_price, target_price, upside_pct, currency, price_pct)` — 불릿 차트 (라인 444~481)

**`_macros/theme.html`** (~250 lines, 9 macros):

파일 상단에 크로스 파일 import 필요:
```jinja
{% from "_macros/common.html" import grade_badge %}
```
이유: `theme_header`가 `grade_badge(theme.confidence_score)`를 인라인 호출 (현재 `_macros.html` 라인 238 참조).

- `theme_header(theme, tk=none)` — 테마 헤더 (라인 210~256)
- `scenario_grid(scenarios)` — 시나리오 2x2 그리드 (라인 257~278)
- `indicator_tags(key_indicators)` — 핵심 지표 태그 (라인 279~290)
- `macro_impact_table(macro_impacts)` — 매크로 영향 테이블 (라인 291~319)
- `conf_ring(confidence, size=28)` — 신뢰도 프로그레스 링 (라인 482~496)
- `discovery_donut(discovery_counts)` — 발굴 유형 도넛 (라인 497~531)
- `sector_bubbles(top_sectors)` — 섹터 버블 (라인 532~544)
- `discovery_stackbar(discovery_counts)` — 발굴 유형 스택바 (라인 545~573)
- `sector_chips(top_sectors)` — 섹터 칩 (라인 574~589)

**`_macros/common.html`** (~200 lines, 5 macros):

- `market_summary_body(text, fallback="(분석 요약 없음)")` — 시장 요약 본문 (라인 8~61)
- `grade_badge(score, label=None)` — S/A/B/C/D 등급 배지 (라인 320~342)
- `external_links(ticker, market='', mode='icon')` — 네이버/Yahoo/Finviz 외부 링크 (라인 375~407)
- `sparkline(points, color='var(--accent)')` — 스파크라인 (라인 590~598)
- `yield_curve(bond_yields)` — 채권 수익률 곡선 (라인 599~655)

### 도메인 분류 근거

| 도메인 | 기준 | 예외 사유 |
|---|---|---|
| `proposal.html` | 특정 투자 제안(종목)에 대한 표현 | `bullet_chart`는 공통 차트로도 쓸 수 있으나 현재 proposal 상세에서만 사용 |
| `theme.html` | 테마 단위의 표현 (시나리오, 매크로 영향, 분포) | `conf_ring`은 제안 카드에도 쓰일 수 있으나 주 사용처는 테마 신뢰도 |
| `common.html` | 도메인 독립적 display primitives | `market_summary_body`는 대시보드 Market Summary 영역 전용이지만, 텍스트 → HTML 변환 로직이라 common |

## 4. Import 경로 교체 (6개 파일)

### 4.1 `dashboard.html` (12 macros)

기존:
```jinja
{% from "_macros.html" import theme_header, indicator_tags, grade_badge, external_links, change_indicator, risk_gauge, bullet_chart, conf_ring, discovery_stackbar, sector_chips, sparkline, yield_curve %}
```

변경:
```jinja
{% from "_macros/theme.html" import theme_header, indicator_tags, conf_ring, discovery_stackbar, sector_chips %}
{% from "_macros/proposal.html" import change_indicator, risk_gauge, bullet_chart %}
{% from "_macros/common.html" import grade_badge, external_links, sparkline, yield_curve %}
```

### 4.2 `session_detail.html` (6 macros)

기존:
```jinja
{% from "_macros.html" import theme_header, scenario_grid, indicator_tags, macro_impact_table, external_links, market_summary_body %}
```

변경:
```jinja
{% from "_macros/theme.html" import theme_header, scenario_grid, indicator_tags, macro_impact_table %}
{% from "_macros/common.html" import external_links, market_summary_body %}
```

### 4.3 `themes.html` (4 macros)

기존:
```jinja
{% from "_macros.html" import theme_header, indicator_tags, scenario_grid, macro_impact_table %}
```

변경:
```jinja
{% from "_macros/theme.html" import theme_header, indicator_tags, scenario_grid, macro_impact_table %}
```

### 4.4 `proposals.html`, `stock_analysis.html`, `ticker_history.html` (각 1 macro)

기존:
```jinja
{% from "_macros.html" import external_links %}
```

변경:
```jinja
{% from "_macros/common.html" import external_links %}
```

## 5. 단계별 커밋 전략

| # | 커밋 | 내용 | 검증 |
|---|---|---|---|
| T1 | `feat(tpl): C2.1 — _macros/ 패키지 생성 (proposal/theme/common)` | `api/templates/_macros/` 디렉터리 + 3개 파일 생성, 각 매크로를 원본 `_macros.html`에서 VERBATIM 복사. 기존 `_macros.html`은 유지 | 새 파일에서 import 가능 테스트: `python -c "from jinja2 import Environment, FileSystemLoader; e = Environment(loader=FileSystemLoader('api/templates')); e.get_template('_macros/proposal.html'); print('ok')"` |
| T2 | `refactor(tpl): C2.1 — 6개 템플릿 import 경로 교체` | 위 4.1~4.4의 import 교체. `_macros.html`은 아직 유지 | API 서버 기동 + 주요 6개 페이지 200 응답 |
| T3 | `refactor(tpl): C2.1 — 구 _macros.html 삭제` | 파일 삭제 (`git rm api/templates/_macros.html`) | 재기동 + `grep -r "_macros\.html" api/` 결과 0건 |
| T4 | `docs(refactor): C2.1 검증 완료 메모` | 결과 요약 | — |

**원자성**: 각 커밋 단독으로 페이지가 정상 렌더되어야 함. T2 완료 시점에도 `_macros.html`은 남아있지만 아무도 참조하지 않음 (T3에서 안전하게 삭제).

## 6. 검증 전략

### 6.1 정적 parity

매크로 개수 + 이름 비교:

```bash
# 원본에서 매크로 20개 확인
grep -c "^{% macro " api/templates/_macros.html

# T1 후 새 파일에 20개 분배 확인
grep -c "^{% macro " api/templates/_macros/proposal.html  # 6
grep -c "^{% macro " api/templates/_macros/theme.html      # 9
grep -c "^{% macro " api/templates/_macros/common.html     # 5

# 매크로 이름 셋이 동일한지 비교
diff \
  <(grep -oE "^{% macro (\w+)" api/templates/_macros.html | sort) \
  <(cat api/templates/_macros/*.html | grep -oE "^{% macro (\w+)" | sort)
```

Expected: 매크로 20개, diff 공백.

### 6.2 Import 경로 레퍼런스 0건 (T3 이후)

```bash
grep -rn "_macros\.html" api/templates/
```

Expected: 0건 출력.

### 6.3 동적 스모크

각 템플릿을 사용하는 라우트에 GET 요청:

- `/` → 대시보드 (dashboard.html)
- `/sessions/<id>` → 세션 상세 (session_detail.html)
- `/themes` → 테마 목록 (themes.html)
- `/proposals` → 제안 목록 (proposals.html)
- `/proposals/<id>/stock-analysis` → 종목 심층분석 (stock_analysis.html)
- `/pages/ticker-history/<ticker>` → 티커 이력 (ticker_history.html)

각 200 응답 + Jinja `TemplateNotFound` 에러 없음.

### 6.4 Jinja2 pre-load 검증

```bash
venv/Scripts/python -c "
from jinja2 import Environment, FileSystemLoader, TemplateNotFound
env = Environment(loader=FileSystemLoader('api/templates'))
templates_to_check = [
    '_macros/proposal.html',
    '_macros/theme.html',
    '_macros/common.html',
    'dashboard.html', 'session_detail.html', 'themes.html',
    'proposals.html', 'stock_analysis.html', 'ticker_history.html',
]
for t in templates_to_check:
    env.get_template(t)
    print(f'{t} ok')
"
```

Expected: 모든 템플릿이 `TemplateNotFound` 없이 로드.

## 7. 리스크 및 대응

| # | 리스크 | 가능성 | 영향 | 대응 |
|---|---|---|---|---|
| R1 | 매크로 간 상호 호출 (같은 파일 내에서만 resolving) | 낮 | 고 | **사전 조사 완료 (부록 A 참조)**. `theme_header → grade_badge` 는 크로스 파일(theme → common)이므로 `_macros/theme.html` 상단에 `{% from "_macros/common.html" import grade_badge %}` 추가. `proposal_card_full → krx_badges`는 같은 파일 내이므로 자동 resolve. T1 구현 시 구체적 import 명시 |
| R2 | Jinja2 context 의존 (autoescape, i18n 등)으로 파일 분할 시 동작 차이 | 낮 | 중 | 매크로 본문을 VERBATIM 이동, 파일 자체의 컨텍스트 설정 변경 없음. 모든 매크로 파일은 단순 `{% macro %}...{% endmacro %}` 블록만 가짐 |
| R3 | 일부 템플릿이 `_macros.html`을 변수로 include하거나 하드코딩 | 낮 | 고 | T1 전 `grep -r "_macros\.html" api/` 전체 스캔. 템플릿 import 외 용도 발견 시 별도 처리 |
| R4 | Jinja2 캐시 — uvicorn reload 시 새 경로 미반영 | 낮 | 저 | 서버 재시작으로 해결. 배포 시 일반적 재기동 절차 준수 |
| R5 | C2.2에서 긴 템플릿 partial 추출 시 새 partial이 구 `_macros.html` 경로 사용 | 낮 | 저 | C2.2 구현 시 신규 경로만 사용 가이드. C2.1 메모에 명시 |

## 8. 성공 기준

- [ ] `api/templates/_macros/` 아래 3개 파일 (proposal / theme / common) 생성, 총 20개 매크로 분배
- [ ] 6개 템플릿 import 경로 새 경로로 교체
- [ ] 구 `api/templates/_macros.html` 삭제
- [ ] `grep -r "_macros\.html" api/` 결과 0건
- [ ] Jinja2 pre-load 검증 통과
- [ ] 주요 6개 페이지 GET 200 응답
- [ ] `pytest tests/` 회귀 0건 (A·C1 기준 pre-existing 실패 외 신규 실패 없음)

---

## 부록 A — 매크로 간 상호 호출 조사 결과

`grep -nE "\{\{\s*(<macro_names>)\(" api/templates/_macros.html` 결과:

| 호출자 (파일 라인) | 피호출 | 호출자 도메인 | 피호출 도메인 | 같은 파일? |
|---|---|---|---|---|
| `proposal_card_full` (166) | `krx_badges` | proposal | proposal | ✅ 같은 파일 |
| `theme_header` (238) | `grade_badge` | theme | **common** | ❌ 크로스 파일 |

**결론:**

1. **proposal.html 내부**: `proposal_card_full` → `krx_badges` 는 같은 파일이므로 Jinja2가 자동으로 resolve. 추가 import 불필요.
2. **theme.html**: `theme_header` → `grade_badge` 는 **common.html로 이동되므로 크로스 파일**. 따라서 `theme.html` 파일 상단에 다음 import 필요:
   ```jinja
   {% from "_macros/common.html" import grade_badge %}
   ```

Jinja2에서 import된 매크로는 해당 파일의 다른 매크로에서 `{{ grade_badge(...) }}` 로 호출 가능.

그 외 크로스 매크로 호출 없음 (grep 확인).
