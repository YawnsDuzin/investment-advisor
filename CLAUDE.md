# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

투자 분석 시스템. 매일 RSS 뉴스를 수집하고 Claude Code SDK(`claude-agent-sdk`)로 멀티스테이지 분석을 수행하여 투자 테마/제안을 PostgreSQL에 저장한다. FastAPI 웹서비스로 저장된 분석 결과를 조회할 수 있다.

커스텀 에이전트(`stock_analyst_agents`)의 프롬프트를 SDK `query()` 호출로 포팅하여, 테마 발굴 → 시나리오/매크로 분석 → 종목 심층분석의 2단계 파이프라인으로 동작한다.

과금은 Claude Code 구독(Max 5x 등) 사용량에 포함되며, 별도 API 토큰 과금이 없다.

## Tech Stack

- **AI**: Claude Code SDK (`claude-agent-sdk`) — 멀티스테이지 분석 파이프라인
- **Backend**: FastAPI + Uvicorn (REST API + HTML 서빙)
- **Template**: Jinja2 (다크 테마 UI)
- **Database**: PostgreSQL + psycopg2 (스키마 자동 마이그레이션 v1~v22)
- **News**: feedparser + httpx (RSS 수집)
- **Stock Data**: yfinance (해외 주가/재무 데이터) + pykrx (한국 주식 크로스체크/폴백)
- **Async**: anyio (async/sync 브릿지)
- **Runtime**: Python 3.10+, Node.js LTS (Claude Code CLI 의존)
- **Auth**: JWT (python-jose) + bcrypt (passlib) — httpOnly 쿠키 기반, RBAC (Admin/Moderator/User)
- **Deploy**: systemd (API 상시 기동 + 배치 타이머), Raspberry Pi 4 24/7 운영 가능

## Project Structure

```
investment-advisor/
├── shared/              ← 공용: config(.env 로드), db(마이그레이션+저장), logger(DB 로그), pg_setup(자동 설치), tier_limits(구독 티어 제한)
├── analyzer/            ← 배치: main(엔트리) → news_collector(RSS) → stock_data(주가조회) → analyzer(2단계) → recommender(Top Picks) → price_tracker(수익률추적) → checkpoint(중단점복구) → krx_data(KRX수급/공매도)
├── api/                 ← 웹: main(FastAPI) → routes/(pages, sessions, themes, proposals, stocks, chat, education, inquiry, admin, auth, user_admin, watchlist, track_record)
│   ├── auth/            ← JWT 인증 모듈: dependencies, jwt_handler, password, models
│   ├── chat_engine.py   ← Claude SDK 기반 테마 채팅 엔진
│   ├── education_engine.py ← Claude SDK 기반 투자 교육 AI 튜터 엔진
│   ├── templates/       ← Jinja2 HTML (다크 테마 + 우측 상단 드롭다운 메뉴) + _macros.html(공통 매크로)
│   └── static/css/
└── _docs/               ← 운영 문서 (분석 파이프라인, 라즈베리파이 매뉴얼)
    ├── _prompts/        ← 작업 요청 프롬프트 기록 (날짜별)
    └── _exception/      ← 분석/운영 예외·장애 관리 대장 (README.md가 이슈 인덱스)
```

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

# 라즈베리파이 24/7 운영 (systemd)
sudo systemctl enable --now investment-advisor-api.service       # API 상시 기동
sudo systemctl enable --now investment-advisor-analyzer.timer    # 매일 03:00 배치
```

- 웹 UI: `http://localhost:8000`
- Swagger 문서: `http://localhost:8000/docs`
- PostgreSQL 필요 (Windows는 별도 설치). DB 접속 정보는 `.env` 파일로 관리.
- Claude Code CLI 필요: `npm install -g @anthropic-ai/claude-code` → `claude login`
- 라즈베리파이 배포 상세: `_docs/raspberry-pi-setup.md` (OS 설치부터 포트포워딩까지)
- 라즈베리파이에서 LAN 내 DB 직접 접속이 필요하면 UFW에서 5432 포트를 열고 `postgresql.conf`/`pg_hba.conf` 를 수정한다 (`sudo ufw allow 5432/tcp && sudo ufw reload`). 절차·보안 주의는 `_docs/raspberry-pi-setup.md` 4.5절 참고. **5432는 공유기 포트포워딩 금지** — 외부 접근은 SSH 터널 사용.

