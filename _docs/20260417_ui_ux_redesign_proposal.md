# AlphaScope UI/UX · IA 개편 — Todo 체크리스트

> **작성일**: 2026-04-17 · **최근 업데이트**: 2026-04-17 (P0 + P1 9건 완료 반영)
> **대상**: investment-advisor (AlphaScope) — AI 투자 분석 SaaS
> **전제**: FastAPI + Jinja2(다크 테마) 유지, PC + 모바일(반응형 웹) 동등 UX 제공
> **원천 문서**:
> - [20260417_business_review_subscription.md](20260417_business_review_subscription.md) — 3-Tier 요금제·약점
> - [20260417_subscription_action_items.md](20260417_subscription_action_items.md) — 백엔드 Action Items
> - [20260417_subscription_execution_stages.md](20260417_subscription_execution_stages.md) — 실행 단계

---

## 📊 진행 현황

| Phase | 완료 / 전체 | 비율 |
|-------|:-----------:|:----:|
| **P0 (출시 필수)** | **9 / 9** | ✅ 100% |
| **선행 백엔드 (A-001 · A-002 · A-003)** | **3 / 3** | ✅ 100% |
| **P1 (1개월 내)** | **9 / 9** | ✅ 100% |
| **P2 (확장기)** | 0 / 8 | ⬜ 0% |
| **전체 UI Todo** | **18 / 26** | 69% |

---

## 🎯 Executive Summary (고정)

| 항목 | 내용 |
|------|------|
| **진단 한줄** | 현재 UI는 "분석 결과 뷰어"에 최적화되어 있으나 **구독 전환·신뢰 자산·모바일 정식 대응**이 결여됨 |
| **핵심 문제 3가지** | ① 업그레이드 동선 부재 ② 트랙레코드·면책 UI 자리 미정 ③ 모바일 네비게이션 품질이 PC에 못 미침 |
| **개편 축 3가지** | **(1)** 역할 기반 IA 재그룹화 **(2)** 티어·트랙레코드·면책의 상시 노출 **(3)** 모바일 First-class 경험(하단 탭바 + 카드 UI) |
| **기대 효과** | Free→Pro 전환율 개선, 신뢰 확보, 모바일 이탈률 저감, 경쟁사 대비 "정보 깊이 + 모바일 가벼움" 포지셔닝 |

---

## ✅ P0 — 출시 필수 (완료)

### 선행 백엔드

- [x] **A-003** `users.tier` + `tier_expires_at` 스키마 (v16 마이그레이션) — [shared/db.py](../shared/db.py), [api/auth/models.py](../api/auth/models.py)
- [x] **A-002** 티어 한도 체크 인프라 — [shared/tier_limits.py](../shared/tier_limits.py), [api/auth/dependencies.py](../api/auth/dependencies.py)
  - [x] `require_tier()` Depends 팩토리
  - [x] `quota_exceeded_detail()` 402 응답 스키마
  - [x] watchlist / subscription / chat 한도 인라인 체크
  - [x] 채팅 일일 한도 KST 기준 계산
- [x] **A-001** 트랙레코드 집계 API — [api/routes/track_record.py](../api/routes/track_record.py)
  - [x] `/api/track-record/summary` 공개 엔드포인트 (비로그인 접근 가능)
  - [x] 기간별(1m/3m/6m/1y) 승률·평균 수익률
  - [x] 분류별(discovery_type) 성과 — `LOWER()` 정규화
  - [x] 최근 30일 Top Picks (`daily_top_picks` 활용)
  - [x] disclaimer 문구 — "추천 당일 기준 과거 모멘텀" 명시

### UI-01 상단 헤더·드롭다운 티어 배지

- [x] `_base_ctx()`에 `tier`, `tier_label`, `tier_badge_color`, 사용량 3종 주입 — [api/routes/pages.py](../api/routes/pages.py)
- [x] 헤더 우측 상시 배지 (`FREE` / `PRO` / `PREMIUM`) — [api/templates/base.html](../api/templates/base.html)
- [x] 유저 드롭다운 헤더에 플랜 라벨 + "플랜 / 업그레이드" 링크
- [x] CSS `.badge-plan-{free,pro,premium}` 컬러 팔레트 — [api/static/css/style.css](../api/static/css/style.css)

