# Investment Advisor — 기술 아키텍처 문서

> 최종 갱신: 2026-04-13 | DB 스키마 v5 | Python 3.10+

---

## 1. 시스템 개요

### 1.1 프로젝트 목적

매일 글로벌 RSS 뉴스를 수집하고, Claude Code SDK(`claude-agent-sdk`)의 `query()` 호출로 **2단계 멀티스테이지 분석 파이프라인**을 실행하여 투자 테마·시나리오·종목 제안을 자동 생성한다. 결과는 PostgreSQL에 저장되며, FastAPI 웹서비스를 통해 조회·시각화할 수 있다.

### 1.2 기술 스택

| 영역 | 기술 | 역할 |
|------|------|------|
| AI 분석 | `claude-agent-sdk` | 멀티스테이지 분석 (Stage 1·2) |
| 백엔드 | FastAPI + Uvicorn | REST API + HTML 서빙 |
| 템플릿 | Jinja2 | 다크 테마 UI 렌더링 |
| 데이터베이스 | PostgreSQL + psycopg2 | 스키마 자동 마이그레이션, 분석 결과 저장 |
| 뉴스 수집 | feedparser + httpx | RSS 피드 파싱 |
| 주가 데이터 | yfinance | 실시간 주가·재무 데이터 조회 |
| 비동기 | anyio, asyncio | async/sync 브릿지, 병렬 분석 |
| 런타임 | Python 3.10+, Node.js LTS | Claude Code CLI 의존 |
| 배포 | systemd | API 상시 기동 + 배치 타이머 (Raspberry Pi 4) |

### 1.3 실행 환경

- **개발**: Windows 11, Python venv, 로컬 PostgreSQL
- **운영**: Raspberry Pi 4 (64-bit Bookworm), systemd 서비스/타이머, 24/7 상시 운영
- **과금**: Claude Code 구독(Max 5x 등) 사용량에 포함 — 별도 API 토큰 과금 없음

### 1.4 의존성 (`requirements.txt`)

```
# shared
psycopg2-binary>=2.9.9

# analyzer
claude-agent-sdk>=0.1.0
feedparser>=6.0.11
httpx>=0.27.0
anyio>=4.4.0
yfinance>=0.2.0

# api
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
jinja2>=3.1.0
```

---

## 2. 디렉토리 구조

```
investment-advisor/
│
├── .env.example              # 환경변수 템플릿 (Git 포함)
├── .env                      # 실제 환경변수 (Git 제외)
├── .gitignore
├── CLAUDE.md                 # Claude Code 프로젝트 지침
├── README.md
├── requirements.txt          # Python 패키지 의존성
│
├── analyzer/                 # ── 배치 분석 모듈 ──
│   ├── __init__.py
│   ├── main.py               # 엔트리포인트: DB 초기화 → 뉴스 수집 → 파이프라인 실행 → 저장
│   ├── analyzer.py           # 2단계 파이프라인: stage1 → 모멘텀 체크 → stage2 → 결과 병합
│   ├── prompts.py            # 스테이지별 시스템 프롬프트 및 JSON 출력 스키마
│   ├── news_collector.py     # RSS 피드 수집, 카테고리별 마크다운 포맷팅
│   └── stock_data.py         # yfinance 주가 조회, 모멘텀 체크, 프롬프트용 텍스트 포맷팅
│
├── api/                      # ── FastAPI 웹서비스 모듈 ──
│   ├── __init__.py
│   ├── main.py               # FastAPI 앱 초기화, 라우터 등록, Uvicorn 실행
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── sessions.py       # 세션 CRUD API + _serialize_row() 공유 유틸
│   │   ├── themes.py         # 테마 필터/검색 API
│   │   ├── proposals.py      # 제안 필터/요약/종목분석 API
│   │   └── pages.py          # Jinja2 HTML 페이지 라우트 (대시보드, 이력 등)
│   ├── templates/
│   │   ├── base.html         # 마스터 레이아웃 (사이드바, 반응형)
│   │   ├── _macros.html      # 재사용 매크로 (proposal_card, theme_header 등)
│   │   ├── dashboard.html    # 홈: 투자 신호, 시장 요약, 테마/제안 카드
│   │   ├── sessions.html     # 세션 목록
│   │   ├── session_detail.html  # 세션 상세: 이슈·테마·시나리오·매크로
│   │   ├── themes.html       # 테마 목록 (필터링)
│   │   ├── proposals.html    # 제안 목록 (필터링)
│   │   ├── theme_history.html   # 테마 이력 추적 (신뢰도 변화)
│   │   └── ticker_history.html  # 종목 추천 이력 (액션 변화)
│   └── static/
│       └── css/style.css     # 다크 테마 스타일시트
│
├── shared/                   # ── 공용 모듈 ──
│   ├── __init__.py
│   ├── config.py             # .env 파싱, DatabaseConfig/NewsConfig/AnalyzerConfig/AppConfig
│   ├── db.py                 # 스키마 마이그레이션(v1~v5), save_analysis(), tracking 갱신
│   └── pg_setup.py           # PostgreSQL 설치 감지 및 자동 설치
│
└── _docs/                    # ── 운영 문서 ──
    ├── analysis_pipeline.md  # 분석 파이프라인 상세 문서
    ├── raspberry-pi-setup.md # 라즈베리파이 배포 매뉴얼 (OS~포트포워딩)
    ├── architecture.md       # 기술 아키텍처 문서 (본 문서)
    └── _prompts/             # 작업 요청 프롬프트 기록 (날짜별)
```

