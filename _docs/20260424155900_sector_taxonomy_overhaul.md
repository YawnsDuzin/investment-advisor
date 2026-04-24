# Sector 분류 체계 개편 (P0-A / P0-B / P1 / P2)

**작성일**: 2026-04-24
**배경**: 실측 데이터(`stock_universe` 2,770종목)에서 발견된 분류 왜곡 3건을 체계적으로 교정.

---

## 1. 배경 — 실측이 드러낸 문제

### 1.1 한국 반도체가 엉뚱한 버킷에 분산 (Structural Bug)

```
semiconductors:  21 (0.6%)  ← 수상하게 적음 — US NDX100만 잡힌 숫자
```

SK하이닉스(`000660`)·한미반도체(`042700`)·원익IPS(`240810`)·리노공업(`058470`) 등
한국 반도체 주력군 100~150종목이 `it_hardware`·`industrials`·`materials` 버킷에 흩어짐.

**원인**: `_INDUSTRY_OVERRIDES`의 `"semiconductor"`/`"반도체"` 키워드는
`stock_universe.industry` 컬럼에 의존하는데 —

```
KOSPI  949 / industry=0 (0.0%)    ← 한국 종목 industry 컬럼이 구조적으로 비어있음
KOSDAQ 1821 / industry=0 (0.0%)
NASDAQ  174 / industry=174 (100%)
NYSE    342 / industry=342 (100%)
```

pykrx는 KRX 대분류(`전기·전자`, `화학`, `기계·장비` 등)만 제공하여 세분 industry가 NULL.
결과적으로 한국 종목 전체에서 `_INDUSTRY_OVERRIDES` 영문 키워드가 **한 번도 발동한 적 없음** (dead code).

### 1.2 지주사가 `finance` 버킷을 오염

```
finance 337
├── 기타금융 98 (29%)  ← 지주사·잡화
│   ├── "홀딩스/지주" 명시 47
│   └── 암묵 지주 (이름 안 붙음) 51   ← SK, SK스퀘어, SK디스커버리
└── 진짜 금융 본업 239
```

KRX 업종 `"기타금융"` → `finance` 매핑이 `max_per_sector=2` 다양성 제약을 잠식.
은행 4개 + 보험 2개 + 증권 2개 중 2개만 뽑히는데 지주사가 자리 차지.

### 1.3 해상도 불균형 (IT만 3분할, 나머지 단일 버킷)

| 기존 sector_norm | 실측 종목 수 | 진단 |
|---|---|---|
| industrials | 553 (16.8%) | 기계·건설·운수·항공 혼재 |
| consumer_discretionary | 522 (15.9%) | 자동차·유통·미디어·레저 혼재 |
| materials | 476 (14.5%) | 화학 52% + 철강 29% + 종이 등 |
| it_hardware | 423 (12.9%) | 반도체가 여기 숨어있음 |
| healthcare | 355 (10.8%) | 바이오·제약·의료기기 혼재 |
| finance | 337 (10.3%) | 은행·보험·증권·지주 혼재 |
| semiconductors | 21 (0.6%) | **버그: 한국 반도체 미분류** |

---

## 2. 개편 내용

### 2.1 sector_norm 키 확장: 14 → 24 (+10)

| 기존 버킷 | 유지/deprecate | 신규 세분화 |
|---|---|---|
| semiconductors | 유지 | - |
| it_hardware | 유지 | - |
| it_software | 유지 | - |
| communication | 유지 | - |
| **finance** | deprecated (잔여 fallback) | **banks**, **insurance**, **capital_markets** |
| **healthcare** | deprecated (잔여 fallback) | **biotech**, **pharma_medtech** |
| consumer_discretionary | 유지 | - |
| consumer_staples | 유지 | - |
| energy | 유지 | - |
| **materials** | deprecated (잔여 fallback) | **chemicals**, **steel_metals**, **nonmetallic**, **paper_wood** |
| industrials | 유지 (차후 추가 분해) | - |
| utilities | 유지 | - |
| real_estate | 유지 | - |
| other | 유지 | **holding_co**(지주사 전용) |

총 24개 sector_norm — GICS Industry Group(25)과 거의 동일한 해상도.

### 2.2 `normalize_sector()` 시그니처 확장

```python
normalize_sector(
    ticker=...,          # NEW — KR 화이트리스트
    asset_name=...,      # NEW — KR 종목명 키워드
    market=...,          # NEW — KR 판별
    sector_krx=...,
    sector_gics=...,
    industry=...,
)
```

