# 반응형 UI 감사 + PC/모바일 최적화 계획

**작성일**: 2026-04-24 13:32 KST
**대상**: investment-advisor 웹 UI 전반 (PR-1~5 직후)
**조사 방법**: [api/templates/](../api/templates/), [api/static/css/style.css](../api/static/css/style.css) 정적 코드 분석
**참고 전제**: [20260424124541_ux-restructure-proposal.md](20260424124541_ux-restructure-proposal.md)

---

## 1. 기존 @media 쿼리 맵

| 라인 | Breakpoint | 대상 |
|------|-----------|------|
| [L1341](../api/static/css/style.css) | ≤768 | `.proposal-card-body` 1열 |
| [L1819](../api/static/css/style.css) | ≤1280 | `.hero-row1` 2열→1열 |
| [L1987](../api/static/css/style.css) | ≤768 | Dashboard Hero/KPI/TopPicks 재배치 |
| [L2037](../api/static/css/style.css) | ≤480 | `.stat-grid-6` 3→2열 |
| [L4025](../api/static/css/style.css) | ≤768 | `.table-stack-mobile` 완전 스택 |
| [L4083](../api/static/css/style.css) | ≤768 | **사이드바 off-canvas** + 헤더 재배치 + `.filters` 2열 |
| [L4196](../api/static/css/style.css) | 769~1024 | 태블릿: 사이드바 200px, 하단탭바 숨김 |
| [L4218](../api/static/css/style.css) | ≤768 | `.bottom-tabbar` 노출, `.content-body` padding-bottom 72px + safe-area |

**Breakpoint 현황**: 1280 / 1024 / 768 / 480 4단. 신규 breakpoint 추가 불필요.

## 2. 페이지×뷰포트 매트릭스

✓ 정상 / ⚠ 이슈 있음 / ✗ 미처리

| 페이지 | 360×640 | 768×1024 | 1024×768 | 1440×900 |
|--------|---------|----------|----------|----------|
| base (사이드바·헤더) | ⚠ 헤더 버튼 터치 타겟, user-dropdown overflow | ✓ off-canvas 경계 | ✓ 200px 사이드바 | ✓ |
| dashboard | ✓ Hero 1열, TopPicks 85vw carousel | ⚠ market_summary 접힘 경계 | ✓ | ✓ |
| themes | ⚠ scenario-grid 3열 유지 | ⚠ 동일 | ✓ | ✓ |
| proposals | ✗ **테이블 14열 가로 스크롤**, ⚠ date input 140px 고정 | ⚠ 필터 고급 토글 인지성 | ✓ 우측 패널 560px | ✓ |
| watchlist | ✓ table-stack-mobile 적용, 탭 축소 | ✓ | ✓ | ✓ |
| sessions | ⚠ 상세보기 버튼 터치 타겟 | ✓ | ✓ | ✓ |

## 3. 실측 이슈 상세

### P0 (차단) — 즉시 수정

1. **[proposals.html](../api/templates/proposals.html) 테이블 `.table-stack-mobile` 미적용**
   - 14열 테이블이 360~480px 뷰포트에서 강제 가로 스크롤
   - 동일 파일 [watchlist.html L100-101](../api/templates/watchlist.html)은 이미 적용 → 회귀로 간주할 만한 격차
   - 수정: `<table class="screener-table table-stack-mobile">` + 모든 `<td>`에 `data-label="..."` 부착

### P1 (중요) — 이번 PR 포함

2. **Date input 140px 고정폭**
   - [proposals.html L168, L172](../api/templates/proposals.html) `style="width:140px;"` × 2
   - 360×640 뷰포트 기준 필터 2열 시 280px + gap + padding → 실제 overflow
   - 수정: `flex: 1 1 140px; min-width: 120px;`로 완화

3. **헤더 버튼 터치 타겟 44px 미만**
   - [base.html L176-177](../api/templates/base.html) Pricing/로그인 버튼 `padding:6px 16px;font-size:13px` → 실제 높이 ≈ 30px
   - 수정: 모바일에서 `min-height: 40px; padding: 8px 14px`로 보강

4. **user-dropdown-menu 200px min-width**
   - style.css 고정값이 340px 이하 뷰포트에서 우측 overflow 가능
   - 수정: scoped style로 `max-width: calc(100vw - 24px)` 추가

5. **Themes scenario-grid 모바일 전환 부재**
   - `.scenario-grid`는 3열 기본이며 ≤768 규칙 없음
   - 수정: [themes.html](../api/templates/themes.html) `{% block head %}` scoped style에 `@media (max-width: 768px) { .scenario-grid { grid-template-columns: 1fr; } }` 추가

6. **소형 액션 버튼 터치 타겟 44px 미만**
   - [proposals.html](../api/templates/proposals.html) 메모 저장/삭제 (`padding: 4px 14px` / `4px 10px`)
   - [watchlist.html](../api/templates/watchlist.html) 삭제·해제 (`padding: 3px 10px`, `font-size: 11px`)
   - [sessions.html L22](../api/templates/sessions.html) 상세보기 (`padding: 6px 16px`)
   - 수정: 모바일 스코프에서 최소 `min-height: 36px; padding: 8px 12px`

### P2 (여유) — 별도 PR 후보