---

## 3. 데이터 흐름

### 3.1 전체 파이프라인

```
┌─────────────┐    ┌───────────────────────┐    ┌──────────────┐    ┌───────────────────────┐    ┌──────────┐
│  RSS 뉴스   │───▶│  Stage 1: 테마 발굴    │───▶│  모멘텀 체크  │───▶│  Stage 2: 종목 심층분석 │───▶│ DB 저장  │
│  수집       │    │  claude_agent_sdk      │    │  yfinance    │    │  claude_agent_sdk      │    │ PostgreSQL│
└─────────────┘    └───────────────────────┘    └──────────────┘    └───────────────────────┘    └──────────┘
```

### 3.2 단계별 상세

#### Step 0: 뉴스 수집 (`news_collector.py`)

`feedparser`로 7개 카테고리의 RSS 피드를 수집하여 마크다운 텍스트로 변환한다.

| 카테고리 | 라벨 | 주요 피드 소스 |
|----------|------|----------------|
| `global` | 글로벌 종합 | BBC World, Reuters World News |
| `finance` | 경제·금융·시장 | Reuters Business, Bloomberg, CNBC |
| `technology` | 기술·AI·반도체 | NYT Technology, Ars Technica |
| `commodities` | 에너지·원자재 | Oil Price |
| `korea` | 한국 경제 | 한경 경제, 한경 증권 |
| `early_signals` | 선행 지표·규제 | Federal Register, DigiTimes |
| `korea_early` | 한국 산업·M&A | 전자신문, 더벨 |

출력 형식:
```
### [카테고리 라벨] (N건)

  • [Source](date) Title
    Summary text (최대 500자, HTML 태그 제거)
```

#### Step 1: 테마 발굴 (`analyzer.py` → `stage1_discover_themes()`)

뉴스 텍스트 + 최근 7일 추천 이력을 Claude SDK에 전달하여 3단계 분석을 수행한다.

```python
async def stage1_discover_themes(
    news_text: str,
    date: str,
    max_turns: int = 6,
    recent_recs: list[dict] | None = None,
) -> dict:
```

**입력**: 수집된 뉴스 텍스트, 분석 날짜, 최근 추천 이력 (중복 방지용)

**출력** (JSON):
- `issues` (8~15개): 글로벌 이슈 — 카테고리, 중요도, 시계별 영향, 역사적 유사 사례
- `themes` (4~7개): 투자 테마 — 신뢰도, 시나리오(Bull/Base/Bear), 매크로 영향
- 테마당 `proposals` (10~15개): 투자 제안 — 티커, 액션, 확신도, 목표가, 발굴 유형
- `market_summary`: 시장 환경 요약
- `risk_temperature`: `high` | `medium` | `low`

**핵심 차별화 규칙**:
- 얼리시그널 종목 60%+ (2~3차 공급망, 중소형, 낮은 커버리지)
- 컨트래리안/딥밸류 10~20% (반전 촉매 보유)
- 컨센서스 20~30% (대형주 벤치마크 참조용)
- `discovery_type`: `consensus` | `early_signal` | `contrarian` | `deep_value`
- `price_momentum_check`: `already_run` | `fair_priced` | `undervalued` | `unknown`

#### Step 1.5: 모멘텀 체크 (`stock_data.py` → `fetch_momentum_batch()`)

Stage 1 추천 종목의 1개월 수익률을 yfinance로 조회하여 급등 종목을 필터링한다.

```python
def fetch_momentum_check(ticker: str, market: str) -> dict | None:
    # return_1m_pct >= +20% → "already_run" (급등, Stage 2 우선순위 하향)
    # return_1m_pct <= -10% → "undervalued" (기회, Stage 2 우선순위 상향)
    # 그 외 → "fair_priced"
```

- 최대 8개 워커로 병렬 실행 (`ThreadPoolExecutor`)
- 실패 시 해당 종목 건너뜀 (graceful degradation)

#### Step 1.5: 주가 데이터 조회 (`stock_data.py` → `fetch_multiple_stocks()`)

Stage 2 대상 종목의 실시간 재무 데이터를 yfinance로 조회한다.

```python
def fetch_stock_data(ticker: str, market: str) -> dict | None:
    # 반환: price, change_pct, high_52w, low_52w, market_cap,
    #       per, pbr, eps, dividend_yield, sector, industry 등
```

- `_normalize_ticker()`로 시장별 yfinance 티커 변환 (KRX → `.KS`, KOSDAQ → `.KQ`, TSE → `.T` 등)
- `format_stock_data_text()`로 프롬프트 삽입용 마크다운 포맷팅
- `_format_number()`로 한국어 단위 변환 (조, 억)

#### Step 2: 종목 심층분석 (`analyzer.py` → `stage2_analyze_stock()`)

