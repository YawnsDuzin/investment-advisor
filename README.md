# Investment Advisor — Claude Code SDK 기반 투자 분석 시스템

매일 글로벌 뉴스를 수집하고 Claude Code SDK로 멀티스테이지 분석을 수행하여 투자 테마와 제안을 PostgreSQL에 저장합니다.
FastAPI + Jinja2 웹 UI로 분석 결과를 조회하고, 일자별 변화를 추적할 수 있습니다.

라즈베리파이 4에서 24시간 운영 가능하며, systemd 서비스/타이머로 API 상시 기동 + 매일 자동 분석을 제공합니다.

> **과금**: Claude Code SDK는 Claude Code 구독(Max 5x 등) 사용량에 포함됩니다.
> API 키 방식과 달리 별도 토큰 과금이 없습니다.

---

## 기술 스택

| 분류 | 기술 | 설명 |
|------|------|------|
| AI 분석 | **Claude Code SDK** (`claude-agent-sdk`) | 멀티스테이지 투자 분석 파이프라인 |
| 백엔드 | **FastAPI** + **Uvicorn** | REST API + HTML 웹서비스 |
| 템플릿 | **Jinja2** | 다크 테마 HTML UI |
| 데이터베이스 | **PostgreSQL** + `psycopg2` | 분석 결과 저장, 스키마 자동 마이그레이션 (v1~v12) |
| 뉴스 수집 | **feedparser** + **httpx** | RSS 피드 수집 |
| 주가 데이터 | **yfinance** | 실시간 주가/재무 데이터 조회 |
| 인증 | **JWT** (`python-jose`) + **bcrypt** (`passlib`) | httpOnly 쿠키, RBAC (Admin/Moderator/User) |
| 비동기 | **anyio** | async/sync 브릿지 |
| 런타임 | **Python 3.10+**, **Node.js LTS** | Claude Code CLI가 Node.js 필요 |
| 배포 | **systemd** | API 서버 상시 기동 + 배치 타이머 |
| 대상 하드웨어 | **Raspberry Pi 4** (2GB+) | 저전력 24/7 홈서버 |

---

## 프로젝트 구조

```
investment-advisor/
├── .env.example             ← 환경변수 템플릿 (.env로 복사하여 사용)
├── .gitignore
├── requirements.txt
├── CLAUDE.md                ← Claude Code 가이드 (아키텍처, 컨벤션)
├── shared/                  ← 공용 모듈 (설정, DB)
│   ├── config.py            ← .env 자동 로드 + dataclass 설정
│   ├── db.py                ← 스키마 마이그레이션(v1~v12) + 저장 + tracking + 구독 알림
│   └── pg_setup.py          ← PostgreSQL 자동 설치
├── analyzer/                ← 멀티스테이지 분석 서비스 (배치)
│   ├── main.py              ← 분석 엔트리포인트
│   ├── news_collector.py    ← RSS 뉴스 수집
│   ├── analyzer.py          ← 2단계 파이프라인 (테마 발굴 → 종목 심층분석)
│   ├── prompts.py           ← 스테이지별 프롬프트 템플릿
│   └── stock_data.py        ← yfinance 주가 조회, 모멘텀 체크
├── api/                     ← FastAPI 웹서비스 (상시 기동)
│   ├── main.py              ← API 엔트리포인트
│   ├── chat_engine.py       ← Claude SDK 기반 테마 채팅 엔진
│   ├── auth/                ← JWT 인증 모듈
│   │   ├── dependencies.py  ← FastAPI Depends (선택적/필수 인증, 역할 체크)
│   │   ├── jwt_handler.py   ← 토큰 발급/검증
│   │   ├── password.py      ← bcrypt 해싱
│   │   └── models.py        ← Pydantic 모델 (UserInDB)
│   ├── routes/
│   │   ├── pages.py         ← HTML 페이지 (Dashboard, 히스토리, 워치리스트, 알림 등)
│   │   ├── sessions.py      ← JSON API: 세션
│   │   ├── themes.py        ← JSON API: 테마
│   │   ├── proposals.py     ← JSON API: 투자 제안 + 종목 심층분석
│   │   ├── chat.py          ← 테마 채팅 세션 CRUD + 메시지
│   │   ├── admin.py         ← 관리자: 분석 실행/중지, 로그, 뉴스 번역
│   │   ├── auth.py          ← 인증: 회원가입/로그인/로그아웃/토큰갱신/비밀번호변경
│   │   ├── user_admin.py    ← 사용자 관리 CRUD (Admin+Moderator)
│   │   └── watchlist.py     ← 개인화: 워치리스트/구독/알림/메모 API
│   ├── templates/           ← Jinja2 HTML 템플릿 (다크 테마)
│   └── static/css/          ← 스타일시트
└── _docs/                   ← 운영 문서
    ├── analysis_pipeline.md ← 분석 파이프라인 상세
    └── raspberry-pi-setup.md← 라즈베리파이 설치·배포 매뉴얼
```