7. 인라인 폰트 11~13px 고정 20+개 — 모바일 가독성 영향. 디자인 토큰 정비 PR(S4)에서 일괄 처리 권장.
8. `.confidence-bar` / `.tr-return-block` min-width 고정 — 컨텍스트 필요 (Hero Row 1 디자인 검토).

## 4. 모바일 하단 탭바 (`_bottom_tabbar.html`)

- ≤768에서만 노출, 769~1024(태블릿)에서는 숨김
- 5~6개 퀵 탭 (Today / Themes / Picks / Record / Watch 또는 Pricing)
- PR-5 사이드바 6그룹과 **충돌 없음** — 하단 탭바는 핵심 4~5개만 노출해 사이드바의 축약 역할
- `.bottom-tab { min-height: 44px }` — 터치 타겟 준수
- `content-body { padding-bottom: calc(72px + env(safe-area-inset-bottom)) }` — iOS notch 대응

## 5. 최적화 전략 (To-Be)

**Breakpoint 정책**: 기존 1280/1024/768/480 유지. 추가 없음.

**터치 타겟 가이드**:
- 모바일(≤768): 주요 액션 버튼 `min-height: 44px`, 보조 액션 `min-height: 36px`
- 아이콘 단독 버튼: 아이콘 크기와 무관하게 40×40px 히트박스

**레이아웃 전환 규칙**:
- 테이블 5열 이상 → `.table-stack-mobile`로 카드 변환
- 사이드바 → off-canvas (이미 구현)
- 필터 `<select>` → 2열 그리드 (이미 구현)
- 차트 컨테이너 → `width: 100%` + `height: auto` (이미 구현)

**인라인 px 고정 완화**:
- 고정 `width:NNNpx`는 `flex: 1 1 NNNpx; min-width: Npx`로 대체
- 폰트 사이즈는 최대한 유지하되, 모바일 스코프에서 11px → 12px 보강

## 6. 이번 PR(PR-6) 수정 대상

| # | 파일 | 조치 |
|---|------|------|
| 1 | [proposals.html](../api/templates/proposals.html) | 테이블에 `table-stack-mobile` 클래스 + 모든 `<td>`에 `data-label` / date input 유연 폭 / scoped 모바일 스타일 |
| 2 | [base.html](../api/templates/base.html) | 모바일 헤더 버튼 터치 타겟 보강 + user-dropdown-menu max-width 제한 |
| 3 | [themes.html](../api/templates/themes.html) | scenario-grid 모바일 1열 규칙 추가 |
| 4 | [watchlist.html](../api/templates/watchlist.html) | 액션 버튼 모바일 터치 타겟 보강 |
| 5 | [sessions.html](../api/templates/sessions.html) | 상세보기 버튼 모바일 height 보강 |

자동 생성 [style.css](../api/static/css/style.css) 직접 수정 없음 — partial/페이지 `{% block head %}` scoped `<style>`로.

## 7. 커밋 전 검증 체크리스트

### 360×640 (모바일 소형)
- [ ] 사이드바 오프캔버스 열림/닫힘 (햄버거·오버레이·메뉴 클릭)
- [ ] 하단 탭바 5~6개 항목 터치 영역 ≥44px
- [ ] 헤더 Pricing/로그인 버튼 터치 가능 (비로그인)
- [ ] 헤더 알림 벨·아바타 드롭다운 열림, 메뉴 overflow 없음
- [ ] Dashboard — Hero 1열, TopPicks 가로 스와이프, DeepDive 탭 전환
- [ ] Themes — 테마 카드 1열, scenarios·macro 펼침 시 1열
- [ ] Proposals — **테이블 스택 카드** 표시, 가로 스크롤 없음, 필터 2열, 고급 토글, 우측 패널이 바텀시트로 슬라이드업
- [ ] Watchlist — 탭 전환, 테이블 스택 카드
- [ ] Sessions — 상세보기 버튼 ≥44px

### 768×1024 (태블릿 세로)
- [ ] Breakpoint 경계 — 사이드바 오프캔버스 동작
- [ ] 하단 탭바 숨김 (≥769에서는 미노출)
- [ ] Dashboard DeepDive 탭 가로 스크롤 가능
- [ ] Proposals 패널 바텀시트 유지

### 1024×768 (태블릿 가로·노트북 소형)
- [ ] 사이드바 200px 폭 (태블릿 모드)
- [ ] Dashboard Hero Row 1 1열 (1280 이하 규칙)
- [ ] Proposals 우측 패널 560px 정상
- [ ] Themes 카드 가독성 확인

### 1440×900 (PC 표준)
- [ ] 사이드바 240px, 컨텐츠 좌측 여백 정상
- [ ] Dashboard Hero Row 1 2열 (KPI + Track Record)
- [ ] Proposals 필터 한 줄 정렬
- [ ] 모든 탭 전환 호버 상태 정상

## 8. 비포함 항목 (별도 PR)

- 인라인 폰트/패딩 → 디자인 토큰 일괄 정비 (S4, style.css src/ 빌드 파이프라인 파악 선행)
- Hero Row 2 (`_hero_row2.html`) 3열 그리드 모바일 auto-fit 전환
- `_sector_heatmap` 셀 크기 반응형 (현재 충분히 동작)
- 전역 Ctrl+K 검색 팔레트 (S3)