5관점 심층분석을 실행한다. `asyncio.gather()`로 복수 종목을 병렬 처리한다.

```python
async def stage2_analyze_stock(
    ticker: str, asset_name: str, market: str,
    theme_context: str, date: str,
    max_turns: int = 6, stock_data_text: str = "",
) -> dict:
```

**5관점 분석**:

| 관점 | 분석 항목 |
|------|-----------|
| 펀더멘털 | 사업 구조, 매출 믹스, 3개년 재무(매출, 영업이익률, ROE, 부채비율) |
| 산업/경쟁 | 산업 성장률, 시장 규모, 동종업체 2+ 비교 |
| 모멘텀/수급 | 기술적 지표(RSI, MACD), 기관/외국인 수급, 센티먼트(-1.0~+1.0) |
| 퀀트 팩터 | Value·Momentum·Quality·Growth·Size/Liquidity (1.0~5.0점), 종합 점수 |
| 리스크/스트레스 | 3~5개 핵심 리스크, Bull/Base/Bear 목표가, 진입/청산 전략 |

**대상 종목 선정 우선순위** (`run_pipeline()` 내):
1. `early_signal` + `undervalued` 종목 우선
2. `fair_priced` 종목 차선
3. `already_run` 종목 후순위
4. `top_themes`(기본 2)개 테마 × `top_stocks_per_theme`(기본 2)개 종목

#### Step 3: DB 저장 (`db.py` → `save_analysis()`)

분석 결과를 PostgreSQL에 저장하고, tracking 테이블을 갱신한다.

```python
def save_analysis(cfg: DatabaseConfig, analysis_date: str, result: dict) -> int:
    # 1) 같은 날짜 기존 데이터 DELETE → 세션 INSERT
    # 2) 글로벌 이슈 INSERT (issue_id_map 생성)
    # 3) 테마별: 테마 INSERT → 시나리오 INSERT → 매크로 INSERT
    # 4) 제안별: 제안 INSERT → stock_analyses INSERT (있는 경우)
    # 5) _update_tracking() → theme_tracking, proposal_tracking UPSERT
    # → session_id 반환
```

### 3.3 호출 관계도

```
analyzer/main.py::main()
├── shared/db.py::init_db(cfg.db)
│   └── pg_setup.py::ensure_postgresql()
│
├── news_collector.py::collect_news(cfg.news)
│   └── feedparser.parse(url)  ×  피드 수
│
├── analyzer.py::run_full_analysis(news, date, cfg.analyzer, cfg.db)
│   └── analyzer.py::run_pipeline()  [async]
│       ├── db.py::get_recent_recommendations()     ─── 7일 중복 방지
│       │
│       ├── stage1_discover_themes()                 ─── Stage 1
│       │   ├── _format_recent_recommendations()
│       │   ├── _query_claude(STAGE1_PROMPT, STAGE1_SYSTEM)
│       │   │   └── claude_agent_sdk.query()
│       │   └── _parse_json_response()
│       │
│       ├── stock_data.py::fetch_momentum_batch()    ─── 모멘텀 체크
│       │   └── fetch_momentum_check()  ×  종목 수 (ThreadPool, 8 workers)
│       │
│       ├── stock_data.py::fetch_multiple_stocks()   ─── 주가 데이터
│       │   └── fetch_stock_data()  ×  대상 종목 (ThreadPool, 8 workers)
│       │
│       └── stage2_analyze_stock()  ×  대상 종목     ─── Stage 2 (asyncio.gather)
│           ├── stock_data.py::format_stock_data_text()
│           ├── _query_claude(STAGE2_PROMPT, STAGE2_SYSTEM)
│           │   └── claude_agent_sdk.query()
│           └── _parse_json_response()
│
├── shared/db.py::save_analysis(cfg.db, date, result)
│   ├── INSERT: sessions → issues → themes → scenarios → macros → proposals → stock_analyses
│   └── _update_tracking()  →  theme_tracking, proposal_tracking UPSERT
│
└── _print_summary(result, session_id)
```

---

## 4. 모듈별 상세

### 4.1 analyzer/ — 배치 분석 모듈

#### `main.py` — 엔트리포인트

| 함수 | 설명 |
|------|------|
| `main() -> int` | 전체 배치 실행. DB 초기화 → 뉴스 수집 → 파이프라인 → 저장 → 요약 출력. 성공 시 0, 실패 시 1 반환 |
| `_print_summary(result, session_id)` | 분석 결과 콘솔 요약 출력 (이슈, 테마, 제안, 퀀트 점수 등) |

실행: `python -m analyzer.main`

#### `analyzer.py` — 2단계 파이프라인 엔진