### UI-02 공용 업그레이드 모달 + 402 자동 가로채기

- [x] `partials/_upgrade_modal.html` 공용 모달 — [api/templates/partials/_upgrade_modal.html](../api/templates/partials/_upgrade_modal.html)
- [x] `openUpgradeModal(detail)` / `closeUpgradeModal()` 전역 JS
- [x] 전역 `fetch` 인터셉터 — 402 감지 시 자동 모달 오픈
- [x] Esc / 배경 클릭으로 닫기
- [x] 한도 초과 payload(`feature`, `current_tier`, `usage`, `limit`, `message`) 파싱

### UI-03 표준 면책 배너 (Footer)

- [x] `partials/_disclaimer_banner.html` 신규 — [api/templates/partials/_disclaimer_banner.html](../api/templates/partials/_disclaimer_banner.html)
- [x] `base.html` content-body 뒤 전 페이지 노출
- [x] CSS `.global-disclaimer` 반응형 스타일
- [x] "투자 권유가 아닙니다" 임시 문구 + 트랙레코드 링크
- [ ] **후속 필요**: A-020 법무 자문 문구로 교체

### UI-04 트랙레코드 공개 페이지

- [x] 페이지 라우트 `/pages/track-record` — [api/routes/pages.py](../api/routes/pages.py)
- [x] 템플릿 [api/templates/track_record.html](../api/templates/track_record.html)
  - [x] 기간별 승률·평균 수익률 4-카드 (모바일 2x2)
  - [x] 분류별 성과 테이블
  - [x] 최근 Top Picks 카드 리스트
  - [x] 클라이언트 fetch → 렌더 + 에러/빈 상태 처리
- [x] 대시보드 Track Record 위젯 삽입 — [api/templates/dashboard.html](../api/templates/dashboard.html)

### UI-05 Pricing (요금제 비교) 페이지

- [x] 페이지 라우트 `/pages/pricing` — 비로그인도 접근 가능
- [x] 템플릿 [api/templates/pricing.html](../api/templates/pricing.html)
  - [x] 3-Tier 카드 (Pro 강조 ribbon)
  - [x] 한도 수치 행(관심종목·구독·Stage2·채팅·이력)
  - [x] "현재 이용 중" 배지 (로그인 시 자신의 티어)
  - [x] 비로그인은 "무료로 시작 / 가입 후 선택" CTA
- [ ] **후속 필요**: 실제 결제 연동 (A-004 + A-023 PG) — Phase 1

### UI-06 모바일 하단 탭바 (Bottom Tab Bar)

- [x] `partials/_bottom_tabbar.html` — 5개 탭 (Today / Themes / Picks / Record / Watch 또는 Pricing)
- [x] CSS `.bottom-tabbar` + `.bottom-tab` — 모바일(`@media max-width: 768px`) 전용
- [x] 활성 탭 강조 (`active::before` 상단 3px 바)
- [x] `.content-body`에 탭바 높이만큼 `padding-bottom` 추가
- [x] 비로그인은 5번째 탭이 `Pricing`으로 전환

### UI-07 터치 타겟 ≥ 44px 일괄 상향

- [x] `.modal-actions .btn`, `.pricing-cta`, `.bottom-tab` min-height 44px
- [x] 모바일에서만 `.content-body .btn`, `.filter-select`, `.filter-input`, `.user-dropdown-item` 44px
- [x] 전역 상향이 아닌 **컨텍스트 한정** 적용 (헤더 소형 버튼 보존)

### UI-08 워치리스트 / 알림 `[n/한도]` 사용량 표기

- [x] [api/templates/watchlist.html](../api/templates/watchlist.html) 헤더에 티어 배지 + `n / 한도` 표기
- [x] 한도 도달 시 "업그레이드" 버튼 즉시 노출
- [x] 알림 구독 섹션에도 동일 표기
- [x] 프로필 페이지에 사용량 행 추가 — [api/templates/profile.html](../api/templates/profile.html)

### UI-09 iOS 세이프 에어리어 (Safe Area Inset)

