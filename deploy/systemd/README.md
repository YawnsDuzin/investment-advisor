# systemd unit 템플릿 (라즈베리파이 24/7 운영)

투자 분석 시스템의 정기 배치·상시 기동 서비스 systemd 템플릿 모음.
`_docs/raspberry-pi-setup.md` §7 에 문서화된 unit을 **파일 형태로 관리**하여 재사용·재배포·버전 관리 용이하게 함.

설치 전에 아래 **플레이스홀더**를 본인 환경에 맞게 치환해야 한다.

| 플레이스홀더 | 예시 | 설명 |
|---|---|---|
| `__INSTALL_DIR__` | `/home/pi/investment-advisor` | 프로젝트 clone 경로 (루트 디렉터리) |
| `__VENV_PYTHON__` | `/home/pi/investment-advisor/venv/bin/python` | 가상환경 Python 절대경로 |
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
| `universe-sync-meta.service` | universe 메타(섹터/시총) 주간 sync (`--mode meta`) | `universe-sync-meta.timer` |
| `universe-sync-meta.timer` | 매주 일요일 07:30 KST | 위 service 트리거 |
| `ohlcv-cleanup.service` | OHLCV retention 초과 row 정리 (`--mode cleanup`) | `ohlcv-cleanup.timer` |
| `ohlcv-cleanup.timer` | 매주 일요일 08:00 KST | 위 service 트리거 |

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
06:30 (매일 트리거)
  → universe-sync-price          ← KRX+US OHLCV 일괄 sync (먼저 종료)
  → pre-market-briefing          ← After=universe-sync-price (sync 끝난 후)
  → investment-advisor-analyzer  ← After=pre-market-briefing  (분석 배치)

07:30  universe-sync-meta        ← 주간 메타 (일요일만)
07:45  monthly-sector-refresh    ← 월간 섹터 리프레시 (매월 1일)
08:00  ohlcv-cleanup             ← retention 정리 (일요일만)
```

세 일일 배치(sync/briefing/analyzer)는 **같은 06:30 transaction** 안에서
systemd `After=` 체인으로 직렬 실행됨. 주간/월간 작업은 시간 차이로 충돌 회피.
API 서버는 상시 기동이므로 타이머 없음.

## 설치 절차 (라즈베리파이)

```bash
cd /home/pi/investment-advisor/deploy/systemd

# 1. 플레이스홀더 치환 → /etc/systemd/system/ 에 복사
INSTALL_DIR=/home/pi/investment-advisor
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
                              universe-sync-meta.timer \
                              ohlcv-cleanup.timer

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
  universe-sync-meta.timer \
  ohlcv-cleanup.timer \
  monthly-sector-refresh.timer

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
