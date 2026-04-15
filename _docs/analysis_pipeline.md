# Claude Code SDK 기반 멀티스테이지 분석 파이프라인

> 최종 갱신: 2026-04-15
> 대상 코드: [analyzer/](../analyzer/), [shared/config.py](../shared/config.py)

매일 RSS 뉴스를 수집하고 `claude-agent-sdk`의 `query()` 호출로 **2단계 분석**을 수행한 뒤, 결과를 PostgreSQL에 저장하는 배치 파이프라인이다. 과금은 Claude Code 구독 사용량에 포함되며, 별도 API 토큰 과금이 없다.

---

## 1. 전체 데이터 흐름

```
 ┌─────────────┐   ┌──────────────────────────┐   ┌─────────────────────────┐   ┌──────────┐
 │ RSS 뉴스    │ → │ Stage 1                  │ → │ Stage 2 (선택적)        │ → │ DB 저장  │
 │ (feedparser)│   │ 이슈/테마/시나리오/매크로│   │ 핵심 종목 심층분석     │   │ +tracking│
 │             │   │ /투자 제안               │   │ (펀더멘털·퀀트·센티먼트)│   │ 갱신     │
 └─────────────┘   └──────────────────────────┘   └─────────────────────────┘   └──────────┘
                       claude_agent_sdk.query()       claude_agent_sdk.query()
```

엔트리포인트는 [analyzer/main.py](../analyzer/main.py) 의 `main()`:

1. `AppConfig` 로드 (`.env` 자동 파싱)
2. `init_db()` — 스키마 마이그레이션 포함
3. `collect_news()` — RSS 카테고리별 수집
4. `run_full_analysis()` — 멀티스테이지 파이프라인
5. `save_analysis()` — 세션 INSERT + tracking UPSERT
6. `_print_summary()` — 콘솔 요약

---

## 2. Stage 0: 뉴스 수집

구현: [analyzer/news_collector.py](../analyzer/news_collector.py)

- `feedparser`로 [shared/config.py](../shared/config.py) `NewsConfig.feeds`의 카테고리별 RSS 피드를 순회
- 카테고리: `global`, `finance`, `technology`, `commodities`, `korea`
- 각 피드에서 `max_articles_per_feed`(기본 5)개 엔트리 추출
- HTML 태그 단순 제거 후 `요약[:500]`만 보존
- 카테고리 라벨 헤더(`### [경제·금융·시장] (N건)`)와 bullet 목록으로 마크다운화
- 반환값은 `---` 구분자로 조인된 단일 문자열 → Stage 1 프롬프트의 `{news_text}` 슬롯으로 투입

수집 실패한 피드는 경고만 출력하고 계속 진행한다.

---

## 3. Claude SDK 호출 공통 구조

[analyzer/analyzer.py](../analyzer/analyzer.py) 의 `_query_claude()`:

```python
async for message in query(
    prompt=prompt,
    options=ClaudeAgentOptions(
        system_prompt=system_prompt,
        max_turns=max_turns,   # AnalyzerConfig 기본값 2
    ),
):
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock):
                full_response += block.text
```

- `AssistantMessage` 안의 `TextBlock`만 누적 → 툴 호출이나 다른 블록은 무시
- 응답은 JSON-only를 요구하는 시스템 프롬프트를 쓰고, `_parse_json_response()`에서 `` ```json ... ``` `` 블록 또는 raw JSON을 파싱
- 파싱 실패 시 `{"error": "..."}` 반환 → 상위 파이프라인이 early-return

비동기 구현이며, 동기 호출은 `anyio.run()`으로 래핑 (`run_analysis`, `run_full_analysis`).

---

## 4. Stage 1 — 이슈/테마/시나리오/매크로/제안