- [x] `body` 좌우 `env(safe-area-inset-*)` (@supports 가드)
- [x] `.bottom-tabbar padding-bottom` safe-area 반영
- [x] `.modal-backdrop` padding-bottom safe-area 반영
- [x] Dynamic Island / Home Indicator와 겹침 방지

### 검증

- [x] L2 단위 테스트 **29/29 통과** — `tests/test_tier_limits.py`, `tests/test_track_record.py`, `tests/test_pages_new.py`
- [x] L1 스모크 — `/pages/pricing`, `/pages/track-record` 200 응답 + partial 렌더 확인
- [x] Phase 6 품질 리뷰 (3개 에이전트 병렬) → HIGH 5건 + MEDIUM 4건 + LOW 2건 **모두 반영 수정**

---

## ✅ P1 — 1개월 내 (완료)

### UI-10 IA 재그룹화

- [x] 좌측 sidebar 4개 그룹(인사이트 / 내 포트폴리오 / 히스토리 / 커뮤니케이션) + 계정 으로 재배치 — [api/templates/base.html](../api/templates/base.html)
- [x] `.nav-group-title` CSS + 그룹 레이블 추가 — [api/static/css/style.css](../api/static/css/style.css)
- [x] 내 포트폴리오는 로그인 시에만 노출, 알림 미읽음 `.nav-badge` 표기
- [x] 기존 라우트 경로는 그대로 유지 (하위 호환)

### UI-11 대시보드 전면 리뉴얼

- [x] Track Record 위젯 확장 — 1M / 3M / 6M / 1Y 탭 토글 — [api/templates/dashboard.html](../api/templates/dashboard.html)
- [x] Free 사용자에게 3번째 테마부터 🔒 블러 + 업그레이드 CTA 오버레이 (`.card-locked`)
- [x] 상단에 Free 전용 광고 슬롯 배너 (ad_slot 매크로)
- [x] 비로그인은 랜딩으로 자동 리디렉트 (UI-16과 연동)

### UI-12 제안 상세 탭 분할

- [x] `.detail-row` 내부를 탭 구조로 재구성 (페이지 신설 대신 인라인 개선) — [api/templates/proposals.html](../api/templates/proposals.html)
- [x] 탭 구성: **개요** / **수익률** / **분석** / **메모** (기간별 수익률이나 분석 데이터 없으면 해당 탭 자동 숨김)
- [x] 가로 스크롤 탭 + `active::after` 언더라인
- [x] PC/모바일 공통 (모바일은 padding·폰트 축소)

### UI-13 제안 상세 모바일 Sticky CTA 바

- [x] 모바일에서 `[⭐ 관심][🔔 알림][📝 메모]` 3개 버튼을 detail-panel 하단 `position:sticky`로 고정
- [x] 하단 탭바(56px)와 safe-area inset 합산하여 배치 (`bottom: calc(56px + env(...))`)
- [x] 터치 타겟 44px 보장
- [x] 관심 → `/api/watchlist`, 알림 → `/api/subscriptions`, 메모 → 메모 탭 전환 + 포커스

### UI-14 테마 등급 뱃지 (S/A/B/C/D)

- [x] `confidence_score` → 등급 매핑: S(≥0.85) / A(≥0.70) / B(≥0.55) / C(≥0.40) / D(미만)
- [x] `grade_badge()` Jinja2 매크로 — [api/templates/_macros.html](../api/templates/_macros.html)
- [x] `theme_header` 매크로에 자동 삽입 → 대시보드·세션 상세·테마 목록 전체에 적용
- [x] CSS `.grade-badge-{s,a,b,c,d}` 컬러 팔레트

### UI-15 모바일 복잡 테이블 → 카드 스택

- [x] `.table-stack-mobile` 유틸 클래스 — 모바일에서 `<td data-label="...">`의 라벨을 `::before`로 표시하는 스택 레이아웃
- [x] 워치리스트 테이블 적용 — [api/templates/watchlist.html](../api/templates/watchlist.html)
- [ ] **후속**: 세션 상세의 테마별 제안 테이블, 티커 이력 테이블에 동일 유틸 확대 적용

### UI-16 비로그인 공개 랜딩 페이지