| 함수 | 입력 | 출력 | 설명 |
|------|------|------|------|
| `_parse_json_response(full_response)` | Claude 응답 문자열 | `dict` | `` ```json `` `` 블록 또는 raw JSON 파싱 |
| `_query_claude(prompt, system_prompt, max_turns)` | 프롬프트, 시스템 프롬프트, 최대 턴 | 응답 문자열 | `claude_agent_sdk.query()` 래퍼 |
| `_format_recent_recommendations(recent_recs)` | 최근 추천 이력 리스트 | 포맷된 문자열 | 7일 중복 방지용 텍스트 생성 |
| `stage1_discover_themes(news_text, date, max_turns, recent_recs)` | 뉴스, 날짜, 설정 | Stage 1 결과 dict | **async** — 이슈·테마·시나리오·제안 발굴 |
| `stage2_analyze_stock(ticker, asset_name, market, theme_context, date, max_turns, stock_data_text)` | 종목 정보, 테마 컨텍스트, 주가 데이터 | Stage 2 결과 dict | **async** — 5관점 심층분석 |
| `run_pipeline(news_text, date, cfg, db_cfg)` | 뉴스, 날짜, 설정 | 전체 분석 결과 dict | **async** — 전체 파이프라인 오케스트레이션 |
| `run_analysis(news_text, date, max_turns)` | 뉴스, 날짜, 턴 | Stage 1 결과 dict | 하위호환 동기 래퍼 (`anyio.run`) |
| `run_full_analysis(news_text, date, cfg, db_cfg)` | 뉴스, 날짜, 설정 | 전체 결과 dict | 동기 래퍼 (`anyio.run`) |

**핵심 코드 — 병렬 Stage 2 실행**:
```python
# run_pipeline() 내부
async def _analyze_one(proposal, theme_name):
    ticker = proposal["ticker"]
    sd_text = format_stock_data_text(all_stock_data[ticker]) if ticker in all_stock_data else ""
    result = await stage2_analyze_stock(
        ticker, proposal["asset_name"], proposal.get("market", ""),
        theme_name, date, cfg.max_turns, sd_text
    )
    return proposal, result

tasks = [_analyze_one(p, t_name) for t_name, p in targets]
results = await asyncio.gather(*tasks, return_exceptions=True)
```

#### `prompts.py` — 분석 프롬프트

| 상수 | 용도 |
|------|------|
| `SYSTEM_PROMPT_BASE` | 공통 기반: 20년 경력 글로벌 매크로 전략가 역할 정의, 데이터 품질 규칙 |
| `STAGE1_SYSTEM` | Stage 1 시스템 프롬프트 = BASE + 테마 리서치 헤드 역할 |
| `STAGE1_PROMPT` | Stage 1 사용자 프롬프트 템플릿 (`{date}`, `{news_text}`, `{recent_feedback}` 플레이스홀더) |
| `STAGE2_SYSTEM` | Stage 2 시스템 프롬프트 = BASE + 증권사 헤드 애널리스트 역할 |
| `STAGE2_PROMPT` | Stage 2 사용자 프롬프트 템플릿 (`{date}`, `{ticker}`, `{asset_name}`, `{market}`, `{theme_context}`, `{stock_data}` 플레이스홀더) |

**프롬프트 구성 원칙**:
- JSON-only 출력 강제 (마크다운 설명 없이)
- 필수 필드·타입·범위를 JSON 스키마로 명시
- 컨센서스/얼리시그널/컨트래리안 비율 가이드라인 포함
- 피어 비교 2개 이상 필수

#### `news_collector.py` — RSS 뉴스 수집

| 함수 | 입력 | 출력 | 설명 |
|------|------|------|------|
| `collect_news(cfg: NewsConfig)` | 뉴스 설정 | 마크다운 문자열 | 카테고리별 RSS 수집, HTML 태그 제거, 500자 요약 |

- 피드별 최대 `max_articles_per_feed`(기본 10)건 수집
- 파싱 오류 시 해당 피드 건너뜀 (stdout 로깅)

#### `stock_data.py` — 주가 데이터 조회

| 함수 | 입력 | 출력 | 설명 |
|------|------|------|------|
| `_normalize_ticker(ticker, market)` | 티커, 마켓코드 | yfinance 형식 티커 | 시장별 접미사 변환 (`.KS`, `.KQ`, `.T` 등) |
| `_format_number(value, currency)` | 숫자, 통화 | 포맷 문자열 | 한국어 단위(조/억) 또는 M/comma 변환 |
| `fetch_stock_data(ticker, market)` | 티커, 마켓 | `dict \| None` | 단일 종목 상세 데이터 (가격, PER, PBR, 시총 등) |
| `fetch_momentum_check(ticker, market)` | 티커, 마켓 | `dict \| None` | 1개월 수익률 + 모멘텀 태그 |
| `fetch_momentum_batch(stocks)` | 종목 리스트 | `dict[ticker, dict]` | 병렬 모멘텀 체크 (ThreadPool, 8 workers) |
| `fetch_multiple_stocks(stocks)` | 종목 리스트 | `dict[ticker, dict]` | 병렬 상세 데이터 조회 (ThreadPool, 8 workers) |
| `format_stock_data_text(data)` | 주가 데이터 dict | 마크다운 문자열 | Stage 2 프롬프트 삽입용 텍스트 포맷팅 |

**시장 코드 매핑**:
```
KRX/KOSPI  → 005930.KS    KOSDAQ/KQ → 247540.KQ
HKEX/HKG   → 1211.HK      TSE/JPX   → 6758.T
TWSE/TPE   → 2330.TW      SSE/SHA   → XXXX.SS
SZSE/SHE   → XXXX.SZ      LSE/LON   → ticker.L
FSE/XETRA  → ticker.DE    US 시장   → 접미사 없음
```

---

### 4.2 api/ — FastAPI 웹서비스

#### `main.py` — 앱 초기화

```python
app = FastAPI(title="Investment Advisor API")