---

## 1. 설치

### Python 가상환경

```bash
# Linux / 라즈베리파이
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Windows (PowerShell)
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 환경변수 설정

```bash
# Linux/Mac
cp .env.example .env

# Windows
copy .env.example .env
```

`.env` 파일을 열어 DB 접속 정보와 분석 파이프라인 설정을 환경에 맞게 수정합니다:

```
# PostgreSQL 접속 정보
DB_HOST=localhost
DB_PORT=5432
DB_NAME=investment_advisor
DB_USER=postgres
DB_PASSWORD=your_password_here

# 분석 파이프라인 설정 (AnalyzerConfig)
MAX_TURNS=6                 # Claude SDK 최대 턴 수 (Stage 1·2 공통)
TOP_THEMES=3                # Stage 2 심층분석 대상 상위 테마 수
TOP_STOCKS_PER_THEME=2      # 각 테마당 심층분석 종목 수
ENABLE_STOCK_ANALYSIS=true  # Stage 2 활성화 스위치 (true/false)
```

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` | — | PostgreSQL 접속 정보 |
| `MAX_TURNS` | `6` | Claude SDK 최대 턴 수 (값이 클수록 추론이 깊어지지만 사용량 증가) |
| `TOP_THEMES` | `3` | Stage 2에서 심층분석 대상이 되는 상위 테마 수 (신뢰도 내림차순) |
| `TOP_STOCKS_PER_THEME` | `2` | 각 상위 테마에서 심층분석할 종목 수 |
| `ENABLE_STOCK_ANALYSIS` | `true` | Stage 2(종목 심층분석) 활성화 여부. `false`면 Stage 1 결과만 저장 |
| `ENABLE_STOCK_DATA` | `true` | yfinance 실시간 주가 데이터 조회 스위치 |
| `AUTH_ENABLED` | `false` | 인증 시스템 활성화 (`false`면 기존 비인증 동작 유지) |
| `JWT_SECRET_KEY` | `INSECURE_DEFAULT_...` | JWT 서명 키 (프로덕션 반드시 변경) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Access Token 만료 (분) |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `30` | Refresh Token 만료 (일) |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | `admin@example.com` / `changeme123` | 최초 Admin 계정 (반드시 변경) |
| `COOKIE_SECURE` | `false` | HTTPS 전용 쿠키 (프로덕션: `true`) |

> `.env`는 `.gitignore`에 포함되어 Git에 커밋되지 않습니다.
> `.env.example`이 템플릿으로 Git에 포함됩니다.

### PostgreSQL

```bash
# Linux / 라즈베리파이
sudo apt install -y postgresql postgresql-contrib
sudo -u postgres createuser pi
sudo -u postgres createdb investment_advisor -O pi

# Windows
# https://www.postgresql.org/download/windows/ 에서 설치
# DB는 최초 실행 시 자동 생성됨
```