**우선순위 (높음→낮음)**:
1. KR 티커 화이트리스트 (`_KR_TICKER_OVERRIDES`) — 130여 종목 명시
2. KR 종목명 키워드 (`_KR_NAME_KEYWORDS`) — `"반도체"`/`"바이오"`/`"은행"`/`"홀딩스"` 등
3. industry 키워드 (`_INDUSTRY_OVERRIDES`) — 주로 US 종목에 발동
4. KRX 대분류 (`_KRX_TO_NORM`)
5. GICS sector (`_GICS_TO_NORM`)
6. `SECTOR_OTHER` (+ 경고 로그)

### 2.3 KRX 대분류 매핑 재지정

| KRX 업종 | 구 sector_norm | 신 sector_norm |
|---|---|---|
| 화학 | materials | **chemicals** |
| 철강금속 / 금속 / 철강 | materials | **steel_metals** |
| 비금속광물 / 비금속 | materials | **nonmetallic** |
| 종이목재 | materials | **paper_wood** |
| 금융업 / 은행 / 금융 | finance | **banks** |
| 증권 | finance | **capital_markets** |
| 보험 | finance | **insurance** |
| 기타금융 | finance | **holding_co** |
| 의약품 / 제약 / 의료정밀 | healthcare | **pharma_medtech** |

### 2.4 주요 코드 변경

| 파일 | 변경 |
|---|---|
| `shared/sector_mapping.py` | 상수 10개 추가 + `_KR_TICKER_OVERRIDES`(130) + `_KR_NAME_KEYWORDS`(18) + 시그니처 확장 |
| `analyzer/universe_sync.py` | `sync_meta_krx`/`sync_meta_us` 호출부에 ticker/asset_name/market 전달 + **신규 `backfill_industry_kr()` 함수** + CLI `--mode industry_kr` |
| `analyzer/validator.py` | 주석 보강 (AI 자유텍스트 비교는 ticker override 미사용 — tautology 방지) |
| `tools/renormalize_sectors.py` | **신규** — 기존 DB 레코드 일괄 재정규화 (dry-run 기본) |

---

## 3. 실행 가이드

> ⚠ 순서 엄수. P0-B 선행 없이 P1/P2 재정규화를 돌리면 한국 종목의 세분화(`biotech` vs `pharma_medtech`·`chemicals` vs `steel_metals`)가 GICS industry 없이 KRX 대분류+ticker+name 3가지 신호로만 수행된다. 커버리지는 괜찮지만 P0-B 실행 후가 더 완성도 높음.

### Step 1. (옵션) dry-run으로 변경 규모 확인

```bash
python -m tools.renormalize_sectors
```

출력 예시:
```
── 분포 변화 (before → after) ──
sector_norm               before    after     diff
-------------------------------------------------------
semiconductors               21      ~130    +109  *
banks                         0      ~220    +220  *
insurance                     0      ~30     +30   *
capital_markets               0      ~25     +25   *
finance                     337      ~62    -275   *
biotech                       0      ~90     +90   *
pharma_medtech                0      ~260    +260  *
healthcare                  355       ~5    -350   *
chemicals                     0      ~260    +260  *
steel_metals                  0      ~140    +140  *
nonmetallic                   0      ~40     +40   *
paper_wood                    0      ~30     +30   *
materials                   476       ~6    -470   *
holding_co                    0      ~100    +100  *
it_hardware                 423     ~390    -33   *
industrials                 553     ~535    -18   *
```

(실제 수치는 DB 상태에 따라 다름 — 위는 추정치)

### Step 2. P0-A 재정규화 (KR + US 전체, 즉시 적용)

```bash
python -m tools.renormalize_sectors --apply
```

- 입력: 기존 DB의 `ticker`/`market`/`asset_name`/`sector_krx`/`sector_gics`/`industry`
- 출력: `sector_norm` 재계산 UPDATE
- 예상 소요: ~30초 (2,770종목, batch=500, UPDATE 1건/row)

### Step 3. P0-B 한국 종목 industry 백필 (선택이지만 권장)

```bash
# 1회성 백필 — 전체 한국 종목 (약 2,770건 × yfinance 호출)
python -m analyzer.universe_sync --mode industry_kr

# 테스트용: 100건만
python -m analyzer.universe_sync --mode industry_kr --limit 100

# 재수집 (industry 있는 것도 덮어씀)
python -m analyzer.universe_sync --mode industry_kr --all
```

