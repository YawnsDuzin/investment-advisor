# C1 Design — `style.css` 분할 + 빌드 번들링

- **작성일**: 2026-04-20
- **작성자**: Claude (brainstorming skill, opus-4-7)
- **상위 컨텍스트**: "B → C 병렬 → A → D" 로드맵 중 **C 트랙의 첫 서브프로젝트**. A 트랙(shared/db.py 분할) 완료 후 이어짐. C는 C1(CSS)·C2(템플릿/매크로)·C3(인라인 JS)로 분할.
- **다음 단계**: writing-plans skill 호출 → 구현 계획서 작성

---

## 0. 컨텍스트

`api/static/css/style.css`는 **4,165줄 단일 파일**로 다음 기능들이 섞여 있다:

- 118개 주석 섹션 (`/* ===== ... ===== */`)
- `:root` 토큰 (색상·여백·alias, 24줄)
- 레이아웃 (sidebar, header, content, notifications, user dropdown, mobile menu)
- 기본 컴포넌트 (cards, badges, tables, modals, buttons, filters, pagination, toast, indicators)
- 도메인별 컴포넌트 (proposal card, theme scenario, macro impact, price target, risk temperature, tracking badges)
- 페이지 고유 스타일 (dashboard hero, theme chat, admin, inquiry, stock analysis)
- 기능별 스타일 (UI-01 tier badge, UI-03 disclaimer, UI-06 bottom tabbar, UI-11 track record widget, UI-12 proposal tabs, UI-13 sticky CTA, UI-14 grade badge, UI-15 table stack, UI-17 ad slot, UI-18 tablet breakpoint)
- 반응형 (base + tablet + mobile 전용 규칙)

**외부 의존**: `api/templates/base.html`에서 `<link rel="stylesheet" href="/static/css/style.css">` 하나로 로드. 모든 템플릿이 상속.

**현재 상태의 문제**:

1. 한 파일에 7개 이상의 관심사가 섞여 있어 수정 시 영향 범위 파악 어려움
2. 4,165줄이라 특정 컴포넌트를 찾는데 `grep` 의존
3. 새 기능 추가 시 어느 위치에 넣어야 하는지 판단 기준 불명확
4. 협업 시 머지 충돌이 한 파일에 집중

## 1. Goals

1. **모듈화** — 19개 파일로 분리, 최대 500줄 이내 유지
2. **런타임 성능 보존** — HTTP 요청 수 변경 없음(단일 `style.css`). 빌드 스크립트로 src/ → style.css 번들
3. **CSS parity** — 분할 전후 선택자·속성·cascade 순서 완전 동일. 렌더 결과 시각적 회귀 0
4. **빌드 결정성** — 같은 src로 여러 번 빌드 시 `style.css` 바이트 동일
5. **템플릿 무수정** — `<link>` 경로 변경 없음, base.html 수정 불필요

## 2. Non-goals (B / C2 / C3 / D)

- **B 시리즈**: 이미 완료 (B1-B3 모두 main 머지 완료)
- **C2**: 템플릿/매크로 분할 (`_macros.html`, `dashboard.html` 등 partial 추출) — C1 이후 별도 스펙
- **C3**: 인라인 JavaScript 분할 (`base.html`의 fetch 인터셉터 등) — C1 이후 별도 스펙
- **D**: `analyzer/` 파이프라인 분해
- **CSS 기능 변경**: 색상·크기·레이아웃 수정 없음. 순수 파일 분할만
- **CSS 최적화**: 중복 규칙 제거, specificity 정리, 데드 셀렉터 제거 등은 이번 범위 외
- **외부 빌드 툴 도입**: webpack/vite/sass 등 미도입, 순수 Python stdlib만 사용
- **CSS-in-JS / CSS Modules**: 도입 안함

## 3. 분할 후 파일 구조

