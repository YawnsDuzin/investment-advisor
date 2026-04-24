# 월간 섹터 리프레시 설정 가이드

P1-ext2(2026-04-24) 완료 후, KOSDAQ 신규 상장·yfinance industry 갱신분을 자동으로 반영하기 위한 배치 작업. **2가지 운영 방식** 지원:

- **방식 A**: 로컬 systemd timer (라즈베리파이 24/7 운영 환경 — 권장)
- **방식 B**: Anthropic `/schedule` routine (원격 에이전트 — 로컬 인프라 없이도 가능)

---

## 1. 작업 내용

`tools/monthly_sector_refresh.py` 가 단일 엔트리. 실행 내용:

```
Stage A  backfill_industry_kr()       — yfinance로 industry=NULL 종목 채움
Stage B  renormalize_sectors --market KRX --apply
                                       — _INDUSTRY_OVERRIDES/_KR_TICKER_OVERRIDES 재평가
Stage C  분포 스냅샷 비교 + 이상치 감지 — 10% 이상 변동 버킷 WARN
```

예상 소요: **~25분** (백필 21분 + 재정규화 1초 + 리포트 3분)
예상 변경 건수: 월 **20~50건** (신규 상장 10~30 + industry 갱신 10~20)

CLI:
```bash
python -m tools.monthly_sector_refresh              # 실제 실행
python -m tools.monthly_sector_refresh --dry-run    # 변경 없이 리포트만
python -m tools.monthly_sector_refresh --skip-backfill   # 재정규화만 (긴급)
python -m tools.monthly_sector_refresh --limit 50        # 테스트용 소량
```

---

## 2. 방식 A — systemd timer (라즈베리파이 권장)

### 2.1 준비

`deploy/systemd/monthly-sector-refresh.{service,timer}` 이미 포함됨.

### 2.2 배포

기존 systemd 설치 스크립트에 자동 포함됨. 이미 시스템이 돌고 있는 경우 추가만:

```bash
cd /home/pi/investment-advisor/deploy/systemd

INSTALL_DIR=/home/pi/investment-advisor
VENV_PYTHON=$INSTALL_DIR/venv/bin/python
SYSTEM_USER=dzp

for f in monthly-sector-refresh.service monthly-sector-refresh.timer; do
  sed -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
      -e "s|__VENV_PYTHON__|$VENV_PYTHON|g" \
      -e "s|__SYSTEM_USER__|$SYSTEM_USER|g" \
      "$f" | sudo tee "/etc/systemd/system/$f" > /dev/null
done

sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/monthly-sector-refresh.service
sudo systemctl enable --now monthly-sector-refresh.timer
```

### 2.3 스케줄

```
OnCalendar=*-*-01 03:45:00 Asia/Seoul
```

매월 1일 03:45 KST. 다른 배치와의 타이밍:

```
02:30  universe-sync-price       (매일)
03:00  investment-advisor-analyzer  (매일)
03:30  universe-sync-meta        (일요일만)
03:45  monthly-sector-refresh    (매월 1일만)  ← 추가
04:00  ohlcv-cleanup             (일요일만)
```

### 2.4 확인·수동 실행

```bash
# 스케줄 등록 확인
sudo systemctl list-timers monthly-sector-refresh.timer

# 타이머 기다리지 않고 즉시 1회 실행
sudo systemctl start monthly-sector-refresh.service

# 실행 중 로그
journalctl -u monthly-sector-refresh.service -f

# 직전 실행 결과
journalctl -u monthly-sector-refresh.service -n 200 --no-pager
```

### 2.5 실패 시 처리

- 단일 실행은 `Restart=no` — 실패해도 재시도 안 함. `Persistent=true`이므로 다음 달 다시 시도.
- `journalctl`로 실패 원인 확인:
  - yfinance rate limit/네트워크: 다음 달 재시도면 복구됨
  - DB 연결: `shared/config.py` `.env` 확인
  - Python 경로: `ExecStart`의 `__VENV_PYTHON__` 치환 확인

### 2.6 장점/단점

**장점**
- 외부 의존 없음 (라즈베리파이에서 완결)
- 실행 로그는 `journalctl`에 남아 조회 쉬움
- 즉시 수동 트리거 가능 (`systemctl start`)

**단점**
- 라즈베리파이가 죽으면 배치 중단
- 결과 알림을 직접 구현해야 함 (cron 메일 또는 API 호출)

---

## 3. 방식 B — Anthropic `/schedule` routine (원격)

Claude Code의 `schedule` 스킬을 통해 원격 서버에 routine을 등록하면, 지정된 cron에 따라 agentic sandbox에서 자동 실행된다.

### 3.1 생성 명령 (Claude Code에서)