## Environment Variables

`.env.example`을 `.env`로 복사하여 사용. `shared/config.py`가 모듈 로드 시 자동으로 `.env`를 파싱한다.

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `DB_HOST` | `localhost` | PostgreSQL 호스트 |
| `DB_PORT` | `5432` | PostgreSQL 포트 |
| `DB_NAME` | `investment_advisor` | 데이터베이스명 |
| `DB_USER` | `postgres` | DB 사용자 |
| `DB_PASSWORD` | `postgres` | DB 비밀번호 |
| `MAX_TURNS` | `1` | Claude SDK 최대 턴 수 (Stage 1·2 공통) |
| `TOP_THEMES` | `2` | Stage 2 심층분석 대상 상위 테마 수 |
| `TOP_STOCKS_PER_THEME` | `2` | 각 테마당 심층분석할 종목 수 |
| `ENABLE_STOCK_ANALYSIS` | `true` | Stage 2(종목 심층분석) 활성화 스위치 (true/false) |
| `ENABLE_STOCK_DATA` | `true` | yfinance 실시간 주가 데이터 조회 스위치 (true/false) |
| `MODEL_ANALYSIS` | `claude-sonnet-4-6` | 분석(Stage 1·2)에 사용할 모델 |
| `MODEL_TRANSLATE` | `claude-haiku-4-5-20251001` | 번역에 사용할 모델 (Haiku로 비용 최소화) |
| `QUERY_TIMEOUT` | `900` | Claude SDK 단일 쿼리 타임아웃 (초). 서버 부하 시 증가 필요 |
| `MIN_NEW_NEWS` | `5` | 이전 세션 대비 신규 뉴스가 이 수 미만이면 분석 스킵 |
| `MAX_ARTICLES_PER_FEED` | `5` | RSS 피드당 최대 수집 기사 수 |
| `KRX_ID` | (없음) | data.krx.co.kr 로그인 ID (pykrx 1.2.7+ 필요) |
| `KRX_PW` | (없음) | data.krx.co.kr 로그인 비밀번호 |
| `AUTH_ENABLED` | `false` | 인증 시스템 활성화 스위치 (false면 기존 동작 유지) |
| `JWT_SECRET_KEY` | `INSECURE_DEFAULT_...` | JWT 서명 키 (프로덕션 반드시 변경) |
| `JWT_ALGORITHM` | `HS256` | JWT 알고리즘 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Access Token 만료 (분) |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `30` | Refresh Token 만료 (일) |
| `ADMIN_EMAIL` | `admin@example.com` | 최초 Admin 이메일 |
| `ADMIN_PASSWORD` | `changeme123` | 최초 Admin 비밀번호 (반드시 변경) |
| `COOKIE_SECURE` | `false` | HTTPS 전용 쿠키 (프로덕션: true) |

- `.env`는 `.gitignore`에 포함 — Git에 커밋되지 않음
- `.env.example`은 Git에 포함 — 플레이스홀더 값으로 구성
- 환경변수가 이미 설정되어 있으면 `.env`보다 환경변수가 우선 (`os.environ.setdefault` 사용)

## Architecture

모노레포 구조로 `analyzer/`(배치)와 `api/`(웹서버)가 `shared/`를 공유한다.

### 데이터 흐름

```
[RSS 뉴스 수집] → [번역] → [Stage 1: 이슈/테마/시나리오/매크로/제안] → [주가 데이터 조회] → [Stage 2: 핵심 종목 심층분석] → [KRX 확장 데이터] → [DB 저장 + tracking 갱신] → [Stage 3: Top Picks] → [Stage 4: 가격추적]
                  Haiku     claude_agent_sdk.query()              yfinance             claude_agent_sdk.query()        krx_data(pykrx)                                           recommender         price_tracker
```