- [x] 신규 라우트 `/pages/landing` — [api/routes/pages.py](../api/routes/pages.py)
- [x] 템플릿 [api/templates/landing.html](../api/templates/landing.html) — Hero + 기능 3카드 + Track Record 미리보기 + 요금제 티저
- [x] 인증 활성 환경에서 비로그인 `/` → `/pages/landing` 302 리디렉트
- [x] 클라이언트 fetch로 실제 승률 수치 주입

### UI-17 Free 광고 슬롯 컴포넌트

- [x] `partials/_ad_slot.html` — [api/templates/partials/_ad_slot.html](../api/templates/partials/_ad_slot.html)
- [x] `ad_slot('banner'|'compact')` 매크로로 티어 조건 렌더
- [x] Free 티어만 노출 (tier 변수가 'free'일 때)
- [x] 대시보드 상단에 `ad_slot('banner')` 삽입

### UI-18 Tablet 브레이크포인트(769~1024px) 도입

- [x] `@media (min-width: 769px) and (max-width: 1024px)` 블록 추가
- [x] Sidebar 폭 `240px → 200px`, stat-grid-6 3열, scenario-grid 2열, top-picks 2열
- [x] 태블릿에서 하단 탭바 숨김 (Sidebar + 메인 레이아웃 유지)

---

## ⬜ P2 — 확장기 (출시 후 3개월~)

### UI-19 전역 검색 (종목 티커 / 테마 키워드)

- [ ] 상단 헤더에 검색 입력 필드
- [ ] `/api/search?q=...` 엔드포인트 (종목·테마 통합)
- [ ] 키보드 단축키(`Cmd+K` / `Ctrl+K`)
- [ ] 자동완성 드롭다운

### UI-20 내 스크리너 저장

- [ ] 테마·제안 필터 조합을 "내 스크리너"로 저장
- [ ] DB 테이블 `user_screeners` (name, filters JSONB)
- [ ] 사용자 프로필에 저장된 스크리너 목록

### UI-21 스와이프 제스처

- [ ] 대시보드 Top Themes 카드 좌우 스와이프
- [ ] 제안 카드에서 스와이프로 관심/해제
- [ ] Hammer.js 또는 vanilla touch 이벤트

### UI-22 Pull-to-Refresh

- [ ] 대시보드·테마 목록에서 당겨서 새로고침
- [ ] 모바일 전용 (데스크톱 미적용)

### UI-23 PWA 전환

- [ ] `manifest.json` 생성 (아이콘·이름·테마 컬러)
- [ ] `service-worker.js` 등록 (오프라인 캐시 전략)
- [ ] "홈 화면에 추가" 프롬프트 UX
- [ ] 오프라인 시 마지막 분석 결과 열람 가능

### UI-24 다크 / 라이트 토글

- [ ] CSS 변수 기반 테마 스위칭
- [ ] 시스템 설정(`prefers-color-scheme`) 자동 감지
- [ ] 사용자 선택을 `localStorage`에 저장

### UI-25 접근성 감사 (WCAG 2.1 AA)

- [ ] 대비 4.5:1 이상 전수 점검
- [ ] 키보드 포커스 이동 경로 확인
- [ ] 스크린리더 라벨(`aria-label`, `alt`) 보강
- [ ] 폼 에러 메시지 접근성

### UI-26 모바일 성능 최적화

- [ ] 차트 라이브러리 도입 시 lazy 로딩
- [ ] 이미지 WebP + `srcset`
- [ ] Critical CSS 분리 검토
- [ ] Lighthouse 성능 점수 90+ 달성

---

## 📎 참고 — 경쟁사별 UI/UX 벤치마크 요약