- **예상 소요**: 15~25분 (`max_workers=5`, `sleep_ms=50`)
- **예상 커버리지**: 대형주 ~80%, 중소형 ~50%, 신규 상장 ~20%
- **rate limit 주의**: yfinance는 명시적 한도는 없지만 과도한 호출 시 429. 실패는 `failed`로 집계 후 계속 진행.

백필 완료 후:
```bash
# industry 컬럼에 GICS Sub-Industry가 채워지면 _INDUSTRY_OVERRIDES 영문 키워드 정상 작동
python -m tools.renormalize_sectors --apply --market KRX
```

### Step 4. 검증 쿼리

```sql
-- 1) 분포 확인 — 24개 버킷 모두 값 있는지
SELECT sector_norm, COUNT(*) AS n
FROM stock_universe
WHERE listed = TRUE
GROUP BY sector_norm
ORDER BY n DESC;

-- 기대값:
--   semiconductors 100~150 (이전 21에서 +5~7배)
--   banks 200~250, insurance 25~35, capital_markets 20~30
--   biotech 70~100, pharma_medtech 250~300
--   chemicals 250~280, steel_metals 130~150, nonmetallic 35~45, paper_wood 25~35
--   finance/healthcare/materials 거의 0 (deprecated fallback)
--   holding_co 80~110

-- 2) 지주사 오염 해소 확인 — finance 버킷이 240대로 줄었는지
SELECT sector_krx, COUNT(*)
FROM stock_universe
WHERE sector_norm = 'finance'
GROUP BY sector_krx
ORDER BY COUNT(*) DESC;

-- 3) 반도체 복구 확인 — 주요 종목이 semiconductors로 붙었는지
SELECT ticker, asset_name, sector_norm
FROM stock_universe
WHERE asset_name IN ('SK하이닉스', '한미반도체', '원익IPS', '리노공업',
                      'DB하이텍', '동진쎄미켐', '이오테크닉스')
ORDER BY asset_name;

-- 4) 지주사 분리 확인
SELECT ticker, asset_name, sector_norm
FROM stock_universe
WHERE asset_name IN ('SK', 'SK스퀘어', 'SK디스커버리', 'LG', '한화', 'CJ', 'GS', 'HD현대');

-- 5) KR industry 백필 커버리지 (P0-B 실행 후)
SELECT market,
       COUNT(*) AS total,
       COUNT(industry) AS with_industry,
       ROUND(100.0 * COUNT(industry) / COUNT(*), 1) AS coverage_pct
FROM stock_universe
WHERE market IN ('KOSPI', 'KOSDAQ')
GROUP BY market;
```

---

## 4. 롤백

만약 분류 결과가 이상하면:

```sql
-- 문제 종목 개별 확인 — ticker/name/krx/gics/industry 원문 그대로
SELECT ticker, asset_name, market, sector_norm, sector_krx, sector_gics, industry
FROM stock_universe
WHERE ticker IN ('...');
```

재정규화 배치는 **기존 원문(sector_krx/sector_gics/industry)은 건드리지 않고 sector_norm만 재계산**하므로 되돌리려면 매핑 규칙을 고친 뒤 재실행하면 된다.

전체 백업이 필요하면 실행 전:
```sql
CREATE TABLE stock_universe_backup_20260424 AS
SELECT ticker, market, sector_norm FROM stock_universe;
```

복원:
```sql
UPDATE stock_universe u
SET sector_norm = b.sector_norm
FROM stock_universe_backup_20260424 b
WHERE u.ticker = b.ticker AND u.market = b.market;
```

---

## 5. 하위 영향 / 호환성

### 영향받는 코드

| 파일 | 영향 |
|---|---|
| `analyzer/recommender.py` (다양성 제약) | `max_per_sector` 24개 버킷 기준 — 자동으로 세분화된 제약이 적용됨 |
| `analyzer/screener.py` (스크리너 스펙 `sector_norm` 필터) | AI가 `"finance"` 스펙을 주면 종목 0건 (fallback) — AI 프롬프트 업데이트 필요 (§6) |
| `api/routes/sectors.py` (히트맵) | 새 24개 버킷으로 세분화된 히트맵 자동 표시 |
| `analyzer/validator.py` | AI 제시값 vs DB 비교는 정상 — AI가 새 sector 키를 모르면 `other`로 떨어져 mismatch 방지 |

### 프롬프트 업데이트 권장

[analyzer/prompts.py](../analyzer/prompts.py)의 Stage 1/1A/1B 스펙 예시에서 sector_norm 허용값을 명시하는 부분이 있다면 갱신:

