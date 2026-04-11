# Investment Advisor — Claude Code SDK 기반 투자 분석 시스템

매일 글로벌 뉴스를 수집하고 Claude Code SDK로 멀티스테이지 분석을 수행하여 투자 테마와 제안을 PostgreSQL에 저장합니다.
FastAPI + Jinja2 웹 UI로 분석 결과를 조회하고, 일자별 변화를 추적할 수 있습니다.

> **과금**: Claude Code SDK는 Claude Code 구독(Max 5x 등) 사용량에 포함됩니다.
> API 키 방식과 달리 별도 토큰 과금이 없습니다.

---

## 프로젝트 구조

```
investment-advisor/
├── .env.example             ← 환경변수 템플릿 (.env로 복사하여 사용)
├── .gitignore
├── requirements.txt
├── shared/                  ← 공용 모듈 (설정, DB)
│   ├── config.py            ← .env 자동 로드 + dataclass 설정
│   ├── db.py                ← 스키마 마이그레이션(v1~v3) + 저장 + tracking
│   └── pg_setup.py          ← PostgreSQL 자동 설치
├── analyzer/                ← 멀티스테이지 분석 서비스 (배치)
│   ├── main.py              ← 분석 엔트리포인트
│   ├── news_collector.py    ← RSS 뉴스 수집
│   ├── analyzer.py          ← 2단계 파이프라인 (테마 발굴 → 종목 심층분석)
│   └── prompts.py           ← 스테이지별 프롬프트 템플릿
├── api/                     ← FastAPI 웹서비스 (상시 기동)
│   ├── main.py              ← API 엔트리포인트
│   ├── routes/
│   │   ├── pages.py         ← HTML 페이지 (Dashboard, 히스토리 등)
│   │   ├── sessions.py      ← JSON API: 세션
│   │   ├── themes.py        ← JSON API: 테마
│   │   └── proposals.py     ← JSON API: 투자 제안 + 종목 심층분석
│   ├── templates/           ← Jinja2 HTML 템플릿 (다크 테마)
│   └── static/css/          ← 스타일시트
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

`.env` 파일을 열어 DB 접속 정보를 환경에 맞게 수정합니다:

```
DB_HOST=localhost
DB_PORT=5432
DB_NAME=investment_advisor
DB_USER=postgres
DB_PASSWORD=your_password_here
```

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
| Dashboard | `/` | 투자 신호, 어제 대비 변화, 테마별 제안 |
| Sessions | `/pages/sessions` | 분석 세션 목록 (리스크 온도 포함) |
| Session Detail | `/pages/sessions/{id}` | 이슈(시계별 영향) + 테마(시나리오/매크로) + 제안(스코어) |
| Themes | `/pages/themes` | 시계/신뢰도/키워드 필터, tracking 뱃지 |
| Theme History | `/pages/themes/history/{key}` | 특정 테마의 일자별 신뢰도·시나리오·종목 변화 |
| Proposals | `/pages/proposals` | action/자산유형/확신도/티커 필터 |
| Ticker History | `/pages/proposals/history/{ticker}` | 특정 종목의 일자별 추천 이력 |

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

---

## 3. 분석 파이프라인

```
[RSS 뉴스 수집] → [Stage 1] → [Stage 2] → [DB 저장 + tracking]
```

- **Stage 1**: 뉴스 기반 이슈 분석(시계별 영향, 과거 유사 사례) → 테마 발굴(시나리오, 매크로 변수) → 투자 제안(가격 목표, 스코어)
- **Stage 2**: 상위 테마의 핵심 종목을 5관점 심층분석 (펀더멘털·산업·모멘텀·퀀트·리스크) → 센티먼트/퀀트 스코어 업데이트
- **Tracking**: 저장 시 `theme_tracking`/`proposal_tracking` 자동 갱신 → 연속 등장일수, 신뢰도 변동, 액션 변경 추적

---

## 4. 자동 실행 설정 (라즈베리파이)

### Systemd 서비스

```bash
sudo tee /etc/systemd/system/investment-advisor.service << 'EOF'
[Unit]
Description=Investment Advisor Analysis
After=postgresql.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=pi
WorkingDirectory=/home/pi/investment-advisor
ExecStart=/home/pi/investment-advisor/venv/bin/python -m analyzer.main
Environment=HOME=/home/pi
StandardOutput=journal
StandardError=journal
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
EOF
```

### 타이머 (매일 오전 7시)

```bash
sudo tee /etc/systemd/system/investment-advisor.timer << 'EOF'
[Unit]
Description=Run investment analysis daily at 7 AM

[Timer]
OnCalendar=*-*-* 07:00:00
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
EOF
```

### 활성화

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now investment-advisor.timer

# 상태 확인
systemctl status investment-advisor.timer
journalctl -u investment-advisor --since today
```

실행 시간 변경 예시:
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

## 주의사항

- **투자 면책**: AI 생성 투자 제안은 참고 자료일 뿐, 실제 투자 결정은 본인 판단으로 해야 합니다.
- **Rate Limit**: Max 5x 구독이라도 사용량 한도가 있습니다. 하루 1~2회가 안전합니다.
- **로그인 세션**: Claude Code 로그인 세션이 만료되면 자동 실행이 실패합니다. 주기적으로 확인하세요.
- **비밀번호 관리**: `.env` 파일에 DB 비밀번호를 저장합니다. `.gitignore`에 포함되어 있으므로 Git에 올라가지 않습니다.