> 자세한 분석은 [이전 버전 문서](#) 참조. 아래는 핵심 차용 요소만 간추림.

| 경쟁사 | 차용 요소 | 반영 Todo ID |
|--------|-----------|--------------|
| 알파스퀘어 | 종목 상세 탭 분할 | UI-12 |
| 한경컨센서스 | 목표가 변화 타임라인 | UI-11 (Track Record 위젯) |
| 증권사 MTS | 모바일 카드 UI, 자동 큐레이션 | UI-06 ✅, UI-11 |
| 토스/카카오페이 | 저관여 카드 피드 | UI-11 (Free 대시보드) |
| 텔레그램 리딩방 | 1줄 요약 + Confidence 뱃지 | UI-14 |
| Seeking Alpha | S/A/B/C 등급 시각화 | UI-14 |
| Koyfin | 스크리너 + 필터 저장 | UI-20 |

### 목표 포지션

```
                           [깊이 High]
                                ▲
             Koyfin  ●        │         ● Seeking Alpha
                               │
  한경컨센서스 ●                │                 ● 알파스퀘어
                               │
  [전문성 High] ◀───────────────┼───────────────▶ [대중성 High]
                               │
            증권사리서치 ●      │       ● AlphaScope (목표 포지션)
                               │
                               │         ● 토스/카카오페이 인사이트
                               │
                      텔레그램리딩방 ●
                                ▼
                           [깊이 Low]
```

> **목표 포지션**: "대중성 있는 깊이" — 깊이(AI 멀티스테이지 분석) × 대중성(카드·스토리 UI·모바일 동등)

---

## 📎 참고 — 현재 프로젝트 진단 요약

### 강점

- 정보 구조(세션→테마→제안→종목)의 자연스러운 드릴다운
- 다크 테마 CSS 변수 체계 일관성
- 모바일 기초(768px 드로어 네비, 카드 스택) 구비
- 401 자동 갱신·알림 배지 등 기반 UX 탄탄

### 약점 / 공백 — 해소 현황

| # | 약점 | 해소 Todo ID | 상태 |
|---|------|--------------|:---:|
| G1 | 업그레이드 동선 부재 | UI-01 ✅, UI-02 ✅, UI-05 ✅, UI-17 ✅ | ✅ |
| G2 | 티어 가시화 | UI-01 ✅ | ✅ |
| G3 | 한도 UX 없음 | UI-02 ✅, UI-08 ✅ | ✅ |
| G4 | 트랙레코드 없음 | UI-04 ✅ | ✅ |
| G5 | 면책 고지 없음 | UI-03 ✅ | ✅ |
| G6 | IA 카테고리화 부족 | UI-10 ✅ | ✅ |
| G7 | 모바일 하단 탭바 부재 | UI-06 ✅ | ✅ |
| G8 | 모바일 복잡 테이블 | UI-15 ✅ (워치리스트) / 타 테이블 후속 | 🟡 |
| G9 | 제안 상세 CTA 빈약 | UI-12 ✅, UI-13 ✅ | ✅ |
| G10 | 공개 랜딩 부재 | UI-16 ✅ | ✅ |
| G11 | 한국어 가독성 테스트 필요 | UI-25 (접근성 감사 내) | ⬜ |

---

## 📎 반응형 디자인 전략 (확정)

### 브레이크포인트

| 이름 | 범위 | 기기 예시 | 상태 |
|------|------|-----------|:---:|
| Mobile | ≤ 768px | iPhone SE(375), Galaxy S(360) | ✅ 적용 중 |
| Tablet | 769~1024px | iPad(768), iPad Air(820) | ✅ UI-18 완료 |
| Desktop | ≥ 1025px | 일반 데스크톱 | ✅ 적용 중 |

### 기기별 테스트 체크리스트

- [ ] iPhone SE (375×667) — 탭바 간격, 햄버거 터치
- [ ] iPhone 15 (393×852) — Dynamic Island, safe-area
- [ ] Galaxy S24 (360×800) — Samsung Internet 호환
- [ ] iPad (768×1024) — Tablet 브레이크포인트 (UI-18 완료 후)
- [ ] iPad Pro (1024×1366) — Desktop 전환
- [ ] 데스크톱 (1440×900) — Sidebar + 메인 + 여백

> 위 기기 테스트는 **실제 디바이스 또는 브라우저 DevTools**로 P1 작업(UI-11, UI-18) 완료 후 일괄 수행.

---

## ⚖️ 면책

본 문서는 UI/UX 개편 Todo 체크리스트입니다. P1/P2 항목은 착수 전 세부 요구사항을 재확인하고 원천 문서(`business_review_subscription.md`, `subscription_action_items.md`)와 정합성을 검증해야 합니다.
