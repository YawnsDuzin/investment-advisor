# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

투자 분석 시스템. 매일 RSS 뉴스를 수집하고 Claude Code SDK(`claude-agent-sdk`)로 멀티스테이지 분석을 수행하여 투자 테마/제안을 PostgreSQL에 저장한다. FastAPI 웹서비스로 저장된 분석 결과를 조회할 수 있다.

커스텀 에이전트(`stock_analyst_agents`)의 프롬프트를 SDK `query()` 호출로 포팅하여, 테마 발굴 → 시나리오/매크로 분석 → 종목 심층분석의 2단계 파이프라인으로 동작한다.

과금은 Claude Code 구독(Max 5x 등) 사용량에 포함되며, 별도 API 토큰 과금이 없다.

## Commands

```bash
# 설치 (Windows)
python -m venv venv && venv\Scripts\activate && pip install -r requirements.txt

# 설치 (Linux/라즈베리파이)
python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt

# 환경변수 설정
copy .env.example .env   # Windows
cp .env.example .env     # Linux/Mac
# → .env 파일의 DB 접속 정보를 환경에 맞게 수정

# 분석 실행 (배치)
python -m analyzer.main

# API 서버 (개발)
python -m api.main
# 또는: uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# 라즈베리파이 자동 실행
sudo systemctl enable --now investment-advisor.timer
```

- 웹 UI: `http://localhost:8000`
- Swagger 문서: `http://localhost:8000/docs`
- PostgreSQL 필요 (Windows는 별도 설치). DB 접속 정보는 `.env` 파일로 관리.
- Claude Code CLI 필요: `npm install -g @anthropic-ai/claude-code` → `claude login`

## Environment Variables

`.env.example`을 `.env`로 복사하여 사용. `shared/config.py`가 모듈 로드 시 자동으로 `.env`를 파싱한다.

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `DB_HOST` | `localhost` | PostgreSQL 호스트 |
| `DB_PORT` | `5432` | PostgreSQL 포트 |
| `DB_NAME` | `investment_advisor` | 데이터베이스명 |
| `DB_USER` | `postgres` | DB 사용자 |
| `DB_PASSWORD` | `postgres` | DB 비밀번호 |

- `.env`는 `.gitignore`에 포함 — Git에 커밋되지 않음
- `.env.example`은 Git에 포함 — 플레이스홀더 값으로 구성
- 환경변수가 이미 설정되어 있으면 `.env`보다 환경변수가 우선 (`os.environ.setdefault` 사용)

## Architecture

모노레포 구조로 `analyzer/`(배치)와 `api/`(웹서버)가 `shared/`를 공유한다.

### 데이터 흐름

```
[RSS 뉴스 수집] → [Stage 1: 이슈/테마/시나리오/매크로/제안] → [Stage 2: 핵심 종목 심층분석] → [DB 저장 + tracking 갱신]
                    claude_agent_sdk.query()                    claude_agent_sdk.query()
```

- **Stage 1**: 뉴스 → 8~15개 이슈(시계별 영향) → 4~7개 테마(시나리오·매크로) → 테마당 2~4개 투자 제안
- **Stage 2**: 상위 테마의 stock 타입 buy/sell 종목에 대해 5관점 심층분석 (펀더멘털·산업·모멘텀·퀀트·리스크)
- `AnalyzerConfig`로 심층분석 대상 수(`top_themes`, `top_stocks_per_theme`) 및 활성화 여부 조절 가능
- 저장 시 `theme_tracking`, `proposal_tracking` 테이블 자동 갱신 (연속성 추적)

### analyzer/ — 분석 파이프라인 (배치)
- `main.py` — 엔트리포인트. `run_full_analysis()` 호출 → DB 저장 → 요약 출력
- `analyzer.py` — `stage1_discover_themes()`, `stage2_analyze_stock()`, `run_pipeline()`. 하위호환용 `run_analysis()` 유지
- `prompts.py` — 스테이지별 시스템 프롬프트 및 JSON 출력 템플릿
- `news_collector.py` — `feedparser`로 RSS 피드 수집, 카테고리별 마크다운 텍스트 생성

### api/ — FastAPI 웹서비스 (상시 기동)
- `routes/sessions.py` — 세션 목록/상세/날짜별 조회. `_serialize_row()` 공유 유틸.
- `routes/themes.py` — 테마 목록 (horizon, confidence, type, validity 필터), 키워드 검색. 시나리오·매크로·제안 중첩 반환.
- `routes/proposals.py` — 제안 목록 (action, asset_type, conviction, sector 필터), 티커별 이력, 최신 포트폴리오 요약, `/{proposal_id}/stock-analysis` 엔드포인트.
- `routes/pages.py` — Jinja2 HTML 페이지 라우트. 투자 신호, tracking 뱃지, 테마·종목 히스토리 페이지 포함.
- `templates/` — 다크 테마 UI. base, dashboard, sessions, session_detail, themes, proposals, theme_history, ticker_history.

### shared/ — 공용 모듈
- `config.py` — `.env` 파일 자동 로드, `DatabaseConfig`(환경변수 기반), `NewsConfig`, `AnalyzerConfig`, `AppConfig`
- `db.py` — `schema_version` 기반 자동 마이그레이션(v1~v3), `save_analysis()` + tracking 갱신, `get_connection()`
- `pg_setup.py` — PostgreSQL 설치 감지 및 자동 설치 (Linux apt, Windows winget/choco)

## DB Schema

`schema_version` 테이블로 버전 관리. `init_db()` 호출 시 자동 마이그레이션.

**테이블 관계 (CASCADE):**
```
analysis_sessions → global_issues
                  → investment_themes → theme_scenarios
                                      → macro_impacts
                                      → investment_proposals → stock_analyses

theme_tracking (독립, UPSERT로 갱신)
proposal_tracking (독립, UPSERT로 갱신)
```

- `analysis_sessions.analysis_date`는 UNIQUE — 하루 1세션. 같은 날짜 재실행 시 DELETE 후 재생성.
- v2 확장 필드는 모두 NULLABLE — v1 데이터와 하위호환.
- `stock_analyses.financial_summary`와 `factor_scores`는 JSONB 타입.
- `theme_tracking.theme_key`는 테마명 정규화 키 (소문자, 공백·하이픈·가운뎃점 제거). `_normalize_theme_key()` 함수 사용.
- `proposal_tracking`은 `(ticker, theme_key)` UNIQUE — 테마별 종목 추적.

## Key Conventions

- 언어: 코드 주석·출력 메시지는 한국어
- 환경 설정: `.env` 파일로 관리. `config.py`가 모듈 로드 시 자동 파싱. 외부 라이브러리 없이 순수 Python 구현.
- Claude SDK 응답은 JSON-only로 파싱 (````json` 블록 또는 raw JSON). `_parse_json_response()` 공통 함수 사용.
- 동기/비동기 브릿지: `analyzer.py` 내부는 async, 외부 호출은 `anyio.run()`으로 동기 래핑
- DB 연결은 함수 단위로 열고 닫음 (커넥션 풀 미사용). `get_connection(cfg)` → `try/finally conn.close()`
- API 라우트에서 DB 조회 시 `RealDictCursor` 사용하여 dict 반환. `_serialize_row()`로 date/Decimal 변환.
- 새 마이그레이션 추가 시: `SCHEMA_VERSION` 증가, `_migrate_to_vN()` 함수 생성, `init_db()`에 `if current < N` 추가
- 프론트엔드에서 새 필드 표시 시 `{% if field %}` 가드 필수 — 이전 버전 데이터에서 NULL일 수 있음