@asynccontextmanager
async def lifespan(app):
    init_db(DatabaseConfig())  # 서버 시작 시 DB 스키마 마이그레이션
    yield

# 라우터 등록
app.include_router(sessions.router)
app.include_router(themes.router)
app.include_router(proposals.router)
app.include_router(pages.router)

# 정적 파일
app.mount("/static", StaticFiles(directory="api/static"))
```

실행: `python -m api.main` → `0.0.0.0:8000`

#### JSON API 라우트

##### `routes/sessions.py` — 세션 관리

| 메서드 | 경로 | 파라미터 | 응답 |
|--------|------|----------|------|
| GET | `/sessions` | `limit` (1~100, 기본 30) | 세션 목록 (issue_count, theme_count 포함) |
| GET | `/sessions/{session_id}` | — | 세션 상세: issues[], themes[] (scenarios, macro_impacts, proposals 중첩) |
| GET | `/sessions/date/{analysis_date}` | `YYYY-MM-DD` | 해당 날짜 세션 상세 |

**공유 유틸**:
```python
def _serialize_row(row: dict) -> dict:
    """RealDictRow의 date/datetime → isoformat(), Decimal → float 변환"""
```

##### `routes/themes.py` — 테마 필터/검색

| 메서드 | 경로 | 파라미터 | 응답 |
|--------|------|----------|------|
| GET | `/themes` | `limit`, `horizon`, `min_confidence`, `theme_type`, `validity` | 테마 목록 (시나리오·매크로·제안 중첩) |
| GET | `/themes/search` | `q` (필수), `limit` | ILIKE 검색 (theme_name OR description) |

##### `routes/proposals.py` — 제안 필터/요약

| 메서드 | 경로 | 파라미터 | 응답 |
|--------|------|----------|------|
| GET | `/proposals` | `limit`, `action`, `asset_type`, `conviction`, `sector` | 제안 목록 (테마 메타데이터 포함) |
| GET | `/proposals/ticker/{ticker}` | `limit` | 특정 종목 추천 이력 |
| GET | `/proposals/summary/latest` | — | 최신 세션 BUY 제안 포트폴리오 요약 |
| GET | `/proposals/{proposal_id}/stock-analysis` | — | 종목 심층분석 결과 (404 가능) |

#### HTML 페이지 라우트 (`routes/pages.py`)

| 메서드 | 경로 | 템플릿 | 설명 |
|--------|------|--------|------|
| GET | `/` | `dashboard.html` | 홈: 투자 신호, 시장 요약, 테마/제안 |
| GET | `/pages/sessions` | `sessions.html` | 세션 목록 |
| GET | `/pages/sessions/{id}` | `session_detail.html` | 세션 상세 |
| GET | `/pages/themes` | `themes.html` | 테마 목록 (필터링) |
| GET | `/pages/themes/history/{theme_key}` | `theme_history.html` | 테마 이력 추적 |
| GET | `/pages/proposals` | `proposals.html` | 제안 목록 (필터링) |
| GET | `/pages/proposals/history/{ticker}` | `ticker_history.html` | 종목 추천 이력 |

**대시보드 투자 신호 로직** (`GET /`):
```python
# 신호 유형:
# - new_buy: 오늘 새로 등장한 BUY 제안
# - action_change: hold→buy, buy→sell 등 액션 변경
# - confidence_up / confidence_down: 신뢰도 ±5% 이상 변화
# - disappeared: 이전에 있던 테마가 사라짐
```

#### 템플릿 구조

**`base.html`** — 마스터 레이아웃:
- 반응형 사이드바 내비게이션 (모바일 햄버거 메뉴)
- 콘텐츠 블록: `{% block content_header %}`, `{% block content_body %}`
- 하단: API Docs 링크 (`/docs`)

**`_macros.html`** — 재사용 매크로:

| 매크로 | 용도 |
|--------|------|
| `proposal_card_compact(p)` | 테마 내 제안 카드 (축약형: 액션 뱃지, 퀀트/센티먼트, 목표가) |
| `proposal_card_full(p)` | 제안 목록 카드 (상세형: 진입/청산 조건, 섹터, 공급망 포지션) |
| `theme_header(theme, tk)` | 테마 헤더 (신뢰도 바, tracking 뱃지, 트렌드 링크) |
| `scenario_grid(scenarios)` | Bull/Base/Bear 시나리오 카드 그리드 |
| `indicator_tags(key_indicators)` | 핵심 모니터링 지표 태그 |
| `macro_impact_table(macro_impacts)` | 매크로 영향 테이블 (Base/Worse/Better) |

---

### 4.3 shared/ — 공용 모듈

#### `config.py` — 설정 로딩

**로딩 메커니즘**:
```python
# 프로젝트 루트의 .env 파일 경로 계산
_env_path = Path(__file__).resolve().parent.parent / ".env"