```
허용값: semiconductors, it_hardware, it_software, communication,
        banks, insurance, capital_markets,
        biotech, pharma_medtech,
        consumer_discretionary, consumer_staples,
        energy, chemicals, steel_metals, nonmetallic, paper_wood,
        industrials, utilities, real_estate,
        holding_co, other
```

AI가 구 키(`finance`/`healthcare`/`materials`)를 출력해도 `_GICS_TO_NORM`에서
영문 기본값으로 매핑되어 호환됨 (fallback — `finance`→`banks`, `materials`→`chemicals`, `healthcare`→`pharma_medtech`).

---

## 6. 후속 과제

### 단기 (이번 스프린트)
- [ ] P0-B 백필 1회 실행 후 KR industry 커버리지 80%+ 확인
- [ ] Top Picks 파이프라인 1회 실제 돌려서 다양성 제약 동작 확인 (banks 2개 vs 지주 2개 섞여 나오는지)
- [ ] `prompts.py` sector_norm 허용값 목록 갱신

### 중기 (다음 스프린트)
- [ ] Industrials 4분할 (machinery/construction/transport_logistics/aerospace_defense)
- [ ] Consumer Discretionary 5분할 (autos/retail/media/hotels/apparel)
- [ ] 화학 세분화 (석유화학·특수화학·배터리소재) — P0-B KR industry 백필 후

### 장기
- [ ] Cyclicality 레이어 (`cyclical/defensive/sensitive`) — regime과 연동
- [ ] Thematic 태그 테이블 (AI/Cybersecurity/Clean Energy)

---

## 7. 변경 파일 목록

```
shared/sector_mapping.py              — 상수·매핑·normalize_sector 확장
analyzer/universe_sync.py             — KR/US 호출부 + backfill_industry_kr + CLI + retry 로직
analyzer/validator.py                 — 주석 보강
tools/renormalize_sectors.py          — 신규 (재정규화 배치 스크립트)
_docs/20260424155900_sector_taxonomy_overhaul.md — 본 문서
```

---

## 8. 실행 결과 (2026-04-24 KST)

### 8.1 적용 타임라인
| 단계 | 작업 | 변경 row | 소요 |
|---|---|---|---|
| P0-A 1차 | 신규 10개 sector_norm 분배 | 1,274 UPDATE | 0.2s |
| 보강 1차 | 티커 버그 4건 + 카카오·맥쿼리 + biotech 이름만 바이오 9건 (총 15종 매핑 교정) | 18 UPDATE | 0.1s |
| 보강 2차 | holding_co 오분류 10건 + pharma_medtech 반도체/광학/방위 5건 | (3차에서 반영) | - |
| P0-B | yfinance industry 백필 (KOSPI+KOSDAQ) | 2,146/2,644 수집 (81%) | 21분 48초 |
| P0-A 3차 | KRX 재정규화 | 343 UPDATE | 0.1s |

### 8.2 최종 분포 (3,286종목, 21개 활성 버킷)

```
-- IT 4버킷 --
semiconductors    21 →  166  (+145)   ← 반도체 복구 (7.9배)
it_hardware      423 →  320  (-103)
it_software      295 →  298  (+3)
communication     43 →   86  (+43)    ← 통신장비 분리

-- Finance 4버킷 --
finance (dep.)   337 →    0  (-337)   ← 전면 deprecated
banks              0 →   56  (+56)    ← 순수 은행/금융지주
insurance          0 →   48  (+48)
capital_markets    0 →   81  (+81)    ← 증권·자산운용·결제(Visa 등)
holding_co (new)   0 →  117  (+117)   ← 지주사 분리

-- Healthcare 2버킷 --
healthcare (dep.) 355 →    0  (-355)
biotech            0 →  190  (+190)   ← 신약·바이오 플랫폼
pharma_medtech     0 →  239  (+239)   ← 제약·의료기기·CMO

-- Materials 4버킷 --
materials (dep.) 476 →    0  (-476)
chemicals          0 →  255  (+255)
steel_metals       0 →  137  (+137)
nonmetallic        0 →   37  (+37)
paper_wood         0 →   28  (+28)

-- 기타 --
other (스팩 격리)   0 →   75  (+75)    ← SPAC 75개 분리
real_estate       60 →   69  (+9)     ← 리츠 + 인프라펀드
energy            22 →   32  (+10)
industrials      553 →  491  (-62)
consumer_disc    522 →  391  (-131)   ← bio/software 등으로 이동
consumer_staples 135 →  127  (-8)
utilities         44 →   43  (-1)
```

