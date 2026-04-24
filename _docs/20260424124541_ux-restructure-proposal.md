# UX 재설계 제안서 — 메뉴 중복 제거 및 정보 위계 정리

**작성일**: 2026-04-24 12:45 KST
**대상**: investment-advisor 웹 UI (FastAPI + Jinja2, 다크 테마)
**범위**: 정보구조(IA)·내비게이션·페이지 레이아웃 (코드 수정 없음, 제안서 전용)
**조사 기준**: [api/templates/](../api/templates/), [api/routes/](../api/routes/), [api/static/css/style.css](../api/static/css/style.css), [shared/tier_limits.py](../shared/tier_limits.py) 실측

---

## 1. 요약 (Executive Summary)

1. 현 사이드바는 1차 메뉴 **13개·5개 그룹**으로, "사이드바 1차 항목 ≤ 7" 휴리스틱을 크게 초과한다.
2. "최근 분석" 관점의 페이지(Dashboard·Sessions·Themes·Proposals)가 동일 데이터 소스를 서로 다른 관점으로 반복 노출하나, 진입 경로만 네 갈래로 분산되어 있다.
3. **Dashboard는 9~10개 블록이 세로로 쌓이는 롱스크롤 구조**이며 `TIER 1 → TIER 3 → TIER 2` 라는 비표준 순서가 코드 주석에 남아 있다 ([dashboard.html](../api/templates/dashboard.html)). First Glance → Action → Deep Dive로 재정렬이 필요하다.
4. **알림 진입점이 사이드바 링크와 헤더 벨 아이콘으로 완전 중복** ([base.html](../api/templates/base.html) L35 vs L86-89). 사이드바 항목을 제거하면 8개 슬롯이 절약된다.
5. **Theme Chat·AI Tutor·문의**는 개별 사이드바 슬롯을 차지하지만 사용 빈도가 낮은 대화형 섹션이므로 하나의 "대화" 허브로 통합 가능하다.
6. **관리자 진입점 3중화**: 사이드바 푸터(auth 비활성 시), 드롭다운 관리자 섹션, `/admin` 직접 URL이 공존. 드롭다운으로 일원화해야 한다.
7. CSS 토큰은 이미 잘 정의돼 있으나 ([style.css L1-30](../api/static/css/style.css)) 카드·배지·테이블 규격이 페이지마다 미세하게 달라 시각적 리듬이 깨진다.
8. Proposals 테이블은 행 확장 시 5섹션이 한 번에 펼쳐져 정보가 폭발한다 — 탭 기반 세분화 필요.
9. 제안 로드맵: Quick Win 3주 + Medium 4주 + Structural 6주, 총 5개 PR 단위로 분할.
10. 본 제안은 **코드 수정 제로, 문서 제안서 1편**으로 종결한다.

---

## 2. 현상 진단 (As-Is)

### 2.1 메뉴 구조 맵 (사이드바)

[api/templates/base.html](../api/templates/base.html) L18-69 기준.

```
Sidebar (240px fixed)
├─ 인사이트 (5)
│   ├─ Dashboard              →  /                         active: dashboard
│   ├─ Themes                 →  /pages/themes             active: themes
│   ├─ Stock Picks            →  /pages/proposals          active: proposals
│   ├─ Track Record           →  /pages/track-record       active: track_record
│   ├─ Screener               →  /pages/screener           active: screener
│   └─ Theme Chat*            →  /pages/chat               *Pro/Premium 또는 admin/moderator
├─ 내 포트폴리오 (2) — 로그인 필수
│   ├─ Watchlist              →  /pages/watchlist          뱃지: usage/limit
│   └─ Notifications          →  /pages/notifications      뱃지: 미읽수
├─ 조회·히스토리 (2)
│   ├─ 티커 검색 폼           →  /pages/stocks/{ticker}
│   └─ Sessions               →  /pages/sessions
├─ 학습·대화 (2)
│   ├─ Education              →  /pages/education
│   └─ AI Tutor*              →  /pages/education/chat     *로그인 필요
├─ 계정·지원 (2)
│   ├─ Pricing                →  /pages/pricing
│   └─ 문의하기*              →  /pages/inquiry            *로그인 필요
└─ (푸터) auth_enabled=false 시만
    ├─ 관리자                 →  /admin
    └─ API Docs               →  /docs
```

