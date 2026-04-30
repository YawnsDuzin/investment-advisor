# Screener UX 재설계 제안

- 작성: 2026-04-30 KST
- 상태: 제안 (구현 전 — 사용자 검토 대기)
- 범위: `/pages/screener` 페이지의 조회 조건 입력·결과 가독성 개선
- 트리거: "조작도 불편하고 보기에도 가독성이 안좋다"는 사용자 피드백 (2026-04-30)

---

## 1. 현재 구조 (As-Is)

### 1.1 화면 구조

```
┌──────────────────────────────────────────────────────────────────────┐
│ Header: 프리셋 저장 / 내 프리셋                                        │
├──────────────────────────────────────────────────────────────────────┤
│ ⚡ 빠른 시작 (10개 시드 카드 — 거장 5 + 자동 5)   [접힘 토글]          │
├────────────────────┬─────────────────────────────────────────────────┤
│ 좌측 사이드패널     │ 활성 필터 chips bar (편집 불가, x 만)             │
│ (300px)            │ ────────────────────────                          │
│ 7개 group <details>│ 결과 테이블                                        │
│ ─ 검색             │   View 5탭 (Overview/Performance/Tech/Fund/외국인) │
│ ─ 시장·섹터        │   ⭐워치 토글 + ⋮ 행 액션                          │
│ ─ 시총·유동성      │                                                    │
│ ─ 수익률 (정렬도)  │                                                    │
│ ─ 변동성·기술      │                                                    │
│ ─ 펀더멘털         │                                                    │
│ ─ 외국인 수급      │                                                    │
│ [실행/초기화/프리셋]│                                                    │
└────────────────────┴─────────────────────────────────────────────────┘
```

### 1.2 입력 필드 카운트

| 그룹 | 필드 수 | 비고 |
|---|---|---|
| 검색 | 1 | 텍스트 |
| 시장·섹터 | 2 | 4개 체크박스 + multi-select(28개) |
| 시총·유동성 | 5 | range 1쌍 + 버킷 체크 3 + 거래대금 KRX/US 분리 |
| 수익률 | **11** | 5기간 × min/max + 정렬 select(16옵션) |
| 변동성·기술 | 5 | 단일 입력 5개 |
| 펀더멘털 | 5 | range 2쌍 + 단일 + 체크박스 |
| 외국인 수급 | 5 | 단일 3 + radio 윈도우 2 |
| **합계** | **34** | |

→ 모든 옵션을 "보고만" 있어도 4-5번 그룹을 펼쳐야 한다.

---

## 2. 사용자 관점의 Pain Points

### 2.1 입력 흐름 (조작 불편)

| # | 증상 | 근본 원인 |
|---|---|---|
| P1 | 조건 하나 바꾸려고 좌측 패널 열고 → 그룹 펼치고 → 입력 → 실행 (4스텝) | chips 가 read-only |
| P2 | "PER 15 이하" 같은 단순 조건도 숫자 입력 필요. 빠른 선택지 없음 | preset bin 미지원 |
| P3 | 5기간 × min/max = 10개 입력 노출. 대부분 1-2개만 씀 | 사전 분기 없이 모두 시각 노출 |
| P4 | 정렬 dropdown 이 "수익률" 그룹 안에 묻혀 있음 | grouping 오류 (정렬은 결과 영역 속성) |
| P5 | 시총 단위가 KRX(억원) / US(천달러) 분리 → 시장 모르면 둘 다 입력 | 단일 단위 + 시장별 자동 환산 부재 |
| P6 | range 입력은 항상 min/max 두 칸 → 분포감 없음 | slider/histogram 미지원 |
| P7 | 외국인 수급 그룹은 KRX 전용인데 시장에 NASDAQ/NYSE만 골라도 그룹이 활성화 가능 | 의존성 비활성화 미구현 |
| P8 | ℹ tooltip 은 hover only → 모바일·터치 환경에서 안 보임 | click-to-toggle 미구현 |
| P9 | "실행" 누르기 전엔 매칭 종목 수 모름 | 라이브 카운트 없음 |
| P10 | 빠른 시작 카드 클릭 후 사용자가 1개 조건을 바꾸면 어떤 카드 기준으로 출발했는지 추적 불가 | 출발 카드 표시 부재 |

