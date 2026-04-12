# 라즈베리파이 4 설치·배포·운영 매뉴얼

> 대상: Raspberry Pi 4 (2GB 이상) + Raspberry Pi OS (64-bit, Bookworm 권장)
> 프로젝트: [investment-advisor](https://github.com/YawnsDuzin/investment-advisor)
> 작성일: 2026-04-12

본 문서는 라즈베리파이 4에 본 프로젝트를 **처음부터** 설치·배포·자동 실행·외부 공개까지 끝내기 위한 단계별 체크리스트다. 명령어는 복사 붙여넣기로 사용할 수 있도록 블록 단위로 제공하며, 각 단계마다 **확인 방법**과 **실패 시 트러블슈팅**을 함께 기재한다.

프로젝트 루트의 [CLAUDE.md](../CLAUDE.md), [.env.example](../.env.example), [shared/pg_setup.py](../shared/pg_setup.py) 와 반드시 정합성이 유지되어야 한다.

---

## 목차

1. [사전 준비물](#1-사전-준비물)
2. [시스템 기본 설정](#2-시스템-기본-설정)
3. [Python 3.11+ 환경 구성](#3-python-311-환경-구성)
4. [PostgreSQL 설치 및 설정](#4-postgresql-설치-및-설정)
5. [Node.js + Claude Code CLI 설치](#5-nodejs--claude-code-cli-설치)
6. [프로젝트 배포](#6-프로젝트-배포)
7. [systemd 서비스/타이머 등록](#7-systemd-서비스타이머-등록)
8. [방화벽 및 포트포워딩](#8-방화벽-및-포트포워딩-외부-접속)
9. [운영 체크리스트](#9-운영-체크리스트)
10. [트러블슈팅 FAQ](#10-트러블슈팅-faq)

---

## 1. 사전 준비물

### 하드웨어

| 항목 | 최소 | 권장 |
|------|------|------|
| 모델 | Raspberry Pi 4 Model B 2GB | 4GB 이상 |
| microSD | 16GB Class 10 | 32GB A2 등급 |
| 전원 | 공식 USB-C 5V/3A | 공식 어댑터 필수 |
| 케이스 | 방열판 부착 | 팬 포함 |
| 네트워크 | 유선 LAN 권장 | 유선 LAN |

> **주의**: 저렴한 전원 어댑터는 언더볼트 경고(`⚡`)로 분석 중 중단될 수 있다. 공식 어댑터 사용을 강력히 권장한다.

### OS 설치 및 SSH 활성화

1. [Raspberry Pi Imager](https://www.raspberrypi.com/software/) 로 **Raspberry Pi OS (64-bit)** 를 microSD에 플래시한다.
2. Imager의 **고급 옵션(⚙)** 에서 미리 설정한다.
   - 호스트네임: 예) `rpi-advisor`
   - **SSH 활성화** (공개키 또는 비밀번호 방식)
   - 사용자명/비밀번호
   - Wi-Fi SSID/암호 (유선 LAN만 쓸 거면 생략)
   - 로케일: `Asia/Seoul`, 키보드 `us`
3. SD카드를 꽂고 부팅 후, 같은 네트워크에서 SSH 접속:
   ```bash
   ssh <사용자명>@<라즈베리파이-IP>
   ```

**확인**: 프롬프트가 `<user>@rpi-advisor:~ $` 형태로 뜨면 성공.

### 고정 IP 또는 DHCP 예약

외부 포트포워딩을 쓰려면 라즈베리파이의 내부 IP가 변하지 않아야 한다. **둘 중 하나**를 선택한다.

- **공유기 DHCP 예약(권장)**: 공유기 관리자 페이지에서 라즈베리파이의 MAC 주소를 특정 IP에 고정 예약한다. (제조사마다 이름이 다르지만 "DHCP 예약", "주소 예약", "Static Lease" 등으로 표기)
- **라즈베리파이에서 고정 IP 지정**: Bookworm 이후는 NetworkManager(`nmcli`) 를 사용한다.
  ```bash
  # 유선 LAN 기준, 연결 이름 확인
  nmcli connection show
  # 예: "Wired connection 1" 의 IP를 192.168.0.50/24 으로 고정
  sudo nmcli connection modify "Wired connection 1" \
      ipv4.method manual \
      ipv4.addresses 192.168.0.50/24 \
      ipv4.gateway 192.168.0.1 \
      ipv4.dns "1.1.1.1 8.8.8.8"
  sudo nmcli connection up "Wired connection 1"
  ```

**확인**:
```bash
ip -4 addr show | grep inet
ping -c 3 1.1.1.1
```

---

## 2. 시스템 기본 설정

### 패키지 업데이트 및 필수 패키지 설치

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y \
    git build-essential curl ca-certificates \
    python3 python3-venv python3-dev python3-pip \
    libpq-dev ufw
```

### 타임존 · 로케일

```bash
sudo timedatectl set-timezone Asia/Seoul
timedatectl   # 현재 시각이 KST로 표시되는지 확인

# 로케일(선택) - 한글 출력 깨짐 방지
sudo sed -i 's/^# *ko_KR.UTF-8/ko_KR.UTF-8/' /etc/locale.gen
sudo locale-gen
```

**확인**: `date` 명령이 KST로 출력되면 OK.

### 스왑 확장 (권장 2GB)

기본 스왑(100MB~512MB)은 분석 중 Python 메모리 부족을 유발할 수 있다.

```bash
sudo dphys-swapfile swapoff
sudo sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=2048/' /etc/dphys-swapfile
sudo dphys-swapfile setup
sudo dphys-swapfile swapon
free -h   # Swap 2.0Gi 확인
```

**트러블슈팅**: `dphys-swapfile` 이 없으면 `sudo apt install -y dphys-swapfile` 로 설치한다.

---

## 3. Python 3.11+ 환경 구성

`claude-agent-sdk` 가 **Python 3.10 이상**을 요구하므로, 반드시 버전을 확인한다.

```bash
python3 --version
```

### 3-A. Bookworm 이상 — 기본 Python 3.11 사용 (추가 작업 없음)

Bookworm의 기본 `python3` 은 3.11이다. 아래 3-B를 건너뛰고 바로 **6. 프로젝트 배포**로 진행한다.

### 3-B. Bullseye(Python 3.9) — pyenv로 Python 3.11 설치

Bullseye의 기본 Python은 **3.9**이며, `claude-agent-sdk>=0.1.0` 설치가 거부된다. OS 재설치 없이 [pyenv](https://github.com/pyenv/pyenv) 로 Python 3.11을 사용자 영역에 빌드한다.

> Bookworm 재플래시가 가능하다면 그것이 가장 깔끔하지만, 기존 서버 환경을 유지해야 한다면 이 방법이 차선이다.

**① 빌드 의존성 설치**

```bash
sudo apt update
sudo apt install -y make build-essential libssl-dev zlib1g-dev \
    libbz2-dev libreadline-dev libsqlite3-dev wget curl llvm \
    libncursesw5-dev xz-utils tk-dev libxml2-dev libxmlsec1-dev \
    libffi-dev liblzma-dev git
```

**② pyenv 설치**

```bash
curl https://pyenv.run | bash
```

`~/.bashrc` 에 초기화 코드 추가:

```bash
cat >> ~/.bashrc <<'EOF'

# pyenv
export PYENV_ROOT="$HOME/.pyenv"
[[ -d $PYENV_ROOT/bin ]] && export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init - bash)"
EOF

exec "$SHELL"
```

**확인**: `pyenv --version` 이 출력되면 OK.

**③ Python 3.11 빌드 (라즈베리파이4 기준 10~20분 소요)**

```bash
pyenv install 3.11.9
```

- 진행 중 프롬프트가 한동안 멈춘 것처럼 보여도 정상이다. 빌드 완료까지 기다린다.
- 실패 시 에러 메시지 첫 줄의 누락 라이브러리를 `apt install` 로 설치 후 재시도.

**확인**:
```bash
pyenv versions                                          # 3.11.9 가 목록에 보여야 함
~/.pyenv/versions/3.11.9/bin/python --version           # Python 3.11.9
```

> `venv` 생성은 다음 **6. 프로젝트 배포** 단계에서 진행한다. Bullseye에서는 반드시 pyenv의 Python 경로로 venv를 생성해야 한다.

---

## 4. PostgreSQL 설치 및 설정

본 프로젝트는 [shared/pg_setup.py](../shared/pg_setup.py) 에서 설치 여부를 감지해 자동 설치 루틴을 호출하지만, 서버를 안정적으로 운영하려면 **수동 설치 + 명시적 설정**을 권장한다.

### 4.1 설치

```bash
sudo apt install -y postgresql postgresql-contrib
sudo systemctl enable --now postgresql
systemctl status postgresql --no-pager
```

**확인**: `Active: active (running)` 로 표시되면 OK.

### 4.2 `postgres` 사용자 비밀번호 설정

```bash
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres';"
```

- 위 명령은 `shared/pg_setup.py` 의 자동 설치 루틴과 동일한 기본값을 사용한다.
- **운영 환경에서는 반드시 강력한 비밀번호로 변경**하고, 이후 `.env` 파일의 `DB_PASSWORD` 에도 동일하게 반영한다.

### 4.3 데이터베이스 생성

`shared/db.py` 의 `init_db()` 가 테이블/마이그레이션을 자동 생성하지만, **데이터베이스 자체는 사전에 존재해야 한다**.

```bash
sudo -u postgres createdb investment_advisor
sudo -u postgres psql -l | grep investment_advisor
```

### 4.4 접속 테스트

```bash
psql -h localhost -U postgres -d investment_advisor -c "SELECT version();"
```

- 비밀번호 프롬프트에서 위에서 설정한 값을 입력한다.
- 버전 문자열이 출력되면 성공.

### 4.5 `.env` 파일 준비

`.env` 는 [6. 프로젝트 배포](#6-프로젝트-배포) 에서 `git clone` 후 작성한다. 기본 템플릿은 [.env.example](../.env.example) 를 참고한다.

| 변수 | 기본값 | 비고 |
|------|--------|------|
| `DB_HOST` | `localhost` | |
| `DB_PORT` | `5432` | |
| `DB_NAME` | `investment_advisor` | 위 4.3에서 생성한 이름과 일치 |
| `DB_USER` | `postgres` | |
| `DB_PASSWORD` | `your_password_here` | **반드시 변경** |
| `MAX_TURNS` | `6` | Stage 1·2 공통 Claude SDK 턴 수 |
| `TOP_THEMES` | `3` | Stage 2 심층분석 상위 테마 수 |
| `TOP_STOCKS_PER_THEME` | `2` | 테마당 심층분석 종목 수 |
| `ENABLE_STOCK_ANALYSIS` | `true` | `false` 설정 시 Stage 2 비활성화 |

---

## 5. Node.js + Claude Code CLI 설치

본 프로젝트는 `claude-agent-sdk` 가 내부적으로 **Claude Code CLI(Node.js)** 를 호출하므로 Node.js LTS와 CLI 로그인이 필요하다. 과금은 Claude Code 구독(Max 5x 등)에 포함된다.

### 5.1 Node.js LTS 설치 (NodeSource)

```bash
curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
sudo apt install -y nodejs
node --version   # v20.x 이상 기대
npm --version
```

> NodeSource 스크립트의 최신 설치 절차는 [공식 문서](https://github.com/nodesource/distributions) 를 참고한다.

### 5.2 Claude Code CLI 설치 및 로그인

```bash
sudo npm install -g @anthropic-ai/claude-code
claude --version
claude login
```

- `claude login` 실행 시 브라우저 인증 URL이 출력된다. 라즈베리파이에 모니터가 없다면 해당 URL을 **로컬 PC 브라우저에 복사**해 로그인하면 된다.
- 구독(Max 등) 계정으로 로그인되었는지 확인:
  ```bash
  claude --help      # 정상 동작 확인
  ```

**트러블슈팅**: `claude: command not found` → `which claude` 로 경로를 확인하고, `systemd` 유닛의 `PATH` 에 `/usr/bin:/usr/local/bin` 이 포함되어 있는지 확인한다.

---

## 6. 프로젝트 배포

### 6.1 소스 클론

권장 경로: `/home/dzp/dzp-main/program/investment-advisor`

```bash
mkdir -p ~/dzp-main/program && cd ~/dzp-main/program
git clone https://github.com/YawnsDuzin/investment-advisor.git
cd investment-advisor
```

### 6.2 가상환경 생성 및 의존성 설치

#### Bookworm (기본 Python 3.11) 인 경우

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

#### Bullseye + pyenv (3-B 단계를 수행한 경우)

Bullseye의 기본 `python3`(3.9)로 venv를 만들면 `claude-agent-sdk` 설치가 실패한다. 반드시 **pyenv로 설치한 Python 경로**로 venv를 생성한다.

```bash
# 기존에 python3.9로 만든 venv가 있다면 삭제
rm -rf venv

~/.pyenv/versions/3.11.9/bin/python -m venv venv
source venv/bin/activate
python --version     # Python 3.11.9 이어야 함
pip install --upgrade pip
pip install -r requirements.txt
```

**트러블슈팅**:
- `psycopg2` 빌드 실패 → `sudo apt install -y libpq-dev` 확인.
- `claude-agent-sdk` 버전 못 찾음 → `python --version` 이 3.10 이상인지 재확인. 3.9로 venv를 만들었다면 위 절차대로 삭제 후 재생성.

### 6.3 `.env` 작성

```bash
cp .env.example .env
nano .env   # DB_PASSWORD 등 실제 값으로 수정
chmod 600 .env
```

### 6.4 수동 실행으로 동작 확인

**분석 배치 1회 실행**:
```bash
source venv/bin/activate
python -m analyzer.main
```

- 최초 실행 시 `init_db()` 가 `schema_version` 기반으로 전체 스키마를 자동 생성한다.
- `[PostgreSQL] ...` / `[NEWS] ...` / `[ANALYZER] ...` 로그가 순서대로 출력되면 정상이다.

**API 서버 수동 실행**:
```bash
python -m api.main
```

다른 터미널에서:
```bash
curl -s http://localhost:8000/ | head -c 200
curl -s http://localhost:8000/docs | head -c 200
```

- HTML이 반환되면 성공. 확인 후 Ctrl+C로 종료하고 다음 단계(systemd)로 넘어간다.

---

## 7. systemd 서비스/타이머 등록

목표:
- **API 서버**는 상시 기동, 장애 시 자동 재시작
- **분석 배치**는 매일 07:00 (KST) 1회 실행

아래 예시는 사용자 이름을 `dzp`, 프로젝트 경로를 `/home/dzp/dzp-main/program/investment-advisor` 로 가정한다. 자신의 환경에 맞게 **`User`, `WorkingDirectory`, `EnvironmentFile`, `ExecStart`** 경로를 수정한다.

> **Bullseye + pyenv 사용자**: venv 안의 `python`이 이미 pyenv 빌드 경로(`~/.pyenv/versions/3.11.9/bin/python`)를 심볼릭 링크로 참조하므로, `ExecStart` 의 `venv/bin/python` 경로만 올바르면 별도 설정은 필요 없다. 단, `~/.pyenv` 가 `User=` 에 지정한 사용자의 홈 디렉토리에 있어야 한다.

### 7.1 API 서버 유닛 — `investment-advisor.service`

파일 생성:
```bash
sudo nano /etc/systemd/system/investment-advisor.service
```

전체 내용:
```ini
[Unit]
Description=Investment Advisor API (FastAPI)
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
User=dzp
Group=dzp
WorkingDirectory=/home/dzp/dzp-main/program/investment-advisor
EnvironmentFile=/home/dzp/dzp-main/program/investment-advisor/.env
Environment=PATH=/home/dzp/dzp-main/program/investment-advisor/venv/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/home/dzp/dzp-main/program/investment-advisor/venv/bin/python -m api.main
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### 7.2 분석 배치 유닛 — `investment-advisor-analyzer.service`

```bash
sudo nano /etc/systemd/system/investment-advisor-analyzer.service
```

```ini
[Unit]
Description=Investment Advisor daily analyzer batch
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=oneshot
User=dzp
Group=dzp
WorkingDirectory=/home/dzp/dzp-main/program/investment-advisor
EnvironmentFile=/home/dzp/dzp-main/program/investment-advisor/.env
Environment=PATH=/home/dzp/dzp-main/program/investment-advisor/venv/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/home/dzp/dzp-main/program/investment-advisor/venv/bin/python -m analyzer.main
```

### 7.3 분석 배치 타이머 — `investment-advisor.timer`

> CLAUDE.md에 명시된 `investment-advisor.timer` 이름과 정합성을 유지하기 위해 **타이머 이름을 `investment-advisor.timer`** 로 두고, 이 타이머가 `investment-advisor-analyzer.service` 를 트리거하도록 구성한다.

```bash
sudo nano /etc/systemd/system/investment-advisor.timer
```

```ini
[Unit]
Description=Daily trigger for investment-advisor analyzer

[Timer]
# 매일 07:00 (KST) 실행. 부팅 후 놓친 시점이 있다면 즉시 실행.
OnCalendar=*-*-* 07:00:00
Persistent=true
Unit=investment-advisor-analyzer.service

[Install]
WantedBy=timers.target
```

### 7.4 활성화 및 상태 확인

```bash
sudo systemctl daemon-reload

# API 서버 상시 기동
sudo systemctl enable --now investment-advisor.service
sudo systemctl status investment-advisor.service --no-pager

# 매일 분석 배치 타이머
sudo systemctl enable --now investment-advisor.timer
systemctl list-timers | grep investment-advisor
```

**로그 확인**:
```bash
journalctl -u investment-advisor.service -f            # API 실시간
journalctl -u investment-advisor-analyzer.service -n 200  # 최근 배치 로그
```

**트러블슈팅**:
- `status` 에 `code=exited, status=203/EXEC` → `ExecStart` 경로 오타.
- `status` 에 `claude: not found` → `Environment=PATH=...` 에 `/usr/bin` 포함 여부, `claude login` 상태 확인.
- 배치가 실행되지 않음 → `systemctl list-timers` 에서 `NEXT` 시간이 올바른지 확인, `timedatectl` 로 타임존 확인.

---

## 8. 방화벽 및 포트포워딩 (외부 접속)

### 8.1 `ufw` 로컬 방화벽

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp      # SSH
sudo ufw allow 8000/tcp    # API (또는 80/443 리버스 프록시 시 해당 포트)
sudo ufw enable
sudo ufw status verbose
```

> 외부에 직접 8000 포트를 여는 대신 **Nginx 리버스 프록시(80/443)** 를 앞단에 두는 것을 권장한다. 그 경우 8000 은 외부에 열지 않는다.

### 8.2 공유기 포트포워딩 (일반 절차)

제조사별 UI는 상이하므로 자세한 항목은 **공유기 제조사 매뉴얼**을 참조하고, 다음 개념만 이해하면 된다.

1. 공유기 관리자 페이지 접속 (예: `192.168.0.1`).
2. **포트포워딩 / NAT / 가상서버 / 포트 포워드** 메뉴로 이동.
3. 규칙 추가:
   - 외부(WAN) 포트: `80` 또는 `443` (직접 8000 포워딩도 가능하나 권장하지 않음)
   - 내부(LAN) IP: 라즈베리파이의 고정 IP (1단계에서 설정)
   - 내부 포트: `8000` (또는 Nginx 를 쓸 경우 `80`/`443`)
   - 프로토콜: TCP
4. 규칙 저장 후 공유기 재시작이 필요할 수 있다.

**확인**: 외부 네트워크(예: LTE/5G 테더링)에서 `http://<공인IP>/` 접속.

**보안 주의**:
- 공유기 관리자 페이지는 **외부에서 접근 가능하게 절대 열지 말 것**.
- `DB_PASSWORD`, 공유기 관리자, SSH 비밀번호는 **모두 초기값 변경 필수**.
- SSH는 가능하면 **공개키 인증**으로 전환하고 비밀번호 로그인은 비활성화.
- PostgreSQL(5432) 은 **외부 포트포워딩 금지**. 로컬에서만 접근한다.

### 8.3 DDNS (동적 DNS) 개요

가정용 인터넷은 공인 IP가 주기적으로 바뀌므로 DDNS로 고정 도메인을 연결한다.

- 대표 서비스: [duckdns.org](https://www.duckdns.org/) (무료), [no-ip.com](https://www.noip.com/) 등
- 원리: 라즈베리파이가 주기적으로 DDNS 서버에 현재 공인 IP를 통지 → 도메인(예: `myadvisor.duckdns.org`)이 최신 IP로 자동 갱신.
- Duck DNS 설치 예시는 [공식 가이드(duckdns.org → install → pi)](https://www.duckdns.org/install.jsp) 를 따른다.

### 8.4 (선택) Nginx 리버스 프록시 + Let's Encrypt HTTPS

외부 공개 시에는 HTTPS 사용을 강하게 권장한다.

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
```

`/etc/nginx/sites-available/investment-advisor` 생성:
```nginx
server {
    listen 80;
    server_name myadvisor.duckdns.org;   # 본인 DDNS 도메인으로 교체

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}
```

활성화 및 HTTPS 발급:
```bash
sudo ln -s /etc/nginx/sites-available/investment-advisor /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d myadvisor.duckdns.org
```

Nginx 사용 시에는 공유기에서 **80/443** 만 내부 RPi의 **80/443** 으로 포워딩하고, `ufw` 의 8000 포트는 닫는다(외부 노출 방지). 자세한 내용은 [Nginx 공식 문서](https://nginx.org/en/docs/) 와 [Certbot 공식 문서](https://certbot.eff.org/) 참고.

---

## 9. 운영 체크리스트

### 로그 위치

| 대상 | 명령 |
|------|------|
| API 서버 | `journalctl -u investment-advisor.service -f` |
| 분석 배치 | `journalctl -u investment-advisor-analyzer.service -n 500` |
| 타이머 상태 | `systemctl list-timers \| grep investment-advisor` |
| PostgreSQL | `journalctl -u postgresql -n 200` |
| Nginx (선택) | `/var/log/nginx/access.log`, `/var/log/nginx/error.log` |

### 디스크 / 메모리 / 스왑 모니터링

```bash
df -h          # 디스크 여유 확인 (특히 /)
free -h        # 메모리 및 스왑
vcgencmd measure_temp          # CPU 온도 (80°C 이상 지속 시 냉각 점검)
vcgencmd get_throttled         # 0x0 이 아니면 전원/온도 스로틀 발생 중
```

### DB 백업 (`pg_dump`)

일별 백업 스크립트 예시 (`~/dzp-main/program/investment-advisor/scripts/backup.sh`):

```bash
#!/usr/bin/env bash
set -euo pipefail
BACKUP_DIR="/home/dzp/backups"
mkdir -p "$BACKUP_DIR"
STAMP=$(date +%Y%m%d_%H%M%S)
PGPASSWORD=postgres pg_dump -h localhost -U postgres investment_advisor \
    | gzip > "$BACKUP_DIR/investment_advisor_${STAMP}.sql.gz"
# 14일 초과분 삭제
find "$BACKUP_DIR" -name 'investment_advisor_*.sql.gz' -mtime +14 -delete
```

`crontab -e` 에 등록:
```cron
30 6 * * * /home/dzp/dzp-main/program/investment-advisor/scripts/backup.sh
```

### 업데이트 배포 절차

```bash
cd ~/dzp-main/program/investment-advisor
git pull
source venv/bin/activate
pip install -r requirements.txt

sudo systemctl restart investment-advisor.service
# 다음 배치는 타이머가 자동 실행. 즉시 재실행하려면:
sudo systemctl start investment-advisor-analyzer.service
```

**롤백**: 배포 전 `git rev-parse HEAD` 로 커밋 해시를 기록해 두고, 문제가 생기면 `git checkout <해시>` 후 서비스 재시작.

---

## 10. 트러블슈팅 FAQ

### Q1. `claude` CLI 인증이 만료되었다
- 증상: 배치 로그에 `authentication required` 또는 SDK 호출이 즉시 실패.
- 조치: 라즈베리파이에서 `claude login` 재실행 → 브라우저 인증 완료 후 `sudo systemctl restart investment-advisor.service` 및 수동 배치(`sudo systemctl start investment-advisor-analyzer.service`) 확인.

### Q2. PostgreSQL 연결 실패 (`could not connect to server`)
- `systemctl status postgresql` 로 서비스 상태 확인.
- `psql -h localhost -U postgres -d investment_advisor` 로 직접 접속 확인.
- `.env` 의 `DB_PASSWORD` 가 실제 DB 비밀번호와 일치하는지 확인.
- 서비스 파일의 `After=postgresql.service` 가 없으면 부팅 직후 경쟁 상태가 발생할 수 있다.

### Q3. API 서버가 재시작만 반복된다 (`Restart=always`)
- `journalctl -u investment-advisor.service -n 200` 로 파이썬 트레이스백 확인.
- 대표 원인: `.env` 누락, `venv` 경로 오타, `libpq-dev` 미설치로 `psycopg2` 임포트 실패.

### Q4. 외부에서 접속되지 않는다
1. 내부에서 먼저 `curl http://<RPi-IP>:8000/` 이 되는지 확인.
2. `sudo ufw status` 로 포트 허용 여부 확인.
3. 공유기 포트포워딩 규칙의 내부 IP가 RPi의 **현재** 고정 IP와 일치하는지 확인.
4. ISP가 **CGNAT**(공유기 WAN에 사설 IP 할당)을 쓰면 포트포워딩이 불가능하다 — ISP 고객센터에 공인 IP 요청 또는 [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) 같은 터널링 사용을 검토한다.

### Q5. Bullseye에서 `claude-agent-sdk` 설치 실패 (`Could not find a version that satisfies the requirement`)
- 증상: `pip install -r requirements.txt` 에서 `claude-agent-sdk>=0.1.0` 버전을 찾지 못함.
- 원인: Bullseye의 기본 Python이 **3.9**이며, `claude-agent-sdk` 는 **3.10 이상**을 요구한다.
- 조치: 본 문서 **3-B. Bullseye — pyenv로 Python 3.11 설치** 절차를 수행한 뒤, pyenv의 Python으로 venv를 재생성한다.
  ```bash
  python --version   # venv 활성화 상태에서 3.11 이어야 함
  ```
- 이미 3.9로 만든 venv가 있다면 **반드시 삭제 후 재생성**해야 한다 (`rm -rf venv`).

### Q6. 타임존이 UTC로 동작한다
- `timedatectl` 출력 확인 → `Time zone: Asia/Seoul (KST, +0900)` 이어야 함.
- 타이머의 `OnCalendar=*-*-* 07:00:00` 은 시스템 로컬 타임존을 기준으로 한다.
- 변경 후 `sudo systemctl daemon-reload && sudo systemctl restart investment-advisor.timer`.

### Q7. 분석이 메모리 부족으로 죽는다 (`Killed`)
- 스왑이 2GB로 확장되어 있는지 `free -h` 확인.
- `.env` 에서 `TOP_THEMES`, `TOP_STOCKS_PER_THEME` 을 낮추거나 `ENABLE_STOCK_ANALYSIS=false` 로 Stage 2를 일시 비활성화한 뒤 다시 시도.

### Q8. `journalctl` 에 한글이 깨진다
- `sudo apt install -y locales && sudo locale-gen ko_KR.UTF-8` 후 재로그인.
- SSH 클라이언트(PuTTY/Windows Terminal)의 인코딩을 **UTF-8** 로 설정.

---

## 참고 문서

- 프로젝트 개요: [CLAUDE.md](../CLAUDE.md)
- 분석 파이프라인 상세: [_docs/analysis_pipeline.md](./analysis_pipeline.md)
- 환경변수 템플릿: [.env.example](../.env.example)
- PostgreSQL 자동 설치 로직: [shared/pg_setup.py](../shared/pg_setup.py)
- Raspberry Pi 공식 문서: <https://www.raspberrypi.com/documentation/>
- systemd 타이머: <https://www.freedesktop.org/software/systemd/man/systemd.timer.html>
- Claude Code CLI: <https://docs.claude.com/claude-code>