### 8.3 커버리지 지표
- **P0-B yfinance industry 백필**: KOSPI **98%** (935/949), KOSDAQ **73%** (1,337/1,821)
- **`other` 버킷 구성**: **100% 스팩** (75/75) — 분류 누락 없음 확인
- **`finance`/`healthcare`/`materials` deprecated**: **0건** 도달 (완전 세분화)
- **SPAC 격리**: `banks` 145→56, 스팩 73개가 `other`로 이동 (다양성 제약 오염 해소)

### 8.4 발견·교정된 구조적 이슈
1. **티커 화이트리스트 4건 오류** (개발 단계 수기 입력 오타)
   - 119830(엘비세미콘 ❌→아이텍) · 217270(넥스틴 ❌→넵튠) · 241790(오픈엣지 ❌→티이엠씨씨엔에스) · 150840(미상장)
   - 실제 티커로 교정: 061970(엘비세미콘) · 348210(넥스틴) · 394280(오픈엣지) · 064290(인텍플러스)

2. **이름만 "바이오"인 non-biotech 9건** (농우바이오·바이오스마트·서울바이오시스 등)
   - 종자/스마트카드/UV LED/해조류/사료/나노소재 개별 티커 오버라이드

3. **`"바이오제약"` 네임 우선순위 버그**
   - 매칭 순서상 "바이오"가 앞서 "바이오제약" 이름 종목이 전부 biotech로 감
   - `("바이오제약", SECTOR_PHARMA_MEDTECH)` 매칭을 "바이오" 앞으로 이동

4. **KRX "기타금융" 대분류의 지주·신탁·핀테크 혼재** (10건)
   - 카카오페이(핀테크) · 맥쿼리인프라/KB발해인프라(인프라펀드) · 맵스리얼티/한국토지신탁/한국자산신탁(부동산신탁) · 스틱인베스트먼트(PE) · 에이플러스에셋(보험대리) · 샘표/코스맥스비티아이(식품·코스메틱 지주) 개별 매핑

5. **KRX "의료·정밀기기" 대분류의 반도체/광학/방위 혼재** (5건)
   - 미래산업·디아이(반도체 테스트) · 스마트레이더시스템(레이더) · 세코닉스(광학렌즈) · 빅텍(방위전자) 개별 매핑

6. **P0-B 동시성 이슈 — yfinance HTTP 500 간헐 발생**
   - max_workers 5→3, sleep 50→150ms, max_retries=2 + backoff 추가
   - 커버리지 20% → 81% 개선

### 8.5 남은 이슈 → P1-ext(§9)에서 해결

---

## 9. P1-ext 확장 개편 (2026-04-24 KST 추가 세션)

§8에서 남은 과제였던 **(1) KOSDAQ 미커버 대형주 수작업 태깅 · (2) yfinance 부정확성 교정 · (3) 추가 세분화** 를 일괄 처리.

### 9.1 신규 sector_norm 5개 추가 (21 → 26버킷)

| 신규 키 | 설명 | 최종 종목수 |
|---|---|---|
| `autos` | 자동차·부품·타이어 | **160** |
| `media_entertainment` | 미디어·엔터·방송·광고 | 87 |
| `aerospace_defense` | 항공·방위·위성 | 43 |
| `transport_logistics` | 항공·해운·물류·철도 | 46 |
| `battery_materials` | 2차전지 소재·셀 | 9 |

### 9.2 KRX 대분류 매핑 변경

| KRX 업종 | 기존 | 변경 후 |
|---|---|---|
| 운송장비·부품 | industrials | **autos** (대부분 자동차 부품) |
| 운송 / 운송창고 / 운수창고 | industrials | **transport_logistics** |

### 9.3 yfinance 부정확성 교정 (개별 티커)

yfinance Ticker.info가 간혹 무관한 industry를 반환하는 케이스:

| 티커 | 종목 | yfinance 오류 | 교정 |
|---|---|---|---|
| 012700 | 리드코프 | Oil & Gas Refining & Marketing | consumer_discretionary (대부업) |
| 023410 | 유진기업 | Capital Markets | nonmetallic (레미콘) |
| 002230 | 피에스텍 | Auto Parts | it_hardware (측정기기) |
| 126600 | BGF에코머티리얼즈 | Auto Parts | chemicals |
| 005720 | 넥센 | Auto Parts | chemicals |
| 208860 | 다산디엠씨 | Auto Parts | communication |