# .env 파일 파싱: 각 줄을 KEY=VALUE로 분해
# os.environ.setdefault()로 로드 — 이미 설정된 환경변수가 우선
```

**설정 클래스**:

| 클래스 | 역할 | 주요 필드 |
|--------|------|-----------|
| `DatabaseConfig` | DB 접속 정보 | `host`, `port`, `dbname`, `user`, `password`, `dsn` (property) |
| `NewsConfig` | RSS 피드 설정 | `feeds` (dict[str, list[str]]), `max_articles_per_feed` (기본 10) |
| `AnalyzerConfig` | 분석 파라미터 | `max_turns`, `top_themes`, `top_stocks_per_theme`, `enable_stock_analysis`, `enable_stock_data` |
| `AppConfig` | 루트 설정 | `db`, `news`, `analyzer` (위 클래스 프로퍼티) |

**불리언 파싱 헬퍼**:
```python
def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")
```

#### `db.py` — 데이터베이스 관리

**핵심 함수**:

| 함수 | 설명 |
|------|------|
| `init_db(cfg)` | DB 존재 확인 → 생성 → 스키마 마이그레이션 (v1~v5) |
| `get_connection(cfg)` | psycopg2 커넥션 반환 (DSN 기반) |
| `save_analysis(cfg, date, result) -> int` | 분석 결과 저장 + tracking 갱신, session_id 반환 |
| `get_recent_recommendations(cfg, days=7)` | 최근 N일 추천 이력 조회 (중복 방지용) |
| `_normalize_theme_key(name)` | 테마명 → 정규화 키 (소문자, 특수문자 제거) |
| `_update_tracking(cur, date, themes, session_id)` | theme_tracking, proposal_tracking UPSERT |
| `_get_schema_version(cur)` | 현재 스키마 버전 조회 |
| `_ensure_database(cfg)` | DB 미존재 시 CREATE DATABASE |

**커넥션 관리 패턴**:
```python
conn = get_connection(cfg)
try:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(...)
    conn.commit()
finally:
    conn.close()
```
- 커넥션 풀 미사용 — 함수 단위로 열고 닫음
- API 라우트에서 `RealDictCursor`로 dict 반환

#### `pg_setup.py` — PostgreSQL 자동 설치

| 함수 | 설명 |
|------|------|
| `is_pg_installed()` | `shutil.which("psql")`로 설치 여부 확인 |
| `is_pg_running(host, port)` | psycopg2 테스트 연결 (3초 타임아웃) |
| `install_postgresql()` | OS 감지 후 자동 설치 시도 |
| `_install_linux()` | apt install → systemctl enable → 비밀번호 설정 |
| `_install_windows()` | winget → choco → 수동 설치 안내 |
| `ensure_postgresql(host, port)` | 실행 중 확인 → 미설치 시 설치 → 재확인 |

---

## 5. DB 스키마

### 5.1 테이블 관계도 (ERD)

```
                         ┌──────────────────────┐
                         │  schema_version      │
                         │  (version, applied_at)│
                         └──────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│                      analysis_sessions                           │
│  id (PK), analysis_date (UNIQUE), market_summary,               │
│  risk_temperature, data_sources, created_at                      │
└──────────┬──────────────────────────────────┬────────────────────┘
           │ 1:N                              │ 1:N
           ▼                                  ▼
┌─────────────────────┐          ┌──────────────────────────────────┐
│   global_issues     │          │      investment_themes            │
│  id (PK)            │          │  id (PK)                         │
│  session_id (FK)    │          │  session_id (FK)                 │
│  category, region   │          │  theme_name, description         │
│  title, summary     │          │  confidence_score (0.00~1.00)    │
│  importance (1~5)   │          │  time_horizon, theme_type        │
│  impact_short/mid/  │          │  theme_validity, key_indicators  │
│  long               │          │  related_issue_ids (INT[])       │
│  historical_analogue│          └──────┬──────────┬───────────────┘
└─────────────────────┘                 │ 1:N      │ 1:N      │ 1:N
                                        ▼          ▼          ▼
                              ┌──────────────┐ ┌─────────────┐ ┌────────────────────────────┐
                              │theme_scenarios│ │macro_impacts│ │  investment_proposals       │
                              │ id (PK)      │ │ id (PK)     │ │  id (PK)                   │
                              │ theme_id (FK)│ │ theme_id(FK)│ │  theme_id (FK)             │
                              │ scenario_type│ │ variable_   │ │  ticker, asset_name, market│
                              │ probability  │ │   name      │ │  action, conviction        │
                              │ description  │ │ base_case   │ │  target_allocation         │
                              │ key_         │ │ worse_case  │ │  current_price, target_*   │
                              │  assumptions │ │ better_case │ │  sentiment_score,quant_score│
                              │ market_impact│ │ unit        │ │  sector, currency          │
                              └──────────────┘ └─────────────┘ │  vendor_tier, supply_chain │
                                                               │  discovery_type            │
                                                               │  price_momentum_check      │
                                                               └──────────────┬─────────────┘
                                                                              │ 1:1
                                                                              ▼
                                                               ┌─────────────────────────────┐
                                                               │     stock_analyses           │
                                                               │  id (PK)                    │
                                                               │  proposal_id (FK)           │
                                                               │  company_overview           │
                                                               │  financial_summary (JSONB)  │
                                                               │  dcf_fair_value, dcf_wacc   │
                                                               │  industry_position          │
                                                               │  momentum_summary           │
                                                               │  risk_summary               │
                                                               │  bull_case, bear_case       │
                                                               │  factor_scores (JSONB)      │
                                                               │  report_markdown            │
                                                               └─────────────────────────────┘

