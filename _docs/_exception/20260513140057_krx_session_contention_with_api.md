# 2026-05-13 KRX 세션 점유 충돌 — API ↔ 새벽 배치

- **발생 일시**: 2026-05-13 06:30 KST (`pre-market-briefing.timer` + `investment-advisor-analyzer.timer` 동시 트리거)
- **대상 스테이지**: API startup / Stage 1 사전 import / Briefing [4/5] LLM 호출 진입
- **모델**: 무관 (LLM 호출 이전에 사망)
- **상태**: ✅ 해결됨 (lazy import 패치 — 커밋 미생성, 다음 commit 시 묶음)
- **관련 아카이브**: 없음 (`ai_query_archive` 진입 전 단계). 라즈베리 `journalctl -u pre-market-briefing.service` + `-u investment-advisor-analyzer.service` 로그 참조.

---

## 증상

### pre-market-briefing.service (06:30:26 ~ 06:30:34)

```
[INFO] [4/5] Claude SDK 브리핑 쿼리...
KRX 로그인 시도...
  로그인 ID: yawnsduzin
[ERROR] 브리핑 파이프라인 예외: Expecting value: line 1 column 1 (char 0)
systemd[1]: pre-market-briefing.service: Main process exited, code=exited, status=1/FAILURE
```

`KRX 로그인 완료.` 가 찍히지 않음 — 정상 흐름이라면 `로그인 시간 / 만료 시간` 3줄이 따라붙는다.

### investment-advisor-analyzer.service (06:30:35 ~ 06:30:41)

```
KRX 로그인 시도...
  로그인 ID: yawnsduzin
Traceback (most recent call last):
  File "analyzer/main.py", line 27, in <module>
    from analyzer.analyzer import run_full_analysis, translate_news
  File "analyzer/analyzer.py", line 27, in <module>
    from analyzer.stock_data import fetch_multiple_stocks, ...
  File "analyzer/stock_data.py", line 13, in <module>
    from pykrx import stock as pykrx_stock
  ...
  File ".../pykrx/website/comm/auth.py", line 153, in login_krx
    resp.json()
requests.exceptions.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
```

briefing 은 try/except 가 우아하게 받아냈고, analyzer 는 **import 단계에서 터져 try/except 진입조차 못 했다**.

### 결정적 단서

사용자가 `sudo systemctl restart investment-advisor-api` 로 **API 서비스**를 재시작한 후 briefing/analyzer 를 수동 재실행하니 정상 처리됨. KRX 측 장애가 아니라 **API 프로세스가 KRX 세션을 점유**하고 있었음을 시사.

확인:
- data.krx.co.kr 수동 로그인 정상 → 계정·비번 이상 없음
- 라즈베리 `pip show pykrx` → 1.2.7 (KRX 인증 요구 버전)
- 5-10·5-11·5-12 는 모두 정상 작동 → KRX 서버 장기 장애 아님

---

## 근본 원인

### 1. 직접 원인 — KRX 측 동일 ID 중복 세션 거부

KRX 인증 endpoint 가 같은 ID 로 이미 활성 세션이 있는 상태에서 또 로그인 시도가 들어오면 **본문 없는 빈 응답**으로 거부. pykrx 1.2.7 `auth.py:153` 의 `resp.json()` 이 빈 문자열을 파싱하다 `Expecting value: line 1 column 1 (char 0)` 로 사망.

### 2. 구조적 원인 — pykrx 의 import 사이드이펙트

`pykrx` 패키지는 `from pykrx import stock` 만으로도 `pykrx/website/comm/__init__.py` → `webio.py` → `auth.py:192 _session = build_krx_session()` 까지 모듈 로딩 도중 자동 실행 → **KRX 로그인 자동 수행**. 라이브러리 디자인. 사용자가 명시적으로 로그인 함수를 호출하지 않아도 import 만으로 세션이 만들어진다.

### 3. 근본 원인 — API 가 pykrx 의존을 top-level import 로 보유

API 라우터 [`api/routes/stocks.py:9-10`](../../api/routes/stocks.py) 가 모듈 최상단에서

```python
from analyzer.krx_data import fetch_krx_extended, fetch_us_extended
from analyzer.stock_data import fetch_fundamentals
```

두 모듈 모두 [`analyzer/stock_data.py:13`](../../analyzer/stock_data.py) / [`analyzer/krx_data.py:16`](../../analyzer/krx_data.py) 에서 top-level `from pykrx import stock` 을 수행 → **API startup 시 pykrx 자동 로그인 → API 프로세스가 24/7 KRX 세션 점유**.

새벽 06:30 에 briefing/analyzer 가 같은 `KRX_ID` 로 새로 로그인하려 하면 KRX 가 거부. 5-10~5-12 는 API 가 우연히 어떤 시점에 세션이 비어 있었던 우연. 5-13 부터는 안정적으로 점유 → 매일 충돌 예약된 셈.

---

## 수정 사항

### Layer 1: pykrx import 의 lazy 화 — 근본 처방