```
/schedule create
  name:     monthly-sector-refresh
  cron:     0 2 1 * *   (매월 1일 02:00 UTC = 11:00 KST)
  repo:     yawnsduzin/investment-advisor
  branch:   main
  prompt:
    월간 섹터 분류 리프레시를 실행해줘. 순서:
    1. `python -m tools.monthly_sector_refresh` 실행
    2. 결과 리포트(분포 변화·이상치)를 본문으로 PR 생성
    3. `other` 버킷이 10% 이상 증가했다면 제목에 ⚠ 표시
    4. 변경이 10건 미만이면 PR 대신 이슈 주석으로 리포트
```

### 3.2 스케줄

cron 표현식으로 지정. `0 2 1 * *` = 매월 1일 UTC 02:00 = **KST 11:00**.
로컬 systemd(03:45)와 시간 겹치지 않게 주의 — 한 가지 방식만 선택하는 것이 기본.

### 3.3 실행 흐름 (내부)

1. Anthropic 원격 에이전트가 지정 시각에 wake up
2. 지정 repo/branch를 sandbox에 clone
3. `.env` 또는 주입된 시크릿으로 DB 접속 (⚠ 주의 — §4)
4. prompt 수행: 명령 실행 → 결과 수집 → PR/이슈 생성

### 3.4 ⚠ 제약 사항 (중요)

**원격 에이전트는 내 로컬 PostgreSQL(`localhost:5432`)에 접속할 수 없다.**

방식 B를 실제 사용하려면:
- **옵션 B1**: DB를 외부 접근 가능 인프라로 이전 (AWS RDS, Neon, Supabase 등)
- **옵션 B2**: SSH 터널 + 포트포워딩 (보안 복잡)
- **옵션 B3**: 라즈베리파이에 public endpoint 노출 (5432 포트포워딩은 [CLAUDE.md](../CLAUDE.md)에서 금지 — SSH 터널만 허용)

현재 시스템은 **라즈베리파이 로컬 DB** 구조라 방식 B는 **실질적으로 부적합**.
단 DB가 클라우드로 이전될 경우 방식 B의 장점(로컬 인프라 없이도 PR 자동 생성)이 빛을 발함.

### 3.5 장점/단점

**장점**
- 라즈베리파이 다운타임 영향 없음
- 결과가 자동으로 GitHub PR/이슈로 남음 — 리뷰 워크플로우 자연스러움
- 여러 리포지토리를 중앙 관리 가능

**단점**
- 외부 DB 접근 가능해야 함 (현재 로컬 PostgreSQL과 부적합)
- Anthropic 서비스 가용성에 의존
- yfinance rate limit가 원격 IP 기준이라 다른 agent들과 공유 → 429 가능

---

## 4. 권장 결정

| 조건 | 권장 방식 |
|---|---|
| 라즈베리파이 24/7 운영 중 + 로컬 DB | **방식 A (systemd)** ✅ |
| 클라우드 DB 또는 public endpoint 있음 | 방식 A 또는 B 선택 |
| 로컬 인프라 없이 GitHub 중심 워크플로우 | 방식 B (단 DB 접근 선결) |
| 자동화 불필요, 수동 월 1회 실행 가능 | 둘 다 미사용, 수동 실행 |

**현재 시스템 상태 기준 권장**: **방식 A (systemd timer)**.
`_docs/raspberry-pi-setup.md` 와 `deploy/systemd/README.md` 인프라가 이미 구축돼 있으므로 `monthly-sector-refresh.timer` 하나만 `enable --now` 하면 끝.

---

## 5. 수동 테스트 (설치 전 smoke test)

로컬(Windows/Linux) 모두 동일:

```bash
# 변경 없이 리포트만
python -m tools.monthly_sector_refresh --dry-run

# 소량 (20건만 백필 후 재정규화)
python -m tools.monthly_sector_refresh --limit 20

# 긴급 재정규화만 (yfinance 건너뜀)
python -m tools.monthly_sector_refresh --skip-backfill
```

dry-run 결과 예시:
```
월간 섹터 리프레시 리포트 (2026-04-24 16:24 KST)
커버리지: 84.8% → 84.8% (industry NULL 498 → 498, 감소 0)
분포 변화 없음
이상치 없음 (임계값 10%)
총 소요: 0.2s
```

---

## 6. 관련 파일

```
tools/monthly_sector_refresh.py                             — 메인 스크립트
deploy/systemd/monthly-sector-refresh.service               — systemd unit
deploy/systemd/monthly-sector-refresh.timer                 — 스케줄 트리거
deploy/systemd/README.md                                    — 설치 가이드 (Section C)
_docs/20260424155900_sector_taxonomy_overhaul.md            — 본 개편 전체 기록
_docs/20260424162800_monthly_sector_refresh_setup.md        — 본 문서
```