──── 독립 추적 테이블 (UPSERT) ────

┌─────────────────────────────────┐    ┌────────────────────────────────────┐
│       theme_tracking            │    │       proposal_tracking            │
│  id (PK)                       │    │  id (PK)                          │
│  theme_key (UNIQUE)            │    │  (ticker, theme_key) UNIQUE       │
│  theme_name                    │    │  asset_name                       │
│  first_seen_date               │    │  first_recommended_date           │
│  last_seen_date                │    │  last_recommended_date            │
│  streak_days                   │    │  recommendation_count             │
│  appearances                   │    │  latest_action, prev_action       │
│  latest_confidence             │    │  latest_conviction                │
│  prev_confidence               │    │  latest/prev_target_price_low/high│
│  latest_theme_id (FK, SET NULL)│    │  latest_quant/sentiment_score     │
└─────────────────────────────────┘    │  latest_proposal_id (FK, SET NULL)│
                                       └────────────────────────────────────┘
```

**CASCADE 관계**: `analysis_sessions` 삭제 시 하위 모든 데이터 자동 삭제.
**tracking 테이블**: FK에 `ON DELETE SET NULL` — 세션 삭제 시 추적 이력은 유지.

### 5.2 주요 컬럼 설명

| 테이블.컬럼 | 타입 | 설명 |
|-------------|------|------|
| `analysis_sessions.analysis_date` | `DATE UNIQUE` | 하루 1세션 보장. 같은 날짜 재실행 시 DELETE → INSERT |
| `analysis_sessions.risk_temperature` | `VARCHAR(10)` | `high` \| `medium` \| `low` |
| `global_issues.importance` | `INT CHECK (1~5)` | 이슈 중요도 (5=최상) |
| `investment_themes.confidence_score` | `NUMERIC(3,2)` | 0.00~1.00 신뢰도 |
| `investment_themes.time_horizon` | `VARCHAR(20)` | `short` \| `mid` \| `long` |
| `investment_proposals.discovery_type` | `VARCHAR(20)` | `consensus` \| `early_signal` \| `contrarian` \| `deep_value` |
| `investment_proposals.price_momentum_check` | `VARCHAR(20)` | `already_run` \| `fair_priced` \| `undervalued` \| `unknown` |
| `investment_proposals.vendor_tier` | `INT` | 1=대형 리더, 2=중형 부품사, 3=니치 |
| `stock_analyses.financial_summary` | `JSONB` | 3개년 재무 데이터 |
| `stock_analyses.factor_scores` | `JSONB` | 5팩터 퀀트 점수 |
| `theme_tracking.theme_key` | `VARCHAR(200) UNIQUE` | 테마명 정규화 키 (`_normalize_theme_key()`) |
| `theme_tracking.streak_days` | `INT` | 연속 등장일 수 |
| `proposal_tracking.(ticker, theme_key)` | `UNIQUE` | 테마별 종목 추적 복합 유니크 |

### 5.3 마이그레이션 버전 이력

| 버전 | 내용 |
|------|------|
| **v1** | 기본 스키마 — `schema_version`, `analysis_sessions`, `global_issues`, `investment_themes`, `investment_proposals` |
| **v2** | 멀티에이전트 확장 — 기존 테이블 컬럼 추가 + `theme_scenarios`, `macro_impacts`, `stock_analyses` 신규 생성 |
| **v3** | 일별 추적 — `theme_tracking`, `proposal_tracking` 신규 생성 |
| **v4** | 공급망 분석 — `investment_proposals`에 `vendor_tier`, `supply_chain_position` 추가 |
| **v5** | 발굴 유형 — `investment_proposals`에 `discovery_type`, `price_momentum_check` 추가 |

**마이그레이션 실행 방식**:
```python
def init_db(cfg):
    current = _get_schema_version(cur)
    if current < 1: _migrate_to_v1(cur)
    if current < 2: _migrate_to_v2(cur)
    if current < 3: _migrate_to_v3(cur)
    if current < 4: _migrate_to_v4(cur)
    if current < 5: _migrate_to_v5(cur)
    conn.commit()
