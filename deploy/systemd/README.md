# systemd unit 템플릿 (라즈베리파이 24/7 운영)

투자 분석 시스템의 정기 배치·상시 기동 서비스 systemd 템플릿 모음.
`_docs/raspberry-pi-setup.md` §7 에 문서화된 unit을 **파일 형태로 관리**하여 재사용·재배포·버전 관리 용이하게 함.

설치 전에 아래 **플레이스홀더**를 본인 환경에 맞게 치환해야 한다.

| 플레이스홀더 | 예시 | 설명 |
|---|---|---|
| `__INSTALL_DIR__` | `/home/dzp/dzp-main/program/investment-advisor` | 프로젝트 clone 경로 (루트 디렉터리) |
| `__VENV_PYTHON__` | `/home/dzp/dzp-main/program/investment-advisor/venv/bin/python` | 가상환경 Python 절대경로 |
| `__SYSTEM_USER__` | `dzp` | 서비스 실행 유저 (프로젝트 소유자) |

## Unit 일람

### A. 필수 (기존 시스템)

| 파일 | 역할 | 트리거 |
|---|---|---|
| `investment-advisor-api.service` | FastAPI 웹서버 상시 기동 | `Restart=always` |
| `investment-advisor-analyzer.service` | 일일 분석 배치 (RSS → Stage 1~4 → DB) | `investment-advisor-analyzer.timer` |
| `investment-advisor-analyzer.timer` | 매일 06:30 KST | 위 service 트리거 |

### B. Universe / OHLCV 자동화 (Phase 1a/1b + Phase 7)

| 파일 | 역할 | 트리거 |
|---|---|---|
| `universe-sync-price.service` | universe 가격 + OHLCV 일별 sync (`--mode price`, OHLCV 묻어감) | `universe-sync-price.timer` |
| `universe-sync-price.timer` | 매일 06:30 KST | 위 service 트리거 |
| `universe-sync-indices.service` | 시장 지수(KOSPI/KOSDAQ/SP500/NDX100) OHLCV 일별 sync (`--mode indices`) — `market_regime` 계산 입력 | `universe-sync-indices.timer` |
| `universe-sync-indices.timer` | 매일 06:30 KST | 위 service 트리거 |
| `universe-sync-meta.service` | universe 메타(섹터/시총) 주간 sync (`--mode meta`) | `universe-sync-meta.timer` |
| `universe-sync-meta.timer` | 매주 일요일 07:30 KST | 위 service 트리거 |
| `ohlcv-cleanup.service` | OHLCV retention 초과 row 정리 (`--mode cleanup`) | `ohlcv-cleanup.timer` |
| `ohlcv-cleanup.timer` | 매주 일요일 08:00 KST | 위 service 트리거 |
| `investment-advisor-fundamentals.service` | 펀더멘털 PIT 일별 sync (`--mode fundamentals`) — pykrx KR PER/PBR/EPS/배당률 + yfinance.info US (B-Lite) | `investment-advisor-fundamentals.timer` |
| `investment-advisor-fundamentals.timer` | 매일 06:35 KST (sync-price 5분 후) | 위 service 트리거 |
| `investment-advisor-foreign-flow-sync.service` | 외국인/기관/개인 수급 PIT 일별 sync (`--mode foreign`) — KRX KOSPI+KOSDAQ (v44) | `investment-advisor-foreign-flow-sync.timer` |
| `investment-advisor-foreign-flow-sync.timer` | 매일 06:40 KST (fundamentals 5분 후) | 위 service 트리거 |

### C. 섹터 분류 유지보수 (P1-ext2 이후)

| 파일 | 역할 | 트리거 |
|---|---|---|
| `monthly-sector-refresh.service` | KOSDAQ 신규 상장 industry 백필 + KRX 재정규화 + 분포 리포트 | `monthly-sector-refresh.timer` |
| `monthly-sector-refresh.timer` | 매월 1일 07:45 KST | 위 service 트리거 |

### D. 프리마켓 브리핑 (v34, 2026-04-25 도입)