**1차 항목 총 13개** (조건부 포함). 그룹 5개.

### 2.2 메뉴 구조 맵 (헤더 우측)

[base.html](../api/templates/base.html) L72-151 기준.

```
Header (우측 정렬)
├─ Tier Badge                 (FREE/PRO/PREMIUM)
├─ 알림 벨 🔔                 →  /pages/notifications  (미읽수 뱃지)
└─ 유저 아바타 드롭다운
    ├─ [헤더] 닉네임 · 역할 · 티어
    ├─ 관심 종목               →  /pages/watchlist    (usage/limit 표시)
    ├─ 알림                    →  /pages/notifications (미읽수 표시)
    ├─ 프로필                  →  /pages/profile
    ├─ 플랜 업그레이드         →  /pages/pricing
    ├─ [관리자, Admin/Moderator]
    │   └─ 사용자 관리         →  /admin/users
    ├─ [관리자, Admin만]
    │   ├─ 감사 로그           →  /admin/users/audit-logs
    │   ├─ 관리자 메뉴         →  /admin
    │   └─ API Docs            →  /docs
    └─ 로그아웃
```

비로그인 사용자는 Tier Badge 자리에 `Pricing` + `로그인` 버튼 노출 (L146-150).

### 2.3 중복 진입점 매트릭스

| # | A 경로 | B 경로 | 도달 대상 | 중복 유형 | 근거 |
|---|--------|--------|-----------|----------|------|
| 1 | 사이드바 `Notifications` | 헤더 벨 🔔 | `/pages/notifications` | **완전 중복** | [base.html L35 vs L86-89](../api/templates/base.html) |
| 2 | 사이드바 `Watchlist` | 드롭다운 "관심 종목" | `/pages/watchlist` | **완전 중복** (경로도 동일) | [base.html L35 vs L109](../api/templates/base.html) |
| 3 | 드롭다운 "플랜 업그레이드" | 사이드바 `Pricing` | `/pages/pricing` | **완전 중복** | [base.html L55 vs L117](../api/templates/base.html) |
| 4 | 사이드바 푸터 "관리자" | 드롭다운 "관리자 메뉴" | `/admin` | **조건 분기 중복** (auth on/off) | [base.html L66 vs L132-135](../api/templates/base.html) |
| 5 | Dashboard `_themes_list` 블록 | 사이드바 `Themes` | 테마 데이터 | **데이터 중복, 표현 다름** | [dashboard.html](../api/templates/dashboard.html) vs themes.html |
| 6 | Dashboard `_top_picks` 블록 | 사이드바 `Stock Picks` | 제안 데이터 | **데이터 중복, 표현 다름** | dashboard.html vs proposals.html |
| 7 | Dashboard `_market_summary` | 사이드바 `Sessions` → 상세 | 세션 요약 | **데이터 중복, 관점 다름** | dashboard.html vs session_detail.html |
| 8 | 사이드바 `Theme Chat` | 사이드바 `AI Tutor` | 두 별개 채팅 | **UI 패턴 중복** (session list 구조 동일) | [chat_list.html](../api/templates/chat_list.html) vs [education_chat_list.html](../api/templates/education_chat_list.html) |

**결론**: #1~#4는 물리적 중복(동일 URL 링크가 여러 곳에 존재), #5~#7은 데이터 중복(동일 소스를 표현만 바꿔 노출), #8은 구조 중복(패턴을 공유하되 목적이 다름).

### 2.4 페이지별 가독성·편의성 이슈

#### Dashboard ([dashboard.html](../api/templates/dashboard.html))

