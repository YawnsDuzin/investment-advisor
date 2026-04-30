# 공공 수출입 데이터 통합 타당성 검토

**작성**: 2026-04-30 (KST)
**대상 시스템**: investment-advisor (schema v44, KRX+US 멀티스테이지 추천)
**범위**: 검토 문서만. 코드·테이블 생성 없음.

## 목차

1. [데이터 소스 후보 평가](#1-데이터-소스-후보-평가)
2. [인사이트 가설](#2-인사이트-가설)
3. [현 아키텍처 통합 시나리오](#3-현-아키텍처-통합-시나리오)
4. [비용·리스크 평가](#4-비용리스크-평가)
5. [결론 및 권고](#5-결론-및-권고)

---

## 1. 데이터 소스 후보 평가

### 1.1 후보 비교표

| # | 소스 | 단위 | 갱신주기 | 공시 시차 | API 안정성 | HS↔산업 매핑 | 라이선스/비용 |
|---|---|---|---|---|---|---|---|
| **A** | data.go.kr `관세청_품목별 국가별 수출입실적(GW)` (id=15100475) | HS 2/4/6/10 × 국가 | **월간 확정치**(익월 중순) | T+15 ~ T+25일 | 안정. 정부 공공포털, 운영계 10만 req/일 | HS 2/4 → 산업 자동 가능 / HS 6/10 → 종목 매핑 필요 | 무료, 인증키 |
| **B** | data.go.kr `관세청_국가별 수출입실적(GW)` (id=15101612) | 국가 합계 | 월간 확정치 | T+15일 | 안정 | 산업 분해 불가(매크로용) | 무료, 인증키 |
| **C** | 관세청 보도자료 잠정치 (월 3회 — 1~10일 / 1~20일 / 월 종합) | 국가·일부 품목 | **월 3회 (10일 단위)** | T+1일 (잠정) | 보도자료 PDF/HTML — 파싱 불안정 | 품목 분해 제한적 | 무료, 비API |
| **D** | KITA K-stat (stat.kita.net) | HS 2/4/6 × 국가 (61개국 포함) | 월간 | T+15일 | 사이트 조회는 가능 / **OpenAPI 무료 발급은 회원사 전용일 가능성 — 확인 필요** | 동일 | 회원사 전용 가능성, **확인 필요** |
| **E** | 한국무역통계진흥원 (KTSPI) / TRASS | HS 10단위, 자사실적 | 일/월 | T+1 ~ T+15 | 교부대행 기관 — 유료 가능성 높음 | 동일 | **유료 추정, 확인 필요** |
| **F** | 한국은행 ECOS API | 수출입금액지수·수출물량지수·교역조건지수 | 월간 | T+15일 | 안정 | 거시(매크로) 단위 — 산업 분해 약함 | 무료, 인증키 |

### 1.2 1~3순위 선정

1. **1순위 — 후보 A (data.go.kr 15100475)**: HS 코드 단위·국가 분해·공식 인증 무료 OpenAPI 라는 3박자가 갖춰진 유일한 후보. 갱신은 월간이지만 PIT 시계열로 5년치 backfill 시 row 가 가벼움(추정 수십만).
2. **2순위 — 후보 C (관세청 잠정치)**: T+1일 시차로 월간 확정치보다 빠른 *선행 시그널*. 다만 보도자료 파싱은 안정성 떨어짐 → 보조 신호로만.
3. **3순위 — 후보 F (ECOS 수출물량지수)**: 거시 보완용. 산업 분해는 약하지만 *시장 레짐 레이어(`market_indices_ohlcv`)와 동일 입자도*에서 추가 신호로 결합 가능.

후보 D/E 는 무료·합법 조건 미충족 가능성으로 본 검토 범위에서 제외 권고.

---

## 2. 인사이트 가설

### 2.1 가설 표

| # | 가설 (데이터 → 신호 → 종목 후보) | 입자도 | 선행성 | 기존 시그널 중복도 | 비고 |
|---|---|---|---|---|---|
| **H1** | HS 6단위 對글로벌 수출 YoY 급증(>+30%) → 해당 산업 KOSPI/코스닥 대표주 매출 선행 → 모멘텀 진입 | HS 6 × 산업 | **leading** (1~2개월) | 모멘텀 팩터(가격)는 *동행/사후*. 매출 데이터는 분기 펀더(v39) — 본 신호가 1~2개월 선행 → 중복 낮음 | KOSPI 핵심. 자동화 가능 |
| **H2** | 對중국 / 對미국 품목별 수출 격차 확대 → 무역 의존도 높은 코스닥 중소형주 영향 | HS 4 × 국가 (중/미) | **leading** | 외국인 수급(v44)은 종목 단위 *동시진행*. 대중·대미 분해는 외수급으로 못 잡음 → 중복 낮음 | 한미·한중 무역 마찰 시 차별화 |
| **H3** | 자본재(HS 84/85) 수입 증가 → 국내 설비투자 사이클 진입 → 산업재(반도체 장비·기계·전력) 섹터 수혜 | HS 2 × 카테고리 | **leading** (3~6개월) | 시장 레짐(`market_indices_ohlcv`) = 인덱스 단위. 설비투자 사이클은 섹터 단위 → 보완 | sector_norm 28버킷에 직접 매핑 가능 |
| **H4** | 화장품(HS 33) / 식품(HS 19~22) / K-콘텐츠 관련 對동남아·중동 수출 가속 → 코스닥 중소형 컨슈머 매출 선행 | HS 4 × 신흥지역 | **leading** | 외국인 수급·모멘텀 모두 *반응적*. 매출 발 신호는 별개 차원 → 중복 매우 낮음 | 코스닥 정보 비대칭 알파 가설(STAGE1_SYSTEM 의 "남들이 모르는 기회"와 정합) |
| **H5** | 잠정치(T+1일) 對전월 -10% 이상 급락 품목 → 해당 산업 *조기 경고* (Stage 1-A 리스크 톤 상향) | HS 2 × 국가 | **leading** | 시장 레짐의 vol_regime 은 인덱스 변동성 — 산업별 펀더 충격은 별개 | 잠정치 보도자료 파싱 안정성 이슈 |

### 2.2 중복도 종합 평가

기존 4개 시그널 레이어 (외국인 수급 v44 / 모멘텀 팩터 B1 / 시장 레짐 v31 / 펀더 PIT v39) 의 **공통 구조적 공백**: *산업 단위 펀더 선행지표*. 가격·수급은 종목 단위, 펀더는 분기 단위 — 그 사이의 "산업 단위·월간" 레이어가 비어있다. 수출 데이터는 정확히 그 빈 칸을 채운다.

다만 H1/H2/H4 는 **종목 단위 매핑이 발목**. H3 는 sector_norm 28버킷 ↔ HS 2단위만 있으면 즉시 동작.

---

## 3. 현 아키텍처 통합 시나리오

### 3.1 데이터 레이어

#### 신규 테이블 (개념 수준)

- **`trade_stats_hs`**: PIT 시계열. PK 후보 `(hs_code, hs_level, country_code, period_type, period_date)`. period_type ∈ `monthly_final | monthly_provisional | weekly_provisional`.
- **`hs_to_sector_map`**: HS 코드 ↔ sector_norm 28버킷 매핑 (자동 + 수동 큐레이션 혼합). HS 2/4 단위 시드 자동, 6/10 단위는 수기 추가.
- **`hs_to_ticker_map`** *(선택)*: H1/H2/H4 가설용. 대표주 100~300 종목 한정 수기 큐레이션.

#### 마이그레이션 버전

- 다음 버전: **v45 (`trade_stats_hs`)**, **v46 (매핑 테이블 분리)**.
- `_migrate_to_v45` 패턴은 `_migrate_to_v44` (`stock_universe_foreign_flow`) 그대로 차용 가능 — FK 미설정 PIT 원칙, retention 5년.

#### sync 배치 위치 — `analyzer/foreign_flow_sync.py` 패턴 준용 가능 여부

| 항목 | foreign_flow_sync.py | trade_sync.py (제안) | 차이점 |
|---|---|---|---|
| 외부 API 호출 | pykrx 동기 함수 | requests/httpx → data.go.kr | HTTP 레이어만 추가 |
| 병렬 처리 | `ThreadPoolExecutor(max_workers=4)` | 동일 사용 가능 | API 호출 빈도 ↓ (월 1회 fetch) → 단일 스레드로도 충분 |
| UPSERT | `execute_values` + `ON CONFLICT DO UPDATE` | 동일 패턴 | 그대로 |
| 가드 | `max_consecutive_failures` 조기 종료 | 동일 권고 | data.go.kr throttling 회피 |
| 로깅 | `shared.logger.get_logger("foreign_flow_sync")` | `get_logger("trade_sync")` | 그대로 |

**결론**: foreign_flow_sync 패턴 90% 재사용 가능. 차이는 외부 API 가 pykrx → REST 라는 한 점.

### 3.2 분석 레이어 — 3개 옵션 비교

| 옵션 | 통합 방식 | 장점 | 단점 |
|---|---|---|---|
| **B1. Stage 1-A 프롬프트 주입** | `STAGE1A_PROMPT` / `STAGE1A1_PROMPT` / `STAGE1A2_PROMPT` 에 `{trade_signal_section}` 추가. `{market_regime_section}` 패턴(`analyzer/regime.py`) 그대로 미러링 | 기존 패턴 재사용, AI 가 산업·테마 톤 자동 조정. 구현 빠름 | 토큰 부담 ↑(이미 self-interruption 으로 1-A → 1-A1/1-A2 분할한 이력 있음 — 토큰 추가는 신중). AI 가 모든 산업을 다 보지 않음 |
| **B2. Stage 1-B 스크리너 필터 추가** | `analyzer/screener.py` 에 `min_export_yoy_pct` / `target_export_country` spec 키 추가. `trade_stats_hs` LEFT JOIN | 정량 필터 — 결정론적, 신뢰도 높음. UI 빠른 시작 카드에 거장 시드 추가 가능("수출 모멘텀") | 산업↔종목 매핑 사전 작업 필요. 종목 단위 신호화 어려움 (산업 단위만) |
| **B3. 신규 stage 1.5** | Stage 1 직후 별도 `stage1_5_trade_screen()` 단계. 산업 후보 압축 후 Stage 2 로 전달 | 명확한 책임 분리 | 파이프라인 복잡도 ↑, checkpoint 흐름 재설계 필요. ROI 가장 낮음 |

#### 권고 — B1 + B2 결합 (B3 비권장)

- **B1**: 매크로 차원 톤 조정 (H3, H5 가설 — 산업/리스크 톤). 1-A2 (테마 도출) 프롬프트에만 주입. 1-A1(이슈) 토큰 절약.
- **B2**: 정량 필터 (H1, H3 가설 — 산업 단위 매출 선행). 실측 spec 키 1~2개로 시작.
- **B3 비권고**: Stage 1-A → 1-A1/1-A2 분할 이슈(2026-04-22 self-interruption)에서 보듯 새 stage 추가는 검증 부담 큼.

### 3.3 운영 레이어

- **systemd timer**: `investment-advisor-trade-sync.timer` 신규. 월간 확정치(매월 16일 KST 07:00) + 잠정치(매일 KST 07:30). KST 06:30 메인 분석보다 *나중에* — 분석에 늦게 반영되지만 sync 실패가 메인 분석을 막지 않음.
- **MANAGED_UNITS 화이트리스트**: `api/routes/admin_systemd.py:MANAGED_UNITS` 에 신규 unit 등록 + `deploy/systemd/README.md` sudoers 예시 갱신 (CLAUDE.md 명시 규칙).
- **health check**: `tools/trade_health_check.py`. `fundamentals_health_check.py` 패턴 차용 — 결측률 임계 + staleness days 체크.
- **fallback**: API 장애 시 → `trade_stats_hs` 결측 → `format_trade_signal_text()` 가 빈 문자열 반환 → `{trade_signal_section}` 가 비어서 프롬프트 영향 없음 (regime.py 의 `infer_positioning_hint` 와 동일 안전 경로).

---

## 4. 비용·리스크 평가

### 4.1 구현 공수 (MD = 사람·일)

| 작업 | 공수 | 비고 |
|---|---|---|
| `trade_sync.py` (data.go.kr REST → UPSERT) | 2.0 | foreign_flow_sync 90% 재사용 |
| 마이그레이션 v45/v46 + retention | 0.5 | 기존 패턴 답습 |
| 시그널 계산 모듈 (YoY, 가속도, 국가별 분해) | 1.0 | factor_engine 패턴 차용 |
| `format_trade_signal_text()` + Stage 1-A2 주입 | 0.5 | regime.py 미러 |
| 스크리너 spec 키 + LEFT JOIN | 1.0 | 기존 외국인 필터 패턴 |
| `hs_to_sector_map` 시드 (HS 2/4 ↔ 28버킷) | **1.5** | 자동 시드 + 수동 검증. 핵심 마찰점 |
| `hs_to_ticker_map` 시드 (선택, 100~300종목) | **2.0** | 종목 단위 가설(H1/H2/H4) 활성화 시. 미시 PoC 에선 생략 가능 |
| systemd unit + health check + 운영 매뉴얼 | 0.5 | foreign-flow-sync 패턴 |
| **합계 (산업 단위 PoC)** | **6.5 MD** | 종목 매핑 제외 |
| **합계 (종목 단위 풀)** | **8.5 MD** | 종목 매핑 포함 |

### 4.2 외부 API 의존성 리스크

- **data.go.kr**: 정부 공공포털, **유료화 가능성 매우 낮음**. 트래픽 운영계 10만 req/일 — HS 코드 × 국가 5,000 ~ 10,000 fetch/월 정도라 여유. throttling 시 `max_consecutive_failures` 가드로 안전.
- **잠정치 보도자료**: HTML 구조 변경 시 파싱 깨짐 위험. 보조 신호 위치 — 깨져도 메인 분석은 영향 없음.
- **API 정책 변경**: 과거 사례 기준 1~2년 단위 스펙 마이그레이션 발생 가능. 즉시 대응 필요는 아님.

### 4.3 HS 코드 ↔ 종목 매핑 모호성

| 영역 | 자동화 | 큐레이션 필요 | 비고 |
|---|---|---|---|
| HS 2단위 (97개) ↔ sector_norm 28버킷 | **가능** | 검증만 | KSIC 표준 매핑 활용 |
| HS 4단위 (~1,200개) ↔ 28버킷 | 부분 자동 | 일부 | HS 2 매핑 상속 + 예외 큐레이션 |
| HS 6단위 (~5,300개) ↔ 종목 | **불가능** | **수기 필수** | 대표주 위주 한정 운영 |
| HS 10단위 (~13,000개) ↔ 종목 | 의미 약함 | 비권고 | HS 6 단위로 합산 운영 |

**핵심 위험**: HS 6/10 단위 ↔ 종목 매핑은 *산업 전문가의 수기 작업*. 자동화 시도는 거짓 양성률 폭증 우려. → **PoC 는 HS 2/4 단위 산업 신호로 한정**.

### 4.4 데이터 품질

- **국가별 결측**: 신흥국(중남미·아프리카) 빈약. 주요 교역국 5~10개 (중/미/일/EU/베트남/대만/홍콩/인도) 한정 운영.
- **HS 코드 개정**: 약 5년 주기(WCO HS Convention). v45 → v46 마이그레이션 시점에 매핑 재검토 필요 — 운영 부담.
- **잠정치 ↔ 확정치 차이**: 5~10% 흔함. 잠정치는 *방향성 신호*로만, 임계값 기반 필터에는 확정치 사용.

---

## 5. 결론 및 권고

### ⚠ 조건부 구현

기존 4개 시그널 레이어가 못 메우는 *산업 단위·월간 펀더 선행지표* 레이어를 정확히 채우는 데이터다. 가설 H1·H3·H4 는 신규 알파 발굴 가치가 명확하다 — 특히 코스닥 중소형주(STAGE1_SYSTEM 의 "정보 비대칭 알파" 정합) 영역에서 큰 효과 기대.

다만 **종목 단위 매핑(HS 6/10)은 진입 장벽이 높다**. 즉시 풀 구현은 비추.

### 선결 조건

다음 3개 조건이 모두 충족되면 PoC 진행 권고:

1. **(필수) `hs_to_sector_map` 시드 작성** — HS 2/4 단위 ↔ sector_norm 28버킷 매핑. 자동 시드 후 수기 검증 1.5 MD. 매핑 검증 없이는 전체 신호의 신뢰도 보장 불가.
2. **(필수) 국가 화이트리스트 한정** — 중/미/일/EU/베트남/대만/홍콩/인도 (8개) 시드. 신흥국 결측 데이터로 인한 거짓 신호 차단.
3. **(권고) 잠정치 보도자료 파싱 별도 PR 분리** — H5 가설은 PoC 1차 범위에서 제외. 확정치 기반 H1·H3 만으로 시작.

### 1차 PoC 범위 (권고)

- **데이터**: 후보 A (data.go.kr 15100475) 단일 소스. HS 2/4 단위 × 8개 교역국, 5년 backfill. 마이그레이션 v45.
- **신호**: H1 (HS 6 → 산업 매출 선행) — 단, HS 6 → 28버킷 합산만 사용, 종목 단위 매핑은 PoC 후 평가. H3 (자본재 수입 → 산업재 섹터) 동시 적용.
- **통합 지점**: B1 (Stage 1-A2 프롬프트 `{trade_signal_section}` 주입) + B2 (스크리너 거장 시드 1개 신규: "수출 모멘텀"). B3 신규 stage 추가는 비권고.
- **공수**: **6.5 MD (1 sprint)**. 종목 매핑 풀 구현은 PoC 결과 검증 후 별도 sprint.
- **검증 KPI**: PoC 후 30일간 H1·H3 신호 적용 종목의 30일 alpha vs benchmark (`investment_proposals.alpha_vs_benchmark_pct` v29 활용) 가 비적용 군 대비 유의미한 양의 차이를 보일 것.

### 비권고 영역 (현 시점)

- 종목 단위 직접 매핑 (HS 6/10 → ticker) — PoC 결과 검증 전까지 보류.
- KITA / TRASS 유료 데이터 — 무료 후보 A 로 충분, 비용 정당화 어려움.
- 신규 Stage 1.5 — 파이프라인 복잡도 대비 ROI 낮음.

---

## 참고 — 외부 데이터 소스 출처 (실재 확인됨)

- [관세청_품목별 국가별 수출입실적(GW)](https://www.data.go.kr/data/15100475/openapi.do)
- [관세청_국가별 수출입실적(GW)](https://www.data.go.kr/data/15101612/openapi.do)
- [관세청_품목별 수출입실적(GW)](https://www.data.go.kr/data/15101609/openapi.do)
- [공공데이터 포털 사용 가이드](https://www.data.go.kr/ugs/selectPublicDataUseGuideView.do)
- [관세청 수출입무역통계](https://tradedata.go.kr/) — 사이트 조회용
- [한국무역협회 K-stat](https://stat.kita.net/) — OpenAPI 무료 발급 여부 **확인 필요**
- [한국무역통계진흥원 KTSPI](https://www.ktspi.or.kr/) — 유료 가능성, **확인 필요**

## 참고 — 코드베이스 사실 (검증됨)

- `shared/db/schema.py:12` — `SCHEMA_VERSION = 44`
- `shared/db/migrations/__init__.py:53-57` — v40~v44 등록 패턴
- `analyzer/foreign_flow_sync.py` — 본 검토의 sync 패턴 모델
- `analyzer/prompts.py:245-361` — `STAGE1A_PROMPT` `{market_regime_section}` 주입 위치
- `api/routes/admin_systemd.py:MANAGED_UNITS` — 신규 timer 등록 지점
- CLAUDE.md "외부 API 의존성" / "_docs 명명 규칙" / "systemd unit 화이트리스트" 항목 준수

---

*본 문서는 검토 단계 산출물입니다. 구현 착수 전 선결 조건 충족 여부를 다시 점검하세요.*
