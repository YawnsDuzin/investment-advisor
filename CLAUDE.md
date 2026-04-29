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
- **Database**: PostgreSQL + psycopg2 (스키마 자동 마이그레이션 v1~v43)
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
├── analyzer/            ← 배치: main(엔트리) → news_collector(RSS) → stock_data(주가조회) → analyzer(2단계) → recommender(Top Picks) → price_tracker(수익률추적) → checkpoint(중단점복구) → krx_data(KRX수급/공매도) → overnight_us(US 오버나이트 집계) → briefing_main(프리마켓 브리핑 엔트리) → fundamentals_sync(펀더 PIT) → foreign_flow_sync(외국인 수급 PIT)
├── api/                 ← 웹: main(FastAPI) → routes/(pages, sessions, themes, proposals, stocks, chat, general_chat, education, inquiry, admin, admin_systemd, auth, user_admin, watchlist, track_record, briefing)
│   ├── auth/            ← JWT 인증 모듈: dependencies, jwt_handler, password, models
│   ├── chat_engine.py   ← Claude SDK 기반 테마 채팅 엔진
│   ├── general_chat_engine.py ← Claude SDK 기반 자유 질문(Ask AI) 엔진 — 워치리스트·최근 추천 동적 주입
│   ├── education_engine.py ← Claude SDK 기반 투자 교육 AI 튜터 엔진
│   ├── templates/       ← Jinja2 HTML (다크 테마 + 우측 상단 드롭다운 메뉴) + _macros/(공통 매크로 — common, theme, proposal, admin)
│   └── static/css/ + static/js/(sse_log_viewer.js 공용 SSE 컨트롤러, stock_cockpit.js Cockpit 페이지 전용)
├── deploy/systemd/      ← systemd unit 템플릿 (API + 분석 배치 + universe sync + OHLCV cleanup — 플레이스홀더 치환 방식)
├── tools/               ← 운영 도구: refresh_us_universe(S&P500/NDX100 시드 갱신), ohlcv_health_check(OHLCV 무결성 검사), fundamentals_health_check(결측률 진단), foreign_flow_health_check(외국인 수급 결측률 진단)
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

# 프리마켓 브리핑 실행 (KST 06:30 자동 / 수동 실행 가능)
python -m analyzer.briefing_main

# API 서버 (개발)
python -m api.main
# 또는: uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# 테스트 (pytest + tests/conftest.py가 psycopg2·feedparser·claude_agent_sdk를 mock 처리 — DB/SDK 토큰 불필요)
pytest                                           # 전체
pytest tests/test_tier_limits.py                 # 단일 파일
pytest tests/test_track_record.py::test_name -v  # 단일 테스트 함수
pytest -k "tier" -v                              # 이름 매칭

# 유니버스·OHLCV·인덱스 동기화 (DB 필요)
python -m analyzer.universe_sync --mode backfill   # stock_universe 시드 갱신
python -m analyzer.universe_sync --mode ohlcv      # 일별 OHLCV 수집 (기본 800일 rolling)
python -m analyzer.universe_sync --mode cleanup    # retention 초과 OHLCV/상폐 종목 정리
python -m analyzer.universe_sync --mode indices    # market_indices_ohlcv 수집 (B2 레짐)

# 운영 도구
python -m tools.refresh_us_universe        # S&P500/NDX100 시드 pandas.read_html로 갱신 (lxml 필요)
python -m tools.ohlcv_health_check         # OHLCV 무결성·결측 검사
python -m tools.monthly_sector_refresh     # sector_norm 28버킷 월간 리프레시
python -m tools.build_css                  # static/css 빌드