### 9.4 KOSDAQ 미커버 대형주 수작업 태깅 (시총 상위)

yfinance에서 industry 못 받은 498건 중 시총 상위 30여 개를 개별 티커 오버라이드:

- **반도체**: 주성엔지니어링·GST·에스앤에스텍·대주전자재료·덕산하이메탈·이엔에프테크놀로지·두산테스나·티에스이
- **통신**: 쏠리드·RFHIC
- **바이오**: 지아이이노베이션·에스티큐브·젬백스·네이처셀·메디포스트·케어젠·펩트론
- **pharma**: 메지온·에스티팜
- **capital_markets**: 미래에셋벤처투자·아주IB투자 (VC)

### 9.5 신규 버킷 대표 종목 (티커 오버라이드)

**autos** (160): 현대차·기아·현대모비스·HL만도·한온시스템·현대위아·한국타이어·금호타이어·넥센타이어·KG모빌리티 + KRX "운송장비부품" 대분류 일괄 흡수

**battery_materials** (9): 에코프로비엠·에코프로·포스코퓨처엠·엘앤에프·나노신소재·LG에너지솔루션·삼성SDI·에코앤드림·탑머티리얼

**aerospace_defense** (43): LIG넥스원·한화에어로스페이스·한국항공우주(KAI)·현대로템·쎄트렉아이·한화시스템·한화오션·빅텍 + GICS "Aerospace/Defense" 자동 매칭

**transport_logistics** (46): 대한항공·HMM·팬오션·CJ대한통운·KSS해운·대한해운 + KRX "운수창고"/"운송" 일괄 흡수 + GICS "Airlines/Shipping/Trucking" 자동

**media_entertainment** (87): 스튜디오드래곤·JYP·SM·YG·하이브·CJ ENM·SBS·콘텐트리중앙 + GICS "Entertainment/Broadcasting/Publishing/Advertising" 자동

### 9.6 최종 분포 (26버킷 + deprecated 3)

```
[IT 4]     semiconductors 174 · it_hardware 299 · it_software 293 · communication 74
[금융 4]   banks 52 · insurance 48 · capital_markets 82 · holding_co 115
[의료 2]   biotech 197 · pharma_medtech 233
[소비 4]   consumer_discretionary 308 · consumer_staples 127 · media_entertainment 87 · real_estate 69
[운송 3]   autos 160 · aerospace_defense 43 · transport_logistics 46
[자원 6]   energy 31 · chemicals 239 · battery_materials 9 · steel_metals 129 · nonmetallic 38 · paper_wood 28
[제조 1]   industrials 287
[유틸 1]   utilities 43
[기타 1]   other 75
[deprecated] finance 0 · healthcare 0 · materials 0
─────────────────────
총 3,286종목 / 26 활성 버킷
```

### 9.7 주요 변화 지표 (P0 시작 → P1-ext 완료)

```
industrials             553 → 287  (-266, -48%)   ← autos/defense/transport 분리
consumer_discretionary  522 → 308  (-214, -41%)   ← media_entertainment 분리
materials               476 →   0  (deprecated, 4버킷 + battery로 분해)
finance                 337 →   0  (deprecated, 4버킷 분해)
healthcare              355 →   0  (deprecated, 2버킷 분해)
semiconductors           21 → 174  (8.3배, 반도체 복구 완료)
```

최대 버킷(consumer_discretionary 308)과 최소 활성 버킷(battery_materials 9)의 격차 34배 — 이전 최대(553)보다 **44% 개선**. 다양성 제약 왜곡 현저히 축소.

### 9.8 남은 이슈 (다음 스프린트)
- **KOSDAQ 미커버 잔여 ~470건**: 대부분 소형주·신규 상장. 시총 <1000억 구간이라 파이프라인 영향 제한적.
- **조선 분류**: HD현대중공업·한화오션 등 조선업은 현재 industrials/aerospace_defense 혼재. 필요 시 `shipbuilding` 분리 검토.
- **건설 분류**: 현대건설·GS건설 등은 현재 industrials. `construction` 분리 가능.
- **chemicals 239 추가 세분화**: 석유화학(LG화학·롯데케미칼) vs 특수화학 분리 — 이번 스프린트에서 보류 (`chemicals`가 원래 두 번째로 컸던 `materials 476`의 분해 결과라 이미 충분히 세분화됨).
- **이녹스(088390) 등 KRX 분류 명백 오류**: 전자소재인데 KRX "운송장비부품" 분류 → autos로 빠짐. 향후 개별 교정 또는 yfinance 커버리지 확대 시 자동 해소.

