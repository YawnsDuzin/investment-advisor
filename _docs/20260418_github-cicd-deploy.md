# GitHub CI/CD 자동배포 가이드 (라즈베리파이)

> 대상: Raspberry Pi 4 + GitHub Actions Self-hosted Runner
> 프로젝트: [investment-advisor](https://github.com/YawnsDuzin/investment-advisor)
> 최종 갱신: 2026-04-18

본 문서는 라즈베리파이에서 실행 중인 FastAPI 웹서비스를 **GitHub CI/CD로 자동배포**하기 위한 가이드다.
`main` 브랜치에 코드가 머지되면 라즈베리파이가 자동으로 코드를 업데이트하고 서비스를 재시작한다.

사전 조건: [raspberry-pi-setup.md](raspberry-pi-setup.md)의 systemd 서비스 등록 및 정상 운영이 완료된 상태.

---

## 목차

1. [방식 비교 및 선택](#1-방식-비교-및-선택)
2. [Self-hosted Runner 설치](#2-self-hosted-runner-설치)
3. [sudoers 권한 설정](#3-sudoers-권한-설정)
4. [GitHub Actions Workflow 작성](#4-github-actions-workflow-작성)
5. [GitHub Secrets 등록](#5-github-secrets-등록)
6. [배포 흐름 검증](#6-배포-흐름-검증)
7. [롤백 전략](#7-롤백-전략)
8. [보안 고려사항](#8-보안-고려사항)
9. [운영 체크리스트](#9-운영-체크리스트)
10. [트러블슈팅 FAQ](#10-트러블슈팅-faq)
11. [(대안) SSH 방식 배포](#11-대안-ssh-방식-배포)

---

## 1. 방식 비교 및 선택

| 항목 | Self-hosted Runner (추천) | SSH 원격 배포 |
|------|--------------------------|--------------|
| 포트 오픈 | 불필요 (아웃바운드 폴링) | SSH 포트 인바운드 노출 필요 |
| 보안 | Runner ↔ GitHub 간 TLS | SSH 키 관리 + 포트 노출 |
| NAT/공유기 | 추가 설정 없음 | 포트포워딩 또는 Cloudflare Tunnel 필요 |
| 메모리 사용 | ~100MB (Runner 프로세스) | 없음 |
| 설정 난이도 | GitHub UI 안내 따라 설치 | SSH 키 생성 + Secrets 등록 |
| 장애 복구 | systemd로 자동 재시작 | 네트워크 단절 시 배포 불가 |

**결론:** 라즈베리파이가 NAT(공유기) 뒤에 있고 별도 포트 노출 없이 운영하려면 **Self-hosted Runner**가 적합하다.
SSH 방식은 이미 외부 SSH 접속이 구성된 경우 대안으로 사용할 수 있다 → [11장 참조](#11-대안-ssh-방식-배포).

---

## 2. Self-hosted Runner 설치

### 2-1. GitHub에서 Runner 토큰 발급

1. GitHub 저장소 → **Settings** → **Actions** → **Runners**
2. **New self-hosted runner** 클릭
3. OS: **Linux**, Architecture: **ARM64** 선택
4. 화면에 표시되는 토큰과 명령어를 복사

### 2-2. 라즈베리파이에서 Runner 설치

```bash
# 작업 디렉토리 생성
mkdir -p ~/actions-runner && cd ~/actions-runner

# 최신 Runner 다운로드 (ARM64)
# GitHub 안내 페이지의 URL을 그대로 사용
curl -o actions-runner-linux-arm64.tar.gz -L \
  https://github.com/actions/runner/releases/download/v2.XXX.X/actions-runner-linux-arm64-2.XXX.X.tar.gz

# 압축 해제
tar xzf ./actions-runner-linux-arm64.tar.gz

# 의존성 설치
sudo ./bin/installdependencies.sh
```

### 2-3. Runner 등록

```bash
# GitHub에서 복사한 토큰으로 등록
./config.sh \
  --url https://github.com/YawnsDuzin/investment-advisor \
  --token AXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX \
  --name rpi4-runner \
  --labels self-hosted,rpi4,arm64 \
  --work _work
```

**확인:**
```bash
# 등록 완료 메시지
# √ Runner successfully added
# √ Runner connection is good
```

### 2-4. systemd 서비스로 등록 (재부팅 후 자동 시작)

```bash
# Runner 디렉토리에서 실행
cd ~/actions-runner
sudo ./svc.sh install pi    # 'pi'는 실행 사용자명
sudo ./svc.sh start
sudo ./svc.sh status
```

**확인:**
```bash
sudo systemctl status actions.runner.YawnsDuzin-investment-advisor.rpi4-runner.service
# Active: active (running) 이면 정상
```

**재부팅 테스트:**
```bash
sudo reboot
# 재부팅 후
sudo systemctl is-active actions.runner.*.service
# active
```

---

## 3. sudoers 권한 설정

Runner가 `systemctl restart`를 비밀번호 없이 실행할 수 있도록 최소 권한만 부여한다.

```bash
sudo visudo -f /etc/sudoers.d/github-runner
```

아래 내용 입력:
```
# GitHub Actions Runner - investment-advisor 배포용 최소 권한
pi ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart investment-advisor-api.service
pi ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart investment-advisor-analyzer.service
pi ALL=(ALL) NOPASSWD: /usr/bin/systemctl is-active investment-advisor-api.service
pi ALL=(ALL) NOPASSWD: /usr/bin/systemctl is-active investment-advisor-analyzer.service
pi ALL=(ALL) NOPASSWD: /usr/bin/systemctl status investment-advisor-api.service
```

**확인:**
```bash
# 비밀번호 프롬프트 없이 실행되어야 함
sudo systemctl is-active investment-advisor-api.service
```

> **주의:** `pi ALL=(ALL) NOPASSWD: ALL`처럼 전체 권한을 주면 안 된다. 서비스 제어 명령만 허용.

---

## 4. GitHub Actions Workflow 작성

### 4-1. 메인 배포 Workflow

프로젝트 루트에 `.github/workflows/deploy.yml` 생성:

```yaml
name: Deploy to Raspberry Pi

on:
  push:
    branches: [main]
    paths-ignore:
      - '_docs/**'
      - '*.md'
      - '.claude/**'
      - '.github/ISSUE_TEMPLATE/**'

  # 수동 배포 트리거 (GitHub UI에서 "Run workflow" 버튼)
  workflow_dispatch:

env:
  PROJECT_DIR: /home/pi/investment-advisor
  VENV_ACTIVATE: /home/pi/investment-advisor/venv/bin/activate

jobs:
  deploy:
    runs-on: [self-hosted, rpi4]
    timeout-minutes: 10

    steps:
      - name: Pull latest code
        run: |
          cd $PROJECT_DIR
          git fetch origin main
          git reset --hard origin/main
          echo "Deployed commit: $(git log --oneline -1)"

      - name: Install/update dependencies
        run: |
          cd $PROJECT_DIR
          source $VENV_ACTIVATE
          pip install -r requirements.txt --quiet --no-warn-script-location 2>&1 | tail -5

      - name: Run DB migration
        run: |
          cd $PROJECT_DIR
          source $VENV_ACTIVATE
          python -c "
          from shared.db import init_db
          from shared.config import DatabaseConfig
          init_db(DatabaseConfig())
          print('DB migration completed successfully')
          "

      - name: Restart API service
        run: |
          sudo systemctl restart investment-advisor-api.service
          sleep 3
          sudo systemctl is-active investment-advisor-api.service

      - name: Health check
        run: |
          for i in 1 2 3; do
            if curl -sf http://localhost:8000/docs > /dev/null 2>&1; then
              echo "Health check passed (attempt $i)"
              exit 0
            fi
            echo "Attempt $i failed, retrying in 3s..."
            sleep 3
          done
          echo "Health check failed after 3 attempts"
          sudo systemctl status investment-advisor-api.service --no-pager -l
          exit 1

      - name: Notify deployment result
        if: always()
        run: |
          COMMIT=$(cd $PROJECT_DIR && git log --oneline -1)
          if [ "${{ job.status }}" = "success" ]; then
            echo "✅ 배포 성공: $COMMIT"
          else
            echo "❌ 배포 실패: $COMMIT"
            echo "로그 확인: sudo journalctl -u investment-advisor-api.service -n 50"
          fi
```

### 4-2. PR 검증 Workflow (선택사항)

`main` 머지 전에 기본 검증을 수행하려면 `.github/workflows/validate.yml` 추가:

```yaml
name: Validate PR

on:
  pull_request:
    branches: [main]

jobs:
  validate:
    runs-on: [self-hosted, rpi4]
    timeout-minutes: 5

    steps:
      - uses: actions/checkout@v4

      - name: Check Python syntax
        run: |
          source ${{ env.VENV_ACTIVATE }}
          python -m py_compile analyzer/main.py
          python -m py_compile api/main.py
          python -m py_compile shared/db.py
          python -m py_compile shared/config.py
          echo "Syntax check passed"
        env:
          VENV_ACTIVATE: /home/pi/investment-advisor/venv/bin/activate

      - name: Import check
        run: |
          source /home/pi/investment-advisor/venv/bin/activate
          python -c "
          import importlib
          modules = ['shared.config', 'shared.db', 'api.main']
          for m in modules:
              importlib.import_module(m)
              print(f'  {m} OK')
          "
```

---

## 5. GitHub Secrets 등록

Self-hosted Runner 방식은 별도 Secrets가 필수가 아니지만, 알림 연동 등 확장 시 필요하다.

| Secret | 용도 | 필수 여부 |
|--------|------|----------|
| (없음) | Self-hosted Runner는 Pi에서 직접 실행하므로 SSH 키 불필요 | - |
| `SLACK_WEBHOOK_URL` | 배포 성공/실패 Slack 알림 (선택) | 선택 |
| `DISCORD_WEBHOOK_URL` | Discord 알림 (선택) | 선택 |

### Slack 알림 추가 시 (선택)

`deploy.yml`의 마지막 step에 추가:

```yaml
      - name: Slack notification
        if: always()
        run: |
          STATUS="${{ job.status }}"
          COMMIT=$(cd $PROJECT_DIR && git log --oneline -1)
          EMOJI=$([ "$STATUS" = "success" ] && echo "✅" || echo "❌")
          curl -sf -X POST -H 'Content-type: application/json' \
            --data "{\"text\":\"${EMOJI} investment-advisor 배포 ${STATUS}: ${COMMIT}\"}" \
            "${{ secrets.SLACK_WEBHOOK_URL }}" || true
```

---

## 6. 배포 흐름 검증

### 전체 흐름

```
[dev 브랜치 작업] → [PR 생성 → main 머지] → [GitHub Actions 트리거]
                                                    ↓
                                          [Self-hosted Runner on Pi]
                                                    ↓
                                    git pull → pip install → DB migrate
                                                    ↓
                                    systemctl restart → health check
                                                    ↓
                                          [배포 완료 / 실패 알림]
```

### 수동 검증 절차

1. **Runner 상태 확인**: GitHub → Settings → Actions → Runners에서 `rpi4-runner`가 "Idle" 상태
2. **테스트 커밋**: `dev` 브랜치에서 사소한 변경 → `main`으로 PR 머지
3. **Actions 탭 확인**: GitHub → Actions 탭에서 워크플로우 실행 상태 확인
4. **Pi에서 확인**:
   ```bash
   # 최신 커밋 확인
   cd /home/pi/investment-advisor && git log --oneline -1

   # 서비스 상태 확인
   sudo systemctl status investment-advisor-api.service

   # API 응답 확인
   curl -s http://localhost:8000/docs | head -5
   ```

### 수동 배포 (긴급 시)

GitHub Actions UI에서 직접 실행:
1. GitHub → Actions → "Deploy to Raspberry Pi"
2. **Run workflow** 버튼 클릭 (`workflow_dispatch` 트리거)

---

## 7. 롤백 전략

### 자동 롤백 (Workflow 확장)

배포 실패 시 이전 커밋으로 자동 복구하려면 `deploy.yml`에 롤백 job 추가:

```yaml
  rollback:
    runs-on: [self-hosted, rpi4]
    needs: deploy
    if: failure()
    steps:
      - name: Rollback to previous commit
        run: |
          cd /home/pi/investment-advisor
          echo "현재 실패 커밋: $(git log --oneline -1)"
          git reset --hard HEAD~1
          echo "롤백 대상: $(git log --oneline -1)"

      - name: Reinstall dependencies
        run: |
          cd /home/pi/investment-advisor
          source venv/bin/activate
          pip install -r requirements.txt --quiet

      - name: Restart service
        run: |
          sudo systemctl restart investment-advisor-api.service
          sleep 3
          if sudo systemctl is-active investment-advisor-api.service > /dev/null; then
            echo "✅ 롤백 성공"
          else
            echo "❌ 롤백도 실패 — 수동 개입 필요"
            sudo journalctl -u investment-advisor-api.service -n 30 --no-pager
            exit 1
          fi
```

### 수동 롤백

```bash
# Pi에서 직접 실행
cd /home/pi/investment-advisor

# 특정 커밋으로 롤백
git log --oneline -10          # 최근 커밋 확인
git reset --hard <commit-hash>  # 원하는 커밋으로 이동

# 서비스 재시작
source venv/bin/activate
pip install -r requirements.txt --quiet
sudo systemctl restart investment-advisor-api.service
```

---

## 8. 보안 고려사항

### 필수

- [ ] `.env` 파일은 Pi에만 존재하며 Git에 포함되지 않음 (`.gitignore` 확인)
- [ ] sudoers 권한은 **서비스 제어 명령만** 허용 (전체 NOPASSWD 금지)
- [ ] Runner 프로세스는 일반 사용자(`pi`)로 실행, root 아님
- [ ] GitHub 저장소가 **private**이면 외부 Runner 접근 불가 — 자체 Runner만 사용

### 권장

- [ ] Runner 토큰은 등록 시 1회만 사용, 이후 자동 갱신됨
- [ ] `workflow_dispatch` 권한을 repo admin으로 제한 (기본값)
- [ ] 민감 정보가 Actions 로그에 출력되지 않도록 `--quiet` 옵션 사용
- [ ] Pi의 SSH 접속은 키 인증만 허용, 비밀번호 인증 비활성화 (이미 설정된 경우)

### Runner 보안 격리 (선택)

Self-hosted Runner는 Pi의 전체 파일시스템에 접근 가능하므로, 저장소 접근 권한 관리에 유의:
- GitHub 저장소에 외부 collaborator를 추가할 때 주의 (악성 워크플로우 실행 가능)
- Fork된 PR에서 Runner가 실행되지 않도록 설정: Settings → Actions → Fork pull request workflows → 비활성화

---

## 9. 운영 체크리스트

### 일상 모니터링

```bash
# Runner 상태
sudo systemctl is-active actions.runner.*.service

# 최근 배포 이력 (GitHub Actions 탭 또는)
cd /home/pi/investment-advisor && git log --oneline -5

# 서비스 상태
sudo systemctl status investment-advisor-api.service
```

### 주간 점검

- [ ] GitHub Actions 탭에서 최근 배포 성공률 확인
- [ ] Runner 프로세스 메모리 사용량 확인: `ps aux | grep Runner`
- [ ] 디스크 여유 공간 확인: `df -h` (Runner 로그가 쌓일 수 있음)

### Runner 업데이트

GitHub Actions Runner는 자동 업데이트를 지원한다. 수동 업데이트가 필요한 경우:

```bash
cd ~/actions-runner
sudo ./svc.sh stop
# 최신 버전 다운로드 및 압축 해제 (2-2절 참조)
sudo ./svc.sh start
```

---

## 10. 트러블슈팅 FAQ

### Runner가 "Offline" 상태

```bash
# 서비스 상태 확인
sudo systemctl status actions.runner.*.service

# 재시작
sudo systemctl restart actions.runner.*.service

# 로그 확인
journalctl -u actions.runner.*.service -n 50 --no-pager
```

**원인:** 네트워크 단절, Pi 재부팅 후 서비스 미시작, Runner 프로세스 크래시

### 배포 시 "Permission denied"

```bash
# sudoers 설정 확인
sudo visudo -c -f /etc/sudoers.d/github-runner

# 직접 테스트
sudo systemctl restart investment-advisor-api.service
```

**원인:** sudoers 파일 미설정 또는 문법 오류

### Health check 실패

```bash
# 서비스 로그 확인
sudo journalctl -u investment-advisor-api.service -n 50 --no-pager

# 포트 점유 확인
ss -tlnp | grep 8000

# 수동 실행으로 에러 확인
cd /home/pi/investment-advisor
source venv/bin/activate
python -m api.main
```

**원인:** DB 연결 실패, 포트 충돌, Python 의존성 누락, .env 설정 오류

### pip install 실패 (ARM64 빌드 에러)

```bash
# 시스템 빌드 도구 설치
sudo apt install -y build-essential libffi-dev libpq-dev python3-dev

# 재시도
source venv/bin/activate
pip install -r requirements.txt
```

**원인:** C 확장 모듈(psycopg2 등)이 ARM64용 wheel이 없어 소스 빌드 시 도구 누락

### git pull 충돌

배포 Workflow는 `git reset --hard`를 사용하므로 로컬 변경이 무시된다.
Pi에서 직접 코드를 수정한 경우 배포 시 덮어씌워진다.

**대처:** Pi에서 직접 코드 수정 금지. 긴급 패치도 GitHub을 통해 커밋.

---

## 11. (대안) SSH 방식 배포

이미 외부 SSH 접속이 가능한 환경(포트포워딩 또는 Cloudflare Tunnel)이면 Runner 없이 배포할 수 있다.

### 사전 준비

1. **SSH 키 생성** (로컬 또는 GitHub Actions용):
   ```bash
   ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/github_deploy
   ```
2. Pi의 `~/.ssh/authorized_keys`에 공개키 추가
3. GitHub Secrets에 등록:
   - `PI_HOST`: Pi의 외부 IP 또는 도메인
   - `PI_USER`: SSH 사용자명 (예: `pi`)
   - `PI_SSH_KEY`: 비밀키 전체 내용
   - `PI_SSH_PORT`: SSH 포트 (기본 22, 변경 권장)

### Workflow 파일

`.github/workflows/deploy-ssh.yml`:

```yaml
name: Deploy via SSH

on:
  push:
    branches: [main]
    paths-ignore:
      - '_docs/**'
      - '*.md'
  workflow_dispatch:

jobs:
  deploy:
    runs-on: ubuntu-latest
    timeout-minutes: 10

    steps:
      - name: Deploy to Raspberry Pi
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.PI_HOST }}
          username: ${{ secrets.PI_USER }}
          key: ${{ secrets.PI_SSH_KEY }}
          port: ${{ secrets.PI_SSH_PORT }}
          command_timeout: 5m
          script: |
            set -e
            cd /home/pi/investment-advisor

            echo "=== Pulling latest code ==="
            git fetch origin main
            git reset --hard origin/main
            echo "Deployed: $(git log --oneline -1)"

            echo "=== Installing dependencies ==="
            source venv/bin/activate
            pip install -r requirements.txt --quiet

            echo "=== Running DB migration ==="
            python -c "
            from shared.db import init_db
            from shared.config import DatabaseConfig
            init_db(DatabaseConfig())
            print('DB migration OK')
            "

            echo "=== Restarting service ==="
            sudo systemctl restart investment-advisor-api.service
            sleep 3

            echo "=== Health check ==="
            curl -sf http://localhost:8000/docs > /dev/null && echo "API OK" || exit 1
```

### SSH 방식 보안 주의

- SSH 포트를 기본 22에서 변경 (예: 2222)
- fail2ban 설치하여 무차별 대입 공격 방어
- 배포 전용 SSH 키는 제한된 명령만 실행 가능하도록 `command=""` 옵션 사용 (고급)
- Cloudflare Tunnel 사용 시 포트 노출 없이 SSH 접근 가능 (권장)