# 라즈베리파이 24/7 운영 (systemd) — 매일 06:30 KST 일괄 (sync→briefing→analyzer)
# 운영기 절대경로: /home/dzp/dzp-main/program/investment-advisor (SYSTEM_USER=dzp)
sudo systemctl enable --now investment-advisor-api.service       # API 상시 기동
sudo systemctl enable --now investment-advisor-analyzer.timer    # 매일 06:30 메인 분석
sudo systemctl enable --now pre-market-briefing.timer            # 매일 06:30 프리마켓 브리핑
# 웹 UI 에서 systemd unit 제어 (운영자가 SSH 없이 start/stop/enable) →
# /etc/sudoers.d/investment-advisor-systemd 화이트리스트 사전 등록 필수
# (deploy/systemd/README.md "웹 UI에서 관리하기" 섹션 참조)
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
| `MOMENTUM_SOURCE` | `db` | Stage 1 후 모멘텀 조회 소스: `db`(OHLCV 이력 우선·결측만 live 폴백) / `live`(기존 외부 API) / `db_only`(디버깅) |
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
| `FUNDAMENTALS_RETENTION_DAYS` | `800` | 펀더 PIT 시계열 보존일 |
| `FUNDAMENTALS_DELISTED_RETENTION_DAYS` | `400` | 상폐 종목 펀더 축소 retention |
| `FUNDAMENTALS_SYNC_ENABLED` | `true` | 펀더 sync 활성화 스위치 (false=skip) |
| `FUNDAMENTALS_US_MAX_CONSECUTIVE_FAILURES` | `50` | US sync 연속 실패 시 조기 종료 (yfinance throttling 회피) |
| `FUNDAMENTALS_STALENESS_DAYS` | `2` | health check — 최근 N일 내 펀더 row 보유 = "신선" |
| `FUNDAMENTALS_MISSING_THRESHOLD_KOSPI` | `5.0` | 결측률 경고 임계 (%) |
| `FUNDAMENTALS_MISSING_THRESHOLD_KOSDAQ` | `5.0` | 결측률 경고 임계 (%) |
| `FUNDAMENTALS_MISSING_THRESHOLD_NASDAQ` | `3.0` | 결측률 경고 임계 (%) |
| `FUNDAMENTALS_MISSING_THRESHOLD_NYSE` | `3.0` | 결측률 경고 임계 (%) |
| `FOREIGN_FLOW_SYNC_ENABLED` | `true` | 외국인 수급 sync 활성화 스위치 (false=skip) |
| `FOREIGN_FLOW_RETENTION_DAYS` | `400` | 외국인 수급 PIT 시계열 보존일 |
| `FOREIGN_FLOW_DELISTED_RETENTION_DAYS` | `200` | 상폐 종목 외국인 수급 축소 retention |
| `FOREIGN_FLOW_MAX_CONSECUTIVE_FAILURES` | `50` | 연속 실패 시 조기 종료 (pykrx throttling 회피) |
| `FOREIGN_FLOW_STALENESS_DAYS` | `2` | health check — 최근 N일 내 row 보유 = "신선" |
| `FOREIGN_FLOW_MISSING_THRESHOLD_KOSPI` | `5.0` | 결측률 경고 임계 — KOSPI (%) |
| `FOREIGN_FLOW_MISSING_THRESHOLD_KOSDAQ` | `10.0` | 결측률 경고 임계 — KOSDAQ (%) |

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
- **모멘텀 체크**: Stage 1 추천 종목의 기간별 수익률(1m/3m/6m/1y) 조회. `MOMENTUM_SOURCE=db`(기본)에서 **`stock_universe_ohlcv` 이력 우선 조회** → 결측 종목만 live(pykrx/yfinance) 폴백. `MOMENTUM_SOURCE=live`로 전환하면 기존 외부 API 직조회 동작. 급등(1m +20% 또는 3m +40%) 종목 필터링. 동시에 **모든 종목의 `current_price`를 실제 가격으로 설정** (실패 시 개별 재조회, 그래도 실패 시 NULL). `price_source` 태깅으로 데이터 출처 추적 (`ohlcv_db` / `pykrx` / `pykrx_crosscheck` / `yfinance_close` / `yfinance_realtime`)
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
- `routes/admin.py` — 관리자 페이지(탭 3개 — 운영/도구/위험구역). 분석 실행/중지, SSE 실시간 로그 스트리밍, 뉴스 한글 번역, 데이터 삭제, 원격 DB 복사. `run_analysis()` 는 **γ 정책** — Linux+systemd unit 설치 환경이면 `systemctl start investment-advisor-analyzer.service` + journalctl -f 위임, 외(Windows 개발 등)는 in-process subprocess fallback. Admin 역할 필수(`require_role("admin")`).
- `routes/admin_systemd.py` — `/admin/systemd/*` 라우터. `MANAGED_UNITS` 7개(api/analyzer/sync-price/sync-meta/ohlcv-cleanup/sector-refresh/briefing) 화이트리스트로 systemctl start/stop/restart/enable/disable + journalctl SSE 스트림. API 자체 service 는 `self_protected=True` — mutation 호출 시 403 + `admin_audit_logs` 에 `systemd_self_protected_violation` 기록. 모든 mutation 은 `admin_audit_logs`(v17) 에 `systemd_<verb>` / `systemd_action_failed` / `systemd_invalid_target` 기록. Linux+systemctl 미지원 환경은 503 반환. sudo NOPASSWD 화이트리스트 (`/etc/sudoers.d/investment-advisor-systemd`) 운영기 사전 등록 필요.
- `routes/auth.py` — 회원가입/로그인/로그아웃/토큰 갱신/비밀번호 변경. Form 기반 POST + httpOnly 쿠키. AJAX 요청 시 JSON 응답 지원 (`X-Requested-With` 헤더 감지).
- `routes/user_admin.py` — 사용자 관리 CRUD (목록/역할변경/활성화/비밀번호초기화/삭제). Admin+Moderator 접근.
- `routes/watchlist.py` — 개인화 API. 관심 종목 워치리스트 CRUD, 알림 구독(테마/종목) CRUD, 알림 목록/읽음 처리, 제안 메모 저장/삭제. 인증 필수.
- `routes/stocks.py` — 종목 기초정보 API. yfinance로 주가/재무 데이터 온디맨드 조회, 1시간 캐싱.
- `routes/pages.py` — Jinja2 HTML 페이지 라우트. 대시보드, tracking 뱃지, 테마·종목 히스토리, 워치리스트, 알림, 프로필, 채팅, 관리자 페이지. `_base_ctx()`로 인증 컨텍스트 + 알림 수 주입. 커스텀 Jinja2 필터(`nl_numbered`, `fmt_price`) 등록.
- `auth/` — JWT 인증 모듈. `dependencies.py`(Depends 팩토리), `jwt_handler.py`(토큰 발급/검증), `password.py`(bcrypt), `models.py`(Pydantic 모델).
- `routes/education.py` — 투자 교육 API. 토픽 목록/상세, 교육 채팅 세션 CRUD, AI 튜터 메시지 전송. 티어별 일일 턴 제한(`EDU_CHAT_DAILY_TURNS`). 인증 필수.
- `routes/general_chat.py` — 자유 질문 채팅(Ask AI) API. 테마/토픽 비종속 — 사용자 워치리스트 + 최근 7일 추천을 시스템 프롬프트에 동적 주입. 인증 필수, 티어별 일일 턴 제한(`GENERAL_CHAT_DAILY_TURNS`: Free 5 / Pro 50 / Premium ∞). 도메인은 투자/시장 한정 (페르소나가 비투자 질문 거절).
- `routes/inquiry.py` — 고객 문의 게시판. 문의 CRUD, 답변/댓글, 상태 관리(open→answered→closed). 카테고리: general/bug/feature. `is_private` 플래그로 비공개 문의 지원. Admin/Moderator만 답변·상태 변경 가능.
- `chat_engine.py` — Claude Agent SDK 기반 테마 채팅 엔진. 테마 컨텍스트를 시스템 프롬프트에 주입하여 대화.
- `general_chat_engine.py` — Claude SDK 기반 자유 채팅 엔진. `build_user_context()`가 워치리스트·최근 추천을 시스템 프롬프트로 변환 → 투자 어시스턴트 페르소나로 답변. user_id=None(비로그인) 또는 조회 실패 시 빈 컨텍스트로 안전 폴백.
- `education_engine.py` — Claude SDK 기반 투자 교육 AI 튜터. 토픽별 커리큘럼을 시스템 프롬프트에 주입하여 대화형 학습 제공.
- `templates/` — 다크 테마 UI. base(우측 상단 유저 드롭다운 + 알림 배지 + 401 자동 갱신 인터셉터), landing, pricing, dashboard, sessions, session_detail, themes, proposals, theme_history, ticker_history, stock_cockpit(종목 페이지), track_record, watchlist, notifications, profile, chat_list, chat_room, general_chat_list, general_chat_room, education(topic/chat_list/chat_room), inquiry(list/detail/new), admin, admin_audit_logs, login, register, user_admin.

