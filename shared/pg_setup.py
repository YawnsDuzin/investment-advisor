"""PostgreSQL 설치 감지 및 자동 설치 모듈"""
import shutil
import subprocess
import sys
import platform


def is_pg_installed() -> bool:
    """PostgreSQL 클라이언트(psql)가 PATH에 있는지 확인"""
    return shutil.which("psql") is not None


def is_pg_running(host: str = "localhost", port: int = 5432) -> bool:
    """PostgreSQL 서버에 연결 가능한지 확인"""
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=host, port=port, dbname="postgres",
            user="postgres", password="postgres",
            connect_timeout=3,
        )
        conn.close()
        return True
    except Exception:
        return False


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """명령 실행 (출력 실시간 표시)"""
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, check=check)


def install_postgresql() -> bool:
    """OS를 감지하여 PostgreSQL을 자동 설치. 성공 시 True 반환."""
    os_type = platform.system()

    if os_type == "Linux":
        return _install_linux()
    elif os_type == "Windows":
        return _install_windows()
    else:
        print(f"[PostgreSQL] {os_type}은 자동 설치를 지원하지 않습니다.")
        print("[PostgreSQL] 수동으로 설치해 주세요: https://www.postgresql.org/download/")
        return False


def _install_linux() -> bool:
    """Linux(Debian/Ubuntu/Raspberry Pi OS) 자동 설치"""
    print("[PostgreSQL] Linux 환경 — apt로 설치를 시작합니다...")
    try:
        _run(["sudo", "apt", "update", "-y"])
        _run(["sudo", "apt", "install", "-y", "postgresql", "postgresql-contrib"])
        _run(["sudo", "systemctl", "enable", "--now", "postgresql"])
        print("[PostgreSQL] 설치 및 서비스 시작 완료")

        # 기본 postgres 사용자 비밀번호 설정
        print("[PostgreSQL] 기본 사용자 비밀번호 설정 중...")
        subprocess.run(
            ["sudo", "-u", "postgres", "psql", "-c",
             "ALTER USER postgres PASSWORD 'postgres';"],
            check=True,
        )
        print("[PostgreSQL] Linux 설치 완료")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[PostgreSQL] 설치 실패: {e}")
        print("[PostgreSQL] sudo 권한이 필요합니다. 수동으로 설치해 주세요.")
        return False


def _install_windows() -> bool:
    """Windows 자동 설치 (winget → choco → 수동 안내 순서로 시도)"""
    # 1) winget 시도
    if shutil.which("winget"):
        print("[PostgreSQL] Windows 환경 — winget으로 설치를 시도합니다...")
        try:
            _run(["winget", "install", "-e", "--id", "PostgreSQL.PostgreSQL.17",
                  "--accept-package-agreements", "--accept-source-agreements"])
            print("[PostgreSQL] winget 설치 완료")
            print("[PostgreSQL] 새 터미널을 열어야 PATH가 적용됩니다.")
            _configure_windows_pg()
            return True
        except subprocess.CalledProcessError:
            print("[PostgreSQL] winget 설치 실패, 다른 방법을 시도합니다...")

    # 2) choco 시도
    if shutil.which("choco"):
        print("[PostgreSQL] Windows 환경 — choco로 설치를 시도합니다...")
        try:
            _run(["choco", "install", "postgresql17", "--params",
                  "/Password:postgres", "-y"])
            print("[PostgreSQL] choco 설치 완료")
            _configure_windows_pg()
            return True
        except subprocess.CalledProcessError:
            print("[PostgreSQL] choco 설치 실패")

    # 3) 수동 안내
    print("[PostgreSQL] 자동 설치 도구(winget/choco)를 찾을 수 없습니다.")
    print("[PostgreSQL] 아래 링크에서 수동으로 설치해 주세요:")
    print("  https://www.postgresql.org/download/windows/")
    print("[PostgreSQL] 설치 시 superuser 비밀번호를 'postgres'로 설정하면 기본 설정으로 바로 사용 가능합니다.")
    return False


def _configure_windows_pg():
    """Windows 설치 후 PATH 안내"""
    pg_paths = [
        r"C:\Program Files\PostgreSQL\17\bin",
        r"C:\Program Files\PostgreSQL\16\bin",
        r"C:\Program Files\PostgreSQL\15\bin",
    ]
    for p in pg_paths:
        import os
        if os.path.isdir(p) and p not in os.environ.get("PATH", ""):
            print(f"[PostgreSQL] PATH에 추가 권장: {p}")
            break


def ensure_postgresql(host: str = "localhost", port: int = 5432) -> bool:
    """
    PostgreSQL이 사용 가능한지 확인하고, 없으면 설치를 시도한다.
    Returns: True(사용 가능), False(사용 불가)
    """
    # 1) 이미 서버가 실행 중이면 OK
    if is_pg_running(host, port):
        return True

    # 2) 설치되어 있지만 서버가 꺼져 있는 경우
    if is_pg_installed():
        print("[PostgreSQL] 설치되어 있지만 서버에 연결할 수 없습니다.")
        print(f"[PostgreSQL] {host}:{port} 에서 PostgreSQL 서비스가 실행 중인지 확인하세요.")
        if platform.system() == "Linux":
            print("  $ sudo systemctl start postgresql")
        elif platform.system() == "Windows":
            print("  > net start postgresql-x64-17")
            print("  또는 Windows 서비스에서 'postgresql' 서비스를 시작하세요.")
        return False

    # 3) 설치되어 있지 않음 → 자동 설치 시도
    print("[PostgreSQL] PostgreSQL이 설치되어 있지 않습니다.")
    print("[PostgreSQL] 자동 설치를 시도합니다...\n")

    if not install_postgresql():
        return False

    # 설치 후 연결 재확인
    if is_pg_running(host, port):
        return True

    print("[PostgreSQL] 설치는 완료되었으나 서버 연결에 실패했습니다.")
    print("[PostgreSQL] 서비스를 시작한 후 다시 실행해 주세요.")
    return False