| 섹션 순서 | 내용 | 이슈 |
|----------|------|------|
| 0 | 광고 배너 (Free만) | 상단에 배치되어 유료 유도 동선보다 먼저 광고가 노출됨 |
| 1 | 시장 레짐 배너 | 조건부(`session.market_regime`) — 없을 때와 있을 때 레이아웃 점프 |
| **2a-b** | Hero Row 1/2 (KPI + 스파크라인) | **First Glance 역할, 정확한 자리** |
| 3 | 시장 요약 | 이슈 카드 + 테마 미리보기 — Sessions 페이지와 겹치는 정보 |
| 4 | 채권 수익률 곡선 | 저사용 추정, 조건부 노출임에도 중앙에 배치 |
| 5 | 테마 목록 | Themes 페이지와 동일 데이터 — 필터 없이 라벨만 |
| **6** | **Top Picks** | 코드 주석에 "TIER 2"지만 실제 배치는 5번째 아래 — **Action 블록이 Deep Dive보다 뒤에** |
| 7 | 오늘의 이상 신호 | 중요 신호임에도 7번째 순서 |
| 8 | 섹터 히트맵 (16×8) | 화면 폭이 좁을 때 가로 스크롤 발생 |
| 9 | 뉴스 by 카테고리 | 조건부(`news_by_category`) |

**핵심 이슈**: 코드 주석(L15-34)이 명시한 `TIER 1 → TIER 3 → TIER 2` 순서가 정보 소비 패턴(요약 → 행동 → 탐색)과 역행한다. 사용자가 Top Picks에 도달하기까지 5~6개 블록을 스크롤해야 함.

#### Sessions ([sessions.html](../api/templates/sessions.html))

- **밀도가 너무 낮다**: 카드당 필드 5개, 한 화면에 10개 카드 이하. 리스트뷰인데 메타데이터만 나열.
- 세션 간 "변화점"이 시각화되지 않음 (어제 대비 신규 테마, 신규 Buy 수 등). "오늘 vs 어제" 비교 스캔이 불가능.
- 엔트리 포인트가 약함: 이 페이지에 도달하는 사용자는 "과거 어느 날의 분석"을 찾는데, 검색/날짜 점프가 없다.

#### Themes ([themes.html](../api/templates/themes.html))

- **정보 밀도 Very High** — 테마 카드 하나에 설명/지표/시나리오 그리드/매크로 테이블/구성 종목 태그가 모두 펼쳐짐. 20개 테마가 쌓이면 세로 길이 폭발.
- 필터 3종(horizon, confidence, 검색)만으로는 4~7개 정도의 테마를 "선택적으로" 비교하기 어려움.
- 테마 간 비교 뷰(예: "매크로 영향이 유사한 테마 묶기") 없음.

#### Proposals ([proposals.html](../api/templates/proposals.html))

- **필터 9종이 한 행에 나열** — 시각적 소음. 핵심 필터(Action·확신도·시장)와 보조 필터(발굴유형·투자기간 등) 구분 없음.
- 행 확장 시 5섹션(개요/수익률/분석/메모/Sticky CTA)이 동시에 펼쳐져 세로 1000px 이상 점유. 테이블이 다시 접혀 보이지 않음.
- 관심 종목·알림·메모 CTA가 Sticky이지만 데스크톱에서는 오히려 시선을 분산시킴.

#### Watchlist ([watchlist.html](../api/templates/watchlist.html))

- **두 테이블(관심 종목 + 구독)이 세로로 쌓임** — 한쪽이 비어있을 때도 공간을 차지.
- 구독 테이블에서 "해제" 버튼이 삭제와 시각적으로 유사(red variant) — 실수 유발 가능.
- 한도 초과 업그레이드 버튼이 헤더에만 있어, 테이블 말미에서 추가 등록 시도 시 유도 동선 없음.

### 2.5 공통 레이아웃/스타일 이슈