#### [`analyzer/stock_data.py`](../../analyzer/stock_data.py)

- top-level `from pykrx import stock as pykrx_stock` 제거
- `_get_pykrx_stock()` lazy 헬퍼 신설 (sentinel: `None`=미시도, `False`=import 실패, `module`=성공)
- `_check_pykrx()` 가 헬퍼 경유 — 첫 호출 시점까지 import 지연
- 사용 사이트 6곳 (`_pykrx_fetch_price`, `_pykrx_fetch_history`, `_build_krx_lookup`, `validate_krx_tickers`) 모두 `pk = _get_pykrx_stock()` 패턴으로 변경

#### [`analyzer/krx_data.py`](../../analyzer/krx_data.py)

- top-level `from pykrx import stock/bond` 제거
- `stock_data._get_pykrx_stock` 재사용 + `_get_pykrx_bond()` 신설
- 사용 사이트 12곳 (`fetch_investor_trading`, `fetch_short_selling`, `fetch_korea_bond_yields`, `fetch_market_cap_info`, `_build_index_cache`, `fetch_theme_etf_flows`, `_fetch_krx_series`) 모두 헬퍼 경유

#### [`api/routes/stocks.py`](../../api/routes/stocks.py)

- top-level import 는 유지 (테스트 monkey-patch 호환성)
- 단, 안전성 근거 주석 추가 — 두 의존 모듈이 lazy 화된 이상 이 import 는 pykrx 사이드이펙트를 트리거하지 않음

### Layer 2: 운영 절차

라즈베리 배포 후 1회 적용:
```bash
cd /home/dzp/dzp-main/program/investment-advisor
git pull
sudo systemctl restart investment-advisor-api
```
이후 API startup 에서 pykrx 모듈이 로드되지 않아 KRX 세션 점유 사라짐. 실제 종목 페이지에서 KRX 데이터 요청이 들어올 때만 lazy import 가 트리거.

### 설정·스키마 변경

없음. 코드 변경만.

---

## 검증

### 1. 회귀 테스트

```bash
python -m pytest -q
```

- pykrx 관련 88건 (`-k "pykrx or stock or krx or fundamentals"`) **전부 통과**
- 전체 회귀에서 본 변경으로 깨진 건 0건
  - 변경 전 13 fail → 변경 후 10 fail (정확히 우리가 깨뜨렸다 복구한 stock_cockpit 3건 차이)
  - 남은 10 fail (track_record, admin_tier_audit, chat_stream_broker, optimizations) 은 본 작업 무관 — 사전부터 깨져있던 것

### 2. lazy 헬퍼 동작 검증

- `_get_pykrx_stock()` 미호출 상태에서 stock_data/krx_data 모듈 import 시 pykrx 가 로드되지 않음
- `_check_pykrx()` 첫 호출 시점에서 lazy import 트리거되며 KRX 로그인 발생
- 두 번째 호출부터는 sentinel 캐시 사용 (중복 import 없음)

### 3. 운영기 검증 항목

- API 재기동 후 `lsof -p $(pidof python ... investment-advisor-api) | grep pykrx` 로 pykrx 모듈 로딩 여부 확인 (요청 들어오기 전엔 없어야 정상)
- 다음날 06:30 배치 정상 완료 여부 (`systemctl status pre-market-briefing.service investment-advisor-analyzer.service`)

---

## 후속 모니터링

### 관찰할 지표

- 매일 06:30 ~ 06:40 의 briefing/analyzer service exit code (정상 0)
- `journalctl -u pre-market-briefing.service` 의 `KRX 로그인 완료.` 라인 존재 여부
- `app_runs` 테이블의 `run_type='briefing'` 행 `status='success'` 비율

### 재발 시 에스컬레이션

1. KRX 측 실제 장애 가능성 확인 — 라즈베리에서 `python -c "from pykrx import stock; print(stock.get_market_ticker_list()[:5])"`
2. 같은 `Expecting value` 패턴이라면 KRX 인증 API 변경 또는 IP 차단 가능성 → pykrx 1.2.8 업그레이드 시도
3. pykrx 신버전도 안 풀리면 KRX 의존 함수를 yfinance 폴백으로 대체

### 잠재 위험 (현재 패치 범위 밖)

- `analyzer/foreign_flow_sync.py`, `analyzer/fundamentals_sync.py`, `analyzer/universe_sync.py` 도 top-level pykrx import 보유
- 이들은 API 가 import 하지 않으므로 본 회귀와 무관
- 단 배치들끼리 시각이 겹치면 자기들끼리 KRX 세션 경합 가능 — systemd timer 시각 분리(`OnCalendar` 차등)로 회피 권장
- 필요 시 별도 작업으로 이들 모듈도 lazy 화 가능

### 관련 패턴 (Lessons Learned)

본 README "자주 발생하는 패턴" 섹션 3번 "KRX 로그인 실패 연쇄" 에 본 케이스 추가 — **import 부수효과로 인한 프로세스 간 세션 경합** 은 같은 KRX 로그인 실패 카테고리지만 진원이 다르다.