| 파일 | 역할 | 트리거 |
|---|---|---|
| `pre-market-briefing.service` | 미국 오버나이트 → 한국 수혜 매핑 (`analyzer.briefing_main`) | `pre-market-briefing.timer` |
| `pre-market-briefing.timer` | 매일 06:30 KST | 위 service 트리거 |

## 실행 타임라인 (KST) — 06:30 일괄 정렬

미국 장 마감(EDT 16:00→KST 05:00 / EST 16:00→KST 06:00)에 맞춰 모든 일일 배치를
**06:30**에 시작하고, `After=` 의존성으로 직렬 실행 보장.

```
06:30 (매일 트리거 — 3 sync unit 동시 시작, 별도 테이블이라 충돌 없음)
  → universe-sync-price          ← KRX+US 종목 OHLCV (stock_universe_ohlcv)
  → universe-sync-indices        ← KOSPI/KOSDAQ/SP500/NDX100 인덱스 (market_indices_ohlcv) — regime 입력
  → pre-market-briefing          ← After=sync-price + After=sync-indices
  → investment-advisor-analyzer  ← After=pre-market-briefing  (분석 배치)

06:35  investment-advisor-fundamentals  ← 펀더멘털 PIT (stock_universe_fundamentals) — sync-price 직후
06:40  investment-advisor-foreign-flow-sync  ← 외국인/기관/개인 수급 PIT (stock_universe_foreign_flow) — fundamentals 직후
07:30  universe-sync-meta        ← 주간 메타 (일요일만)
07:45  monthly-sector-refresh    ← 월간 섹터 리프레시 (매월 1일)
08:00  ohlcv-cleanup             ← retention 정리 (일요일만)
```

세 일일 배치(sync/briefing/analyzer)는 **같은 06:30 transaction** 안에서
systemd `After=` 체인으로 직렬 실행됨. 주간/월간 작업은 시간 차이로 충돌 회피.
API 서버는 상시 기동이므로 타이머 없음.

## 설치 절차 (라즈베리파이)

```bash
cd /home/dzp/dzp-main/program/investment-advisor/deploy/systemd

# 1. 플레이스홀더 치환 → /etc/systemd/system/ 에 복사
INSTALL_DIR=/home/dzp/dzp-main/program/investment-advisor
VENV_PYTHON=$INSTALL_DIR/venv/bin/python
SYSTEM_USER=dzp

for f in *.service *.timer; do
  sed -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
      -e "s|__VENV_PYTHON__|$VENV_PYTHON|g" \
      -e "s|__SYSTEM_USER__|$SYSTEM_USER|g" \
      "$f" | sudo tee "/etc/systemd/system/$f" > /dev/null
done

# 2. 권한/의존성 검증
sudo systemctl daemon-reload
for f in /etc/systemd/system/investment-advisor-*.{service,timer} \
         /etc/systemd/system/universe-sync-*.{service,timer} \
         /etc/systemd/system/ohlcv-cleanup.{service,timer}; do
  [ -f "$f" ] && sudo systemd-analyze verify "$f"
done

# 3. 필수 서비스 활성화 (API + 분석 타이머)
sudo systemctl enable --now investment-advisor-api.service
sudo systemctl enable --now investment-advisor-analyzer.timer

# 4. Universe / OHLCV 자동화 활성화
sudo systemctl enable --now universe-sync-price.timer \
                              universe-sync-indices.timer \
                              universe-sync-meta.timer \
                              ohlcv-cleanup.timer \
                              investment-advisor-fundamentals.timer \
                              investment-advisor-foreign-flow-sync.timer

# 5. 섹터 분류 월간 유지보수 활성화 (P1-ext2 이후)
sudo systemctl enable --now monthly-sector-refresh.timer

# 6. 프리마켓 브리핑 활성화 (v34, 2026-04-25 도입)
sudo systemctl enable --now pre-market-briefing.timer

# 6. 상태 확인
sudo systemctl list-timers --all | grep -E "investment-advisor|universe|ohlcv|sector"
sudo systemctl status investment-advisor-api.service
journalctl -u investment-advisor-analyzer.service -n 100 --no-pager
```