- **카드 규격 불일치**: `.card` 기본 padding 20px지만 일부 dashboard partial(`_hero_row1`, `_top_picks`)은 자체 padding을 별도로 정의.
- **배지 색상 팔레트 남발**: Free/Pro/Premium 배지, Action 배지(Buy/Sell/Hold), 확신도 배지, 발굴유형 배지 등 한 페이지에 색상 카테고리 5종 이상. [style.css L21-29](../api/static/css/style.css)에 정의된 의미색(`--success`/`--danger`/`--warning`/`--signal`/`--neutral`)만으로는 분리가 부족.
- **반응형**: 사이드바 off-canvas는 잘 동작하나, 테이블(`proposals`, `watchlist`)은 `table-stack-mobile` 클래스가 일관 적용되지 않음.
- **Empty State**: 정의되어 있으나 CTA가 없음("세션 없음"만 표시, "분석 시작" 버튼 등 없음).

### 2.6 티어·역할별 가시성

| 역할 | 사이드바 | 헤더 | 제외 메뉴 |
|------|---------|------|---------|
| Anonymous | 인사이트·조회·학습·Pricing (Theme Chat/Watchlist/Notifications/AI Tutor/문의 제외) | 로그인 버튼 | 포트폴리오 전체 |
| Free | 전체 (Theme Chat 제외) | 벨·아바타 | Theme Chat |
| Pro/Premium | 전체 | 벨·아바타 | (없음) |
| Moderator | 전체 + 사용자 관리(드롭다운) | 벨·아바타 + Admin 섹션 부분 | 감사 로그 |
| Admin | 전체 + 전체 관리 메뉴(드롭다운) | 벨·아바타 + Admin 섹션 전체 | (없음) |

티어 분기가 복잡하지 않고 이미 깔끔한 편. 문제는 **Free 사용자에게 너무 많은 메뉴를 노출**하면서 유료 전환 포인트가 `Pricing` 하나로만 약하게 걸려 있다는 것.

---

## 3. 개선안 (To-Be)

### 3.1 재구성된 IA — 1차 메뉴 5개 원칙

```
Sidebar (재설계안)
├─ 🏠 홈                      /                         (Dashboard 리네이밍)
├─ 📊 분석
│   ├─ Themes                 /pages/themes
│   ├─ Stock Picks            /pages/proposals
│   ├─ Screener               /pages/screener
│   └─ Track Record           /pages/track-record
├─ 📁 내 자료*                *로그인 필수
│   ├─ Watchlist              /pages/watchlist
│   └─ Sessions               /pages/sessions  (히스토리 성격상 "내 자료"로 이동)
├─ 💬 대화*                   *로그인 필수
│   ├─ Theme Chat             /pages/chat             (Pro/Premium 제한 유지)
│   └─ AI Tutor               /pages/education/chat
└─ 📚 학습
    ├─ Education              /pages/education
    └─ 문의하기*              /pages/inquiry           *로그인 필수
```

**1차 메뉴 5개**, **2차 항목 평균 2-4개**, **깊이 2단계 이하**.