---

## 10. 업계 표준 참고 — "1기업 1분류" 원칙

개편 설계 시 참조한 타 서비스 분류 체계:

### 10.1 공식 표준
| 체계 | 1 기업 | 복수 분류 지원 | 비고 |
|---|---|---|---|
| **GICS** (MSCI/S&P) | ✅ 강제 1 Sub-Industry | ❌ | "A company is assigned to ONE sub-industry based on principal business activity" 명시. 지수 편입·ETF 구성 기준. |
| **ICB** (FTSE/Russell) | ✅ 강제 1 | ❌ | 유사 |
| **BICS** (Bloomberg) | ✅ Primary 1개 | ⚠️ Secondary 필드 별도 제공 | 매출 기여도 기반 2차 분류 있으나 UI·지수 편입은 Primary만 사용 |
| **Morningstar** | ✅ 1 Sector | ⚠️ Cyclicality 별도 축 (cyclical/defensive/sensitive) | Equity Style Box (value/core/growth × small/mid/large)도 독립 축 |
| **FactSet RBICS** | ✅ 1 Primary | ⚠️ Revenue Exposure 필드로 다중 제공 | 매출 20% 이상 subsector는 exposure 태깅 — 분류 아닌 "비중" 개념 |

### 10.2 소비자 서비스
| 서비스 | 정책 |
|---|---|
| Yahoo Finance | Primary 1 sector + 1 industry (GICS 기반) |
| Koyfin / Simply Wall St | Primary 1 |
| Fidelity / Schwab / IBKR | Primary 1 + Theme ETF는 별도 상품 |

### 10.3 예외적 다중 분류 사례
1. **Thematic ETF / 투자 테마 태그**: AI·Cybersecurity·Clean Energy 등은 sector와 **독립적인 태그 레이어**로 관리. 1기업이 여러 테마에 속함 (예: NVIDIA = Technology sector × AI/Datacenter/Gaming 테마).
2. **Geographic Revenue**: "Emerging Markets exposure 60%" 같은 지역 비중은 별도 속성.
3. **Bloomberg BICS Secondary**: 자동차+금융을 같이 하는 기업(현대차그룹 일부)은 secondary BICS로 Auto + Financials 둘 다 붙을 수 있음. 단 **Primary 1개**.

### 10.4 본 프로젝트 결정
- **Primary 1분류 원칙 채택** (업계 표준). `stock_universe.sector_norm`은 단일 값.
- 다각화 기업은 **"Principal business activity" = 매출/이익 기여도 최대 segment** 기준 (GICS 방식).
  - 예: SK이노베이션(정유+배터리) → 현재 `energy` (정유 매출 우위). 배터리 비중이 역전하면 `battery_materials`로 이동 검토.
  - 예: 삼성전자(반도체+가전) → `it_hardware` (가전·모바일 매출 비중이 반도체 단독보다 큰 합산 기준).
- **향후 확장 옵션** (이번 스프린트 범위 밖):
  - `stock_themes` 테이블 (다대다) — AI·전기차·방위 등 동적 테마 태깅
  - `cyclicality` 레이어 — Morningstar 방식 cyclical/defensive/sensitive (regime 연동)
  - `revenue_exposure_pct` JSONB — FactSet 방식 subsector 비중 (장기 과제)

1기업 1분류는 **단순성·지수 호환성·다양성 제약 계산 용이성** 때문에 유지하되, **직교 레이어**(theme/cyclicality)로 다차원 분석을 확장하는 것이 업계 공통 패턴.

---

## 11. P1-ext2 확장 개편 (남은 이슈 중 High ROI 3건)

§8.5/§9.8에서 남긴 이슈 중 **조선 오분류 · 건설 미분리 · KOSDAQ 대형주 미커버**는 실제 영향도가 커서 추가 세션에서 처리.

### 11.1 발견된 심각한 문제

#### 조선 5대장이 `aerospace_defense`로 잘못 분류 (합계 **198조원**)
```
HD현대중공업     70조 → aerospace_defense  (yfinance: Aerospace & Defense)
한화오션          41조 → aerospace_defense
HD한국조선해양   34조 → aerospace_defense
삼성중공업       30조 → aerospace_defense
HD현대마린솔루션 11조 → aerospace_defense
```
원인: 한국 조선사가 군함(방산)도 일부 제작 → yfinance가 복합 분류로 "Aerospace & Defense" 반환. 매출 비중은 상선 우위라 오분류.

