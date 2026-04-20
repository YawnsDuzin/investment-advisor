# C2.1 — `_macros.html` 분할 (도메인별 3개 파일) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `api/templates/_macros.html`(655줄, 20개 매크로)를 도메인별 3개 파일(`_macros/{proposal,theme,common}.html`)로 분할하고, 6개 사용처 템플릿의 import 경로를 새 경로로 교체한 뒤 구 `_macros.html` 삭제.

**Architecture:** 현재 단일 파일에 섞여 있는 20개 매크로를 proposal(6) / theme(9) / common(5) 3개 도메인으로 분류하여 각 파일로 VERBATIM 이동. `theme_header → grade_badge` 의 크로스 파일 의존은 `_macros/theme.html` 상단 import로 해결. 4개 커밋(생성 → import 교체 → 구 파일 삭제 → 검증 메모)로 점진적 전환.

**Tech Stack:** Python 3.10+, Jinja2, FastAPI

**Spec:** `docs/superpowers/specs/2026-04-20-c2-1-macros-split-design.md`

---

## Task 1: `_macros/` 패키지 생성 + 매크로 VERBATIM 이동

기존 `_macros.html`은 유지한 채로 `_macros/` 디렉터리와 3개 파일을 생성. 이 시점에는 20개 매크로가 두 위치(구 + 신)에 모두 존재하지만, 외부 호출부는 아직 구 `_macros.html`을 참조하므로 렌더 결과 동일.

**Files:**
- Create: `api/templates/_macros/proposal.html`
- Create: `api/templates/_macros/theme.html`
- Create: `api/templates/_macros/common.html`
- (Unchanged): `api/templates/_macros.html`

### Step 1: 디렉터리 생성

Run:
```bash
mkdir -p api/templates/_macros
```

### Step 2: `api/templates/_macros/common.html` 작성

Read `api/templates/_macros.html` to locate the 5 common macros:

- `market_summary_body(text, fallback="(분석 요약 없음)")` — starts at line 8
- `grade_badge(score, label=None)` — starts at line 320
- `external_links(ticker, market='', mode='icon')` — starts at line 375
- `sparkline(points, color='var(--accent)')` — starts at line 590
- `yield_curve(bond_yields)` — starts at line 599

Create `api/templates/_macros/common.html` with these 5 macros in the ORDER they appeared in the original file (market_summary_body, grade_badge, external_links, sparkline, yield_curve). Copy each macro's body VERBATIM — from `{% macro name(...) %}` to `{% endmacro %}` inclusive. Preserve any comments that sit directly before or inside macros.

File structure:
```jinja
{#
  Common display primitives — 도메인 독립적 매크로
  이동 출처: api/templates/_macros.html (C2.1, 2026-04-20)
#}

{% macro market_summary_body(text, fallback="(분석 요약 없음)") -%}
  [... verbatim body from original lines 8~61 ...]
{%- endmacro %}


{% macro grade_badge(score, label=None) -%}
  [... verbatim body from original lines 320~342 ...]
{%- endmacro %}


{% macro external_links(ticker, market='', mode='icon') %}
  [... verbatim body from original lines 375~407 ...]
{% endmacro %}


{% macro sparkline(points, color='var(--accent)') %}
  [... verbatim body from original lines 590~598 ...]
{% endmacro %}


{% macro yield_curve(bond_yields) %}
  [... verbatim body from original lines 599~end ...]
{% endmacro %}
```

**CRITICAL**: copy macro bodies byte-for-byte. Do not reformat, do not change whitespace within macro bodies, do not rearrange parameters.

### Step 3: `api/templates/_macros/proposal.html` 작성

6 macros to include:

- `proposal_card_compact(p)` — line 62
- `proposal_card_full(p)` — line 112
- `krx_badges(p)` — line 343
- `change_indicator(delta, suffix='')` — line 408
- `risk_gauge(risk_pct, risk_label)` — line 419
- `bullet_chart(current_price, target_price, upside_pct, currency, price_pct)` — line 444

Create `api/templates/_macros/proposal.html` with these 6 macros in original order. File structure:

```jinja
{#
  Proposal-related macros — 투자 제안 카드/지표/차트
  이동 출처: api/templates/_macros.html (C2.1, 2026-04-20)
#}

{% macro proposal_card_compact(p) %}
  [... verbatim ...]
{% endmacro %}


{% macro proposal_card_full(p) %}
  [... verbatim — includes internal call `{{ krx_badges(p) }}` which resolves within this file ...]
{% endmacro %}


{% macro krx_badges(p) %}
  [... verbatim ...]
{% endmacro %}


{% macro change_indicator(delta, suffix='') %}
  [... verbatim ...]
{% endmacro %}


{% macro risk_gauge(risk_pct, risk_label) %}
  [... verbatim ...]
{% endmacro %}


{% macro bullet_chart(current_price, target_price, upside_pct, currency, price_pct) %}
  [... verbatim ...]
{% endmacro %}
```

Note: `proposal_card_full` internally calls `krx_badges(p)`. Since both are in the same file, Jinja2 resolves this automatically without import.

### Step 4: `api/templates/_macros/theme.html` 작성

9 macros + 1 cross-file import:

- (import line at top for cross-file call)
- `theme_header(theme, tk=none)` — line 210
- `scenario_grid(scenarios)` — line 257
- `indicator_tags(key_indicators)` — line 279
- `macro_impact_table(macro_impacts)` — line 291
- `conf_ring(confidence, size=28)` — line 482
- `discovery_donut(discovery_counts)` — line 497
- `sector_bubbles(top_sectors)` — line 532
- `discovery_stackbar(discovery_counts)` — line 545
- `sector_chips(top_sectors)` — line 574

Create `api/templates/_macros/theme.html`:

```jinja
{#
  Theme-related macros — 테마 헤더/시나리오/매크로/분포/버블/칩
  이동 출처: api/templates/_macros.html (C2.1, 2026-04-20)

  크로스 파일 의존: theme_header가 grade_badge(common.html)를 호출.
#}
{% from "_macros/common.html" import grade_badge %}


{% macro theme_header(theme, tk=none) %}
  [... verbatim — uses grade_badge(...) at call site which resolves via import above ...]
{% endmacro %}


{% macro scenario_grid(scenarios) %}
  [... verbatim ...]
{% endmacro %}


{% macro indicator_tags(key_indicators) %}
  [... verbatim ...]
{% endmacro %}


{% macro macro_impact_table(macro_impacts) %}
  [... verbatim ...]
{% endmacro %}


{% macro conf_ring(confidence, size=28) %}
  [... verbatim ...]
{% endmacro %}


{% macro discovery_donut(discovery_counts) %}
  [... verbatim ...]
{% endmacro %}


{% macro sector_bubbles(top_sectors) %}
  [... verbatim ...]
{% endmacro %}


{% macro discovery_stackbar(discovery_counts) %}
  [... verbatim ...]
{% endmacro %}


{% macro sector_chips(top_sectors) %}
  [... verbatim ...]
{% endmacro %}
```

### Step 5: 파일 존재 + 매크로 개수 검증

Run:
```bash
ls api/templates/_macros/
wc -l api/templates/_macros/*.html
grep -c "^{% macro " api/templates/_macros/common.html
grep -c "^{% macro " api/templates/_macros/proposal.html
grep -c "^{% macro " api/templates/_macros/theme.html
```

Expected:
- 3 files: `common.html`, `proposal.html`, `theme.html`
- Line counts approximately: common ~200, proposal ~200, theme ~250
- Macro counts: common=5, proposal=6, theme=9 (total 20, matches original)

### Step 6: 매크로 이름 셋 parity

Run:
```bash
venv/Scripts/python -c "
import re
from pathlib import Path
original = Path('api/templates/_macros.html').read_text(encoding='utf-8')
new_files = [
    Path('api/templates/_macros/common.html').read_text(encoding='utf-8'),
    Path('api/templates/_macros/proposal.html').read_text(encoding='utf-8'),
    Path('api/templates/_macros/theme.html').read_text(encoding='utf-8'),
]
pattern = re.compile(r'^\{%-?\s*macro\s+(\w+)', re.MULTILINE)
orig_names = set(pattern.findall(original))
new_names = set()
for content in new_files:
    new_names.update(pattern.findall(content))
assert orig_names == new_names, f'diff: orig-new={orig_names-new_names}, new-orig={new_names-orig_names}'
print(f'parity OK — {len(orig_names)} macros match')
"
```

Expected: `parity OK — 20 macros match`