```
api/static/css/
├── style.css                   # 빌드 결과물 (git 추적)
├── README.md                   # 분할 구조 + 빌드 방법 설명
└── src/
    ├── 01_tokens.css           # :root 변수 (~30줄)
    ├── 02_base.css             # Reset & Base (~40줄)
    ├── 03_layout.css           # Sidebar + 그룹타이틀 + Content + Header + 알림 아이콘 + 유저 드롭다운 + Mobile menu button (~330줄)
    ├── 04_cards_grids.css      # Cards + Stat Grid(6열) + Summary Body/Insights (~250줄)
    ├── 05_badges.css           # Badges base + Vendor Tier + Theme Type + Importance + Grade(UI-14) + Role/Status + Tier 배지(UI-01) (~130줄)
    ├── 06_tables_bars.css      # Tables + Screener + Confidence Bar + Allocation Bar + Indicators + Sparkline (~200줄)
    ├── 07_modals.css           # 업그레이드 모달 + 범용 모달 + 확인 모달 + Toast (~200줄)
    ├── 08_buttons_forms.css    # Button Variants + Filters + Pagination + Empty State + External Links (~170줄)
    ├── 09_proposals.css        # Proposal Card + Row + 상세 섹션 + Detail 탭(UI-12) + Sticky CTA(UI-13) + Tracking Badges + Price Target / Score (~350줄)
    ├── 10_themes.css           # Scenario Cards + Macro Impact + Issue Timeline + 테마 신뢰도 링 + 발굴 유형 도넛 + 섹터 버블 (~250줄)
    ├── 11_dashboard_hero.css   # Tier1 Hero + KPI Strip + Hero 내부 위젯 + 섹터 칩 + Insight Card Compact + Yield Curve + Market Summary 접이식 (~500줄)
    ├── 12_dashboard_rest.css   # Signals + News Section + Top Picks + Track Record 위젯(UI-11) + Card Locked(UI-11) + 워치리스트 요약 (~450줄)
    ├── 13_stock_analysis.css   # Stock Analysis 전용 (Hero + Meta chips + TOC + Section cards + 재무/팩터 + Bull/Bear + Risk + 진입/청산 + Markdown) (~400줄)
    ├── 14_chat.css             # Theme Chat + 채팅 목록 카드 (~200줄)
    ├── 15_admin.css            # Admin + User Admin + 확장 패널 + 워치리스트 토글 + 세션 카드 탑 (~300줄)
    ├── 16_inquiry.css          # 고객 문의 게시판 전체 (~170줄)
    ├── 17_history.css          # History Timeline + Tracking Badges 히스토리 뷰 (~80줄)
    ├── 18_mobile_features.css  # Bottom Tabbar(UI-06) + Touch Target(UI-07) + Safe Area(UI-09) + Table Stack(UI-15) + Ad Slot(UI-17) + Disclaimer Banner(UI-03) + Risk Temperature SVG (~250줄)
    └── 19_responsive.css       # 기존 `/* Responsive */` 블록 + Tablet(UI-18) + 모바일 전용 추가 규칙 (~350줄)
```

**총 19개 파일**, 예상 최대 500줄. 파일명 prefix(`01_`~`19_`)로 빌드 시 로드 순서를 원본과 동일하게 강제.

### 파일 경계 근거

| 그룹 | 기준 | 예시 |
|---|---|---|
| Core (01~02) | CSS가 의미를 갖기 위한 최소 계층 | tokens, reset |
| Layout (03) | 앱 셸 — 거의 모든 페이지 공통 | sidebar, header, dropdowns |
| 기본 컴포넌트 (04~08) | 페이지 무관하게 여러 곳에서 재사용되는 패턴 | cards, badges, tables, modals, buttons |
| 도메인 컴포넌트 (09~10) | 투자 분석 도메인 고유 패턴 | proposal card, theme scenario |
| 페이지 전용 (11~17) | 특정 페이지에서만 쓰이는 섹션 | dashboard hero, stock analysis, chat, admin |
| 기능/반응형 (18~19) | UI-XX 기능 오버레이 + 미디어 쿼리 | bottom tabbar, tablet breakpoint |

## 4. 빌드 스크립트 (`tools/build_css.py`)