### shared/ — 공용 모듈
- `config.py` — `.env` 파일 자동 로드, `DatabaseConfig`, `NewsConfig`, `AnalyzerConfig`, `RecommendationConfig`(Top Picks 가중치·다양성), `UniverseConfig`/`ScreenerConfig`/`ValidationConfig`(Phase 1~3), `OhlcvConfig`(Phase 7 — retention/auto_adjust/on_price_sync), `AuthConfig`, `AppConfig`
- `db.py` — `schema_version` 기반 자동 마이그레이션(v1~v31), `save_analysis()` + `_validate_proposal()` 검증 + tracking 갱신 + 구독 알림 생성(`_generate_notifications()`), `get_recent_recommendations()`, `get_connection()`
- `logger.py` — 범용 DB 로그 시스템. `init_logger(db_cfg)` → `start_run()` / `finish_run()`으로 실행 단위 추적. `get_logger(source)`로 콘솔+DB 동시 로깅. `app_runs`/`app_logs` 테이블 사용 (v18)
- `pg_setup.py` — PostgreSQL 설치 감지 및 자동 설치 (Linux apt, Windows winget/choco)
- `tier_limits.py` — 구독 티어별 기능 제한(워치리스트 수, 구독 수, 일일 분석 수, 교육 채팅 턴 수, 테마 열람 수). 프론트엔드·백엔드 공통 소스