**영향**: 다양성 제약 `max_per_sector=2`에서 조선 2개 + 실제 방산 2개 중 2개만 뽑힘. 산업 특성 완전히 다른데 같은 버킷에서 경쟁.

### 11.2 신규 버킷 2개 추가 (26 → 28)

| 키 | 설명 | 최종 |
|---|---|---|
| `shipbuilding` | 조선·해양플랜트 | **9** |
| `construction` | 건설·EPC·엔지니어링 | **84** |

### 11.3 KRX 매핑 변경

| KRX 업종 | 기존 | 변경 |
|---|---|---|
| 건설업 | industrials | **construction** |
| 건설 | industrials | **construction** |

### 11.4 구체 교정 건수

| 작업 | 건수 |
|---|---|
| Shipbuilding 티커 오버라이드 (조선 대장주) | 9 |
| Construction 티커 오버라이드 (삼성E&A·한전기술·한전KPS 등 KRX 건설 대분류 밖) | 6 |
| KOSDAQ 미커버 1조↑ 중 오분류 교정 (테스/우리기술/효성중공업) | 4 |
| 세종텔레콤 (KRX "건설" 오분류 → communication) | 1 |
| 이녹스 (KRX "운송장비부품" 오분류 → it_hardware) | 1 |
| 6차 재정규화 (모든 변경 반영) | **98 UPDATE** |
| 7차 재정규화 (세종텔레콤 후속) | 1 UPDATE |

### 11.5 최종 분포 (3,286종목, 28 활성 버킷)

```
-- IT 4 -----------------
semiconductors     175
it_hardware        298
it_software        293
communication       75

-- 금융 4 ----------------
banks               52
insurance           48
capital_markets     82
holding_co         115

-- 의료 2 ----------------
biotech            197
pharma_medtech     233

-- 소비 4 ----------------
consumer_discretionary  299
consumer_staples        127
media_entertainment      87
real_estate              69

-- 제조·운송 6 ----------
industrials             214
autos                   159
aerospace_defense        37   ← 조선 분리로 정확해짐 (43→37)
transport_logistics      45
shipbuilding              9   ← NEW
construction             84   ← NEW

-- 자원·소재 6 ----------
energy                   31
chemicals               239
battery_materials         9
steel_metals            127
nonmetallic              37
paper_wood               28

-- 기타 3 ---------------
utilities                42
other                    75
(deprecated) finance/healthcare/materials  0

─────────────────────────
TOTAL 3,286 / 28 활성 버킷
```

### 11.6 개선 지표 요약 (P0 시작 → P1-ext2 완료)

| 항목 | P0 이전 | P1-ext2 완료 | 변화 |
|---|---|---|---|
| 활성 sector_norm 버킷 | 14 | 28 | **+14 (2배)** |
| 최대 버킷 크기 | 553 (industrials) | 299 (CD) | **-46%** |
| 최대/최소 활성 비율 | 553/22 ≈ 25x | 299/9 ≈ 33x | (세분화 비용) |
| semiconductors | 21 | 175 | **+733%** |
| aerospace_defense 정확도 | N/A | 조선 분리로 순수화 | ✓ |
| GICS Industry Group(25) 수준 해상도 | 미달 | **달성** | ✓ |

### 11.7 보류된 이슈 (영향 낮아 연기)

1. **KOSDAQ 1천억 미만 미커버 443건**: 대부분 시총 1000억 이하 소형주. Top Picks 파이프라인이 이미 `market_cap_bucket >= mid`(1조원 이상) 기준으로 필터링 → 실질 영향 미미.
2. **KRX 분류 자체 오류 개별 케이스**: 발견될 때마다 `_KR_TICKER_OVERRIDES`에 추가하는 식으로 점진 처리. 전수 조사는 비용 대비 효과 낮음.
3. **chemicals 239 추가 분해** (석유화학 vs 특수화학 vs 농약 등): 이미 battery_materials로 핵심 테마는 분리됨. 나머지는 동질성 높아 추가 분해 효용 낮음.

### 11.8 향후 자동 개선 경로

- 주기적 `--mode industry_kr` 재실행 (주 1회 또는 월 1회) → yfinance 신규 등재 종목 자동 포착 → `--market KRX` 재정규화
- 신규 상장 종목은 `sync_meta_krx`에서 KRX 대분류 기반으로 1차 매핑 → yfinance 백필로 세분화
- KRX 분류 업데이트 시 `_KRX_TO_NORM`에 신규 업종 키 추가 (대부분은 이미 커버됨)