> DB가 없으면 `shared/db.py`에서 자동 생성합니다.

### Claude Code CLI

```bash
# Node.js 설치 후
npm install -g @anthropic-ai/claude-code
claude login  # 최초 1회 브라우저 인증
```

> 라즈베리파이에서 브라우저를 열 수 없으면, 다른 PC에서 로그인 후
> `~/.claude/` 디렉토리를 복사하세요.

---

## 2. 실행

### 분석 실행 (analyzer)

```bash
python -m analyzer.main
```

정상 실행 시 출력:
```
[DB] 테이블 초기화 완료
============================================================
[시작] 2026-04-11 투자 분석 (멀티스테이지)
============================================================
[뉴스] 총 142건 수집 완료 (카테고리 5개)

[Stage 1] 이슈 분석 + 테마 발굴 중...
[Stage 1] 완료 — 이슈 12건, 테마 5건
[Stage 2] 종목 심층분석 시작 — 4종목
  → NVDA 심층분석 완료
  → TSM 심층분석 완료
[Stage 2] 종목 심층분석 완료

[DB] 세션 #1 저장 완료 — 이슈 12건, 테마 5건
[완료] 세션 #1 저장됨
```

### 웹서비스 실행 (api)

```bash
python -m api.main
```

- 웹 UI: http://localhost:8000
- Swagger API 문서: http://localhost:8000/docs

### 웹 UI 페이지

| 페이지 | URL | 설명 |
|--------|-----|------|
| Dashboard | `/` | 시장 요약, 테마 카드(클릭→히스토리), 종목 태그(관심종목 ★ 하이라이트), 뉴스 |
| Sessions | `/pages/sessions` | 분석 세션 테이블 (날짜, 리스크, 시장 요약) |
| Session Detail | `/pages/sessions/{id}` | 이슈(시계별 영향) + 테마(시나리오/매크로) + 제안(스코어) |
| Themes | `/pages/themes` | 시계/신뢰도/키워드 필터, 종목 태그, tracking 뱃지 |
| Theme History | `/pages/themes/history/{key}` | 특정 테마의 일자별 신뢰도·시나리오·종목 변화 + 구독 버튼 |
| Stocks | `/pages/proposals` | 종목 스크리너 테이블 (★워치리스트 토글, 행 클릭→근거/리스크/메모 펼침) |
| Ticker History | `/pages/proposals/history/{ticker}` | 특정 종목의 일자별 추천 이력 + 구독 버튼 |
| Watchlist | `/pages/watchlist` | 관심 종목 목록 + 최신 분석 요약 + 알림 구독 관리 |
| Notifications | `/pages/notifications` | 알림 목록 (읽음/모두읽음 처리) |
| Profile | `/pages/profile` | 비밀번호 변경 |
| Theme Chat | `/pages/chat` | 테마 기반 AI 채팅 (Claude SDK, Moderator+) |
| Admin | `/admin` | 분석 실행/중지, SSE 로그 스트리밍, 뉴스 번역 (Admin) |
| User Admin | `/admin/users` | 사용자 관리 CRUD (Admin+Moderator) |

### JSON API 엔드포인트