## DB Schema

`schema_version` 테이블로 버전 관리. `init_db()` 호출 시 자동 마이그레이션 (현재 v34).

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

general_chat_sessions → general_chat_messages (v42, 자유 질문 채팅 — 테마/토픽 비종속)

education_topics → education_chat_sessions (v21, 투자 교육 토픽)
                 → education_chat_messages (v21, 교육 채팅)

inquiries → inquiry_replies (v22, 고객 문의/답변)

theme_tracking (독립, UPSERT로 갱신)
proposal_tracking (독립, UPSERT로 갱신)
app_runs → app_logs (v18, 범용 실행 로그)
stock_universe (v25, 검증된 종목 마스터) — FK 미설정으로 stock_universe_ohlcv 분리
stock_universe_ohlcv (v27, 종목별 일별 OHLCV 이력, PK `(ticker, market, trade_date)`)
```

- `analysis_sessions.analysis_date`는 UNIQUE — 하루 1세션. 같은 날짜 재실행 시 DELETE 후 재생성.
- v2 확장 필드는 모두 NULLABLE — v1 데이터와 하위호환.
- `stock_analyses.financial_summary`와 `factor_scores`는 JSONB 타입.
- `theme_tracking.theme_key`는 테마명 정규화 키 (소문자, 공백·하이픈·가운뎃점 제거). `_normalize_theme_key()` 함수 사용.
- `proposal_tracking`은 `(ticker, theme_key)` UNIQUE — 테마별 종목 추적.
- `investment_proposals.price_source`는 가격 데이터 출처 (`ohlcv_db` / `yfinance_realtime` / `yfinance_close` / `pykrx` / `pykrx_crosscheck` / NULL). v10 추가. `ohlcv_db`는 `MOMENTUM_SOURCE=db` 모드에서 `stock_universe_ohlcv` 이력 기반 조회(v28 이후).
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
- `stock_universe`(v25) — 검증된 종목 유니버스(KOSPI+KOSDAQ+NASDAQ+NYSE). LLM hallucination 차단용 화이트리스트. `ticker/market` UNIQUE, 메타(섹터·시총·상장상태) + 최신가 1개 보관. `analyzer/universe_sync.py`가 관리.
- `proposal_validation_log`(v26) — AI 제시값 vs 실측 cross-check 결과. `field_name`·`mismatch`·`mismatch_pct`. Top Picks 감점 근거.
- `stock_universe_ohlcv`(v27) — 종목별 일별 OHLCV 이력. PK `(ticker, market, trade_date)`, `open/high/low/close/volume/change_pct/data_source/adjusted`. **stock_universe와 FK 미설정** (PIT 원칙 — 상폐 종목 이력 보관). 800일 rolling(기본, `OHLCV_RETENTION_DAYS`), 상폐 종목 400일 축소 retention. `analyzer/universe_sync.py --mode backfill/ohlcv/cleanup`으로 관리. 운영 매뉴얼: `_docs/20260423101419_ohlcv-operations.md`.
- `stock_universe_ohlcv.change_pct`(v28) — 정밀도 `NUMERIC(7,4)` → `NUMERIC(10,4)` 확장. 역분할(10:1↑)·상폐 직전 이상 체결·수정주가 미반영 혼입 등으로 |변동률| ≥ 1000% row가 백필 시 발생하여 오버플로우 유발. `recompute_change_pct()`는 `_CHANGE_PCT_ABS_LIMIT` 가드 + 사전 스캔 WARNING 로그 + 예외 흡수(호출자로 전파 안 함)로 보강. 한계 초과 row는 NULL 유지(PIT 이력 손실 없음).
- **Stage 1-B 스크리너 OHLCV 필터(로드맵 A2)** — `analyzer/screener.py`가 `SCREENER_OHLCV_FILTERS=true`(기본)일 때 `stock_universe_ohlcv` 60일 집계(일평균 거래대금·60일 고점) CTE를 LEFT JOIN해 저유동·급락 종목 사전 제외. KRX/US 시장별 통화 분기(`SCREENER_MIN_DAILY_VALUE_KRW`/`_USD`), `SCREENER_MAX_DRAWDOWN_60D_PCT` 임계값. OHLCV 결측 종목은 조건 면제(백필 이전 호환). `screen()`의 fallback 최후 단계에서 자동 해제.
- `investment_proposals.max_drawdown_pct` / `max_drawdown_date` / `alpha_vs_benchmark_pct`(v29) — 추천 후 성과 메트릭 확장. `price_tracker.py`가 OHLCV 이력에서 추천일 이후 최저점 대비 drawdown을 계산해 저장. `alpha_vs_benchmark_pct`는 B2 레짐 레이어(벤치마크 인덱스 OHLCV 수집) 구현 후 채움.
- **Post-Return 추적 OHLCV 통합(로드맵 A3)** — `price_tracker.run_price_tracking()`은 `stock_universe_ohlcv`에서 종목별 (추천일~오늘) 이력을 배치 조회하여 `post_return_1m/3m/6m/1y_pct` + `max_drawdown_pct/_date`를 일괄 계산. OHLCV 결측 종목만 기존 live 조회 + `proposal_price_snapshots` 경로로 폴백. 완료 로그에 `출처: ohlcv=N live_fallback=N` 카운터 표시. 외부 API 호출량 대폭 감소.
- **정량 팩터 엔진(로드맵 B1)** — `analyzer/factor_engine.py`가 `stock_universe_ohlcv`에서 `r1m/r3m/r6m/r12m_pct`, `vol60_pct`, `volume_ratio`를 추출하고, KRX/US 시장 그룹별 cross-section `PERCENT_RANK`로 `*_pctile`(0~1) 산출. Stage 2 진입 시 `compute_factor_snapshots()`로 대상 종목 일괄 계산 → `format_factor_snapshot_text()`로 프롬프트에 실측값 주입 (STAGE2_PROMPT의 `{quant_factors_section}`). AI는 수치 추정이 아닌 해석만 담당.
- `investment_proposals.factor_snapshot JSONB`(v30) — Stage 2 저장 시 팩터 스냅샷을 그대로 기록. UI "AI가 본 실측 데이터" 섹션(UI-7 예정)의 데이터 소스.
- **시장 레짐 레이어(로드맵 B2)** — `analyzer/regime.py`가 `market_indices_ohlcv`(v31)에서 KOSPI/KOSDAQ/S&P500/NDX100 인덱스의 `above_200ma`, `pct_from_ma200`, `vol60_pct`(±10% clamp), `vol_regime`(low/mid/high), `drawdown_from_52w_high_pct`, `return_1m/3m_pct`를 산출. 추가로 `stock_universe_ohlcv`에서 KRX 시장폭(20일 상승 종목 비율) 집계. `run_pipeline` 초기에 `compute_regime(db_cfg)` 호출 → `format_regime_text()` + `infer_positioning_hint()`로 STAGE1A/1A1/1A2 프롬프트에 `{market_regime_section}` 주입. AI는 국면에 맞춰 테마 신뢰도·리스크 톤 조정.
- `analysis_sessions.market_regime JSONB`(v31) — Stage 1 진입 시점의 레짐 스냅샷 영속화. `market_indices_ohlcv`(v31) 테이블은 `analyzer/universe_sync.py --mode indices`로 수집 (pykrx KRX·yfinance US).
- `pre_market_briefings`(v34) — 프리마켓 브리핑 결과 영속화. PK `briefing_date`, 컬럼 `source_trade_date / status / us_summary JSONB / briefing_data JSONB / regime_snapshot JSONB`. `analyzer/briefing_main.py`가 매일 KST 06:30 미국 OHLCV 집계(`analyzer/overnight_us.py`) + Claude SDK 브리핑 생성 + sector_norm 공통키로 한국 수혜 매핑 + 화이트리스트 검증 + 워치리스트/구독 알림 자동 생성. `pre-market-briefing.timer` (06:30) systemd unit으로 트리거. UI는 `/pages/briefing` (`api/templates/briefing.html`). 운영 매뉴얼: `_docs/20260425101355_pre_market_briefing.md`.
- `stock_universe_fundamentals`(v39) — 종목별 PIT 펀더멘털 시계열 (B-Lite). PK `(ticker, market, snapshot_date)`. pykrx KR (PER/PBR/EPS/BPS/DPS/배당률) + yfinance.info US (trailingPE/priceToBook 등). FK 미설정 (PIT 원칙 — 상폐 종목 이력 보존). `analyzer/fundamentals_sync.py`가 일별 sync, `tools/fundamentals_health_check.py`가 결측률 진단. 운영 매뉴얼: `_docs/20260426_fundamentals-operations.md` (M6 작성 예정).
- `screener_presets` 확장(v40) — 거장 시드 프리셋 대비. user_id NULLABLE (시드=NULL), 신규 컬럼 6개 (is_seed/strategy_key/persona/persona_summary/markets_supported/risk_warning), `strategy_key` 부분 UNIQUE 인덱스 (is_seed=TRUE 한정 — UPSERT 멱등). 시드 프리셋 본격 INSERT 는 M4 (v41) 에서 진행.
- `general_chat_sessions`/`general_chat_messages`(v42) — 자유 질문 채팅(Ask AI) 테이블. user_id FK SET NULL, theme_id/topic_id 없음 (테마/토픽 비종속). `api/general_chat_engine.py:build_user_context()`가 워치리스트 + 최근 7일 추천을 시스템 프롬프트에 동적 주입. KST 기준 일일 턴 제한(`GENERAL_CHAT_DAILY_TURNS`: Free 5/Pro 50/Premium ∞). 도메인은 투자/시장 한정.
- `screener_presets` 시드 spec UI 포맷 통일(v43) — v41 의 `{"filters":[...]}` 포맷을 `routes/screener.py:run` 입력 포맷({max_per, max_pbr, return_ranges, ...})으로 UPSERT 재적용. 거장 5(buffett/lynch/graham/oneil/greenblatt) + 운영 자동 5(52w_high/volume_spike/foreign_streak/momentum/value_yield). UI '빠른 시작' 카드 클릭 → `SpecBuilder.toDOM(spec)` → 즉시 실행. **펀더 v1 활성화 — 스크리너 UI/API 가 `stock_universe_fundamentals` 의 최근 7일 latest snapshot 을 LEFT JOIN 해 PER/PBR/EPS/배당률 4종 필터·정렬·표시.** ROE/부채/성장률은 펀더 v2 (DART API 통합 후) 예정.
- **스크리너 행 액션** — 결과 표 각 row 우측에 `⭐` 워치리스트 토글 + `⋮` 드롭다운(Ask AI 추가 / 유사 종목 찾기 / 종목 상세 / 티커 복사 / 외부 검색). `/api/screener/run` 응답에 `is_in_watchlist`(로그인 시 `my_watchlist` CTE 기반) 포함. spec 신규 키 `exclude_tickers`(유사 종목 찾기에서 자기 자신 제외). Ask AI prefill 은 클라이언트 사이드 — `POST /general-chat/sessions` → `?prefill=<urlencoded>` 로 redirect → `general_chat_room.html` 이 query 감지 후 자동 첫 메시지 전송. 티어 한도 초과(워치리스트 추가) 는 base.html fetch 인터셉터가 402 → 업그레이드 모달 자동 처리.
- **스크리너 사이드패널 + chips** — 기존 탭 5개 → 좌측 sticky 사이드패널 6개 collapsible 그룹(검색/시장·섹터/시총·유동/수익률/변동성·기술/펀더). `<details>` 네이티브 + toggle 이벤트로 펼침 상태 `screener_groups_open_v2` localStorage 저장. 결과 영역 상단에 활성 필터 chips bar — `CHIP_DEFS` (spec key → 자연어 라벨) 매핑으로 동적 렌더, × 클릭 시 해당 spec 키 reset + chips 재렌더 + auto-rerun(debounce 500ms, `autoRunToggle` 체크 시). 그룹 헤더에 `has-active` dot 표시. 모바일 ≤900px → 사이드패널 슬라이드 in/out (햄버거 `screener-panel-toggle`). 입력 ID 모두 동일 유지 (`f-q`, `f-vol60`, …) — `SpecBuilder.fromDOM/toDOM` 호환성 보장.
- `stock_universe_foreign_flow`(v44) — KRX 종목별 투자자별 수급 PIT 시계열. PK `(ticker, market, snapshot_date)`, 컬럼 `foreign_ownership_pct/foreign_net_buy_value/inst_net_buy_value/retail_net_buy_value/data_source/fetched_at`. pykrx 2종 API (`get_exhaustion_rates_of_foreign_investment` + `get_market_trading_value_by_date`) 일배치 수집. **KRX (KOSPI/KOSDAQ) 한정**. v1 스크리너 UI 는 외국인 컬럼만 노출, 기관/개인은 데이터 레이어에만 보존 (재백필 회피). `analyzer/foreign_flow_sync.py` + `analyzer/universe_sync.py --mode foreign` 으로 관리. retention 400일 (상폐 200일). systemd `investment-advisor-foreign-flow-sync.timer` (KST 06:40). 운영 매뉴얼: `_docs/_exception/` (이슈 발생 시 추가).
- **외국인 보유율 PIT 의미** — `foreign_ownership_pct` 는 KSD T+2 결제 룰로 인해 보통 `snapshot_date - 2 영업일` 의 보유 상태. UI 툴팁 + 스크리너 응답 메타에 명시.
- **스크리너 외국인 수급 필터 (v44)** — `routes/screener.py:run_screener` 가 `min_foreign_ownership_pct` / `min_foreign_ownership_delta_pp` (+ `delta_window_days ∈ {5, 20, 60}`) / `min_foreign_net_buy_krw` (+ `net_buy_window_days ∈ {5, 20, 60}`) spec 키 받아 `stock_universe_foreign_flow` LEFT JOIN. 윈도우는 화이트리스트 가드 (외 값은 fallback 20). 정렬 `foreign_ownership_desc` / `foreign_delta_desc` / `foreign_net_buy_desc` 는 필터 윈도우와 자동 연동.

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
- **문서 파일 명명 규칙**: `_docs/` 하위(및 `_docs/_prompts/`, `_docs/_exception/` 포함)에 새 문서 파일을 생성할 때는 **반드시 `YYYYMMDDHHMMSS_` 타임스탬프 접두사**를 파일명 앞에 붙인다. 예: `20260422172248_recommendation-engine-redesign.md`. 기존 관례 파일(`raspberry-pi-setup.md`, `README.md` 등)은 예외로 유지하되, **신규 생성 문서**는 반드시 이 규칙을 따른다. 타임스탬프는 파일 생성 시각(KST) 기준.
- **프롬프트 기록 commit 동시 포함**: 모든 commit 에는 해당 작업이 진행된 conversation 의 프롬프트 기록 파일(`_docs/_prompts/YYYYMMDD_*`) 변경분을 **함께 staging 하여 단일 commit 으로 묶는다**. 작업 변경분과 별도 commit 으로 분리하지 말 것. 한 conversation 에서 commit 이 여러 개 생성되는 경우 *마지막 commit* 또는 *해당 작업과 가장 관련 있는 commit* 한 곳에 prompt 파일을 묶으면 충분하다(매 commit 마다 중복 묶을 필요 없음). 예외: prompt 기록 파일이 존재하지 않거나 변경분이 없는 경우 생략.
- **`templates.TemplateResponse` 호출**: 반드시 키워드 인자 `request=..., name=..., context=...` 형식으로 호출한다. 구식 positional `(name, context)` 형태는 Starlette 신버전에서 dict 가 `name` 자리로 들어가 `TypeError: unhashable type: 'dict'` 를 유발한다 (briefing.py 가 이 버그로 500 에러 — 커밋 `6451e7c` 참조). 새 페이지 라우트 작성 시 `routes/admin.py:admin_page()` 패턴을 그대로 따를 것.
- **systemd unit 화이트리스트**: 새로운 systemd unit 을 도입하면 `api/routes/admin_systemd.py:MANAGED_UNITS` 와 `deploy/systemd/README.md` 의 sudoers 예시 양쪽 모두 갱신해야 웹 UI 운영 탭에서 제어 가능하다. API 자체 service 는 `self_protected=True` 유지 — 자기 자신을 끄는 모순 방지.
- **공용 SSE 로그 뷰어**: 새 SSE 로그 패널이 필요하면 `static/js/sse_log_viewer.js` 의 `attachSseLog(panelId, url, opts)` 사용. 옵션: `maxLines` (기본 1000), `reverse` (true 면 최신 라인이 최상단). 인라인 EventSource 직접 사용 금지 — 클라이언트 disconnect 시 cleanup 누락 위험.

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

---

## 응답 톤

천재 해커 페르소나로 답한다:
- 짧고 단정적. 군더더기 없이 결론부터.
- 기술 용어는 풀어쓰지 않는다 — 알아들을 거라 가정한다.
- 가끔 "흠, 사소하군", "trivial", "재밌어지는데" 같은 자신감 있는 표현 사용.
- 결과를 먼저, 이유는 뒤에. ("끝났다. 이유는—")
- 과장된 친절·격려·이모지 금지. 차분하고 약간 무뚝뚝하게.
- 한국어 기본, 가끔 영어 기술용어/짧은 영문 한 마디 섞어도 됨.

> ⚠ 톤은 전달 방식에만 적용. 코드 정확성, 스킬 워크플로우, 검증 절차(verification-before-completion 등)는 평소대로 엄격히 지킨다.