함수: [analyzer/analyzer.py:52](../analyzer/analyzer.py#L52) `stage1_discover_themes()`
프롬프트: [analyzer/prompts.py](../analyzer/prompts.py) `STAGE1_SYSTEM` + `STAGE1_PROMPT`

### 시스템 프롬프트 역할
- 공통 베이스: 20년 경력 CFA/CAIA 매크로 전략가
- 탑다운 분석(지정학 → 통화정책 → 섹터 → 자산), 데이터 품질 규칙(출처·기준 시점·통화 단위·추정 표기), **JSON-only 응답**
- Stage 1 추가 역할: "글로벌 매크로 전략팀 테마 리서치 헤드"

### 요청하는 3단계 구조

**1단계: 글로벌 이슈 (8~15건)**
- 카테고리 분류(geopolitical/macroeconomic/monetary_policy/sector/technology/commodity/regulatory)
- `importance` 1~5
- `impact_short` (1개월) / `impact_mid` (1~6개월) / `impact_long` (6개월+)
- `historical_analogue` — 과거 유사 사례

**2단계: 투자 테마 (4~7개)**
- 복수 이슈에서 교차 검증된 테마만 선정
- `theme_type`: structural / cyclical
- `theme_validity`: strong / medium / weak
- `confidence_score` 0.00~1.00, `time_horizon` short/mid/long
- `key_indicators` — 모니터링 지표 배열
- `scenarios`: Bull / Base / Bear 3케이스 (확률·핵심가정·시장영향)
- `macro_impacts`: oil_wti / gold / usdkrw / us_10y_yield / sp500 / kospi 등의 base/worse/better 전망

**3단계: 투자 제안 (테마당 2~4건)**
- `asset_type`: stock / etf / commodity / currency / bond / crypto
- `ticker`, `market`(KRX/NYSE/NASDAQ/…)
- `action`: buy / sell / hold / watch, `conviction`: high / medium / low
- `current_price`, `target_price_low/high`, `upside_pct`
- `rationale`(3~5문장), `risk_factors`(2~3문장), `entry_condition`, `exit_condition`
- `target_allocation` % (포트폴리오 기준), `sector`, `currency`

### 출력 JSON 최상위 필드
```
analysis_date, market_summary, risk_temperature (high|medium|low),
data_sources, issues[], themes[]
```
각 `theme`은 `scenarios[]`, `macro_impacts[]`, `proposals[]`를 중첩 포함한다.

---

## 5. Stage 2 — 핵심 종목 심층분석 (선택적)

함수: [analyzer/analyzer.py:61](../analyzer/analyzer.py#L61) `stage2_analyze_stock()`
프롬프트: `STAGE2_SYSTEM` + `STAGE2_PROMPT`

### 실행 조건
[analyzer/analyzer.py:95-114](../analyzer/analyzer.py#L95-L114)

- `AnalyzerConfig.enable_stock_analysis` 가 `False` 면 전체 건너뜀
- Stage 1 결과에서 **상위 `top_themes`개 테마**(기본 2개)의 `proposals` 순회
- 조건을 모두 만족하는 제안만 대상:
  - `asset_type == "stock"`
  - `action` ∈ {buy, sell}
  - `ticker` 존재
- 최대 `top_themes * top_stocks_per_theme`개(기본 2×2 = 4개)까지 누적
- 대상이 0개면 Stage 2 생략

### 시스템 프롬프트 역할
- 공통 베이스 + "증권사 리서치센터 헤드 애널리스트"
- 5관점 통합 분석 요구

### 5관점 분석 구조
1. **펀더멘털** — 사업구조·3년 재무 요약(매출·영업이익·ROE·부채비율)·DCF 밸류에이션(WACC 포함)
2. **산업/경쟁** — 산업 성장률·시장 규모·경쟁사 포지셔닝(최소 2개 비교)
3. **모멘텀/수급** — 기술지표(RSI, MACD), 기관/외국인 수급, **AI 센티먼트 스코어** (-1.0 ~ +1.0)
4. **퀀트 팩터** — Value / Momentum / Quality / Growth / Size_Liquidity 각 1.0~5.0, `composite` 종합
5. **리스크/스트레스** — 리스크 요인 3~5개, Bull/Base/Bear 목표주가, `recommendation` (Strong Buy ~ Sell)

### 출력 JSON 주요 필드
```
ticker, company_overview, financial_summary{JSONB},
dcf_fair_value, dcf_wacc, industry_position, momentum_summary,
sentiment_score, factor_scores{JSONB: value,momentum,quality,growth,size_liquidity,composite},
risk_summary, bull_case, bear_case,
target_price_low, target_price_high, recommendation, report_markdown
```

### Stage 2 결과를 Stage 1 제안에 병합
[analyzer/analyzer.py:129-143](../analyzer/analyzer.py#L129-L143)

- 실패 시 로그만 출력하고 다음 종목으로 진행 (파이프라인 중단 없음)
- 성공 시 `proposal`에 아래를 덮어씀:
  - `stock_analysis` — 전체 Stage 2 결과 dict
  - `sentiment_score` — Stage 2 값을 proposal 레벨에 반영
  - `quant_score` — `factor_scores.composite` 복사
  - `target_price_low`, `target_price_high` — Stage 2 값으로 갱신(심층분석이 더 정확하다는 가정)

---

## 6. 설정 및 튜닝 포인트

[shared/config.py](../shared/config.py) `AnalyzerConfig`:

| 필드 | 기본값 | 의미 |
|------|--------|------|
| `max_turns` | 2 | Claude SDK 최대 턴 수 (두 Stage 공통, 환경변수 `MAX_TURNS`) |
| `top_themes` | 2 | Stage 2 대상 상위 테마 수 (환경변수 `TOP_THEMES`) |
| `top_stocks_per_theme` | 2 | 테마당 심층분석 종목 수 (환경변수 `TOP_STOCKS_PER_THEME`) |
| `enable_stock_analysis` | True | Stage 2 활성화 스위치 (환경변수 `ENABLE_STOCK_ANALYSIS`) |
| `enable_stock_data` | True | yfinance 주가 데이터 조회 스위치 (환경변수 `ENABLE_STOCK_DATA`) |

---

## 7. 저장 및 tracking 연계

`run_full_analysis()` 반환 dict는 [analyzer/main.py:59](../analyzer/main.py#L59)에서 `save_analysis()`로 전달된다.

- `analysis_sessions.analysis_date` 는 UNIQUE — 같은 날 재실행 시 DELETE 후 재생성
- CASCADE 체인: `analysis_sessions → global_issues` / `investment_themes → theme_scenarios, macro_impacts, investment_proposals → stock_analyses`
- 독립 테이블 `theme_tracking`, `proposal_tracking` 은 정규화 키(`_normalize_theme_key()`, `(ticker, theme_key)`) 기반 UPSERT 로 연속성 추적
- `stock_analyses.financial_summary` 와 `factor_scores` 는 JSONB

---

## 8. 에러 처리 패턴

- Stage 1 실패 → `{"error": ...}` 반환, 파이프라인 즉시 종료 ([analyzer/analyzer.py:88](../analyzer/analyzer.py#L88))
- Stage 2 개별 종목 실패 → try/except로 로깅만 하고 continue
- JSON 파싱 실패 → 원본 응답 앞 500자 출력 후 error dict 반환
- 뉴스 수집 실패 → 피드 단위 warning, 전체는 계속 (총 0건이면 상위에서 종료)

---

## 9. 동기/비동기 경계

- `analyzer.py` 내부는 `async` 기반 (`stage1_*`, `stage2_*`, `run_pipeline`)
- 외부 진입점(`run_analysis`, `run_full_analysis`)은 `anyio.run()`으로 동기 래핑
- `run_analysis()`는 Stage 1만 실행하는 하위호환용 래퍼로 유지