### 2.2 결과 가독성 (보기에도 안 좋다)

| # | 증상 | 근본 원인 |
|---|---|---|
| R1 | 결과 view 5탭 (Overview/Performance/Technical/Fundamental/외국인) — 한 번에 한 탭만 → 수익률 + PER 동시 비교 불가 | 컬럼 셋 고정 |
| R2 | 컬럼 폭 / 정렬 화살표 기준 시각적 약함 | 헤더 styling 미흡 |
| R3 | 행 클릭 → 상세 펼침 없음. ⋮ 드롭다운만 | 인라인 드릴다운 부재 |
| R4 | 스파크라인 on/off 토글 1개. 다른 시각 보강(섹터 색칠, MDD 게이지 등) 부재 | 시각 인덱스 한 종류 |
| R5 | 결과 영역 상단 "결과: N건" 만 표시. 평균 PER/PBR/수익률 등 분포 통계 부재 | 집계 패널 부재 |
| R6 | URL 에 spec 영속화 안 됨 → 조건 공유 불가, 새로고침 시 초기화 | URL state 미사용 |

---

## 3. 레퍼런스 조사 (직접 확인)

| 서비스 | 채택할 패턴 | 본 페이지에 적용 가능한 점 |
|---|---|---|
| **Finviz** ([finviz.com/screener.ashx](https://finviz.com/screener.ashx)) | 5탭(Descriptive/Fundamental/Technical/News/ETF) **+ preset bin dropdown** | "PER ≤15 / ≤20 / ≤25" 같은 1-클릭 빈으로 90% 케이스 커버 |
| **Finviz** | 헤더 "**1 / 11037 Total**" 라이브 카운트 | spec 변경 즉시 카운트만 갱신 (전체 row 페치 전 EXPLAIN COUNT) |
| **TradingView** ([tradingview.com/screener](https://tradingview.com/screener)) | "**+ Add Filter**" 모달 — 검색·타입어헤드로 필터 발견 | 우리도 34개 필드 평면 평면화로 검색 가능하게 |
| **TradingView** | column-header 클릭 시 그 컬럼에 대한 inline 필터 popover | 결과 테이블 헤더 = 필터 진입점 (좌측 패널 보조화) |
| **Stockanalysis.com** ([stockanalysis.com/stocks/screener](https://stockanalysis.com/stocks/screener/)) | "**Popular Screens**" + "**Saved Screens**" 분리 dropdown | 우리 빠른 시작 카드 + 사용자 프리셋 분리 ✅ (이미 유사) |
| **Stockanalysis.com** | "**Add View / Edit View**" — 사용자가 컬럼 직접 선택 + 저장 | View 5탭 폐지 → 컬럼 빌더 |
| **Naver Finance** ([finance.naver.com/sise](https://finance.naver.com/sise/sise_market_sum.naver)) | 사전 큐레이션된 리스트(시총상위/거래량/등락률) + 컬럼 ON/OFF 체크 | 빠른 시작 ↔ 컬럼 빌더 결합 |

### 3.1 핵심 통찰

- **Finviz 의 핵심**: 사용자는 "PER<15" 가 아니라 "**저PER**" 을 찾고 있다. 빈(bin) 으로 표현하면 입력 부담 0
- **TradingView 의 핵심**: 필터 = **검색 가능한 commands**. 찾는 데 시간 안 걸림
- **Stockanalysis 의 핵심**: **컬럼 = 1급 객체**. 탭이 아니라 사용자가 조립

---

## 4. 제안 (To-Be)

### 4.1 화면 구조 재설계

```
┌──────────────────────────────────────────────────────────────────────┐
│ Header: 프리셋 저장 · 내 프리셋 · [URL 공유]                            │
├──────────────────────────────────────────────────────────────────────┤
│ ⚡ 빠른 시작 (접힘, 사용자 마지막 선택 카드 강조)                       │
├──────────────────────────────────────────────────────────────────────┤
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ [+ 조건 추가]  PER≤15 ⊗  3M>20% ⊗  KOSPI ⊗  🔍자동실행 (1,234건) │ │ ← chips bar (편집형) + 라이브 카운트
│ └──────────────────────────────────────────────────────────────────┘ │
├────────────────────┬─────────────────────────────────────────────────┤
│ 좌측 패널          │ ┌ 결과 테이블 ───────────────────────────────┐  │
│ (모달/슬라이드)    │ │ [+컬럼] | 표/카드 | 다운로드 | 정렬 |       │  │
│ 4 그룹으로 축소    │ ├──────────────────────────────────────────┤   │
│ ─ 🎯 기본          │ │ 컬럼 헤더 → 클릭 → inline filter popover  │   │
│   (시장·섹터·시총) │ │ 행 클릭 → 인라인 펼침 (mini-cockpit)      │   │
│ ─ 💎 펀더          │ ├──────────────────────────────────────────┤   │
│ ─ 📈 기술·수익률   │ │ 평균 PER 14.3 · 평균 r3m +8.2% (집계 chip)│   │
│ ─ 🌐 수급          │ └──────────────────────────────────────────┘   │
│  (KRX 자동 disable)│                                                 │
└────────────────────┴─────────────────────────────────────────────────┘
```

### 4.2 핵심 변경 사항 (우선순위 순)

#### **[A] 빠른 임팩트 (1주 단위 작업)**

##### A1. **Chips bar = 편집형** ⭐ 가장 임팩트 큼
- chip 클릭 → 작은 popover (slider + 숫자 입력 + ≤/≥ 토글)
- chip 위 hover → "수정" / "삭제" 빠른 버튼
- "+ 조건 추가" 버튼 → typeahead 모달 (TradingView 패턴)
- 좌측 패널 = 보조 (전체 필터 한눈에 보고 싶을 때만)

**구현**: 기존 CHIP_DEFS 확장. `def.editor(currentValue, onChange)` 추가 → popover 컴포넌트 재사용.

##### A2. **Preset bin 빠른 선택** (Finviz 패턴)
각 입력 우측에 칩 형태 빠른 선택:
- PER: `[≤15]` `[≤20]` `[≤25]`
- 3M 수익률: `[>0%]` `[>10%]` `[>20%]`
- 변동성: `[보수 2.5]` `[균형 3.0]` `[적극 4.0]`
- 시총: `[Large]` `[Mid]` `[Small]` (이미 있음 — 다른 필드도 패턴 통일)

**구현**: input 우측 `<button>` 그룹. 클릭 = `setValue + 자동 실행`.

##### A3. **라이브 카운트 + 자동 실행 강화**
- 현재: `chip 변경 시 자동 실행` 토글 ✅ 이미 있음
- 추가: 입력 디바운스 후 (300ms) **항상** count 만 페치 (행 데이터 X) → "**1,234 / 4,500**" 사이드패널 footer 에 표시
- 매칭 0 또는 너무 많으면 (>500) 색상 변경

**구현**: 신규 `/api/screener/count` 엔드포인트 — `SELECT COUNT(*) ...` 경량 쿼리.

##### A4. **그룹 7개 → 4개 통합**
- 🎯 기본 = 검색 + 시장 + 섹터 + 시총·유동성
- 💎 펀더 = PER + PBR + 배당 + EPS
- 📈 기술·수익률 = 5기간 + 변동성 + MA + drawdown + **정렬 (이전 위치 수정)**
- 🌐 수급 = 외국인 (KRX 미선택 시 disable + 안내)

**구현**: 기존 `data-group` 재배치, GROUP_TO_CATS / DEFAULT_OPEN 키 갱신.

##### A5. **정렬 select → 결과 영역 헤더로 이동**
"수익률" 그룹 안에 박혀있던 `f-sort` 16옵션 → 결과 카드 헤더 우측 상단으로. 또는 컬럼 헤더 클릭 정렬로 일원화.

##### A6. **외국인 그룹 의존성 비활성화**
시장 체크박스에 KOSPI/KOSDAQ 둘 다 미선택 → 외국인 그룹 헤더에 "🔒 KRX 시장 선택 시 활성" + body disabled.

##### A7. **URL state 영속화**
spec → URL query (json 압축 or 평면 query). 새로고침 / 공유 / back 동작.

**구현**: `Screener.run()` 끝에 `history.replaceState(null, '', '?spec=' + encodeSpec(spec))`. `DOMContentLoaded` 시 URL 파싱 → `SpecBuilder.toDOM()`.

---

#### **[B] 중간 임팩트 (2-3주 작업)**

##### B1. **컬럼 빌더 (View 5탭 폐지)**
- 결과 헤더에 `[+ 컬럼]` 버튼 → 추가 가능한 컬럼 30개 type-ahead 검색
- 사용자 컬럼 셋은 localStorage 저장
- 추천 셋 (Overview/Performance/...) 은 **빠른 시작 카드** 처럼 1-클릭 적용 (현재 view 탭의 본질을 보존)

**구현**: `ColumnDefs` 평면화, view 5개 → `RECOMMENDED_VIEWS` 데이터로. 현재 `currentView` 변수 → `currentColumns: string[]`.

##### B2. **인라인 행 펼침 (mini-cockpit)**
행 클릭 → 그 자리에서 펼쳐지며 미니 차트 + 추천 이력 요약 + 워치/구독 버튼. "한 번 더 클릭" 비용 제거.

**구현**: row click → `renderRowDetail(ticker)` → fetch `/overview` + `/proposals`. 이미 cockpit JS 가 보유 중 → 부분 재사용.

##### B3. **결과 위 집계 chip**
"평균 PER 14.3 · 평균 r3m +8.2% · 섹터 분포: Tech 32% / Health 18% / ..."
한 줄 chip strip. 결과 분포감을 즉시 제공.

##### B4. **range slider + histogram**
PER / 시총 / 변동성 등 분포가 의미있는 필드는 slider + 미니 히스토그램 (전체 유니버스 분포). 사용자가 "흠, 평균이 18 이구나, 15 이하면 상위 30%네" 직관 형성.

**구현**: 신규 `/api/screener/distribution?metric=per` (binned). slider lib: noUiSlider (외부) 또는 custom.

##### B5. **모바일 — 사이드패널 닫고 즉시 적용**
필터 변경 → 패널 닫음 → 즉시 결과 갱신 (현재는 사용자가 명시적 "실행" 눌러야).

##### B6. **빠른 시작 카드 — 출발점 표시**
사용자가 카드 클릭 후 한 조건이라도 바꾸면 chips bar 위에 "🎯 출발: Buffett 가치주 (수정됨) [원본으로]" 띠.

##### B7. **ℹ tooltip → click-to-toggle**
mobile 친화. 탭 외부 클릭 시 닫힘.

##### B8. **시총 단위 통합**
"억원" / "$M" 분리 → 시장 선택 따라 자동 (KRX 시장 1개라도 있으면 KRW, 없으면 USD). 단일 input + 단위 자동 라벨.

---

#### **[C] 고임팩트·고비용 (별도 sprint)**

##### C1. **NL → spec ("자연어 스크리너")**
"**3개월 +20% 이상이면서 PER 20 미만 KOSPI 종목**" → spec 변환.
이미 sprint1 design 에 NL→SQL 가 있음 — 이를 screener spec 으로 좁히면 충분.

##### C2. **Saved Views → 비교 모드**
저장된 프리셋 2개 동시 실행 → 좌/우 split 결과 비교.

##### C3. **알림 — "필터 결과 변경 시"**
"이 spec 으로 어제는 12개 / 오늘 신규 진입 3개" 알림. user_subscriptions 테이블 확장.

---

## 5. 즉시 착수 추천 — Phase A 7개

작업량 / 임팩트 비율로 **Phase A 7개를 1차 sprint** 로 묶는 것을 추천:

| Task | 작업량 | 임팩트 | 의존성 |
|---|---|---|---|
| A1. chips bar 편집형 | M (1.5d) | ★★★★★ | 없음 |
| A2. preset bin 빠른 선택 | S (1d)   | ★★★★  | 없음 |
| A3. 라이브 카운트 | S (1d)   | ★★★★  | 신규 endpoint 1개 |
| A4. 그룹 7→4 통합 | XS (0.5d)| ★★★   | 없음 |
| A5. 정렬을 결과 헤더로 | XS (0.3d)| ★★    | 없음 |
| A6. 외국인 그룹 disable | XS (0.3d)| ★★    | 없음 |
| A7. URL state 영속화 | S (1d)   | ★★★★  | 없음 |

**합계 ≈ 5-6 일 작업**. 회귀 테스트는 기존 `tests/test_screener_*.py` 36개 PASS 유지가 기준.

### 5.1 우선 1개 추천 — **A1 chips bar 편집형**

이거 하나만 적용해도 "조작 불편" 의 P1·P2 의 70% 가 해소된다. 좌측 패널 거의 안 열어도 됨.

---

## 6. 비범위 (Out of Scope)

- 결과 테이블 자체의 가상 스크롤(virtualization) — 현재 limit 200 으로 운용 중, 필요 시점에 분리 spec
- 차트 모드 (테이블 ↔ 산점도 토글) — Koyfin 패턴, B 단계에서 backlog
- 결과 export (CSV / xlsx) — 운영 사용량 미관측, B 단계 backlog
- 다국어(en) 라벨 — 현재 페이지 KR-only

---

## 7. 결정 필요 항목

1. **Phase A 전체 vs 일부**: 7개 모두 진행 / A1+A2+A3 만 우선 등
2. **컬럼 빌더 (B1)**: View 5탭 폐지에 대한 사용자 동의 — 익숙한 사용자는 5탭이 빠름
3. **NL 입력 (C1)**: sprint1 NL→SQL 와 통합할지, screener 단독으로 갈지
4. **빠른 시작 카드 위치**: chips bar 위 (현재) 유지 / 좌측 패널 상단 / 결과 빈 상태일 때만 노출

---

## 8. 참고

### 8.1 직접 확인 — 레퍼런스 URL

- [Finviz Screener](https://finviz.com/screener.ashx) — preset bin 의 가성비
- [TradingView Screener](https://www.tradingview.com/screener/) — Add Filter typeahead
- [Stockanalysis Screener](https://stockanalysis.com/stocks/screener/) — 컬럼 빌더
- [Koyfin Screener](https://www.koyfin.com/screener/) — 인라인 셀 편집
- [Naver Finance — 시총상위](https://finance.naver.com/sise/sise_market_sum.naver) — KR 큐레이션 리스트

### 8.2 본 제안의 설계 원칙

1. **chips 가 1차 입력. 좌측 패널은 2차 (보조).**
2. **숫자 입력은 최후 수단. 빈(bin) 이 90% 케이스 커버.**
3. **결과 = 1급 객체. 컬럼·정렬은 결과 영역에 귀속.**
4. **URL 이 spec 의 single source of truth — 공유 가능해야 한다.**
5. **외부 의존성 추가 없이 (lightweight-charts 외) JavaScript 만으로 가능한 범위.**