- **Stage 1**: 뉴스 → 8~15개 이슈(시계별 영향) → 4~7개 테마(시나리오·매크로) → 테마당 10~15개 투자 제안
  - 최근 7일 추천 이력 피드백으로 중복 추천 방지
  - 컨센서스/얼리시그널/컨트래리안/딥밸류 분류 (`discovery_type`)
  - 주가 반영도 태깅 (`price_momentum_check`)
- **모멘텀 체크**: Stage 1 추천 종목의 기간별 수익률(1m/3m/6m/1y) 조회. 한국 주식은 pykrx 우선, 해외는 yfinance 사용. 급등(1m +20% 또는 3m +40%) 종목 필터링. 동시에 **모든 종목의 `current_price`를 실시간 가격으로 설정** (한국: pykrx 크로스체크, 해외: yfinance. 실패 시 개별 재조회, 그래도 실패 시 NULL). `price_source` 태깅으로 데이터 출처 추적
- **주가 데이터**: Stage 2 대상 종목의 현재가/PER/PBR/시총 등을 yfinance로 실시간 조회 (ENABLE_STOCK_DATA로 on/off)
- **Stage 2**: 실제 주가 데이터 + 5관점 심층분석 (펀더멘털·산업·모멘텀·퀀트·리스크)
  - 급등 종목보다 미반영 종목(early_signal/undervalued) 우선 선정
- `AnalyzerConfig`로 심층분석 대상 수(`top_themes`, `top_stocks_per_theme`) 및 활성화 여부 조절 가능
- 저장 시 `theme_tracking`, `proposal_tracking` 테이블 자동 갱신 (연속성 추적)

### analyzer/ — 분석 파이프라인 (배치)
- `main.py` — 엔트리포인트. `run_full_analysis()` 호출 → DB 저장 → 요약 출력
- `analyzer.py` — `stage1_discover_themes()`, `stage2_analyze_stock()`, `run_pipeline()`. 하위호환용 `run_analysis()` 유지
- `prompts.py` — 스테이지별 시스템 프롬프트 및 JSON 출력 템플릿
- `news_collector.py` — `feedparser`로 RSS 피드 수집, 카테고리별 마크다운 텍스트 생성
- `stock_data.py` — `yfinance` + `pykrx`(한국 주식 크로스체크/폴백)로 주가/재무 데이터 조회, 기간별 수익률(1m/3m/6m/1y) 모멘텀 체크(`current_price` 포함 반환), 프롬프트 삽입용 텍스트 포맷팅
- `recommender.py` — Stage 3 Top Picks 추천 엔진. 룰 기반 스코어링(`compute_rule_based_picks()`) + 선택적 AI 재정렬(`ai_rerank_picks()`). `RecommendationConfig`로 가중치·다양성 제약 조절
- `price_tracker.py` — Stage 4 추천 후 실제 수익률 추적. `entry_price` 확정 → 주기적 가격 스냅샷 → `post_return_*_pct` 갱신 (v19)
- `checkpoint.py` — 파이프라인 중단점 저장/복구. 스테이지별 결과를 JSON 파일로 저장하여 실패 시 마지막 성공 스테이지부터 재개. 뉴스 핑거프린트 검증
- `krx_data.py` — KRX 확장 데이터 수집. pykrx로 투자자별 매매동향(외국인/기관), 공매도 잔고, 국채 금리 조회 (v20)