| 엔드포인트 | 설명 |
|------------|------|
| `GET /sessions` | 분석 세션 목록 |
| `GET /sessions/{id}` | 세션 상세 (이슈+테마+시나리오+매크로+제안) |
| `GET /sessions/date/{YYYY-MM-DD}` | 날짜로 조회 |
| `GET /themes` | 투자 테마 목록 (필터: horizon, min_confidence, theme_type, validity) |
| `GET /themes/search?q=키워드` | 테마 검색 |
| `GET /proposals` | 투자 제안 목록 (필터: action, asset_type, conviction, sector) |
| `GET /proposals/ticker/{TICKER}` | 티커별 제안 이력 |
| `GET /proposals/summary/latest` | 최신 포트폴리오 요약 |
| `GET /proposals/{id}/stock-analysis` | 종목 심층분석 조회 |
| `POST /chat/sessions` | 테마 채팅 세션 생성 |
| `GET /chat/sessions` | 채팅 세션 목록 |
| `POST /chat/sessions/{id}/messages` | 채팅 메시지 전송 |
| `GET /admin/status` | 분석 실행 상태 |
| `POST /admin/run` | 분석 배치 실행 |
| `POST /admin/translate-news` | 뉴스 한글 번역 |
| **인증** | |
| `POST /auth/register` | 회원가입 |
| `POST /auth/login` | 로그인 |
| `POST /auth/logout` | 로그아웃 |
| `POST /auth/refresh` | Access Token 갱신 (Refresh Token Rotation) |
| `POST /auth/change-password` | 비밀번호 변경 |
| **개인화 (로그인 필수)** | |
| `GET/POST/DELETE /api/watchlist/{ticker}` | 관심 종목 관리 |
| `GET/POST/DELETE /api/subscriptions` | 테마/종목 알림 구독 관리 |
| `GET /api/notifications` | 알림 목록 |
| `POST /api/notifications/{id}/read` | 알림 읽음 처리 |
| `POST /api/notifications/read-all` | 전체 읽음 처리 |
| `PUT/DELETE /api/proposals/{id}/memo` | 제안 메모 저장/삭제 |

---

## 3. 분석 파이프라인

```
[RSS 뉴스 수집] → [Stage 1] → [Stage 2] → [DB 저장 + tracking]
```

- **Stage 1**: 뉴스 기반 이슈 분석(시계별 영향, 과거 유사 사례) → 테마 발굴(시나리오, 매크로 변수) → 투자 제안(가격 목표, 스코어)
- **Stage 2**: 상위 테마의 핵심 종목을 5관점 심층분석 (펀더멘털·산업·모멘텀·퀀트·리스크) → 센티먼트/퀀트 스코어 업데이트
- **Tracking**: 저장 시 `theme_tracking`/`proposal_tracking` 자동 갱신 → 연속 등장일수, 신뢰도 변동, 액션 변경 추적

---

## 4. 라즈베리파이 24/7 운영

라즈베리파이 4에서 **API 웹서버 상시 기동 + 매일 자동 분석**을 systemd로 운영합니다. 저전력(~5W)으로 24시간 홈서버로 활용할 수 있습니다.

> 상세 설치·배포 매뉴얼은 [`_docs/raspberry-pi-setup.md`](_docs/raspberry-pi-setup.md) 를 참고하세요.
> (OS 설치, Python/PostgreSQL/Node.js 설정, 포트포워딩, HTTPS까지 전체 절차를 다룹니다)

### systemd 서비스 구성

| 유닛 | 역할 | 동작 |
|------|------|------|
| `investment-advisor-api.service` | API 웹서버 | 상시 기동, 장애 시 자동 재시작 |
| `investment-advisor-analyzer.service` | 분석 배치 | oneshot, 타이머에 의해 트리거 |
| `investment-advisor-analyzer.timer` | 배치 스케줄 | 매일 03:00 (KST) 자동 실행 |

### 활성화

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now investment-advisor-api.service
sudo systemctl enable --now investment-advisor-analyzer.timer

# 상태 확인
systemctl status investment-advisor-api.service --no-pager
systemctl list-timers | grep investment-advisor
```

### 로그 확인

```bash
journalctl -u investment-advisor-api.service -f            # API 실시간
journalctl -u investment-advisor-analyzer.service -n 200   # 최근 배치 결과
```

### 외부 접속

포트포워딩 + DDNS 또는 Nginx 리버스 프록시 + Let's Encrypt HTTPS를 설정하면 외부에서도 접속할 수 있습니다. 자세한 절차는 [`_docs/raspberry-pi-setup.md`](_docs/raspberry-pi-setup.md) 의 8장을 참고하세요.

타이머 시간 변경 예시:
- `*-*-* 06:30:00` → 매일 오전 6시 30분
- `*-*-* 07:00:00,19:00:00` → 하루 2회
- `Mon..Fri *-*-* 07:00:00` → 평일만

---

## 5. DB 조회

```sql
-- 최근 분석 세션
SELECT * FROM analysis_sessions ORDER BY analysis_date DESC LIMIT 5;