## 수동 테스트

```bash
# 타이머 기다리지 않고 즉시 실행
sudo systemctl start investment-advisor-analyzer.service
sudo systemctl start universe-sync-price.service
sudo systemctl start ohlcv-cleanup.service
sudo systemctl start monthly-sector-refresh.service

# 로그 실시간 관찰
journalctl -u universe-sync-price.service -f
journalctl -u monthly-sector-refresh.service -f
journalctl -u investment-advisor-api.service -f
```

## 제거

```bash
sudo systemctl disable --now \
  investment-advisor-api.service \
  investment-advisor-analyzer.timer \
  universe-sync-price.timer \
  universe-sync-indices.timer \
  universe-sync-meta.timer \
  ohlcv-cleanup.timer \
  monthly-sector-refresh.timer \
  investment-advisor-fundamentals.timer \
  investment-advisor-foreign-flow-sync.timer

sudo rm /etc/systemd/system/investment-advisor-*.{service,timer}
sudo rm /etc/systemd/system/universe-sync-*.{service,timer}
sudo rm /etc/systemd/system/ohlcv-cleanup.{service,timer}
sudo rm /etc/systemd/system/monthly-sector-refresh.{service,timer}
sudo systemctl daemon-reload
```

## `raspberry-pi-setup.md`와의 관계

- `_docs/raspberry-pi-setup.md` §7 은 인라인(hand-edit) 방식의 최초 가이드였음.
- 본 디렉터리(`deploy/systemd/`)는 **템플릿 파일 기반 관리**로 이를 대체한다. 향후 unit 추가·수정은 이쪽에 커밋하고, raspberry-pi-setup.md 는 본 디렉터리를 가리키도록 유지.
- 경로 불일치 시 **본 디렉터리가 정본(source of truth)**.

## 웹 UI에서 관리하기 (Admin → 운영 탭)

라즈베리파이 SSH 없이 `/admin` 페이지의 **운영** 탭에서 unit 제어가 가능하다 (start/stop/restart/enable/disable + journalctl 라이브 로그). 백엔드 라우터는 `api/routes/admin_systemd.py`, 화이트리스트는 모듈 상단 `MANAGED_UNITS` 에 정의되어 있다.

### 권한 설정 — sudoers NOPASSWD 화이트리스트

API 서비스는 `__SYSTEM_USER__` 권한으로 실행되므로 `sudo systemctl ...` 호출에 비밀번호가 필요하다. 다음 화이트리스트를 등록하면 비밀번호 없이 지정된 명령만 실행 가능하다.

```bash
sudo visudo -f /etc/sudoers.d/investment-advisor-systemd
```

내용 (`dzp` 자리에 실제 운영 유저 치환):