### api/ — FastAPI 웹서비스 (상시 기동)
- `routes/sessions.py` — 세션 목록/상세/날짜별 조회. `_serialize_row()` 공유 유틸.
- `routes/themes.py` — 테마 목록 (horizon, confidence, type, validity 필터), 키워드 검색. 시나리오·매크로·제안 중첩 반환.
- `routes/proposals.py` — 제안 목록 (action, asset_type, conviction, sector 필터), 티커별 이력, 최신 포트폴리오 요약, `/{proposal_id}/stock-analysis` 엔드포인트.
- `routes/track_record.py` — 트랙레코드 API (`/api/track-record`). 과거 추천 성과 요약.
- `routes/chat.py` — 테마 채팅 세션 CRUD + 메시지 전송. Claude SDK로 테마 맥락 기반 대화. 인증 필수(`get_current_user_required`).
- `routes/admin.py` — 관리자 페이지. 분석 실행/중지, SSE 실시간 로그 스트리밍, 뉴스 한글 번역. Admin 역할 필수(`require_role("admin")`).
- `routes/auth.py` — 회원가입/로그인/로그아웃/토큰 갱신/비밀번호 변경. Form 기반 POST + httpOnly 쿠키. AJAX 요청 시 JSON 응답 지원 (`X-Requested-With` 헤더 감지).
- `routes/user_admin.py` — 사용자 관리 CRUD (목록/역할변경/활성화/비밀번호초기화/삭제). Admin+Moderator 접근.
- `routes/watchlist.py` — 개인화 API. 관심 종목 워치리스트 CRUD, 알림 구독(테마/종목) CRUD, 알림 목록/읽음 처리, 제안 메모 저장/삭제. 인증 필수.
- `routes/stocks.py` — 종목 기초정보 API. yfinance로 주가/재무 데이터 온디맨드 조회, 1시간 캐싱.
- `routes/pages.py` — Jinja2 HTML 페이지 라우트. 대시보드, tracking 뱃지, 테마·종목 히스토리, 워치리스트, 알림, 프로필, 채팅, 관리자 페이지. `_base_ctx()`로 인증 컨텍스트 + 알림 수 주입. 커스텀 Jinja2 필터(`nl_numbered`, `fmt_price`) 등록.
- `auth/` — JWT 인증 모듈. `dependencies.py`(Depends 팩토리), `jwt_handler.py`(토큰 발급/검증), `password.py`(bcrypt), `models.py`(Pydantic 모델).
- `routes/education.py` — 투자 교육 API. 토픽 목록/상세, 교육 채팅 세션 CRUD, AI 튜터 메시지 전송. 티어별 일일 턴 제한(`EDU_CHAT_DAILY_TURNS`). 인증 필수.
- `routes/inquiry.py` — 고객 문의 게시판. 문의 CRUD, 답변/댓글, 상태 관리(open→answered→closed). 카테고리: general/bug/feature. `is_private` 플래그로 비공개 문의 지원. Admin/Moderator만 답변·상태 변경 가능.
- `chat_engine.py` — Claude Agent SDK 기반 테마 채팅 엔진. 테마 컨텍스트를 시스템 프롬프트에 주입하여 대화.
- `education_engine.py` — Claude SDK 기반 투자 교육 AI 튜터. 토픽별 커리큘럼을 시스템 프롬프트에 주입하여 대화형 학습 제공.
- `templates/` — 다크 테마 UI. base(우측 상단 유저 드롭다운 + 알림 배지 + 401 자동 갱신 인터셉터), landing, pricing, dashboard, sessions, session_detail, themes, proposals, theme_history, ticker_history, track_record, watchlist, notifications, profile, chat_list, chat_room, education(topic/chat_list/chat_room), inquiry(list/detail/new), admin, admin_audit_logs, login, register, user_admin.

### shared/ — 공용 모듈
- `config.py` — `.env` 파일 자동 로드, `DatabaseConfig`, `NewsConfig`, `AnalyzerConfig`, `RecommendationConfig`(Top Picks 가중치·다양성), `AuthConfig`, `AppConfig`
- `db.py` — `schema_version` 기반 자동 마이그레이션(v1~v22), `save_analysis()` + `_validate_proposal()` 검증 + tracking 갱신 + 구독 알림 생성(`_generate_notifications()`), `get_recent_recommendations()`, `get_connection()`
- `logger.py` — 범용 DB 로그 시스템. `init_logger(db_cfg)` → `start_run()` / `finish_run()`으로 실행 단위 추적. `get_logger(source)`로 콘솔+DB 동시 로깅. `app_runs`/`app_logs` 테이블 사용 (v18)
- `pg_setup.py` — PostgreSQL 설치 감지 및 자동 설치 (Linux apt, Windows winget/choco)
- `tier_limits.py` — 구독 티어별 기능 제한(워치리스트 수, 구독 수, 일일 분석 수, 교육 채팅 턴 수, 테마 열람 수). 프론트엔드·백엔드 공통 소스