```python
"""src/*.css를 정렬된 순서로 합쳐 style.css 생성.

사용: python -m tools.build_css
"""
from pathlib import Path

SRC = Path("api/static/css/src")
OUT = Path("api/static/css/style.css")
HEADER = "/* AUTO-GENERATED — edit files in src/ and run `python -m tools.build_css` */\n\n"


def main() -> int:
    files = sorted(SRC.glob("*.css"))
    parts = [HEADER]
    for f in files:
        parts.append(f"/* ======== {f.name} ======== */\n")
        parts.append(f.read_text(encoding="utf-8").rstrip() + "\n")
        parts.append("\n")
    OUT.write_text("".join(parts), encoding="utf-8")
    total = sum(f.stat().st_size for f in files)
    print(f"[css] bundled {len(files)} files → {OUT} ({total} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

**특성:**

- 외부 의존 0 (stdlib만)
- 결정적: `sorted()`로 alphabetical 순서, 파일명 prefix로 로드 순서 강제
- `rstrip()` + 고정 줄바꿈으로 파일 간 공백 일관화
- 빌드 결과 상단에 `AUTO-GENERATED` 헤더 + 각 섹션 앞에 `/* ======== NN_name.css ======== */` 구분자
- CI/CD 불필요 — 커밋 시 src + style.css 함께 커밋

## 5. CSS Parity 검증 도구 (`tools/c1_css_split/capture_css.py`)

분할 전후 렌더 동일성을 프로그램적으로 검증:

```python
"""style.css의 선택자→속성 매핑을 JSON으로 캡처.

사용: python -m tools.c1_css_split.capture_css > tools/c1_css_split/baseline.json
비교: python -m tools.c1_css_split.capture_css | diff - tools/c1_css_split/baseline.json

주석·공백 무시, 규칙 순서(cascade)는 리스트로 보존.
"""
import hashlib
import json
import re
import sys
from pathlib import Path


def parse_css(text: str) -> list[dict]:
    """단순 파서 — {selector, decls_hash, position} 리스트 반환.

    실제 CSS 파서 라이브러리 도입 피하기 위한 최소 구현.
    block 구조만 식별, 중첩(@media, @keyframes)은 해당 블록 전체를 단일 item으로.
    """
    # 주석 제거
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    # 최상위 블록 추출: at-rule (@media/@keyframes 등) 또는 selector { ... }
    items: list[dict] = []
    pos = 0
    while pos < len(text):
        # 공백 스킵
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text):
            break
        # 블록 끝 찾기 (중첩 고려)
        depth = 0
        start = pos
        in_block = False
        while pos < len(text):
            c = text[pos]
            if c == "{":
                depth += 1
                in_block = True
            elif c == "}":
                depth -= 1
                if depth == 0:
                    pos += 1
                    break
            pos += 1
        if not in_block:
            break
        block = text[start:pos].strip()
        # selector = { 앞까지, 나머지는 decls
        brace = block.find("{")
        selector = re.sub(r"\s+", " ", block[:brace].strip())
        decls = block[brace + 1 : -1]
        # 속성: 이름→값 정규화 (공백·줄바꿈 무시)
        decl_map: dict[str, str] = {}
        for d in decls.split(";"):
            d = d.strip()
            if not d or ":" not in d:
                continue
            name, _, value = d.partition(":")
            decl_map[name.strip().lower()] = re.sub(r"\s+", " ", value.strip())
        # cascade 순서 유지 위해 position도 기록
        items.append(
            {
                "selector": selector,
                "decls_hash": hashlib.sha256(
                    json.dumps(sorted(decl_map.items()), ensure_ascii=False).encode()
                ).hexdigest()[:16],
                "decl_count": len(decl_map),
            }
        )
    return items


