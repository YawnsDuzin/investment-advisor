# Analyzer Pipeline Deep Dive — 처리 로직 상세 문서 및 개선 제안

> 작성일: 2026-04-16  
> 대상 코드: `analyzer/`, `shared/` 모듈  
> 엔트리포인트: `python -m analyzer.main`

---

## 목차

1. [전체 흐름도](#1-전체-흐름도)
2. [단계별 상세 설명](#2-단계별-상세-설명)
   - 2a. [DB 초기화](#2a-db-초기화)
   - 2b. [RSS 뉴스 수집](#2b-rss-뉴스-수집)
   - 2c. [뉴스 지문 비교](#2c-뉴스-지문-비교)
   - 2d. [Stage 1-A: 이슈 분석 + 테마 발굴](#2d-stage-1-a-이슈-분석--테마-발굴)
   - 2e. [Stage 1-B: 테마별 투자 제안 생성](#2e-stage-1-b-테마별-투자-제안-생성)
   - 2f. [모멘텀 체크](#2f-모멘텀-체크)
   - 2g. [Stage 2: 종목 심층분석](#2g-stage-2-종목-심층분석)
   - 2h. [뉴스 한글 번역](#2h-뉴스-한글-번역)
   - 2i. [DB 저장](#2i-db-저장)
3. [Claude SDK 호출 상세](#3-claude-sdk-호출-상세)
4. [JSON 파싱 및 복구](#4-json-파싱-및-복구)
5. [설정값 영향 매트릭스](#5-설정값-영향-매트릭스)
6. [에러 시나리오](#6-에러-시나리오)
7. [개선 제안: 역발상/차별화 종목 추출 강화](#7-개선-제안-역발상차별화-종목-추출-강화)
   - 7A. [프롬프트 개선](#7a-프롬프트-개선)
   - 7B. [로직 개선](#7b-로직-개선)
   - 7C. [구조적 개선](#7c-구조적-개선)

---

## 1. 전체 흐름도

```
┌──────────────────────────────────────────────────────────────────────┐
│  analyzer/main.py:main() (L21)                                       │
│                                                                      │
│  ┌────────────────┐                                                  │
│  │ AppConfig()     │ ← .env 로드 (shared/config.py)                  │
│  └───────┬────────┘                                                  │
│          │                                                           │
│  ┌───────▼────────┐     실패 → return 1                              │
│  │ init_db(cfg.db) │ ← 스키마 마이그레이션 (shared/db.py)              │
│  └───────┬────────┘                                                  │
│          │                                                           │
│  ┌───────▼──────────────────┐                                        │
│  │ collect_news_structured() │ ← RSS 수집 + 24h 필터 + 중복 제거      │
│  │ (news_collector.py:43)    │                                       │
│  └───────┬──────────────────┘                                        │
│          │ news_text 없으면 → return 1                                │
│          │                                                           │
│  ┌───────▼──────────────────┐                                        │
│  │ 뉴스 지문 비교            │ ← get_latest_news_titles() (db.py:951) │
│  │ (main.py:42-55)           │   신규 < MIN_NEW_NEWS → return 0      │
│  └───────┬──────────────────┘                                        │
│          │                                                           │
│  ┌───────▼──────────────────────────────────────────────────────┐    │
│  │ run_full_analysis() → anyio.run(run_pipeline)                 │    │
│  │ (analyzer.py:606-611)                                         │    │
│  │                                                               │    │
│  │  ┌─────────────────────────┐                                  │    │
│  │  │ 최근 추천 이력 조회       │ ← get_recent_recommendations()  │    │
│  │  │ (analyzer.py:308-315)    │   7일간 중복 방지 피드백          │    │
│  │  └──────────┬──────────────┘                                  │    │
│  │             │                                                 │    │
│  │  ┌──────────▼──────────────┐                                  │    │
│  │  │ Stage 1-A: 이슈+테마     │ ← Claude SDK (1회 호출)          │    │
│  │  │ stage1a_discover_themes  │   issues[], themes[] 반환        │    │
│  │  │ (analyzer.py:241-248)    │                                 │    │
│  │  └──────────┬──────────────┘                                  │    │
│  │             │                                                 │    │
│  │  ┌──────────▼──────────────┐                                  │    │
│  │  │ Stage 1-B: 투자 제안     │ ← Claude SDK (테마당 1회, 순차)  │    │
│  │  │ stage1b_generate_proposals│  각 테마에 proposals[] 추가      │    │
│  │  │ (analyzer.py:251-269)    │                                 │    │
│  │  └──────────┬──────────────┘                                  │    │
│  │             │                                                 │    │
│  │  ┌──────────▼──────────────┐  (ENABLE_STOCK_DATA=true일 때)   │    │
│  │  │ 모멘텀 체크              │ ← fetch_momentum_batch()         │    │
│  │  │ (analyzer.py:352-403)   │   ThreadPool max 8 병렬           │    │
│  │  │                         │   current_price + momentum_tag    │    │
│  │  └──────────┬──────────────┘                                  │    │
│  │             │                                                 │    │
│  │  ┌──────────▼──────────────┐  (ENABLE_STOCK_ANALYSIS=true)    │    │
│  │  │ Stage 2: 종목 심층분석   │ ← Claude SDK (종목당 1회, 병렬)  │    │
│  │  │ (analyzer.py:404-494)   │   asyncio.gather() 사용           │    │
│  │  │ + 주가 데이터 일괄 조회  │   fetch_multiple_stocks()        │    │
│  │  └──────────┬──────────────┘                                  │    │
│  │             │                                                 │    │
│  └─────────────┼────────────────────────────────────────────────┘    │
│                │                                                     │
│  ┌─────────────▼─────────────┐                                       │
│  │ translate_news()           │ ← Claude SDK Haiku (30건 배치)       │
│  │ (analyzer.py:592-596)     │                                       │
│  └─────────────┬─────────────┘                                       │
│                │                                                     │
│  ┌─────────────▼─────────────┐                                       │
│  │ save_analysis()            │ ← DB 트랜잭션 (shared/db.py:647)     │
│  │ save_news_articles()       │   tracking 갱신 + 구독 알림 생성      │
│  └─────────────┬─────────────┘                                       │
│                │                                                     │
│  ┌─────────────▼─────────────┐                                       │
│  │ _print_summary()           │ ← 콘솔 요약 출력                     │
│  └───────────────────────────┘                                       │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 2. 단계별 상세 설명

### 2a. DB 초기화

| 항목 | 내용 |
|------|------|
| **함수** | `shared/db.py:init_db()` → `_ensure_database()` → `_get_schema_version()` → `_create_base_schema()` ~ `_migrate_to_v12()` |
| **호출 위치** | `analyzer/main.py:26` |
| **입력** | `DatabaseConfig` (host, port, dbname, user, password) |
| **출력** | 없음 (DB 스키마가 최신 상태로 갱신됨) |
| **에러 처리** | Exception 발생 시 `main()`에서 catch → `return 1`로 즉시 종료 |

**동작 상세:**

1. `_ensure_database(cfg)`: `postgres` DB에 접속하여 대상 DB 존재 여부 확인. 없으면 `CREATE DATABASE` 실행.
2. `_get_schema_version(cur)`: `schema_version` 테이블에서 현재 버전 조회 (테이블 없으면 0).
3. 현재 버전이 `SCHEMA_VERSION`(=12)보다 낮으면 순차적으로 `_migrate_to_vN()` 실행.
4. 각 마이그레이션은 `ALTER TABLE`, `CREATE TABLE`, 인덱스 생성 등 포함.

---

### 2b. RSS 뉴스 수집

| 항목 | 내용 |
|------|------|
| **함수** | `analyzer/news_collector.py:collect_news_structured()` (L43) |
| **호출 위치** | `analyzer/main.py:36` |
| **입력** | `NewsConfig` (feeds: dict[str, list[str]], max_articles_per_feed: int) |
| **출력** | `(news_text: str, articles: list[dict])` |

**`news_text` 구조:**
```
### [글로벌 종합] (N건)

  • [Reuters] (04/16 09:30) 뉴스 제목
    뉴스 요약 (300자 이내)

---

### [경제·금융·시장] (N건)
...
```

**`articles` 각 항목 구조:**
```python
{
    "category": str,     # "global" | "finance" | "technology" | ...
    "source": str,       # RSS 피드 제목
    "title": str,        # 원문 제목
    "summary": str,      # HTML 제거 후 1000자 이내
    "link": str,         # 기사 URL
    "published": str,    # RSS published 원문
}
```

**최적화 로직 (L59-126):**

1. **24시간 필터링** (L78-81): `_parse_published()`로 발행 시간 파싱 → UTC 기준 24시간 이전 기사 제외
2. **교차 중복 제거** (L83-88): 제목 앞 30자를 소문자 정규화하여 `seen_titles` set으로 중복 판단
3. **토큰 축소**: 요약을 300자로 절단 (프롬프트용, L103), DB 저장용은 1000자 (L111)
4. **날짜 축약** (L97-101): 불필요한 요일·시간대 정보 제거하여 `MM/DD HH:MM` 형식으로 압축

**분기 조건:**
- `news_text`가 빈 문자열이면 `main()`에서 `return 1` (L37-39)
- 개별 피드 파싱 실패 시 해당 피드만 스킵, 나머지 계속 (L117)

**설정 영향:**
- `NewsConfig.feeds`: 카테고리별 RSS URL 목록 (7개 카테고리, 기본 12개 피드)
- `NewsConfig.max_articles_per_feed`: 피드당 최대 기사 수 (기본 5)

---

### 2c. 뉴스 지문 비교

| 항목 | 내용 |
|------|------|
| **함수** | `shared/db.py:get_latest_news_titles()` (L951) |
| **호출 위치** | `analyzer/main.py:42-55` |
| **입력** | `DatabaseConfig` |
| **출력** | `list[str]` — 최근 세션의 뉴스 제목 목록 |

**동작 상세 (main.py:42-55):**

1. DB에서 가장 최근 `analysis_sessions`의 뉴스 제목 목록 조회
2. 현재 수집된 뉴스 제목 set과 이전 제목 set을 비교하여 신규 뉴스 수 계산
3. `new_count = len(curr_titles - prev_titles)`

**분기 조건:**
- `new_count < min_new_news` **AND** `prev_titles`가 존재 → `return 0` (분석 스킵)
- `prev_titles`가 비어있으면 (첫 실행) → 무조건 분석 진행
- DB 조회 실패 시 → 경고 출력 후 분석 진행 (L54-55)

**설정 영향:**
- `MIN_NEW_NEWS` (기본값 5): 신규 뉴스 임계값. 이 수 미만이면 "뉴스가 충분히 바뀌지 않았다"고 판단하여 분석 생략

---

### 2d. Stage 1-A: 이슈 분석 + 테마 발굴

| 항목 | 내용 |
|------|------|
| **함수** | `analyzer/analyzer.py:stage1a_discover_themes()` (L241) |
| **호출 위치** | `analyzer/analyzer.py:319` (`run_pipeline` 내부) |
| **입력** | `news_text: str`, `date: str`, `max_turns: int`, `model: str` |
| **출력** | `dict` — 아래 구조 |
| **프롬프트** | `STAGE1A_SYSTEM` + `STAGE1A_PROMPT` (prompts.py:222-329) |

**출력 데이터 구조:**
```python
{
    "analysis_date": "2026-04-16",
    "market_summary": str,        # 15~25줄 구조화 텍스트
    "risk_temperature": str,      # "high" | "medium" | "low"
    "data_sources": ["RSS뉴스"],
    "issues": [                   # 8~15건
        {
            "category": str,      # geopolitical|macroeconomic|...|regulatory
            "region": str,
            "title": str,
            "summary": str,
            "source": str,
            "importance": int,    # 1~5
            "impact_short": str,
            "impact_mid": str,
            "impact_long": str,
            "historical_analogue": str | None,
        }
    ],
    "themes": [                   # 4~7건
        {
            "theme_name": str,
            "description": str,
            "related_issue_indices": list[int],
            "confidence_score": float,  # 0.00~1.00
            "time_horizon": str,        # "short" | "mid" | "long"
            "theme_type": str,          # "structural" | "cyclical"
            "theme_validity": str,      # "strong" | "medium" | "weak"
            "key_indicators": list[str],
            "scenarios": [              # Bull/Base/Bear 3건
                {"scenario_type": str, "probability": int, "description": str,
                 "key_assumptions": str, "market_impact": str}
            ],
            "macro_impacts": [
                {"variable_name": str, "base_case": str, "worse_case": str,
                 "better_case": str, "unit": str}
            ],
        }
    ]
}
```

**분기 조건:**
- `result.get("error")` → Stage 1-A 실패, `run_pipeline()`이 에러 결과를 그대로 반환

**주의사항:**
- Stage 1-A에는 `proposals`가 없음 — 투자 제안은 Stage 1-B에서 별도 생성
- `STAGE1A_SYSTEM`은 `STAGE1_SYSTEM`과 동일 (L224: `STAGE1A_SYSTEM = STAGE1_SYSTEM`)

---

### 2e. Stage 1-B: 테마별 투자 제안 생성

| 항목 | 내용 |
|------|------|
| **함수** | `analyzer/analyzer.py:stage1b_generate_proposals()` (L251) |
| **호출 위치** | `analyzer/analyzer.py:334-349` (for 루프, 순차 실행) |
| **입력** | `theme: dict`, `date: str`, `max_turns: int`, `recent_recs: list[dict]`, `model: str` |
| **출력** | `list[dict]` — proposals 배열 |
| **프롬프트** | `STAGE1B_SYSTEM` + `STAGE1B_PROMPT` (prompts.py:334-413) |

**실행 방식:**
- **순차 실행**: 테마 수만큼 순서대로 호출 (병렬 아님)
- 각 테마에 대해 `theme["proposals"] = proposals`로 직접 할당

**최근 추천 이력 피드백 (L189-221):**
- `_format_recent_recommendations()`가 7일간 추천 이력을 프롬프트 텍스트로 포맷
- 티커별로 그룹핑 → 테마명 합산 → `"제외 종목 목록"` 섹션으로 삽입
- LLM에게 "이 종목들은 신규 추천에서 제외하고, 동일 밸류체인 내 2~3차 수혜주를 대신 찾으라"고 지시

**출력 데이터 구조 (각 proposal):**
```python
{
    "asset_type": str,             # stock|etf|commodity|currency|bond|crypto
    "asset_name": str,
    "ticker": str,
    "market": str,                 # KRX|NYSE|NASDAQ|etc
    "action": str,                 # buy|sell|hold|watch
    "conviction": str,             # high|medium|low
    "current_price": None,         # 항상 null (실시간 주입 대기)
    "target_price_low": float,
    "target_price_high": float,
    "upside_pct": None,
    "vendor_tier": int,            # 1|2|3
    "supply_chain_position": str,
    "discovery_type": str,         # consensus|early_signal|contrarian|deep_value
    "price_momentum_check": str,   # already_run|fair_priced|undervalued|unknown
    "rationale": str,
    "risk_factors": str,
    "target_allocation": float,
    "sector": str,
    "currency": str,               # KRW|USD|JPY|EUR
}
```

**에러 처리:**
- 개별 테마 실패 시 해당 테마만 `theme["proposals"] = []`로 설정, 나머지 테마는 계속 (L344-346)

---

### 2f. 모멘텀 체크

| 항목 | 내용 |
|------|------|
| **함수** | `analyzer/stock_data.py:fetch_momentum_batch()` (L165) → `fetch_momentum_check()` (L124) |
| **호출 위치** | `analyzer/analyzer.py:352-403` |
| **입력** | `stocks: list[dict]` — `[{"ticker": str, "market": str}]` |
| **출력** | `dict[str, dict]` — `{ticker: {"return_1m_pct", "momentum_tag", "current_price"}}` |

**실행 방식:**
- `ThreadPoolExecutor(max_workers=min(N, 8))` — 최대 8 스레드 병렬
- 중복 티커 자동 제거 (L176-183)

**대상 선정 (L354-357):**
- 모든 테마의 proposals 중 `ticker`가 존재하고 `asset_type == "stock"`인 것만

**모멘텀 태깅 로직 (`fetch_momentum_check`, L146-153):**
- `return_1m >= +20%` → `"already_run"` (급등, 진입 매력 낮음)
- `return_1m <= -10%` → `"undervalued"` (미반영, 진입 매력 높음)
- 그 외 → `"fair_priced"`

**현재가 처리 (analyzer.py:369-388):**

```
모멘텀 조회 성공?
├─ YES → current_price = mdata["current_price"], price_source = "yfinance_close"
│        momentum_tag 할당
└─ NO  → fetch_stock_data() 개별 재시도
         ├─ 성공 → current_price = sd["price"], price_source = "yfinance_realtime"
         └─ 실패 → current_price = None, price_source = None (AI 추정치 제거)
```

**`ENABLE_STOCK_DATA=false`일 때 (L397-402):**
- 모멘텀 체크 전체 스킵
- 모든 stock 타입 제안의 `current_price = None`, `price_source = None`

---

### 2g. Stage 2: 종목 심층분석

| 항목 | 내용 |
|------|------|
| **함수** | `analyzer/analyzer.py:stage2_analyze_stock()` (L274) |
| **호출 위치** | `analyzer/analyzer.py:404-494` (`_analyze_one` 내부) |
| **프롬프트** | `STAGE2_SYSTEM` + `STAGE2_PROMPT` (prompts.py:418-499) |

**대상 선정 로직 (L411-426):**

1. 상위 `TOP_THEMES`개 테마만 대상 (L412: `themes[:cfg.top_themes]`)
2. 각 테마에서 `asset_type == "stock"` AND `action in ("buy", "sell")` AND `ticker` 존재하는 후보 추출
3. **우선순위 정렬** (L420-424):
   - 1차 키: `price_momentum_check` 기반 — `undervalued`/`early_signal`(0) > `unknown`/`fair_priced`(1) > `already_run`(2)
   - 2차 키: `discovery_type`이 `early_signal`/`contrarian`/`deep_value`면 -1 (우선)
4. 테마당 상위 `TOP_STOCKS_PER_THEME`개 선택

**주가 데이터 조회 (L432-443):**
- `ENABLE_STOCK_DATA=true` → `fetch_multiple_stocks()` 병렬 조회 (ThreadPool max 8)
- `ENABLE_STOCK_DATA=false` → "Claude 추정치 사용" 메시지, `stock_data_map = {}`

**병렬 실행 (L489-492):**
```python
await asyncio.gather(*[
    _analyze_one(proposal, theme_name)
    for proposal, theme_name in stock_targets
])
```

**`_analyze_one()` 내부 (L448-487):**
1. `stock_data_map`에서 해당 종목 데이터 조회
2. `format_stock_data_text(sd)` — 프롬프트 삽입용 텍스트 생성 (현재가, 52주 고저, PER/PBR, 시총 등)
3. `stage2_analyze_stock()` 호출
4. 성공 시 proposal에 결과 필드 병합:
   - `stock_analysis` (전체 심층분석 결과)
   - `sentiment_score`, `quant_score`, `target_price_low/high`, `entry_condition`, `exit_condition`
   - `current_price`, `price_source` (yfinance 실시간)

**Stage 2 출력 데이터 구조:**
```python
{
    "ticker": str,
    "company_overview": str,
    "financial_summary": {
        "revenue_3y": list[str],
        "operating_margin": str,
        "roe": str, "debt_ratio": str,
        "per": float, "pbr": float,
    },
    "dcf_fair_value": float,
    "dcf_wacc": float,
    "industry_position": str,
    "momentum_summary": str,
    "sentiment_score": float,       # -1.0 ~ +1.0
    "factor_scores": {
        "value": float, "momentum": float, "quality": float,
        "growth": float, "size_liquidity": float,
        "composite": float,         # 1.0 ~ 5.0
    },
    "risk_summary": str,
    "bull_case": str,
    "bear_case": str,
    "target_price_low": float,
    "target_price_high": float,
    "recommendation": str,          # Strong Buy|Buy|Neutral|Reduce|Sell
    "entry_condition": str,
    "exit_condition": str,
    "report_markdown": str,
}
```

**분기 조건:**
- `ENABLE_STOCK_ANALYSIS=false` → Stage 2 전체 스킵, Stage 1 결과만 반환 (L405-407)
- `stock_targets`가 비어있으면 → 스킵 (L428-429)
- 개별 종목 분석 실패 시 해당 종목만 스킵, 나머지 계속 (L486-487)

---

### 2h. 뉴스 한글 번역

| 항목 | 내용 |
|------|------|
| **함수** | `analyzer/analyzer.py:translate_news()` (L592) → `_translate_news_batch()` (L500) |
| **호출 위치** | `analyzer/main.py:75` |
| **입력** | `articles: list[dict]`, `model: str` |
| **출력** | `list[dict]` — 각 항목에 `title_ko`, `summary_ko` 추가됨 |

**동작 상세:**

1. **한국어 판별** (L511-512): 정규식 `[\uac00-\ud7af]`로 한글 포함 여부 확인
2. 이미 한글인 필드 → 원문 그대로 `title_ko`/`summary_ko`에 복사 (L520-523)
3. 번역 필요한 기사만 수집 (L526-527): 요약은 200자로 축약 (`summary[:200]`)
4. **30건 배치** (L538): `BATCH_SIZE = 30`으로 묶어 시스템 프롬프트 반복 최소화
5. Claude SDK 호출: `max_turns=1`, 시스템 프롬프트 = "뉴스 제목/요약 번역 전문가"
6. 응답 파싱 후 `articles[idx]["title_ko"]`, `articles[idx]["summary_ko"]` 업데이트

**에러 처리:**
- 배치 번역 실패 시 해당 배치 원문 유지, 나머지 배치 계속 (L585-586)

**동기 래핑:**
- `translate_news()` (L592-596)는 `anyio.run(_translate_news_batch, articles, model)`로 동기 호출

**설정 영향:**
- `MODEL_TRANSLATE` (기본 `claude-haiku-4-5-20251001`): 번역에 사용할 저비용 모델

---

### 2i. DB 저장

| 항목 | 내용 |
|------|------|
| **함수** | `shared/db.py:save_analysis()` (L647) + `save_news_articles()` (L817) |
| **호출 위치** | `analyzer/main.py:78-82` |

**`save_analysis()` 트랜잭션 흐름:**

```
1. DELETE 기존 세션 (같은 날짜)
2. INSERT analysis_sessions → session_id
3. INSERT global_issues (N건) → issue_id_map
4. for theme in themes:
   4a. INSERT investment_themes → theme_id
   4b. INSERT theme_scenarios (3건: bull/base/bear)
   4c. INSERT macro_impacts (N건)
   4d. for proposal in theme.proposals:
       - _validate_proposal(proposal)  ← 가격 검증
       - upside_pct 재계산 (current_price 기반)
       - INSERT investment_proposals → proposal_id
       - if stock_analysis: INSERT stock_analyses
5. _update_tracking(cur, ...)  ← theme_tracking, proposal_tracking UPSERT
6. _generate_notifications(cur, ...)  ← 구독 알림 생성
7. COMMIT
```

**`_validate_proposal()` (db.py:594-644) 검증 규칙:**

1. `price_source`가 None(AI 추정)이면 `current_price` → None
2. `current_price <= 0` → None으로 리셋
3. `target_price_low > target_price_high` → 스왑
4. `current_price`가 None이면 `upside_pct` → None
5. `target_price_low < current_price * 0.5` → 목표가 전체 무효화 (AI 추정 목표가로 판단)

**`_update_tracking()` (db.py:1036-1131):**
- `theme_tracking`: UPSERT — 연속 출현 일수(`streak_days`), 총 출현 횟수, 최신/이전 신뢰도
- `proposal_tracking`: UPSERT — `(ticker, theme_key)` 기준, 추천 횟수, 최신/이전 action·목표가

**`_generate_notifications()` (db.py:973-1025):**
- `user_subscriptions` 테이블의 모든 구독 조회
- 이번 분석에 등장한 ticker/theme_key와 매칭
- 매칭 시 `user_notifications`에 알림 INSERT

**`save_news_articles()` (db.py:817-845):**
- 별도 커넥션으로 뉴스 기사 일괄 INSERT (`title_ko`, `summary_ko` 포함)

---

## 3. Claude SDK 호출 상세

### `_query_claude()` — `analyzer/analyzer.py:126-184`

**옵션 설정:**
```python
ClaudeAgentOptions(
    system_prompt=system_prompt,  # 스테이지별 시스템 프롬프트
    max_turns=max_turns,          # MAX_TURNS 환경변수 (기본 1)
    model=model,                  # MODEL_ANALYSIS 또는 MODEL_TRANSLATE
    tools=[],                     # 도구 사용 안 함 (순수 텍스트 응답만)
    permission_mode="plan",       # 최소 권한 (실행 차단)
    setting_sources=[],           # 프로젝트 설정 무시 (CLI 오버헤드 최소화)
)
```

**재시도 로직 (L128-184):**
- `max_retries` 기본값: 2회
- 재시도 간 대기: `10 * attempt`초 (1차 실패→10초, 2차 실패→20초)
- `asyncio.sleep()` 사용
- 마지막 재시도 실패 시 `raise last_error`

**응답 스트리밍 (L146-168):**

| 메시지 타입 | 처리 |
|------------|------|
| `AssistantMessage` | `TextBlock`의 `.text`를 누적 → `full_response`에 합산. 수신마다 진행 로그 출력 |
| `ResultMessage` | 완료 시그널. `num_turns` 기록 |
| `SystemMessage` | 시스템 서브타입 출력 (예: 초기화, 종료 등) |

**로그 출력 예시:**
```
  [SDK] 쿼리 시작 (max_turns=1, model=claude-sonnet-4-6)
  [SDK] 응답 수신 #1 (+8,432자, 누적 8,432자, 12초)
  [SDK] 완료 — 턴 1회, 15초 소요
  [SDK] 쿼리 종료 (응답 8,432자, 총 15초)
```

---

## 4. JSON 파싱 및 복구

### `_parse_json_response()` — `analyzer/analyzer.py:96-123`

**1단계: JSON 추출**
```
원본 응답 → strip()
├─ "```json" 포함 → 첫 번째 ```json ~ ``` 사이 추출
├─ "```" 포함 → 첫 번째 ``` ~ ``` 사이 추출
└─ 그 외 → 전체를 JSON으로 시도
```

**2단계: 파싱 시도**
- `json.loads(json_str)` 성공 → 즉시 반환

**3단계: 잘린 JSON 복구 (`_try_fix_truncated_json`, L26-93)**

SDK의 `max_turns` 제한으로 응답이 중간에 잘릴 수 있음. 복구 과정:

1. **미종료 문자열 닫기** (L31-50): 문자열 리터럴 내부에서 잘린 경우 `"` 추가
2. **열린 브래킷 계산** (L56-77): `{`, `[`를 스택으로 추적
3. **trailing comma 제거** (L79-80): JSON 문법 오류 방지
4. **역순으로 닫기** (L83-91): 스택의 역순으로 `}`, `]` 추가

**복구 실패 시:**
- `{"error": str(e)}` 반환 → 호출자에서 `result.get("error")`로 검출

---

## 5. 설정값 영향 매트릭스

| 환경변수 | 기본값 | 영향 단계 | 동작 |
|---------|--------|----------|------|
| `MAX_TURNS` | `1` | Stage 1-A, 1-B, 2, 번역 | `ClaudeAgentOptions.max_turns`에 전달. 값이 클수록 LLM이 더 많은 턴을 사용 가능 (번역은 항상 1). 비용에 직접 영향 |
| `TOP_THEMES` | `2` | Stage 2 대상 선정 | `themes[:cfg.top_themes]`로 상위 N개 테마만 심층분석 대상 |
| `TOP_STOCKS_PER_THEME` | `2` | Stage 2 대상 선정 | 각 테마에서 상위 N개 종목만 심층분석 (`candidates[:cfg.top_stocks_per_theme]`) |
| `ENABLE_STOCK_ANALYSIS` | `true` | Stage 2 전체 | `false` → Stage 2 전체 스킵, Stage 1 결과만 저장 |
| `ENABLE_STOCK_DATA` | `true` | 모멘텀 체크 + Stage 2 주가 조회 | `false` → 모멘텀 체크 스킵, 모든 stock 종목의 current_price=None. Stage 2에서 주가 데이터 미제공 |
| `MODEL_ANALYSIS` | `claude-sonnet-4-6` | Stage 1-A, 1-B, 2 | 분석에 사용할 Claude 모델. 비용/성능 트레이드오프 |
| `MODEL_TRANSLATE` | `claude-haiku-4-5-20251001` | 뉴스 번역 | 번역 전용 저비용 모델 |
| `MIN_NEW_NEWS` | `5` | 뉴스 지문 비교 | 신규 뉴스가 이 수 미만이면 분석 생략 (전날과 뉴스가 거의 동일) |

**조합 시나리오:**

| 시나리오 | 설정 | SDK 호출 횟수 |
|---------|------|-------------|
| 최소 실행 | `ENABLE_STOCK_ANALYSIS=false` | 1(1-A) + N(1-B) + 번역배치 |
| 기본 실행 | 기본값 (TOP_THEMES=2, TOP_STOCKS_PER_THEME=2) | 1 + N + 최대4 + 번역배치 |
| 최대 실행 | TOP_THEMES=7, TOP_STOCKS_PER_THEME=5 | 1 + N + 최대35 + 번역배치 |

---

## 6. 에러 시나리오

| 단계 | 에러 | 시스템 대응 | 영향 |
|------|------|-----------|------|
| DB 초기화 | 연결 실패 / 마이그레이션 실패 | **즉시 종료** (`return 1`) | 전체 파이프라인 중단 |
| RSS 수집 | 개별 피드 파싱 실패 | 해당 피드 스킵, **계속 진행** | 해당 카테고리 뉴스 수 감소 |
| RSS 수집 | 전체 뉴스 0건 | **즉시 종료** (`return 1`) | 분석할 데이터 없음 |
| 뉴스 지문 비교 | DB 조회 실패 | 경고 출력, **계속 진행** | 지문 비교 없이 무조건 분석 |
| 최근 추천 이력 | DB 조회 실패 | 경고 출력, **계속 진행** | 중복 방지 미적용 (빈 리스트) |
| Stage 1-A | SDK 호출 실패 (2회 재시도 후) | **즉시 종료** (에러 결과 반환) | 전체 파이프라인 중단 |
| Stage 1-A | JSON 파싱 실패 + 복구 실패 | `{"error": ...}` 반환 → **즉시 종료** | 전체 파이프라인 중단 |
| Stage 1-B | 개별 테마 제안 생성 실패 | 해당 테마 proposals=[], **계속 진행** | 해당 테마 제안 없음 |
| 모멘텀 체크 | 개별 종목 조회 실패 | 개별 재조회(fetch_stock_data) 시도 → 재실패 시 current_price=None, **계속 진행** | 해당 종목 가격 미확보 |
| Stage 2 | 개별 종목 분석 실패 | 해당 종목 스킵, **계속 진행** | 해당 종목 심층분석 없음 |
| 뉴스 번역 | 배치 번역 실패 | 해당 배치 원문 유지, **계속 진행** | 해당 뉴스 title_ko/summary_ko 없음 |
| DB 저장 | 저장 실패 | **즉시 종료** (`return 1`) | 분석 결과 유실 |

**핵심 패턴**: Stage 1-A 실패와 DB 관련 에러만 파이프라인을 중단. 나머지는 모두 graceful degradation.

---

## 7. 개선 제안: 역발상/차별화 종목 추출 강화

### 7A. 프롬프트 개선

#### 7A-1. "차별화 원칙" 섹션의 실효성 문제

**현재 상태:**
`SYSTEM_PROMPT_BASE` (prompts.py:10-38)에 "차별화 원칙 — 남들이 모르는 기회 발굴" 섹션이 4개 원칙으로 구성됨:
- 컨센서스 vs 얼리 시그널 분리
- 진입 타이밍 우선
- 정보 비대칭 활용 (중소형주 60%)
- 역발상(Contrarian) 별도 표기

**문제점:**
1. **선언적 지시만 존재**: "60% 이상은 중소형 종목으로 구성하세요"라고 했지만, LLM은 학습 데이터 편향으로 대형주(삼성전자, NVIDIA 등)를 과다 추천하는 경향이 있음. 단순 비율 지시는 실효성이 낮음
2. **검증 메커니즘 부재**: LLM이 실제로 비율을 준수했는지 후처리에서 체크하지 않음
3. **역발상의 구체성 부족**: "펀더멘털 반전 시그널이 보이는 경우"라는 조건이 추상적이라 LLM이 일반적인 저PER 종목을 역발상으로 분류하는 경향

**개선안:**

**(A) Few-shot 예시 추가**  
`STAGE1B_PROMPT`에 구체적인 역발상/얼리시그널 예시를 삽입:

```
## 역발상 종목 발굴 예시 (참고용, 그대로 따라하지 말 것):

<예시>
테마: AI 반도체 수요 급증
- 컨센서스: NVIDIA (GPU 시장 지배)
- 얼리 시그널: 한미반도체 (HBM 후공정 본딩장비, NVIDIA 밸류체인 3차 수혜)
- 컨트래리안: 인텔 (파운드리 전환 베팅, 시장 극단적 비관 속 CHIPS법 보조금 수혜 시작)
- 딥밸류: SK하이닉스 과거 DRAM 불황기 매수 사례 (2019 PBR 0.7배 → 2020 +180% 수익)
</예시>
```

기대 효과: LLM이 "구체적으로 무엇이 얼리시그널인가"를 패턴으로 학습하여 유사한 발굴을 수행

**(B) Chain-of-Thought 단계 분리**  
현재는 "밸류체인 전체를 조망한 뒤" 한 번에 제안하라고 지시하지만, 사고 과정을 명시적으로 분리:

```
### 종목 선정 사고 과정 (반드시 순서대로 수행):

1단계 — 밸류체인 매핑:
  이 테마의 완성품 → 핵심 부품 → 소재·장비 → 원재료 체인을 먼저 나열하세요.

2단계 — 컨센서스 분리:
  위 체인에서 뉴스에 직접 언급된 종목, 시총 5조 이상 대형주를 "컨센서스"로 분류하세요.

3단계 — 얼리 시그널 발굴:
  밸류체인 2~3차에 위치하면서, 최근 3개월 애널리스트 리포트가 3건 미만인 종목을 찾으세요.

4단계 — 역발상 탐색:
  이 테마와 반대되는 시나리오에서 수혜를 받을 종목,
  또는 이 테마로 인해 과도하게 매도된 연관 종목을 찾으세요.
```

기대 효과: LLM이 각 단계를 명시적으로 밟으면서 다양한 유형의 종목을 고르게 생성

**(C) 비율 검증 후처리 지시 추가**  
프롬프트 마지막에 자가 검증 지시:

```
### 최종 검증 (응답 생성 후 반드시 확인):
- [ ] early_signal + contrarian + deep_value 비율이 70% 이상인가?
- [ ] consensus 종목이 30% 이하인가?
- [ ] 시총 3,000억~2조 중소형주가 50% 이상인가?
- 위 조건 미충족 시, consensus 종목을 줄이고 얼리시그널을 추가하세요.
```

기대 효과: LLM이 출력 직전에 비율을 재확인하여 편향 보정

---

#### 7A-2. discovery_type 분류의 실효성

**현재 상태:**
- `discovery_type`: `consensus` / `early_signal` / `contrarian` / `deep_value` 4가지 (prompts.py:107-110)
- Stage 2 대상 선정에서 `early_signal`/`contrarian`/`deep_value`에 우선순위 부여 (analyzer.py:423)

**문제점:**
- LLM이 자체적으로 분류하므로, 실제 시장 데이터와 무관하게 태깅됨
- 예: 시총 50조 대형주를 `early_signal`로 태깅할 수 있음 (검증 불가)

**개선안:**
- **후처리 검증 로직 추가**: `fetch_stock_data()`에서 조회한 `market_cap`을 기반으로 discovery_type 교차 검증
  - `market_cap > 10조` → `early_signal` 불가, 자동으로 `consensus`로 재분류
  - `market_cap < 3000억` → `consensus` 불가, `early_signal` 또는 `deep_value`로 재분류
- `_validate_proposal()` 함수(db.py:594)에 discovery_type 검증 규칙 추가

기대 효과: 시총 데이터와 discovery_type의 정합성 확보, Stage 2 대상 선정 품질 향상

---

#### 7A-3. STAGE2_PROMPT 역발상 분석 강화

**현재 상태:**
`STAGE2_PROMPT` (prompts.py:424-499)은 5가지 관점 분석을 요구하지만, 역발상 관점이 별도 섹션으로 없음.

**문제점:**
- 컨트래리안 종목이 Stage 2에 올라와도, 분석 프롬프트가 일반 종목과 동일
- "왜 시장이 이 종목을 비관하는지"에 대한 분석이 부족

**개선안:**
`STAGE2_PROMPT`에 조건부 섹션 추가:

```
### 6. 역발상 분석 (해당 종목이 contrarian/deep_value인 경우)
- 시장 비관의 핵심 근거 3가지
- 각 비관 근거에 대한 반론 (구체적 데이터 기반)
- 반전 카탈리스트 타임라인 (3/6/12개월)
- 유사 역발상 성공/실패 사례
```

기대 효과: 역발상 종목에 대한 분석 깊이 증가, 투자 확신도 판단에 실질적 도움

---

### 7B. 로직 개선

#### 7B-1. Stage 2 대상 선정 우선순위 로직

**현재 상태 (`analyzer.py:420-424`):**
```python
priority = {"undervalued": 0, "early_signal": 0, "unknown": 1, "fair_priced": 1, "already_run": 2}
candidates.sort(key=lambda p: (
    priority.get(p.get("price_momentum_check", "unknown"), 1),
    -1 if p.get("discovery_type") in ("early_signal", "contrarian", "deep_value") else 0,
))
```

**문제점:**
1. **`priority` dict에 `"early_signal"` 키가 있지만 이는 `price_momentum_check`의 값이 아님** — `price_momentum_check`의 유효 값은 `already_run`/`fair_priced`/`undervalued`/`unknown`임. `"early_signal"`은 사실상 dead code
2. `discovery_type`의 `contrarian`과 `deep_value` 간 차이가 없음 (동일 우선순위)
3. `conviction` (high/medium/low)이 선정에 반영되지 않음

**개선안:**
```python
def _stage2_priority(p: dict) -> tuple:
    # 1차: 모멘텀 — 미반영(undervalued) 우선
    momentum_rank = {"undervalued": 0, "fair_priced": 1, "unknown": 1, "already_run": 3}
    # 2차: 발굴 유형 — contrarian/deep_value 최우선
    discovery_rank = {"contrarian": 0, "deep_value": 0, "early_signal": 1, "consensus": 3}
    # 3차: 확신도
    conviction_rank = {"high": 0, "medium": 1, "low": 2}

    return (
        momentum_rank.get(p.get("price_momentum_check", "unknown"), 1),
        discovery_rank.get(p.get("discovery_type", "consensus"), 2),
        conviction_rank.get(p.get("conviction", "medium"), 1),
    )
```

기대 효과: 역발상/딥밸류 종목이 확실히 Stage 2에 진입, 확신도도 선정에 반영

---

#### 7B-2. 모멘텀 체크 임계값

**현재 상태 (`stock_data.py:148-153`):**
- `+20%` 이상 → `already_run`
- `-10%` 이하 → `undervalued`
- 그 사이 → `fair_priced`

**문제점:**
1. **단일 기간(1개월)만 사용**: 1개월 +15% 상승 후 조정 중인 종목과 꾸준히 올라온 종목을 구분 못함
2. **시장/섹터 대비 상대 수익률 미반영**: 시장 전체가 +15%일 때 개별 종목 +20%는 큰 의미 없음
3. **`-10%` 기준이 너무 관대**: 하락 종목은 하락 이유가 있을 수 있으며, 단순 `-10%` = `undervalued`는 위험

**개선안:**
```python
def fetch_momentum_check(ticker, market):
    # 기존 1개월에 추가로 3개월 데이터도 조회
    hist_3m = stock.history(period="3mo")
    hist_1m = hist_3m.tail(22)  # 최근 1개월 (대략 22거래일)

    return_1m = (price_end - price_1m_start) / price_1m_start * 100
    return_3m = (price_end - price_3m_start) / price_3m_start * 100

    # 복합 판단
    if return_1m >= 20 or return_3m >= 40:
        tag = "already_run"
    elif return_1m <= -15 and return_3m <= -25:
        tag = "deeply_oversold"     # 새 카테고리: 심각 과매도
    elif return_1m <= -10:
        tag = "undervalued"
    elif return_3m <= -5 and return_1m > 0:
        tag = "early_recovery"      # 새 카테고리: 반등 초기
    else:
        tag = "fair_priced"
```

기대 효과: 단순 1개월 수익률 이상의 다차원 모멘텀 판단, 역발상 종목의 정교한 분류

---

#### 7B-3. 최근 추천 이력 피드백의 실효성

**현재 상태 (`analyzer.py:189-221`):**
- 7일간 추천된 종목 리스트를 프롬프트에 "제외 종목 목록"으로 삽입
- "동일 밸류체인 내 아직 발굴되지 않은 2~3차 수혜주를 대신 찾으세요"라고 지시

**문제점:**
1. **단순 제외**: 좋은 종목을 단순 배제하면 대안 품질이 떨어질 수 있음
2. **연속 추적 부재**: 같은 종목이 3일 연속 추천되는 것 자체가 "강한 확신" 시그널일 수 있으나, 현재는 무조건 배제
3. **밸류체인 연관성 무시**: "삼성전자 제외" 시 LLM이 다른 테마의 전혀 무관한 종목을 추천할 수 있음

**개선안:**

```python
def _format_recent_recommendations(recent_recs, tracking_data=None):
    """추천 이력 + 추적 데이터를 결합한 피드백"""

    lines = []
    for tk, info in ticker_map.items():
        status = ""
        if tracking_data and tk in tracking_data:
            td = tracking_data[tk]
            # 연속 추천 종목은 제외하지 않고 "포지션 관리" 지시
            if td["recommendation_count"] >= 3:
                status = " → [포지션 관리 대상] 기존 목표가 유효성 재평가 필요"
            else:
                status = " → [신규 추천 제외] 동일 밸류체인 2~3차 수혜주로 대체"
        lines.append(f"  - {tk} ({info['name']}){status}")

    return "\n".join(lines)
```

기대 효과: 연속 추천 종목을 무조건 제외하지 않고, 포지션 관리(목표가 조정/청산) 판단을 유도

---

#### 7B-4. 새로운 데이터 소스 활용

**현재 상태:**
- 뉴스: RSS 피드 7개 카테고리 12개 소스
- 주가: yfinance (현재가, PER/PBR, 52주 고저, 시총)

**개선안: 공매도/대차잔고 데이터 추가**

역발상 종목 발굴에 가장 효과적인 데이터 중 하나는 **공매도 비율**:
- 공매도 비율이 높은 종목 = 시장이 강하게 비관 → 역발상 후보
- yfinance의 `shortPercentOfFloat` 필드 활용 가능 (미국 주식)

```python
# stock_data.py의 fetch_stock_data()에 추가
"short_pct": info.get("shortPercentOfFloat"),  # 유동주식 대비 공매도 비율
"short_ratio": info.get("shortRatio"),          # 숏커버링 일수
```

프롬프트에 삽입:
```
- 공매도 비율: 15.2% (유동주식 대비), 숏커버링 일수: 4.2일
  → 공매도 과다 (short squeeze 가능성 모니터링)
```

기대 효과: 데이터 기반 역발상 종목 발굴 (감이 아닌 수치 근거)

---

### 7C. 구조적 개선

#### 7C-1. Stage 간 피드백 루프 부재

**현재 상태:**
```
Stage 1-A → Stage 1-B → 모멘텀 체크 → Stage 2
```
단방향 파이프라인. Stage 2 결과가 Stage 1 제안의 타당성을 뒤집어도 반영 경로 없음.

**문제점:**
- Stage 2에서 "펀더멘털이 약해 Neutral 의견"이 나온 종목이 Stage 1에서 "high conviction buy"로 남아있음
- 심층분석 후 포트폴리오 재조정 기회 없음

**개선안: Stage 2.5 — 포트폴리오 최종 조정 단계**

```python
async def stage2_5_portfolio_review(themes, stock_results):
    """Stage 2 심층분석 결과를 반영한 최종 포트폴리오 조정"""
    # 1. Stage 2 결과에서 Neutral/Reduce/Sell 의견 종목 추출
    # 2. 해당 종목의 conviction을 하향 조정
    # 3. 빈 비중을 다른 종목에 재배분
    # 4. (선택) Claude SDK로 최종 포트폴리오 밸런싱 검토

    for theme in themes:
        for p in theme["proposals"]:
            sa = p.get("stock_analysis", {})
            if sa.get("recommendation") in ("Neutral", "Reduce", "Sell"):
                p["conviction"] = "low"
                p["action"] = "watch" if sa["recommendation"] == "Neutral" else "sell"
```

기대 효과: 심층분석 결과의 실질적 반영, 불일치 제안 자동 보정. SDK 추가 호출 없이 코드 레벨에서 구현 가능.

---

#### 7C-2. 과거 분석 성과 추적(백테스팅) 연동

**현재 상태:**
- `proposal_tracking` 테이블에 추천 이력만 저장 (목표가, 추천일)
- 실제 수익률 추적 없음 — "이 시스템의 과거 추천이 맞았는지" 확인 불가

**문제점:**
- 시스템이 반복적으로 틀린 유형의 추천(예: 항상 반도체 과대추천)을 해도 자가 보정 없음
- 역발상 종목의 실제 성과를 측정하지 않으므로, 역발상 비율 최적화 불가

**개선안: 일일 성과 추적 + 피드백 프롬프트**

1. **성과 추적 배치** (매일 분석 전 실행):
```python
def track_performance(cfg):
    """과거 추천 종목의 현재 수익률 추적"""
    # proposal_tracking에서 최근 30일간 추천 이력 조회
    # yfinance로 현재가 조회
    # (현재가 - 추천시 current_price) / current_price * 100 = 수익률
    # discovery_type별 평균 수익률 집계
    # 결과를 proposal_tracking.actual_return_pct 컬럼에 업데이트
```

2. **프롬프트 피드백**: Stage 1-B에 성과 통계 삽입
```
## 시스템 과거 성과 (최근 30일, 참고용)
- early_signal 종목 평균 수익률: +5.2% (12건 중 8건 양수)
- contrarian 종목 평균 수익률: -2.1% (5건 중 2건 양수)
- consensus 종목 평균 수익률: +3.8% (8건)
→ 최근 역발상 성과가 부진합니다. 역발상 추천 시 더 보수적인 진입 조건을 설정하세요.
```

기대 효과:
- 자가 학습 루프: 잘 맞는 발굴 유형을 더 많이, 잘 안 맞는 유형은 조건 강화
- "이 시스템이 실제로 돈이 되는가"에 대한 객관적 답변 가능

**구현 난이도:** 중간. `proposal_tracking` 테이블에 `actual_return_pct` 컬럼 추가(v13 마이그레이션) + 별도 배치 스크립트.

---

#### 7C-3. 시장 레짐 감지에 따른 전략 자동 전환

**현재 상태:**
- `risk_temperature` (high/medium/low)를 Stage 1-A에서 생성하지만, 이것이 파이프라인 동작에 영향을 주지 않음
- 불/베어 시장에서 동일한 프롬프트와 비율 가이드라인 사용

**문제점:**
- 강세장: 모멘텀 종목이 더 수익적 → 역발상 비율 20%가 과다할 수 있음
- 약세장: 역발상/딥밸류가 더 효과적 → 현재 10~20% 비율이 너무 낮음
- 횡보장: 배당주/턴어라운드 전략이 적합하나 별도 조절 없음

**개선안: 레짐 기반 프롬프트 파라미터 동적 조정**

```python
# analyzer.py의 run_pipeline() 초반
def _detect_regime(result):
    """Stage 1-A 결과의 risk_temperature로 레짐 감지"""
    risk = result.get("risk_temperature", "medium")
    return {
        "high": {  # 베어/위기
            "contrarian_ratio": "20~30%",
            "early_signal_ratio": "40~50%",
            "consensus_ratio": "10~20%",
            "strategy_note": "방어적 포지션, 역발상 비율 확대, 현금성 자산 포함",
        },
        "medium": {  # 기본
            "contrarian_ratio": "10~20%",
            "early_signal_ratio": "60% 이상",
            "consensus_ratio": "20~30%",
            "strategy_note": "균형 포트폴리오",
        },
        "low": {  # 강세
            "contrarian_ratio": "5~10%",
            "early_signal_ratio": "50~60%",
            "consensus_ratio": "30~40%",
            "strategy_note": "모멘텀 추종 강화, 역발상 비율 축소",
        },
    }[risk]
```

Stage 1-B 프롬프트에 레짐 파라미터 동적 삽입:
```python
STAGE1B_PROMPT = """...
현재 시장 레짐: {regime_risk} ({regime_strategy_note})
비중 가이드:
  - 얼리 시그널: {regime_early_signal_ratio}
  - 컨트래리안/딥밸류: {regime_contrarian_ratio}
  - 컨센서스: {regime_consensus_ratio}
..."""
```

기대 효과: 시장 상황에 따른 자동 전략 전환, 역발상 비율의 상황 적응형 조절

**구현 난이도:** 낮음. Stage 1-A 결과의 `risk_temperature`를 읽어 Stage 1-B 프롬프트 파라미터만 변경.

---

### 개선안 우선순위 요약

| # | 개선안 | 난이도 | 기대 효과 | 추천 순서 |
|---|--------|--------|----------|----------|
| 7C-3 | 시장 레짐 기반 비율 동적 조정 | **낮음** | 상황 적응형 전략 | 1순위 |
| 7A-1B | Chain-of-Thought 단계 분리 | **낮음** | 종목 다양성 향상 | 2순위 |
| 7A-1C | 비율 자가 검증 지시 | **낮음** | 비율 준수 강화 | 3순위 |
| 7B-1 | Stage 2 대상 선정 로직 개선 | **낮음** | 역발상 종목 Stage 2 진입 보장 | 4순위 |
| 7A-1A | Few-shot 예시 추가 | **낮음** | 발굴 품질 향상 | 5순위 |
| 7C-1 | Stage 2.5 포트폴리오 조정 | **중간** | 심층분석 결과 반영 | 6순위 |
| 7B-3 | 추천 이력 피드백 고도화 | **중간** | 연속 추천 종목 관리 | 7순위 |
| 7B-2 | 모멘텀 체크 다차원화 | **중간** | 정교한 모멘텀 판단 | 8순위 |
| 7A-2 | discovery_type 후처리 검증 | **중간** | 데이터 정합성 | 9순위 |
| 7B-4 | 공매도 데이터 추가 | **중간** | 역발상 데이터 근거 | 10순위 |
| 7A-3 | Stage 2 역발상 분석 섹션 | **낮음** | 역발상 분석 깊이 | 11순위 |
| 7C-2 | 백테스팅/성과 추적 | **높음** | 자가 학습 루프 | 12순위 |