**이동·제거된 항목**:
- `Notifications`: 사이드바 제거 → 헤더 벨로 일원화 (#1 중복 제거)
- `Pricing`: 사이드바 제거 → 비로그인 헤더 버튼 + 드롭다운 "플랜 업그레이드"로만 유지 (#3 중복 제거)
- `티커 검색 폼`: 사이드바 제거 → 전역 Ctrl+K 검색 팔레트로 이동 (프리미엄 패턴)
- `관리자` 푸터: 제거 → 드롭다운 관리자 섹션으로 일원화 (#4 중복 제거)
- `Sessions`: 기존 "조회·히스토리" → "내 자료"로 이동 (개인 분석 타임라인 관점)

**근거**:
- 기능 중심 그룹핑 → 사용 목적 중심 그룹핑으로 전환 (분석 탐색 / 내 포트폴리오 / 대화 / 학습)
- 사이드바 아이콘 도입 → 접힌 사이드바(태블릿)에서 스캔성 확보
- 2차 항목 노출은 1차 항목 hover 또는 expand 방식 (사이드바 세로 길이 절감)

### 3.2 중복 제거 전략 매트릭스

| 대상 | 조치 | 근거 | 영향도 |
|------|------|------|--------|
| Notifications (사이드바) | **삭제** | 헤더 벨과 동일 URL | ★★★ |
| Pricing (사이드바) | **삭제** | 비로그인 헤더 + 드롭다운 이미 존재 | ★★ |
| Watchlist (드롭다운) | **유지** | 사이드바 우선, 드롭다운은 "한도 표시"용 속성 유지 | ★ |
| 관리자 (사이드바 푸터) | **삭제** | auth_enabled=false는 개발 환경 전용, 드롭다운에 `{% if not auth_enabled %}` 블록 추가로 대체 | ★★ |
| Dashboard 테마 블록 | **변경** (링크 카드 3개로 축소) | Themes 페이지 FT(First-time) 유도용으로만 기능 | ★★ |
| Dashboard 섹터 히트맵 | **2차 탭으로 이동** | 정보량 과잉, First Glance 역할 아님 | ★★★ |
| Dashboard 채권 수익률 | **축소** (sparkline만) | 저사용 추정, 풀 차트 불필요 | ★ |
| Theme Chat vs AI Tutor 진입 | **"대화" 허브 신설** | UI 패턴 공유, 사이드바 슬롯 1개 절약 가능 | ★★ |

### 3.3 페이지별 레이아웃 개선 — Top 5

#### 3.3.1 Dashboard

**현재**: TIER 1 → TIER 3(시장요약·채권·테마) → TIER 2(Top Picks·신호·히트맵·뉴스) 9~10블록 롱스크롤.

**개선**:
```
┌──────────────────────────────────────┐
│ [선택] 광고 배너 — 작게, Free만      │
├──────────────────────────────────────┤
│ 시장 레짐 배너 (있을 때만, 단일 행)   │
├──────────────────────────────────────┤
│ Hero Row — KPI 4종 + 7일 스파크 2개 │ ← First Glance
├──────────────────────────────────────┤
│ Top Picks (카드 3장 가로 배열)       │ ← Action
│ 오늘의 이상 신호 (우측 열, 세로)     │
├──────────────────────────────────────┤
│ [탭] 테마요약 | 섹터 히트맵 | 뉴스  │ ← Deep Dive (탭으로 묶기)
│       │채권곡선│                     │
└──────────────────────────────────────┘
```

**기대 효과**:
- First view(스크롤 없이 보이는 영역)에 Hero + Top Picks가 들어와 **핵심 인사이트 도달 시간 2~3스크롤 → 0스크롤**
- Deep Dive 블록을 탭으로 묶어 세로 길이 50% 축소
- 코드 주석의 TIER 순서(1→3→2)도 자연스러운 1→2→3로 정렬

#### 3.3.2 Sessions

**현재**: 카드 단순 나열, 필드 5개.

**개선**:
```
┌──────────────────────────────────────┐
│ [날짜 피커] [키워드] [리스크온도]    │
├──────────────────────────────────────┤
│ 2026-04-24 [오늘] 🔴 High Risk       │
│   신규 테마 2 · Buy 5 · 이슈 8       │
│   Δ 어제 대비 +3 Buy, -1 테마        │
│   [상세 보기]                         │
├──────────────────────────────────────┤
│ 2026-04-23 🟡 Medium                 │
│   ...                                 │
└──────────────────────────────────────┘
```

**기대 효과**:
- "어제 대비 Δ" 표시로 **분석 연속성 시각화** → Sessions가 단순 리스트가 아닌 "분석 타임라인"으로 전환
- 날짜 피커로 과거 특정일 점프 가능

#### 3.3.3 Themes

**현재**: 세로 카드 전체 펼침, 카드당 subsection 5-8개.

**개선**:
```
┌──────────────────────────────────────┐
│ [필터] horizon · confidence · 검색   │
│ [정렬] 신뢰도 | 연속일수 | 새로움    │
├──────────────────────────────────────┤
│ 테마명  [확신도] [추적 30일] ▼       │ ← 기본 접힘
│   설명 1줄 + 지표 태그 + 종목 3개    │
│   [펼치기] → 시나리오·매크로 표시    │
├──────────────────────────────────────┤
│ (다음 테마...)                        │
└──────────────────────────────────────┘
```

**기대 효과**:
- 기본 접힘 상태로 **20개 테마를 한 화면에 스캔 가능**
- 필요한 테마만 펼침 → 세로 길이 70% 감소
- 정렬 옵션 추가로 "가장 오래 추적된 테마" 등 접근 가능

#### 3.3.4 Proposals

**현재**: 필터 9종 한 행 + 행 확장 시 5섹션 동시 전개.

**개선**:
```
┌──────────────────────────────────────┐
│ [핵심] Action · 시장 · 확신도         │
│ [▼ 고급] 섹터·발굴유형·투자기간...   │
├──────────────────────────────────────┤
│ 테이블 (기본 20행)                   │
│ ▸ 행 클릭 → 우측 슬라이드 패널      │
│   [개요] [수익률] [분석] [메모]      │ ← 탭 분리
│   Sticky CTA는 패널 하단             │
└──────────────────────────────────────┘
```

**기대 효과**:
- 필터 "핵심/고급" 분리로 인지 부하 감소
- 슬라이드 패널 + 탭 → 테이블이 계속 보이면서 상세도 접근
- 모바일에서는 바텀 시트로 자연 전환

#### 3.3.5 Watchlist

**현재**: 2개 테이블 세로 스택.

**개선**:
```
┌──────────────────────────────────────┐
│ [탭] 관심 종목 (5/30) | 구독 (3/30)  │
├──────────────────────────────────────┤
│ 검색/필터 (해당 탭 내)               │
├──────────────────────────────────────┤
│ 테이블 (현재 탭만 표시)              │
├──────────────────────────────────────┤
│ [우측 고정] + 새 추가 버튼          │
└──────────────────────────────────────┘
```

**기대 효과**:
- 탭 전환으로 한 번에 한 테이블에 집중
- FAB 스타일 추가 버튼으로 한도 체크/업그레이드 유도 가능

### 3.4 우선순위 매트릭스

| # | 항목 | 구분 | 영향도 | 난이도 | 비고 |
|---|------|------|--------|--------|------|
| Q1 | 사이드바 Notifications 제거 | Quick Win | 3 | 1 | base.html 5줄 삭제 |
| Q2 | 사이드바 Pricing 제거 (비로그인은 헤더 버튼 유지) | Quick Win | 2 | 1 | base.html 수정 |
| Q3 | 관리자 푸터 제거, 드롭다운에 `auth_enabled=false` 블록 추가 | Quick Win | 2 | 1 | base.html 수정 |
| Q4 | Empty State에 CTA 추가 (Sessions/Watchlist/Themes) | Quick Win | 3 | 2 | 각 템플릿 수정 |
| Q5 | Dashboard 블록 순서 재정렬 (TIER 1→2→3) | Quick Win | 5 | 2 | dashboard.html include 순서 |
| M1 | Dashboard Deep Dive 블록 탭화 | Medium | 4 | 3 | 신규 탭 partial |
| M2 | Themes 카드 기본 접힘 + 펼치기 | Medium | 4 | 3 | 토글 JS + CSS |
| M3 | Proposals 필터 "핵심/고급" 분리 | Medium | 3 | 2 | 폼 재구성 |
| M4 | Watchlist 탭 레이아웃 | Medium | 3 | 3 | 탭 컴포넌트 신규 |
| M5 | Sessions 타임라인 뷰 (Δ 표시) | Medium | 4 | 4 | 세션 diff 계산 로직 필요 |
| S1 | 사이드바 재구성 (5개 1차 메뉴, 아이콘 도입) | Structural | 5 | 5 | IA 전면 변경, 회귀 테스트 필요 |
| S2 | Proposals 우측 슬라이드 패널 + 탭 상세 | Structural | 5 | 5 | JS 상호작용 대수술 |
| S3 | 전역 Ctrl+K 검색 팔레트 | Structural | 4 | 5 | 신규 컴포넌트 |
| S4 | 카드·배지·테이블 디자인 토큰 통일 (시각 리듬 정비) | Structural | 3 | 4 | style.css 리팩토링 |

점수: 1=낮음 ~ 5=높음.

---

## 4. 실행 로드맵

### PR-1 · Quick Win 번들 (Q1~Q5)

- **변경 파일**:
  - [api/templates/base.html](../api/templates/base.html) — 사이드바 Notifications/Pricing/관리자 푸터 삭제, 드롭다운에 auth=false 블록 추가
  - [api/templates/dashboard.html](../api/templates/dashboard.html) — include 순서 조정 (`_hero_row1` → `_hero_row2` → `_top_picks` → `_signals_today` → 나머지)
  - [api/templates/sessions.html](../api/templates/sessions.html), [watchlist.html](../api/templates/watchlist.html), [themes.html](../api/templates/themes.html) — empty state에 CTA 추가
- **리스크**:
  - Notifications/Pricing 링크를 북마크한 사용자의 404 우려 (URL은 유지되므로 실제로는 없음, 단 UI 위치만 변경)
  - Dashboard 순서 변경으로 기존 사용자 혼란 가능 — 릴리즈 노트 고지 필요
- **검증**:
  - auth_enabled=true/false 각각에서 모든 메뉴 경로 수동 탐색
  - 5개 테스트 계정(Anonymous/Free/Pro/Premium/Admin)으로 드롭다운 노출 확인
  - Lighthouse 접근성 점수 (사이드바 축소 후) 비교

### PR-2 · Dashboard 탭화 (M1)

- **변경 파일**:
  - `dashboard.html` — Deep Dive 영역을 `<div role="tablist">`로 감싸기
  - 신규 partial: `dashboard/_deep_dive_tabs.html`
  - `style.css` — 탭 컴포넌트 스타일
- **리스크**: 탭 전환 JS 미지원 환경 대응 (noscript fallback 필요)
- **검증**: 섹터 히트맵/채권곡선/뉴스/테마 탭 각각에서 데이터 렌더 확인

### PR-3 · Themes/Watchlist 인터랙션 (M2, M4)

- **변경 파일**:
  - `themes.html` — 카드 내부에 `<details>` 또는 커스텀 토글
  - `watchlist.html` — 탭 레이아웃
  - 관련 매크로 `templates/_macros/theme_card.html` (신규 분리)
- **리스크**: SEO 영향 (접힌 콘텐츠) — 단, 인증 페이지이므로 무시 가능
- **검증**: 테마 20개 이상 목업으로 스크롤 성능 측정, Watchlist 탭 간 상태 유지 확인

### PR-4 · Proposals 필터 분리 + 패널 (M3, S2)

- **변경 파일**:
  - `proposals.html` — 필터 접기/펼치기 UI, 상세 패널을 `<aside>` + 탭으로 재구성
  - `_macros/proposal_card_full.html` — 섹션별 분리
  - `style.css` — 슬라이드 패널 스타일, 모바일 바텀 시트
- **리스크**: 기존 DOM 구조에 의존하는 JS(`onclick` 행 확장)와의 충돌 — 전수 검토 필요
- **검증**: Playwright E2E로 행 클릭 → 패널 오픈 → 각 탭 데이터 확인

### PR-5 · 사이드바 재구성 (S1) + 디자인 토큰 정비 (S4)

- **변경 파일**:
  - `base.html` — 사이드바 전체 재작성 (아이콘, 그룹핑, 2차 expand)
  - `style.css` — 카드/배지/테이블 규격 통일 (primitive + variant 패턴)
  - `_macros/` 내 카드·배지 매크로 통일
- **리스크**:
  - **전역 영향** — 모든 페이지에서 회귀 테스트 필요
  - 아이콘 세트 선정(lucide vs heroicons) 후 약 15개 SVG 추가
- **검증**:
  - 32개 페이지 수동 시각 회귀 (Percy 또는 스크린샷 비교)
  - 다크 테마 WCAG AA 대비비 재검증

### 권장 순서

1. PR-1 (Quick Win): 1주 내 배포 — 즉시 중복 제거 체감
2. PR-2: 1주 — Dashboard UX 개선 효과 측정
3. PR-3: 1~2주 — 탐색 페이지 완성도
4. PR-4: 2주 — 가장 무거운 인터랙션 페이지 정리
5. PR-5: 2~3주 — 최종 리뉴얼

**총 6~9주** 예상. PR-1·2는 PR-3·4·5와 병렬 진행 가능.

---

## 부록 A · 참고 파일 경로 목록

### 템플릿
- [api/templates/base.html](../api/templates/base.html) — 사이드바·헤더·드롭다운·글로벌 JS
- [api/templates/dashboard.html](../api/templates/dashboard.html) — 대시보드 컴포지션
- [api/templates/dashboard/](../api/templates/dashboard/) — Dashboard partials (`_hero_row1`, `_hero_row2`, `_market_summary`, `_yield_curve`, `_themes_list`, `_top_picks`, `_signals_today`, `_sector_heatmap`, `_news_by_category`)
- [api/templates/sessions.html](../api/templates/sessions.html)
- [api/templates/session_detail.html](../api/templates/session_detail.html)
- [api/templates/themes.html](../api/templates/themes.html)
- [api/templates/theme_history.html](../api/templates/theme_history.html)
- [api/templates/proposals.html](../api/templates/proposals.html)
- [api/templates/ticker_history.html](../api/templates/ticker_history.html)
- [api/templates/watchlist.html](../api/templates/watchlist.html)
- [api/templates/notifications.html](../api/templates/notifications.html)
- [api/templates/chat_list.html](../api/templates/chat_list.html), [chat_room.html](../api/templates/chat_room.html)
- [api/templates/education.html](../api/templates/education.html), [education_topic.html](../api/templates/education_topic.html), [education_chat_list.html](../api/templates/education_chat_list.html), [education_chat_room.html](../api/templates/education_chat_room.html)
- [api/templates/inquiry_list.html](../api/templates/inquiry_list.html), [inquiry_detail.html](../api/templates/inquiry_detail.html), [inquiry_new.html](../api/templates/inquiry_new.html)
- [api/templates/pricing.html](../api/templates/pricing.html), [landing.html](../api/templates/landing.html)
- [api/templates/partials/](../api/templates/partials/) — 공통 배너·모달·탭바
- [api/templates/_macros.html](../api/templates/_macros.html) + [_macros/](../api/templates/_macros/)

### 라우트
- [api/routes/pages.py](../api/routes/pages.py) — 페이지 라우트 진입점
- [api/routes/sessions.py](../api/routes/sessions.py), [themes.py](../api/routes/themes.py), [proposals.py](../api/routes/proposals.py)
- [api/routes/watchlist.py](../api/routes/watchlist.py), [chat.py](../api/routes/chat.py), [education.py](../api/routes/education.py), [inquiry.py](../api/routes/inquiry.py)
- [api/routes/admin.py](../api/routes/admin.py), [user_admin.py](../api/routes/user_admin.py), [auth.py](../api/routes/auth.py)

### 스타일 및 설정
- [api/static/css/style.css](../api/static/css/style.css) — 메인 CSS (자동 생성, L1-30 토큰)
- [api/static/css/src/](../api/static/css/src/) — CSS 소스 (별도 빌드)
- [shared/tier_limits.py](../shared/tier_limits.py) — 티어별 기능 제한

---

## 부록 B · 본 제안서에서 다루지 않은 것

- 서버사이드 변경 없음: 라우트 URL·API 스펙·DB 스키마 유지
- React/Vue 전환 제안 없음
- 다크 테마 → 라이트 테마 전환 제안 없음
- 본 문서는 "설계 제안"까지. 실제 구현은 별도 PR로 검토 후 진행.

---

**끝.**