def main() -> int:
    path = Path("api/static/css/style.css")
    items = parse_css(path.read_text(encoding="utf-8"))
    json.dump(
        {"total_rules": len(items), "rules": items},
        sys.stdout,
        indent=2,
        ensure_ascii=False,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

**검증 방법**: T1에서 baseline 캡처, 이후 각 커밋마다 재캡처 후 `diff` — 차이가 있으면 그 규칙이 누락/중복됐다는 의미.

## 6. 단계별 커밋 전략

B/A 패턴 차용 — 11개 커밋으로 점진적 이동:

| # | 커밋 | 내용 | 검증 |
|---|---|---|---|
| T1 | `chore(css): C1 — baseline 캡처 스크립트 + 스냅샷` | `tools/c1_css_split/capture_css.py` + `baseline.json` 생성 | baseline 캡처 성공 |
| T2 | `chore(css): C1 — 빌드 스크립트 + src 스캐폴드` | `tools/build_css.py` + 빈 `src/01_tokens.css` ~ `src/19_responsive.css` (각 파일에 `/* TODO: populate in T3-T10 */` placeholder만) + `src/00_legacy.css`(원본 전체 복사) + `python -m tools.build_css` 실행 | 빌드된 `style.css` 내용이 원본과 기능적 동일(legacy가 전부 담고 있음) |
| T3 | `refactor(css): C1 — 01_tokens + 02_base + 03_layout 이동` | 해당 섹션을 `src/01_*.css` ~ `03_*.css`로 이동, `_legacy.css`에서 제거, 빌드 | 사이드바/헤더/드롭다운 렌더 확인 |
| T4 | `refactor(css): C1 — 04_cards_grids + 05_badges + 06_tables_bars 이동` | 3파일 이동, 빌드 | 대시보드 카드/뱃지/테이블 렌더 확인 |
| T5 | `refactor(css): C1 — 07_modals + 08_buttons_forms 이동` | 2파일 이동, 빌드 | 업그레이드 모달/필터 렌더 |
| T6 | `refactor(css): C1 — 09_proposals + 10_themes 이동` | 2파일 이동 | 제안/테마 페이지 렌더 |
| T7 | `refactor(css): C1 — 11_dashboard_hero + 12_dashboard_rest 이동` | 2파일 이동 | 대시보드 Hero/위젯 |
| T8 | `refactor(css): C1 — 13_stock_analysis + 14_chat 이동` | 2파일 이동 | Stock Analysis/채팅 |
| T9 | `refactor(css): C1 — 15_admin + 16_inquiry + 17_history 이동` | 3파일 이동 | 관리자/문의/히스토리 |
| T10 | `refactor(css): C1 — 18_mobile_features + 19_responsive + legacy 삭제` | 마지막 2파일 이동 + `_legacy.css` 제거 | 전체 페이지 모바일 스모크 |
| T11 | `docs(refactor): C1 검증 완료 메모` | 결과 요약 | — |

각 단계마다:
1. 해당 섹션을 `_legacy.css`에서 그대로 복사 → 목적 파일
2. `_legacy.css`에서 해당 섹션 삭제
3. `python -m tools.build_css` 재실행
4. CSS parity 검증 (`capture_css.py`)
5. 브라우저 스모크 (관련 페이지)
6. 커밋 (src + 결과 `style.css` 함께)

**원자성**: 각 커밋 단독으로 브라우저 렌더가 정상이어야 함. 문제 시 해당 커밋만 revert.

## 7. 검증 전략

### 7.1 CSS Rule Parity

- T1에서 `baseline.json` 생성
- 매 커밋 후 재캡처 → `diff baseline.json current.json` 공백이어야 함
- 차이가 있다면: 규칙 누락(이동 중 실수), 중복(양쪽에 남음), 또는 파싱 실수 중 하나
- 파서는 주석/공백 무시, 선택자·속성명·값·선언 개수만 비교

### 7.2 빌드 결정성

- `python -m tools.build_css` 연속 실행 시 `style.css` SHA-256 동일
- `stat` 으로 파일 크기 변화 없음 확인

### 7.3 런타임 스모크

각 커밋 후 브라우저에서 다음 페이지 시각 점검:

- `/` → 대시보드 (Hero 포함)
- `/sessions` → 세션 목록
- `/themes` → 테마 목록
- `/proposals` → 제안 목록 + 상세 탭
- `/proposals/<id>/stock-analysis` → 종목 심층분석
- `/admin` → 관리자 페이지
- `/admin/users` → 유저 관리
- `/chat` → 테마 채팅 목록
- `/inquiry` → 고객 문의
- 모바일 viewport (375×667) — 하단 탭바, touch target, safe area

### 7.4 responsive 검증

- 데스크톱(1440px) / 태블릿(900px) / 모바일(375px) 3종 viewport에서 주요 페이지 스크린샷 비교 (수동, 체크리스트)

## 8. 리스크 및 대응

| # | 리스크 | 가능성 | 영향 | 대응 |
|---|---|---|---|---|
| R1 | 특정 CSS 규칙이 cascade/specificity 순서에 의존해 이동 시 바뀜 | 중 | 고 | T2에서 `00_legacy.css`(원본 전체)를 유지 + prefix 숫자로 로드 순서 원본과 동일하게. T3~T10에서 이동 시 원본 순서 보존하며 이동 |
| R2 | `!important` 위치 변경으로 효력 역전 | 중 | 중 | T1 이전 grep `grep -c "!important" style.css` 개수 기록. T10 완료 시 동일 개수 확인 |
| R3 | @media query가 base 규칙 앞에 위치 (원본 순서 깨짐) | 낮 | 중 | `19_responsive.css`를 마지막에 두어 원본 패턴 유지. 페이지 내부 @media(예: 11_dashboard_hero 내부의 @media)는 해당 파일에 함께 이동 |
| R4 | 빌드 결과 `style.css`를 다른 개발자가 직접 편집 | 중 | 저 | 파일 상단 `AUTO-GENERATED` 헤더 + `.gitattributes`에 `api/static/css/style.css linguist-generated` + `api/static/css/README.md` 가이드 추가 |
| R5 | `src/*.css`가 로드 되지 않고 legacy만 남음 | 낮 | 저 | T11에서 `ls src/` 결과가 19개 파일이고 legacy 없음 확인. 빌드 시 파일 수 출력 확인 |
| R6 | CSS parity 파서가 at-rule(@keyframes 등) 처리 실수 | 중 | 저 | 파서 구현 시 `@keyframes`, `@font-face`, `@supports` 등의 블록 전체를 단일 item으로 처리. 의심되면 수동 diff로 확인 |
| R7 | C2(템플릿 분할)·C3(JS 분할)에서 CSS 파일명 참조 충돌 | 낮 | 저 | src/ 구조 확정 후 C2/C3 스펙 작성 시 참조. 파일 경로 변경 없음 |

## 9. 성공 기준

- [ ] `api/static/css/src/` 아래 19개 파일, 최대 500줄 이내
- [ ] `python -m tools.build_css`가 결정적 — 2회 연속 실행 시 `style.css` SHA-256 동일
- [ ] `tools/c1_css_split/capture_css.py`로 캡처한 분할 전후 규칙 셋 parity 통과
- [ ] `grep -c "!important" style.css` 개수 원본과 동일
- [ ] 주요 10개 페이지 + 모바일 viewport 렌더 회귀 0
- [ ] `api/static/css/README.md` 작성 — 분할 구조 설명 + `python -m tools.build_css` 사용법
- [ ] `.gitattributes`에 `api/static/css/style.css linguist-generated` 추가

---

## 부록 A — 섹션 → 파일 매핑 (118 섹션 기준)

현재 `style.css`의 주석 섹션을 타겟 파일에 매핑. T3~T10 구현 시 참조.

| 섹션(시작 라인) | 타겟 파일 |
|---|---|
| `Reset & Base` (1) | 02_base.css |
| (내부 `:root`) | 01_tokens.css |
| `Sidebar` (42) + `그룹 타이틀` (135) | 03_layout.css |
| `Content` (168) | 03_layout.css |
| `알림 아이콘` (197) | 03_layout.css |
| `유저 드롭다운` (223) | 03_layout.css |
| `Cards` (342) | 04_cards_grids.css |
| `Stat Grid` (364) + `Stat Grid 확장` (498) | 04_cards_grids.css |
| `Market Summary` (392) + `Summary Body` (412) + `Summary Insights` (512) | 04_cards_grids.css |
| `Badges` (603) + `Vendor Tier` (628) + `Theme Type` (633) + `Importance Stars` (639) | 05_badges.css |
| `Tables` (642) + `Screener Table` (668) | 06_tables_bars.css |
| `Confidence Bar` (677) + `Allocation Bar` (696) + `Indicators` (715) | 06_tables_bars.css |
| `Detail Sections` (732) | 09_proposals.css |
| `Proposal Card` (741) + `Proposal Row` (887) | 09_proposals.css |
| `Filters` (829) | 08_buttons_forms.css |
| `Empty State` (902) | 08_buttons_forms.css |
| `Risk Temperature` (912) | 18_mobile_features.css (SVG 게이지 — 18에 둠) |
| `Scenario Cards` (933) | 10_themes.css |
| `Macro Impact Table` (975) | 10_themes.css |
| `Issue Impact Timeline` (980) | 10_themes.css |
| `Price Target / Score Display` (1018) | 09_proposals.css |
| `Signals (Dashboard)` (1069) | 12_dashboard_rest.css |
| `Tracking Badges` (1109) | 09_proposals.css |
| `History Timeline` (1147) | 17_history.css |
| `Mobile Menu Button` (1182) | 03_layout.css |
| `Session Card Top` (1216) | 15_admin.css |
| `Responsive` (1244) | 19_responsive.css |
| `Theme Chat` (1357) + `채팅 목록 카드` (1505) | 14_chat.css |
| `News Section (Dashboard)` (1569) | 12_dashboard_rest.css |
| `Admin Page` (1663) | 15_admin.css |
| `워치리스트 토글` (1743) | 15_admin.css (또는 12_dashboard_rest.css — 실측 확인) |
| `Top Picks` (1754) | 12_dashboard_rest.css |
| `티어 배지` (1934) | 05_badges.css |
| `업그레이드 모달` (1971) + `범용 모달` (2049) | 07_modals.css |
| `면책 배너` (2084) | 18_mobile_features.css |
| `하단 탭바` (2120) | 18_mobile_features.css |
| `터치 타겟 44px` (2172) | 18_mobile_features.css |
| `세이프 에어리어` (2181) | 18_mobile_features.css |
| `제안 상세 탭 + Sticky CTA` (2189) | 09_proposals.css |
| `Dashboard Track Record 위젯` (2316) | 12_dashboard_rest.css |
| `카드 잠금 오버레이 (Free Blur)` (2373) | 12_dashboard_rest.css |
| `등급 배지 (S/A/B/C/D)` (2406) | 05_badges.css |
| `Free 광고 슬롯` (2458) | 18_mobile_features.css |
| `Tablet 브레이크포인트` (2514) | 19_responsive.css |
| `모바일 테이블 → 라벨 스택` (2536) | 18_mobile_features.css |
| `모바일 전용 추가 규칙` (2577) | 19_responsive.css |
| `Role / Status Badges` (2657) | 05_badges.css |
| `Button Variants` (2664) | 08_buttons_forms.css |
| `Pagination` (2685) | 08_buttons_forms.css |
| `Toast / Result Messages` (2714) | 07_modals.css |
| `Confirm Modal` (2736) | 07_modals.css |
| `User Admin 확장 패널/모바일` (2781) | 15_admin.css |
| `외부 사이트 링크` (2872) | 08_buttons_forms.css |
| `고객 문의 게시판` (2923~3087) | 16_inquiry.css |
| `Tier 1 Hero 레이아웃` (3093) | 11_dashboard_hero.css |
| `변화량 인디케이터` (3101) | 06_tables_bars.css |
| `Sparkline` (3123) | 06_tables_bars.css |
| `리스크 온도 SVG` (3130) | 18_mobile_features.css |
| `워치리스트 요약 위젯` (3144) | 12_dashboard_rest.css |
| `KPI Strip / Row 1~3` (3220~3308) | 11_dashboard_hero.css |
| `Hero 내부 Track Record 위젯` (3310) | 11_dashboard_hero.css |
| `발굴 유형 분포 스택바` (3316) | 11_dashboard_hero.css |
| `섹터 칩` (3369) | 11_dashboard_hero.css |
| `Insight Card Compact` (3405) | 11_dashboard_hero.css |
| `Track Record 시각화 (도넛+바)` (3413) | 12_dashboard_rest.css |
| `Top Picks 불릿 차트` (3470) + `3-Zone 카드` (3525) | 12_dashboard_rest.css |
| `테마 신뢰도 링` (3618) + `발굴 유형 도넛` (3624) + `섹터 버블` (3656) | 10_themes.css |
| `Yield Curve` (3689) + `Market Summary 접이식` (3692) | 11_dashboard_hero.css |
| `모바일 반응형 (대시보드)` (3717) | 19_responsive.css |
| `Stock Analysis: Hero ~ Full report/Markdown` (3782~4112) | 13_stock_analysis.css |
| `Stock Analysis 모바일 반응형` (4152) | 19_responsive.css |

(T3~T10 구현 시 이 매핑을 기준으로 진행 — 실측 차이가 있으면 가까운 파일로 조정)