## DB Schema

`schema_version` 테이블로 버전 관리. `init_db()` 호출 시 자동 마이그레이션 (현재 v22).

**테이블 관계 (CASCADE):**
```
analysis_sessions → global_issues
                  → investment_themes → theme_scenarios
                                      → macro_impacts
                                      → investment_proposals → stock_analyses
                  → news_articles (v7, 뉴스 원문 저장, v8에서 title_ko 한글 번역 추가)
                  → user_notifications (v12, 구독 알림 이력)
                  → daily_top_picks (v15, 일별 Top Picks 순위)

users → refresh_tokens (v11, CASCADE)
     → theme_chat_sessions (v11, user_id FK, SET NULL)
     → user_watchlist (v12, 관심 종목)
     → user_subscriptions (v12, 테마/종목 알림 구독)
     → user_notifications (v12, 알림 이력)
     → user_proposal_memos (v12, 제안 메모)
     → admin_audit_logs (v17, actor/target SET NULL, 이메일 denormalize)

theme_chat_sessions → theme_chat_messages (v6, 테마 기반 채팅)

education_topics → education_chat_sessions (v21, 투자 교육 토픽)
                 → education_chat_messages (v21, 교육 채팅)

inquiries → inquiry_replies (v22, 고객 문의/답변)

theme_tracking (독립, UPSERT로 갱신)
proposal_tracking (독립, UPSERT로 갱신)
app_runs → app_logs (v18, 범용 실행 로그)
```

- `analysis_sessions.analysis_date`는 UNIQUE — 하루 1세션. 같은 날짜 재실행 시 DELETE 후 재생성.
- v2 확장 필드는 모두 NULLABLE — v1 데이터와 하위호환.
- `stock_analyses.financial_summary`와 `factor_scores`는 JSONB 타입.
- `theme_tracking.theme_key`는 테마명 정규화 키 (소문자, 공백·하이픈·가운뎃점 제거). `_normalize_theme_key()` 함수 사용.
- `proposal_tracking`은 `(ticker, theme_key)` UNIQUE — 테마별 종목 추적.
- `investment_proposals.price_source`는 가격 데이터 출처 (`yfinance_realtime` / `yfinance_close` / `pykrx` / `pykrx_crosscheck` / NULL). v10 추가.
- `investment_proposals.return_1m/3m/6m/1y_pct`는 기간별 수익률(%). v14 추가. 한국 주식은 pykrx, 해외는 yfinance history 기반.
- `user_watchlist`는 `(user_id, ticker)` UNIQUE — 사용자별 관심 종목.
- `user_subscriptions`는 `(user_id, sub_type, sub_key)` UNIQUE — `sub_type`은 `'ticker'` 또는 `'theme'`.
- `user_notifications`는 분석 저장(`save_analysis()`) 시 구독 매칭으로 자동 생성. `is_read` 인덱스로 읽지 않은 알림 빠른 조회.
- `user_proposal_memos`는 `(user_id, proposal_id)` UNIQUE — UPSERT로 저장/수정.
- `investment_themes.theme_key`(v13) — AI 생성 영문 키. 테마 히스토리 연결에 사용.
- `daily_top_picks`는 `(analysis_date, rank)` UNIQUE — 룰 기반 스코어(`score_rule`) + AI 재정렬(`score_final`) + 근거/리스크 텍스트. v15 추가.
- `users.tier`(v16) — 구독 티어 (`free`/`pro`/`premium`), `tier_expires_at`로 만료 관리.
- `admin_audit_logs`(v17) — 관리자 감사 로그. `action`: tier_change/role_change/status_change/password_reset/user_delete. actor/target 이메일 denormalize로 계정 삭제 후에도 이력 유지.
- `app_runs`/`app_logs`(v18) — 범용 실행 로그. `run_type`별 실행 이력 + 상세 로그. `shared/logger.py`가 사용.
- `investment_proposals.entry_price`/`post_return_*_pct`(v19) — 추천 후 실제 수익률 추적. `entry_price` 확정 → `proposal_price_snapshots` 테이블에 일별 가격 스냅샷(snapshot_date, price, price_source) 누적 → 1m/3m/6m/1y 실제 수익률 갱신. `price_tracker.py`가 사용.
- `investment_proposals.foreign_net_buy_signal`/`squeeze_risk`/`index_membership`/`foreign_ownership_pct`(v20) — KRX 확장 데이터. 외국인 순매수 신호, 숏스퀴즈 위험도, 주요 지수 편입, 외국인 보유비율. `krx_data.py`가 수집.
- `education_topics`(v21) — 투자 교육 커리큘럼. 6개 카테고리(basics/analysis/risk/macro/practical/stories), slug/title/content/examples(JSONB)/difficulty(beginner/intermediate/advanced). 시드 데이터 26개 토픽 자동 삽입 (v21에서 11개, v24에서 신규 15개 추가).
- `education_chat_sessions`/`education_chat_messages`(v21) — 교육 AI 튜터 채팅. user_id + topic_id FK. KST 기준 일일 턴 제한 적용.
- `inquiries`/`inquiry_replies`(v22) — 고객 문의 게시판. category(general/bug/feature), status(open/answered/closed), `is_private` 비공개 플래그. `user_email` denormalize. Admin/Moderator만 답변·상태 변경.