```
- 서버 시작 시(`api/main.py` lifespan) 및 배치 시작 시(`analyzer/main.py`) 자동 실행
- 각 버전에서 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 사용 — 멱등성 보장
- v2 확장 필드는 모두 NULLABLE — v1 데이터와 하위호환

---

## 6. 설정과 환경변수

### 6.1 `.env` 항목별 역할

| 변수 | 기본값 | 타입 | 설명 |
|------|--------|------|------|
| `DB_HOST` | `localhost` | string | PostgreSQL 호스트 |
| `DB_PORT` | `5432` | int | PostgreSQL 포트 |
| `DB_NAME` | `investment_advisor` | string | 데이터베이스명 |
| `DB_USER` | `postgres` | string | DB 사용자 |
| `DB_PASSWORD` | `postgres` | string | DB 비밀번호 |
| `MAX_TURNS` | `2` | int | Claude SDK 최대 턴 수 (Stage 1·2 공통) |
| `TOP_THEMES` | `2` | int | Stage 2 심층분석 대상 상위 테마 수 |
| `TOP_STOCKS_PER_THEME` | `2` | int | 각 테마당 심층분석할 종목 수 |
| `ENABLE_STOCK_ANALYSIS` | `true` | bool | Stage 2 활성화 스위치 (`false`면 Stage 1만 저장) |
| `ENABLE_STOCK_DATA` | `true` | bool | yfinance 주가 조회 스위치 (`false`면 Claude 추정) |

### 6.2 조절 가능한 파라미터

| 파라미터 | 영향 | 권장값 |
|----------|------|--------|
| `MAX_TURNS=2` | Claude SDK 대화 턴 수. JSON-only 응답이므로 2면 충분 | 2 |
| `TOP_THEMES=2` | Stage 2 분석 범위. 증가 시 분석 시간·비용 비례 증가 | 1~3 |
| `TOP_STOCKS_PER_THEME=2` | 테마당 종목 수. 2×2=4종목이 기본 | 1~3 |
| `ENABLE_STOCK_ANALYSIS=false` | Stage 2 비활성화 — Stage 1 결과만 저장 (빠른 실행) | true |
| `ENABLE_STOCK_DATA=false` | yfinance 미사용 — Claude 학습 데이터 기반 추정 | true |

### 6.3 로딩 우선순위

```
환경변수 (OS) > .env 파일 > 코드 기본값
```

`os.environ.setdefault()` 사용으로 이미 설정된 환경변수가 `.env`보다 우선한다.

---

## 7. 배포 구성

### 7.1 systemd 서비스/타이머 (Raspberry Pi 4)

프로젝트에 미리 만들어진 서비스 파일은 없으며, `_docs/raspberry-pi-setup.md`의 섹션 7에서 수동 생성 템플릿을 제공한다.

#### API 서버 (상시 기동)

파일: `/etc/systemd/system/investment-advisor-api.service`

```ini
[Unit]
Description=Investment Advisor API (FastAPI)
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
User=dzp
WorkingDirectory=/home/dzp/dzp-main/program/investment-advisor
EnvironmentFile=/home/dzp/dzp-main/program/investment-advisor/.env
Environment=PATH=.../venv/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=.../venv/bin/python -m api.main
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- `Restart=always` + `RestartSec=5` — 크래시 시 5초 후 자동 재시작
- `EnvironmentFile`로 `.env` 환경변수 로드
- `0.0.0.0:8000`에서 수신

#### 분석 배치 (원샷)

파일: `/etc/systemd/system/investment-advisor-analyzer.service`

```ini
[Service]
Type=oneshot
ExecStart=.../venv/bin/python -m analyzer.main
```

#### 일일 타이머

파일: `/etc/systemd/system/investment-advisor-analyzer.timer`

```ini
[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

- `OnCalendar=*-*-* 03:00:00` — 매일 새벽 3시(KST) 실행
- `Persistent=true` — 시스템이 꺼져 있었으면 부팅 후 즉시 실행

#### 활성화 명령

```bash
sudo systemctl enable --now investment-advisor-api.service       # API 상시 기동
sudo systemctl enable --now investment-advisor-analyzer.timer    # 매일 03:00 배치
```

### 7.2 Raspberry Pi 4 운영 환경

`_docs/raspberry-pi-setup.md`에 665줄 분량의 상세 매뉴얼이 포함되어 있다.

**주요 체크리스트**:

| 단계 | 내용 |
|------|------|
| 1. 하드웨어 | RPi 4 2GB+, microSD 16GB+, 공식 5V/3A 전원 |
| 2. 시스템 | apt 업데이트, swap 2GB 확장, 타임존/로캘 설정 |
| 3. Python | Bookworm 기본 3.11 사용 (Bullseye는 pyenv) |
| 4. PostgreSQL | apt install → DB 생성 → 접속 테스트 |
| 5. Node.js + Claude | NVM 설치 → `npm install -g @anthropic-ai/claude-code` → `claude login` |
| 6. 프로젝트 | git clone → venv → pip install |
| 7. systemd | 서비스/타이머 등록 (위 참조) |
| 8. 네트워크 | UFW 방화벽, 라우터 포트포워딩, DDNS, Nginx 리버스 프록시 + Let's Encrypt |
| 9. 운영 | 로그 확인 (`journalctl`), 백업, 배포 절차 |
| 10. 트러블슈팅 | 12개 Q&A 항목 |

### 7.3 개발 환경 실행

```bash
# Windows
python -m venv venv && venv\Scripts\activate && pip install -r requirements.txt
copy .env.example .env   # DB 접속 정보 수정

# 배치 실행
python -m analyzer.main

# API 서버 (개발)
python -m api.main
# 또는
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

- 웹 UI: `http://localhost:8000`
- Swagger 문서: `http://localhost:8000/docs`