```
Cmnd_Alias INV_SVC_ACTIONS = \
  /bin/systemctl start   investment-advisor-analyzer.service, \
  /bin/systemctl stop    investment-advisor-analyzer.service, \
  /bin/systemctl restart investment-advisor-analyzer.service, \
  /bin/systemctl start   universe-sync-price.service, \
  /bin/systemctl stop    universe-sync-price.service, \
  /bin/systemctl restart universe-sync-price.service, \
  /bin/systemctl start   universe-sync-indices.service, \
  /bin/systemctl stop    universe-sync-indices.service, \
  /bin/systemctl restart universe-sync-indices.service, \
  /bin/systemctl start   universe-sync-meta.service, \
  /bin/systemctl stop    universe-sync-meta.service, \
  /bin/systemctl restart universe-sync-meta.service, \
  /bin/systemctl start   ohlcv-cleanup.service, \
  /bin/systemctl stop    ohlcv-cleanup.service, \
  /bin/systemctl restart ohlcv-cleanup.service, \
  /bin/systemctl start   monthly-sector-refresh.service, \
  /bin/systemctl stop    monthly-sector-refresh.service, \
  /bin/systemctl restart monthly-sector-refresh.service, \
  /bin/systemctl start   pre-market-briefing.service, \
  /bin/systemctl stop    pre-market-briefing.service, \
  /bin/systemctl restart pre-market-briefing.service, \
  /bin/systemctl start   investment-advisor-fundamentals.service, \
  /bin/systemctl stop    investment-advisor-fundamentals.service, \
  /bin/systemctl restart investment-advisor-fundamentals.service, \
  /bin/systemctl start   investment-advisor-foreign-flow-sync.service, \
  /bin/systemctl stop    investment-advisor-foreign-flow-sync.service, \
  /bin/systemctl restart investment-advisor-foreign-flow-sync.service

Cmnd_Alias INV_TIMER_ACTIONS = \
  /bin/systemctl enable  --now investment-advisor-analyzer.timer, \
  /bin/systemctl disable --now investment-advisor-analyzer.timer, \
  /bin/systemctl enable  --now universe-sync-price.timer, \
  /bin/systemctl disable --now universe-sync-price.timer, \
  /bin/systemctl enable  --now universe-sync-indices.timer, \
  /bin/systemctl disable --now universe-sync-indices.timer, \
  /bin/systemctl enable  --now universe-sync-meta.timer, \
  /bin/systemctl disable --now universe-sync-meta.timer, \
  /bin/systemctl enable  --now ohlcv-cleanup.timer, \
  /bin/systemctl disable --now ohlcv-cleanup.timer, \
  /bin/systemctl enable  --now monthly-sector-refresh.timer, \
  /bin/systemctl disable --now monthly-sector-refresh.timer, \
  /bin/systemctl enable  --now pre-market-briefing.timer, \
  /bin/systemctl disable --now pre-market-briefing.timer, \
  /bin/systemctl enable  --now investment-advisor-fundamentals.timer, \
  /bin/systemctl disable --now investment-advisor-fundamentals.timer, \
  /bin/systemctl enable  --now investment-advisor-foreign-flow-sync.timer, \
  /bin/systemctl disable --now investment-advisor-foreign-flow-sync.timer

dzp ALL=(root) NOPASSWD: INV_SVC_ACTIONS, INV_TIMER_ACTIONS
```

검증:

```bash
sudo visudo -c -f /etc/sudoers.d/investment-advisor-systemd
sudo chmod 0440 /etc/sudoers.d/investment-advisor-systemd
sudo -u dzp sudo -n systemctl start investment-advisor-analyzer.service   # 비밀번호 묻지 않으면 OK
```

### journalctl 권한

웹 UI의 "로그" 버튼이 `journalctl -u <unit> -f` 를 띄우려면 운영 유저가 `adm` 또는 `systemd-journal` 그룹에 속해야 한다:

```bash
sudo usermod -aG adm,systemd-journal dzp
# 재로그인 필요
```

### 보안 주의

- **API 자체 service**(`investment-advisor-api.service`) 는 sudoers에 포함하지 않는다 — 백엔드에서 self_protected 로 차단한다 (이중 방어). 웹 UI의 API 카드는 상태/로그만 보여주고 모든 mutation 버튼이 비활성화된다.
- 화이트리스트 외 systemctl 명령(`daemon-reload`, `mask`, 임의 unit 등) 절대 추가 금지 — 권한 확장 위험.
- `sudoers.d/*` 파일 권한은 반드시 `0440`. 그렇지 않으면 sudo가 무시한다.
- 모든 mutation 호출은 `admin_audit_logs` (v17) 에 기록된다 (action: `systemd_<verb>` / `systemd_action_failed` / `systemd_invalid_target` / `systemd_self_protected_violation`).

### 미지원 환경 동작

- Windows / non-Linux: 운영 탭에 안내 메시지 노출, 모든 systemd 엔드포인트 503 반환.
- 분석 실행은 systemd 미지원 시 자동으로 in-process subprocess(`python -m analyzer.main`) 경로로 fallback (γ 정책). 도구 탭의 "분석 실행" 버튼 한 개로 환경 자동 분기.