### Step 7: Jinja2 pre-load 검증

Run:
```bash
venv/Scripts/python -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('api/templates'))
for t in ['_macros/common.html', '_macros/proposal.html', '_macros/theme.html', '_macros.html']:
    env.get_template(t)
    print(f'{t} ok')
"
```

Expected: all 4 templates load without errors (including the old `_macros.html` which is still in place).

### Step 8: API 서버 스모크 — 기존 동작 유지 확인

Run:
```bash
venv/Scripts/python -c "import api.main; print('api.main ok')"
```

Expected: `api.main ok`

No routes changed yet; existing callers still use `_macros.html`.

### Step 9: Commit

```bash
git add api/templates/_macros/
git commit -m "$(cat <<'EOF'
feat(tpl): C2.1 — _macros/ 패키지 생성 (proposal/theme/common)

api/templates/_macros.html(655줄, 20 매크로)을 3개 도메인 파일로
VERBATIM 이동하여 _macros/proposal.html(6), _macros/theme.html(9),
_macros/common.html(5)에 분배. 기존 _macros.html은 유지 — T2에서
호출부를 새 경로로 이전 후 T3에서 삭제 예정.

_macros/theme.html 상단에 grade_badge 크로스 파일 import 추가
(theme_header 내부 호출 해결).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: 6개 사용처 템플릿 import 경로 교체

`_macros.html` 를 참조하는 6개 템플릿의 import 라인을 새 `_macros/*.html` 경로로 수정.

**Files:**
- Modify: `api/templates/dashboard.html` (line 2)
- Modify: `api/templates/session_detail.html` (line 2)
- Modify: `api/templates/themes.html` (line 2)
- Modify: `api/templates/proposals.html` (line 2)
- Modify: `api/templates/stock_analysis.html` (line 2)
- Modify: `api/templates/ticker_history.html` (line 2)

### Step 1: `dashboard.html` 수정

Find the current line 2 (exact content):
```jinja
{% from "_macros.html" import theme_header, indicator_tags, grade_badge, external_links, change_indicator, risk_gauge, bullet_chart, conf_ring, discovery_stackbar, sector_chips, sparkline, yield_curve %}
```

Replace with (split into 3 lines by domain):
```jinja
{% from "_macros/theme.html" import theme_header, indicator_tags, conf_ring, discovery_stackbar, sector_chips %}
{% from "_macros/proposal.html" import change_indicator, risk_gauge, bullet_chart %}
{% from "_macros/common.html" import grade_badge, external_links, sparkline, yield_curve %}
```

### Step 2: `session_detail.html` 수정

Find:
```jinja
{% from "_macros.html" import theme_header, scenario_grid, indicator_tags, macro_impact_table, external_links, market_summary_body %}
```

Replace:
```jinja
{% from "_macros/theme.html" import theme_header, scenario_grid, indicator_tags, macro_impact_table %}
{% from "_macros/common.html" import external_links, market_summary_body %}
```

### Step 3: `themes.html` 수정

Find:
```jinja
{% from "_macros.html" import theme_header, indicator_tags, scenario_grid, macro_impact_table %}
```

Replace:
```jinja
{% from "_macros/theme.html" import theme_header, indicator_tags, scenario_grid, macro_impact_table %}
```

### Step 4: `proposals.html` 수정

Find:
```jinja
{% from "_macros.html" import external_links %}
```

Replace:
```jinja
{% from "_macros/common.html" import external_links %}
```

### Step 5: `stock_analysis.html` 수정

Same change as `proposals.html`:

Find:
```jinja
{% from "_macros.html" import external_links %}
```

Replace:
```jinja
{% from "_macros/common.html" import external_links %}
```

### Step 6: `ticker_history.html` 수정

Same change:

Find:
```jinja
{% from "_macros.html" import external_links %}
```

Replace:
```jinja
{% from "_macros/common.html" import external_links %}
```

### Step 7: `_macros.html` 참조 확인 — 사용처 6개 모두 새 경로로 이전됨

Run:
```bash
grep -rn "_macros\.html" api/templates/ api/
```

Expected output should NOT contain any `{% from "_macros.html" import %}` lines from the 6 modified templates. The only remaining references should be:
1. The `api/templates/_macros.html` file itself (still exists as file)
2. Possibly comments in other files referencing the old path (acceptable)

If any template still has `{% from "_macros.html" import X %}`, update it.

### Step 8: Jinja2 pre-load 검증

Run:
```bash
venv/Scripts/python -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('api/templates'))
for t in ['dashboard.html', 'session_detail.html', 'themes.html', 'proposals.html', 'stock_analysis.html', 'ticker_history.html']:
    env.get_template(t)
    print(f'{t} ok')
"
```

Expected: all 6 templates load without errors (Jinja2 resolves the new `_macros/*.html` paths).

### Step 9: API 서버 import 스모크

Run:
```bash
venv/Scripts/python -c "import api.main; print('api.main ok')"
```

Expected: `api.main ok`

### Step 10: 런타임 페이지 스모크 (로컬 환경 가능 시)

Start API server and hit 6 representative pages:

```bash
# Start server in background
venv/Scripts/python -m api.main &
SERVER_PID=$!
sleep 3

# Hit pages
for path in "/" "/sessions" "/themes" "/proposals"; do
    code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:8000${path}")
    echo "${path} → ${code}"
done

# Cleanup
kill $SERVER_PID 2>/dev/null
```

Expected: all return 200 or 302 (login redirect — auth_enabled). No 500 errors.

If running API server isn't possible (e.g., DB not available), skip this step and rely on Jinja2 pre-load + `import api.main`.

### Step 11: Commit

```bash
git add api/templates/dashboard.html api/templates/session_detail.html api/templates/themes.html api/templates/proposals.html api/templates/stock_analysis.html api/templates/ticker_history.html
git commit -m "$(cat <<'EOF'
refactor(tpl): C2.1 — 6개 템플릿 import 경로 교체 (_macros → _macros/*)

dashboard.html, session_detail.html, themes.html, proposals.html,
stock_analysis.html, ticker_history.html의 `{% from "_macros.html" import %}`를
새 도메인 경로(_macros/{proposal,theme,common}.html)로 교체.

_macros.html은 T3에서 삭제 예정.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: 구 `_macros.html` 삭제

아무도 참조하지 않는 구 파일 삭제.

**Files:**
- Delete: `api/templates/_macros.html`

### Step 1: 마지막 참조 확인

Run:
```bash
grep -rn "_macros\.html" api/
```

Expected output: **없거나**, 템플릿 외 주석/문서 내 언급만 남아야 함. 템플릿에서 `{% from "_macros.html" import %}` 형태의 활성 참조가 없어야 함.

If any template still has the old reference, fix it before proceeding (return to Task 2).

### Step 2: 파일 삭제

Run:
```bash
git rm api/templates/_macros.html
```

### Step 3: Jinja2 pre-load 재검증

Run:
```bash
venv/Scripts/python -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('api/templates'))
for t in ['dashboard.html', 'session_detail.html', 'themes.html', 'proposals.html', 'stock_analysis.html', 'ticker_history.html', '_macros/common.html', '_macros/proposal.html', '_macros/theme.html']:
    env.get_template(t)
    print(f'{t} ok')
"
```

Expected: 9 templates load.

### Step 4: `_macros.html` 참조 확인 — 0건

Run:
```bash
grep -rn "_macros\.html" api/ _docs/ docs/
```

Expected: 참고용 문서(`_docs/` 또는 `docs/superpowers/specs/`)의 과거 기록 외 **활성 코드 참조는 0건**. Active references in templates/py 파일에 `_macros.html` 없음.

### Step 5: API 서버 기동 + 주요 페이지 스모크

Run:
```bash
venv/Scripts/python -c "import api.main; print('api.main ok')"
```

Expected: `api.main ok`

If possible (DB available):
```bash
venv/Scripts/python -m api.main &
SERVER_PID=$!
sleep 3
for path in "/" "/sessions" "/themes" "/proposals"; do
    code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:8000${path}")
    echo "${path} → ${code}"
done
kill $SERVER_PID 2>/dev/null
```

Expected: 4 pages return 200/302.

### Step 6: pytest 회귀 체크

Run:
```bash
venv/Scripts/python -m pytest tests/ --tb=short -q 2>&1 | tail -15
```

Expected: C1 완료 시점과 동일한 pass/fail 분포 (신규 failure 0건). 기존 pre-existing failures만 유지.

### Step 7: Commit

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor(tpl): C2.1 — 구 _macros.html 삭제

모든 사용처가 _macros/{proposal,theme,common}.html로 이전 완료.
단일 655줄 파일 → 3개 도메인 파일로 분할 완료.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: 검증 완료 메모

**Files:**
- Create: `_docs/20260420_C2_1_macros_split_검증완료.md`

### Step 1: 메트릭 수집

Run:
```bash
wc -l api/templates/_macros/*.html
grep -c "^{% macro " api/templates/_macros/common.html
grep -c "^{% macro " api/templates/_macros/proposal.html
grep -c "^{% macro " api/templates/_macros/theme.html
git log --oneline --grep "C2.1"
```

Record outputs for the memo.

### Step 2: 메모 작성

Create `_docs/20260420_C2_1_macros_split_검증완료.md`:

```markdown
# C2.1 — _macros.html 분할 검증 완료 메모

- **일자**: 2026-04-20
- **스펙**: `docs/superpowers/specs/2026-04-20-c2-1-macros-split-design.md`
- **플랜**: `docs/superpowers/plans/2026-04-20-c2-1-macros-split.md`

## 결과 요약

| 항목 | 분할 전 | 분할 후 |
|---|---|---|
| 파일 수 | 1 (`_macros.html` 655줄) | 3 (`_macros/*.html`) |
| 매크로 수 | 20 | 20 (동일) |
| 최대 파일 라인 | 655 | [실측 — wc -l 결과 중 최대값] |
| 외부 참조 템플릿 | 6 | 6 (import 경로만 교체) |

## 파일별 담당

| 파일 | 라인 | 매크로 수 | 매크로 이름 |
|---|---:|---:|---|
| `_macros/proposal.html` | [실측] | 6 | proposal_card_compact, proposal_card_full, krx_badges, change_indicator, risk_gauge, bullet_chart |
| `_macros/theme.html` | [실측] | 9 | theme_header, scenario_grid, indicator_tags, macro_impact_table, conf_ring, discovery_donut, sector_bubbles, discovery_stackbar, sector_chips |
| `_macros/common.html` | [실측] | 5 | market_summary_body, grade_badge, external_links, sparkline, yield_curve |

## 검증 통과 항목

- [x] 매크로 이름 셋 parity — 원본 20개 모두 3개 파일에 분배
- [x] Jinja2 pre-load — 9개 템플릿 (3 macros + 6 callers) 오류 없이 로드
- [x] `grep -rn "_macros\.html" api/` 활성 참조 0건
- [x] `api.main import` 성공
- [x] 주요 6개 페이지 200/302 응답
- [x] `pytest tests/` 신규 실패 0건

## 작업 커밋 (4개)

```
[git log --oneline --grep C2.1 결과 붙여넣기]
```

## 크로스 파일 호출 해결

- `theme_header → grade_badge`: `_macros/theme.html` 상단 `{% from "_macros/common.html" import grade_badge %}` 로 해결

## 남은 관심사 (후속 트랙 C2.2 / C3)

- **C2.2**: 긴 템플릿 partial 추출 (`dashboard.html` 658줄, `admin.html` 486줄 등)
- **C3**: 인라인 JavaScript 분할 (`base.html`의 fetch 인터셉터)

## 다음 단계

**C2.2** (긴 템플릿 partial 추출) 브레인스토밍 → 구현.
```

실제 수치는 실행 후 채워넣음.

### Step 3: Commit

```bash
git add _docs/20260420_C2_1_macros_split_검증완료.md
git commit -m "$(cat <<'EOF'
docs(refactor): C2.1 검증 완료 메모 — _macros.html 분할

655줄 단일 _macros.html을 3개 도메인 파일로 분할. 20개 매크로
parity 통과, 6개 사용처 import 경로 교체 완료, 구 파일 삭제.
다음 단계는 C2.2(긴 템플릿 partial 추출).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## 완료 확인 체크리스트

- [ ] Task 1: `_macros/` 패키지 + 3개 파일 생성 (20개 매크로 VERBATIM 이동)
- [ ] Task 2: 6개 사용처 템플릿 import 경로 교체
- [ ] Task 3: 구 `_macros.html` 삭제 + 전체 스모크
- [ ] Task 4: 검증 완료 메모

**후속:** C2.2 (긴 템플릿 partial 추출) 브레인스토밍 시작.