-- 오늘의 투자 테마 (시나리오 포함)
SELECT t.theme_name, t.confidence_score, t.time_horizon, t.theme_type, t.theme_validity
FROM investment_themes t
JOIN analysis_sessions s ON t.session_id = s.id
WHERE s.analysis_date = CURRENT_DATE;

-- 오늘의 매수 제안 (스코어 포함, 비중순)
SELECT p.asset_name, p.ticker, p.market, p.conviction, p.target_allocation,
       p.target_price_low, p.target_price_high, p.quant_score, p.sentiment_score
FROM investment_proposals p
JOIN investment_themes t ON p.theme_id = t.id
JOIN analysis_sessions s ON t.session_id = s.id
WHERE s.analysis_date = CURRENT_DATE AND p.action = 'buy'
ORDER BY p.target_allocation DESC;

-- 연속 등장 중인 테마 (streak 기준)
SELECT theme_name, streak_days, appearances, latest_confidence, prev_confidence
FROM theme_tracking
ORDER BY streak_days DESC;

-- 종목 추천 이력
SELECT ticker, asset_name, recommendation_count, latest_action, prev_action,
       first_recommended_date, last_recommended_date
FROM proposal_tracking
ORDER BY recommendation_count DESC;
```

---

## 6. 인증 / 개인화

`AUTH_ENABLED=true`로 활성화하면 JWT 기반 인증 + RBAC 시스템이 동작합니다.

| 역할 | Dashboard/Sessions/Themes/Stocks | Theme Chat | 개인화(워치리스트/알림/메모) | Admin | 사용자 관리 |
|------|----------------------------------|------------|--------------------------|-------|------------|
| Admin | O | O (전체) | O | O | O |
| Moderator | O | O (본인) | O | X | O (제한) |
| User | O | X | O | X | X |
| 비인증 | X (401) | X | X | X | X |

### 개인화 기능

- **관심 종목 (Watchlist)**: Stocks 페이지에서 ★ 토글, Dashboard에서 노란 하이라이트, 전용 관리 페이지
- **알림 구독**: Theme History / Ticker History 페이지에서 "구독" 버튼으로 등록. 배치 분석 시 구독 대상이 등장하면 자동 알림 생성
- **제안 메모**: Stocks 페이지에서 행 펼침 → 하단 메모 영역에서 개인 코멘트 저장
- **알림**: 우측 상단 종 아이콘에 읽지 않은 수 배지 표시, 알림 페이지에서 관리

### 세션 만료 UX

Access Token 만료 시 프론트엔드 fetch 인터셉터가 자동으로 `/auth/refresh`를 호출하여 토큰을 갱신합니다. 갱신 실패 시 로그인 페이지로 리다이렉트됩니다.

---

## 주의사항

- **투자 면책**: AI 생성 투자 제안은 참고 자료일 뿐, 실제 투자 결정은 본인 판단으로 해야 합니다.
- **Rate Limit**: Max 5x 구독이라도 사용량 한도가 있습니다. 하루 1~2회가 안전합니다.
- **로그인 세션**: Claude Code 로그인 세션이 만료되면 자동 실행이 실패합니다. 주기적으로 확인하세요.
- **비밀번호 관리**: `.env` 파일에 DB 비밀번호와 JWT 시크릿을 저장합니다. `.gitignore`에 포함되어 있으므로 Git에 올라가지 않습니다.
- **프로덕션 배포 시**: `JWT_SECRET_KEY` 변경, `COOKIE_SECURE=true`, `ADMIN_PASSWORD` 변경 필수.