## Key Conventions

- 언어: 코드 주석·출력 메시지는 한국어
- 환경 설정: `.env` 파일로 관리. `config.py`가 모듈 로드 시 자동 파싱. 외부 라이브러리 없이 순수 Python 구현.
- Claude SDK 응답은 JSON-only로 파싱 (````json` 블록 또는 raw JSON). `_parse_json_response()` 공통 함수 사용.
- 동기/비동기 브릿지: `analyzer.py` 내부는 async, 외부 호출은 `anyio.run()`으로 동기 래핑
- DB 연결은 함수 단위로 열고 닫음 (커넥션 풀 미사용). `get_connection(cfg)` → `try/finally conn.close()`
- API 라우트에서 DB 조회 시 `RealDictCursor` 사용하여 dict 반환. `_serialize_row()`로 date/Decimal 변환.
- 새 마이그레이션 추가 시: `SCHEMA_VERSION` 증가, `_migrate_to_vN()` 함수 생성, `init_db()`에 `if current < N` 추가
- 프론트엔드에서 새 필드 표시 시 `{% if field %}` 가드 필수 — 이전 버전 데이터에서 NULL일 수 있음
- Stage 1의 `target_price_low/high`는 AI 추정치(학습 데이터 기반)로 실제 시세와 괴리가 클 수 있음. Stage 2 분석 종목만 신뢰할 수 있는 목표가를 가짐
- `current_price`는 반드시 실시간 데이터(yfinance/pykrx)만 사용 — AI 추정 가격은 `_validate_proposal()`에서 자동 제거 (v10)
- DB 저장 시 `upside_pct`는 `(target_price_low - current_price) / current_price * 100`으로 재계산됨 (`shared/db.py`). 현재가 없으면 NULL
- 가격 표시 시 `fmt_price` Jinja2 필터 사용 — 통화 기호(₩/$€¥£) + 천 단위 쉼표, KRW/JPY는 소수점 제거
- 번호 목록(①②③) 표시 시 `nl_numbered` Jinja2 필터 사용 — 원문자 앞에 `<br>` 삽입
- `base.html` 레이아웃: 좌측 sidebar(네비게이션만) + 우측 상단 드롭다운(유저 메뉴/알림/관리). 로그인 시 `content-header` 우측에 알림 아이콘 + 유저 아바타 드롭다운 배치
- `_base_ctx()`는 모든 페이지에 `current_user`, `auth_enabled`, `unread_notifications`를 주입 — 알림 배지 표시에 사용
- 401 자동 갱신: `base.html`의 fetch 인터셉터가 401 감지 → `POST /auth/refresh` (AJAX) → 성공 시 원래 요청 재시도, 실패 시 로그인 리다이렉트
- 개인화 API(`/api/watchlist/*`, `/api/subscriptions/*`, `/api/notifications/*`, `/api/proposals/*/memo`)는 인증 필수. `AUTH_ENABLED=false`이면 접근 불가
- `_macros.html`에 공통 Jinja2 매크로 정의: `proposal_card_compact`, `proposal_card_full`, `theme_header`, `scenario_grid`, `indicator_tags`, `macro_impact_table`, `grade_badge`, `krx_badges`, `external_links`. 새 매크로는 여기에 추가
- 종목 외부 링크: `external_links(ticker, market, mode)` 매크로 사용. 한국(KRX) → 네이버증권/Yahoo/KRX, 해외 → Yahoo/Finviz/SeekingAlpha/SimplyWallSt. `mode='icon'`(인라인) 또는 `mode='full'`(블록)
- 투자 교육은 Free 티어도 접근 가능(일 5턴). 테마 채팅은 Pro 이상만 가능. `tier_limits.py`의 `EDU_CHAT_DAILY_TURNS` vs `CHAT_DAILY_TURNS` 참고
- 문의 게시판 프라이버시: Admin/Moderator는 전체 조회, 일반 유저는 공개 문의 + 본인의 비공개 문의만 조회

## Issue & Exception Management

분석 파이프라인·API 운영 중 발생하는 예외·장애·AI 응답 파싱 실패는 `_docs/_exception/` 폴더에서 구조화된 형태로 관리한다. 향후 유사 장애 대응 시 반드시 이 대장을 먼저 참조할 것.

- **인덱스**: [`_docs/_exception/README.md`](_docs/_exception/README.md) — 이슈 대장 표 + 템플릿 + 자주 발생하는 패턴(Lessons Learned)
- **개별 리포트**: `YYYYMMDD_<짧은_영문_slug>.md` 포맷. 증상 → 근본 원인(직접/구조/근본 3계층) → 수정 사항(Layer별) → 검증 → 후속 모니터링 순서로 작성
- **원시 로그 덤프**: `YYYYMMDDHHMM_분석오류.md`는 구조화 전 로그 원본 (레거시)
- **이슈 발생 시 워크플로우**:
  1. 로그·`ai_query_archive` 테이블에서 실패 원인 파악
  2. `_docs/_exception/YYYYMMDD_<slug>.md` 템플릿에 맞춰 리포트 작성
  3. README의 "이슈 대장" 표에 한 줄 추가
  4. 수정 완료 후 상태를 `✅ 해결됨 (커밋 <hash>)`로 업데이트
- **AI 응답 파싱 실패 대응 레이어** (2026-04-22 Stage 1-A 이슈 이후 정립):
  - 프롬프트 레이어: `STAGE1_SYSTEM`의 "출력 형식 엄수" 섹션 — 단일 JSON 블록·raw 개행 금지·메타 주석 금지 강제
  - 파서 레이어: `_sanitize_json_response()` (쪼개진 코드블록 병합·마크다운 헤더 제거·자기주석 제거·제어문자 이스케이프) → `_try_fix_truncated_json()` (잘린 JSON 복구) 2단 복구
  - 아카이빙 레이어: 실패·복구 모두 `ai_query_archive.parse_status`에 기록 (`success` / `sanitized_recovered` / `truncated_recovered` / `timeout_partial` / `failed` / `empty`)
