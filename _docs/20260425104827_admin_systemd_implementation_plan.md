# 관리자 웹 systemd 제어 — 구현 계획

> **For agentic workers:** 이 계획은 [`_docs/20260425103906_admin_systemd_web_control.md`](20260425103906_admin_systemd_web_control.md) 명세를 task 단위로 풀어낸 것이다. TDD가 가능한 부분은 테스트 → 구현 → 검증 → 커밋 순으로 진행. 프론트엔드는 변경 → 수동 검증.

**Goal:** `/admin` 페이지를 탭 3개(운영·도구·위험구역)로 재편하고, deploy/systemd 의 7개 unit을 웹에서 제어한다.

**Architecture:** FastAPI 라우터 신설(`admin_systemd.py`) + 기존 admin 페이지에 unit 카드 매크로 통합 + γ 정책으로 분석 실행 환경 자동 분기.

**Tech Stack:** FastAPI, Jinja2, vanilla JS (EventSource), subprocess/sudo, pytest.

---

## 파일 구조

| 종류 | 경로 | 책임 |
|---|---|---|
| 신설 | `api/routes/admin_systemd.py` | systemd 제어 라우터 (8 endpoint) |
| 신설 | `tests/test_admin_systemd.py` | 단위 테스트 (subprocess mock) |
| 신설 | `api/static/js/sse_log_viewer.js` | 공용 SSE 컨트롤러 |
| 신설 | `api/static/css/admin_extra.css` | admin-status / admin-tab 클래스 |
| 수정 | `api/main.py` | 신규 라우터 등록 |
| 수정 | `api/routes/admin.py` | run_analysis γ 분기 + admin_page 컨텍스트 |
| 수정 | `api/templates/admin.html` | 탭 3개 구조 + 매크로 호출 |
| 수정 | `api/templates/_macros.html` | sse_log_panel + unit_card + tool_card |
| 수정 | `api/templates/base.html` | sse_log_viewer.js 로드 + admin_extra.css |
| 수정 | `deploy/systemd/README.md` | "웹 UI 관리" 섹션 + sudoers 정책 |

---

## Phase 1 — 백엔드 코어 (TDD)

### Task 1: 모듈 스켈레톤 + 레지스트리 + 가드 헬퍼

**Files:**
- Create: `api/routes/admin_systemd.py`
- Test: `tests/test_admin_systemd.py`

- [ ] **1-1. 실패 테스트 작성** (`tests/test_admin_systemd.py`)

```python
"""admin_systemd 라우터 단위 테스트 — subprocess/platform mock"""
import pytest
from unittest.mock import patch


class TestUnitRegistry:
    def test_managed_units_has_seven_entries(self):
        from api.routes.admin_systemd import MANAGED_UNITS
        assert len(MANAGED_UNITS) == 7
        keys = {u["key"] for u in MANAGED_UNITS}
        assert keys == {"api", "analyzer", "sync-price", "sync-meta",
                        "ohlcv-cleanup", "sector-refresh", "briefing"}

    def test_api_is_self_protected(self):
        from api.routes.admin_systemd import _find_unit
        u = _find_unit("api")
        assert u is not None
        assert u["self_protected"] is True
        assert u["timer"] is None

    def test_analyzer_has_timer_and_not_self_protected(self):
        from api.routes.admin_systemd import _find_unit
        u = _find_unit("analyzer")
        assert u["self_protected"] is False
        assert u["timer"] == "investment-advisor-analyzer.timer"

    def test_find_unknown_returns_none(self):
        from api.routes.admin_systemd import _find_unit
        assert _find_unit("nope") is None


class TestSystemdAvailable:
    def test_windows_returns_false(self):
        from api.routes import admin_systemd
        with patch("api.routes.admin_systemd.platform.system", return_value="Windows"):
            avail, plat = admin_systemd._systemd_available()
            assert avail is False
            assert plat == "Windows"

    def test_linux_without_systemctl_returns_false(self):
        from api.routes import admin_systemd
        with patch("api.routes.admin_systemd.platform.system", return_value="Linux"), \
             patch("api.routes.admin_systemd.shutil.which", return_value=None):
            avail, plat = admin_systemd._systemd_available()
            assert avail is False
            assert "systemctl not found" in plat

    def test_linux_with_systemctl_returns_true(self):
        from api.routes import admin_systemd
        with patch("api.routes.admin_systemd.platform.system", return_value="Linux"), \
             patch("api.routes.admin_systemd.shutil.which", return_value="/bin/systemctl"):
            avail, plat = admin_systemd._systemd_available()
            assert avail is True
            assert plat == "Linux"
```

- [ ] **1-2. 테스트 실행 → 실패 확인**

```bash
pytest tests/test_admin_systemd.py -v
# Expected: ImportError (admin_systemd 미존재)
```

- [ ] **1-3. 모듈 + 레지스트리 + 가드 작성** (`api/routes/admin_systemd.py`)

```python
"""관리자 — systemd unit 제어 라우터 (start/stop/restart/enable/disable + journalctl SSE)"""
from __future__ import annotations
import json
import platform
import shutil
import subprocess
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from api.auth.dependencies import require_role
from api.auth.models import UserInDB
from api.deps import get_db_cfg
from shared.db import get_connection

router = APIRouter(prefix="/admin/systemd", tags=["관리자-systemd"])


MANAGED_UNITS: list[dict] = [
    {
        "key": "api", "category": "A", "label": "FastAPI 웹서버",
        "service": "investment-advisor-api.service", "timer": None,
        "self_protected": True,
        "schedule": "상시 (Restart=always)",
        "description": "웹 UI 호스팅 서비스 (자기 자신 제어 불가)",
    },
    {
        "key": "analyzer", "category": "A", "label": "일일 분석 배치",
        "service": "investment-advisor-analyzer.service",
        "timer": "investment-advisor-analyzer.timer",
        "self_protected": False,
        "schedule": "매일 06:30 KST",
        "description": "RSS → Claude Stage 1~4 → DB",
    },
    {
        "key": "sync-price", "category": "B", "label": "Universe 가격/OHLCV sync",
        "service": "universe-sync-price.service",
        "timer": "universe-sync-price.timer",
        "self_protected": False,
        "schedule": "매일 06:30 KST",
        "description": "stock_universe_ohlcv 일별 sync",
    },
    {
        "key": "sync-meta", "category": "B", "label": "Universe 메타 sync",
        "service": "universe-sync-meta.service",
        "timer": "universe-sync-meta.timer",
        "self_protected": False,
        "schedule": "매주 일요일 07:30 KST",
        "description": "섹터·시총 메타 주간 sync",
    },
    {
        "key": "ohlcv-cleanup", "category": "B", "label": "OHLCV retention 정리",
        "service": "ohlcv-cleanup.service",
        "timer": "ohlcv-cleanup.timer",
        "self_protected": False,
        "schedule": "매주 일요일 08:00 KST",
        "description": "retention 초과 row + 상폐 종목 정리",
    },
    {
        "key": "sector-refresh", "category": "C", "label": "섹터 분류 월간 리프레시",
        "service": "monthly-sector-refresh.service",
        "timer": "monthly-sector-refresh.timer",
        "self_protected": False,
        "schedule": "매월 1일 07:45 KST",
        "description": "sector_norm 28버킷 재정규화 + 분포 리포트",
    },
    {
        "key": "briefing", "category": "D", "label": "프리마켓 브리핑",
        "service": "pre-market-briefing.service",
        "timer": "pre-market-briefing.timer",
        "self_protected": False,
        "schedule": "매일 06:30 KST",
        "description": "미국 오버나이트 → 한국 수혜 매핑",
    },
]


def _systemd_available() -> tuple[bool, str]:
    if platform.system() != "Linux":
        return False, platform.system()
    if not shutil.which("systemctl"):
        return False, "Linux (systemctl not found)"
    return True, "Linux"


def _find_unit(key: str) -> Optional[dict]:
    return next((u for u in MANAGED_UNITS if u["key"] == key), None)
```

- [ ] **1-4. 테스트 재실행 → 통과**

```bash
pytest tests/test_admin_systemd.py::TestUnitRegistry tests/test_admin_systemd.py::TestSystemdAvailable -v
# Expected: 7 passed
```

---

### Task 2: systemctl 래퍼 + 상태 파서

- [ ] **2-1. 실패 테스트 추가** (`tests/test_admin_systemd.py`)

```python
class TestSystemctlShow:
    def test_parses_property_lines(self):
        from api.routes import admin_systemd
        fake_stdout = (
            "ActiveState=active\n"
            "SubState=running\n"
            "UnitFileState=enabled\n"
            "LoadState=loaded\n"
            "NextElapseUSecRealtime=0\n"
            "LastTriggerUSec=0\n"
        )
        with patch("api.routes.admin_systemd.subprocess.run") as mock_run:
            mock_run.return_value.stdout = fake_stdout
            mock_run.return_value.returncode = 0
            result = admin_systemd._systemctl_show("foo.service")
        assert result["ActiveState"] == "active"
        assert result["SubState"] == "running"
        assert result["UnitFileState"] == "enabled"

    def test_handles_empty_stdout(self):
        from api.routes import admin_systemd
        with patch("api.routes.admin_systemd.subprocess.run") as mock_run:
            mock_run.return_value.stdout = ""
            mock_run.return_value.returncode = 0
            result = admin_systemd._systemctl_show("foo.service")
        assert result == {}


class TestSystemctlAction:
    def test_start_uses_sudo_n(self):
        from api.routes import admin_systemd
        with patch("api.routes.admin_systemd.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stderr = ""
            ok, err = admin_systemd._systemctl_action("start", "foo.service")
        cmd = mock_run.call_args[0][0]
        assert cmd[:3] == ["sudo", "-n", "systemctl"]
        assert cmd[3] == "start"
        assert cmd[-1] == "foo.service"
        assert ok is True

    def test_enable_inserts_now(self):
        from api.routes import admin_systemd
        with patch("api.routes.admin_systemd.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stderr = ""
            admin_systemd._systemctl_action("enable", "foo.timer")
        cmd = mock_run.call_args[0][0]
        assert "--now" in cmd
        # cmd 형식: ["sudo", "-n", "systemctl", "enable", "--now", "foo.timer"]
        assert cmd.index("--now") == cmd.index("enable") + 1

    def test_failure_returns_stderr(self):
        from api.routes import admin_systemd
        with patch("api.routes.admin_systemd.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stderr = "a password is required\n"
            ok, err = admin_systemd._systemctl_action("stop", "foo.service")
        assert ok is False
        assert "password" in err
```

- [ ] **2-2. 테스트 실행 → 실패**
- [ ] **2-3. 헬퍼 추가** (`api/routes/admin_systemd.py`)

```python
def _systemctl_show(unit_name: str) -> dict:
    """systemctl show 결과를 dict로 파싱. 읽기 전용 — sudo 불필요."""
    props = ["ActiveState", "SubState", "UnitFileState", "LoadState",
             "NextElapseUSecRealtime", "LastTriggerUSec"]
    try:
        result = subprocess.run(
            ["systemctl", "show", unit_name, "--property=" + ",".join(props)],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except subprocess.TimeoutExpired:
        return {}
    parsed: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            parsed[k] = v
    return parsed


def _systemctl_action(verb: str, unit_name: str) -> tuple[bool, str]:
    """sudo NOPASSWD 화이트리스트 가정. (성공 여부, stderr)"""
    cmd = ["sudo", "-n", "systemctl", verb, unit_name]
    if verb in ("enable", "disable"):
        cmd.insert(4, "--now")  # systemctl <verb> --now <unit>
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
    except subprocess.TimeoutExpired:
        return False, "timeout"
    return result.returncode == 0, result.stderr.strip()


def _summarize_unit(unit: dict) -> dict:
    """카드 렌더용 요약 dict — show 호출 + 정규화."""
    show = _systemctl_show(unit["service"])
    timer_show = _systemctl_show(unit["timer"]) if unit["timer"] else {}
    enabled = (timer_show.get("UnitFileState") if unit["timer"] else show.get("UnitFileState")) == "enabled"
    return {
        "key": unit["key"],
        "label": unit["label"],
        "category": unit["category"],
        "service": unit["service"],
        "timer": unit["timer"],
        "self_protected": unit["self_protected"],
        "schedule": unit["schedule"],
        "description": unit["description"],
        "active": show.get("ActiveState", "unknown"),
        "sub_state": show.get("SubState", ""),
        "enabled": enabled,
        "next_trigger_usec": timer_show.get("NextElapseUSecRealtime", "0"),
        "last_trigger_usec": timer_show.get("LastTriggerUSec", "0"),
    }
```

- [ ] **2-4. 테스트 재실행 → 통과** (`pytest tests/test_admin_systemd.py -v`)

---

### Task 3: 감사 로그 헬퍼

- [ ] **3-1. 실패 테스트 추가**

```python
class TestAudit:
    def test_audit_inserts_into_admin_audit_logs(self):
        from api.routes import admin_systemd

        actor = type("U", (), {"id": 1, "email": "admin@x.com"})()
        fake_conn = _FakeConn()
        with patch("api.routes.admin_systemd.get_connection", return_value=fake_conn), \
             patch("api.routes.admin_systemd.get_db_cfg", return_value=None):
            admin_systemd._audit(actor, "systemd_start", "analyzer",
                                 {"active": "inactive"}, {"active": "active"})
        sql, params = fake_conn.executed[0]
        assert "INSERT INTO admin_audit_logs" in sql
        assert params[0] == 1                 # actor_id
        assert params[1] == "admin@x.com"     # actor_email
        assert params[4] == "systemd_start"   # action
        assert "inactive" in params[5]        # before_state JSON
        assert "active" in params[6]          # after_state JSON


class _FakeCursor:
    def __init__(self):
        self.executed: list[tuple] = []
    def execute(self, sql, params=None):
        self.executed.append((sql, params))
    def __enter__(self): return self
    def __exit__(self, *a): pass


class _FakeConn:
    def __init__(self):
        self._cur = _FakeCursor()
        self.committed = False
        self.closed = False
    @property
    def executed(self): return self._cur.executed
    def cursor(self): return self._cur
    def commit(self): self.committed = True
    def close(self): self.closed = True
```

- [ ] **3-2. 테스트 실행 → 실패**
- [ ] **3-3. 헬퍼 추가**

```python
def _audit(actor, action: str, key: str,
           before: Optional[dict] = None, after: Optional[dict] = None,
           reason: Optional[str] = None) -> None:
    """admin_audit_logs INSERT — 실패해도 라우트 응답을 막지 않음."""
    try:
        before_full = {"key": key, **(before or {})}
        after_full = {"key": key, **(after or {})} if after is not None else None
        cfg = get_db_cfg()
        conn = get_connection(cfg)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO admin_audit_logs
                    (actor_id, actor_email, target_user_id, target_email,
                     action, before_state, after_state, reason)
                    VALUES (%s, %s, NULL, NULL, %s, %s::jsonb, %s::jsonb, %s)
                    """,
                    (
                        getattr(actor, "id", None),
                        getattr(actor, "email", None),
                        action,
                        json.dumps(before_full),
                        json.dumps(after_full) if after_full is not None else None,
                        reason,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print(f"[admin_systemd] audit log failed: {e}")
```

- [ ] **3-4. 테스트 재실행 → 통과**

---

### Task 4: GET /admin/systemd/units (목록)

- [ ] **4-1. 테스트 추가**

```python
class TestUnitsListEndpoint:
    def test_returns_seven_units_when_systemd_available(self):
        from fastapi.testclient import TestClient
        from api.main import app
        with patch("api.routes.admin_systemd._systemd_available", return_value=(True, "Linux")), \
             patch("api.routes.admin_systemd._summarize_unit",
                   side_effect=lambda u: {"key": u["key"], "active": "inactive", "enabled": False,
                                           "label": u["label"], "category": u["category"],
                                           "service": u["service"], "timer": u["timer"],
                                           "self_protected": u["self_protected"],
                                           "schedule": u["schedule"], "description": u["description"],
                                           "sub_state": "", "next_trigger_usec": "0", "last_trigger_usec": "0"}), \
             _override_admin():
            client = TestClient(app)
            resp = client.get("/admin/systemd/units")
        assert resp.status_code == 200
        body = resp.json()
        assert body["systemd_available"] is True
        assert len(body["units"]) == 7

    def test_returns_503_when_unavailable(self):
        from fastapi.testclient import TestClient
        from api.main import app
        with patch("api.routes.admin_systemd._systemd_available", return_value=(False, "Windows")), \
             _override_admin():
            client = TestClient(app)
            resp = client.get("/admin/systemd/units")
        assert resp.status_code == 200  # 가용 여부도 정보로 반환
        body = resp.json()
        assert body["systemd_available"] is False
        assert body["platform"] == "Windows"
        assert body["units"] == []


def _override_admin():
    """require_role('admin') 의존성 우회 — 테스트 컨텍스트 매니저"""
    from contextlib import contextmanager
    from api.main import app
    from api.auth.dependencies import require_role

    fake_admin = type("U", (), {"id": 1, "email": "a@x.com", "role": "admin"})()
    @contextmanager
    def _ctx():
        dep = require_role("admin")
        app.dependency_overrides[dep] = lambda: fake_admin
        try:
            yield
        finally:
            app.dependency_overrides.pop(dep, None)
    return _ctx()
```

> 주의: `require_role("admin")` 은 호출마다 새 의존성 객체를 만든다. 라우트가 **모듈 임포트 시점**에 한 번 평가하도록 `_ADMIN_DEP = Depends(require_role("admin"))` 모듈 레벨 변수를 사용해야 dependency_overrides가 먹는다 (TestClient 패턴 표준).

- [ ] **4-2. 테스트 실행 → 실패** (404 또는 import 오류)
- [ ] **4-3. 엔드포인트 + 모듈 레벨 의존성 추가**

```python
_ADMIN_DEP = require_role("admin")


@router.get("/units")
def list_units(_admin: UserInDB = Depends(_ADMIN_DEP)):
    avail, plat = _systemd_available()
    if not avail:
        return {"systemd_available": False, "platform": plat, "units": []}
    return {
        "systemd_available": True,
        "platform": plat,
        "units": [_summarize_unit(u) for u in MANAGED_UNITS],
    }
```

- [ ] **4-4. main.py에 라우터 등록**

`api/main.py` 의 `from api.routes import (...)` 블록에 `admin_systemd` 추가, `app.include_router(admin.router)` 다음 줄에 `app.include_router(admin_systemd.router)` 추가.

- [ ] **4-5. 테스트 재실행 → 통과**

---

### Task 5: GET /admin/systemd/units/{key} (상세 + journal 100줄)

- [ ] **5-1. 테스트 추가**

```python
class TestUnitDetailEndpoint:
    def test_unknown_key_returns_400(self):
        from fastapi.testclient import TestClient
        from api.main import app
        with patch("api.routes.admin_systemd._systemd_available", return_value=(True, "Linux")), \
             _override_admin():
            client = TestClient(app)
            resp = client.get("/admin/systemd/units/nope")
        assert resp.status_code == 400

    def test_returns_unit_and_journal(self):
        from fastapi.testclient import TestClient
        from api.main import app
        fake_journal = "line1\nline2\nline3\n"
        with patch("api.routes.admin_systemd._systemd_available", return_value=(True, "Linux")), \
             patch("api.routes.admin_systemd._summarize_unit",
                   return_value={"key": "analyzer", "active": "inactive", "enabled": False}), \
             patch("api.routes.admin_systemd.subprocess.run") as mock_run, \
             _override_admin():
            mock_run.return_value.stdout = fake_journal
            mock_run.return_value.returncode = 0
            client = TestClient(app)
            resp = client.get("/admin/systemd/units/analyzer")
        assert resp.status_code == 200
        body = resp.json()
        assert body["unit"]["key"] == "analyzer"
        assert body["journal"] == ["line1", "line2", "line3"]
```

- [ ] **5-2. 엔드포인트 추가**

```python
@router.get("/units/{key}")
def get_unit(key: str, _admin: UserInDB = Depends(_ADMIN_DEP)):
    avail, plat = _systemd_available()
    if not avail:
        raise HTTPException(503, "systemd_unavailable")
    unit = _find_unit(key)
    if not unit:
        raise HTTPException(400, "invalid unit key")
    summary = _summarize_unit(unit)
    try:
        result = subprocess.run(
            ["journalctl", "-u", unit["service"], "-n", "100", "--no-pager", "-o", "short-iso"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        journal = [ln for ln in result.stdout.splitlines() if ln.strip()]
    except subprocess.TimeoutExpired:
        journal = ["[journalctl timeout]"]
    return {"unit": summary, "journal": journal}
```

- [ ] **5-3. 테스트 통과 확인**

---

### Task 6: POST mutation 엔드포인트 (start/stop/restart/enable/disable)

- [ ] **6-1. 테스트 추가**

```python
class TestMutationEndpoints:
    def test_invalid_key_returns_400_and_audits(self):
        from fastapi.testclient import TestClient
        from api.main import app
        with patch("api.routes.admin_systemd._systemd_available", return_value=(True, "Linux")), \
             patch("api.routes.admin_systemd._audit") as mock_audit, \
             _override_admin():
            client = TestClient(app)
            resp = client.post("/admin/systemd/units/nope/start")
        assert resp.status_code == 400
        # invalid_target audit 1건
        actions = [c.args[1] for c in mock_audit.call_args_list]
        assert "systemd_invalid_target" in actions

    def test_self_protected_mutation_blocked(self):
        from fastapi.testclient import TestClient
        from api.main import app
        with patch("api.routes.admin_systemd._systemd_available", return_value=(True, "Linux")), \
             patch("api.routes.admin_systemd._audit") as mock_audit, \
             _override_admin():
            client = TestClient(app)
            resp = client.post("/admin/systemd/units/api/stop")
        assert resp.status_code == 403
        actions = [c.args[1] for c in mock_audit.call_args_list]
        assert "systemd_self_protected_violation" in actions

    def test_successful_start_audits(self):
        from fastapi.testclient import TestClient
        from api.main import app
        with patch("api.routes.admin_systemd._systemd_available", return_value=(True, "Linux")), \
             patch("api.routes.admin_systemd._summarize_unit",
                   side_effect=[{"active": "inactive"}, {"active": "active"}]), \
             patch("api.routes.admin_systemd._systemctl_action", return_value=(True, "")), \
             patch("api.routes.admin_systemd._audit") as mock_audit, \
             _override_admin():
            client = TestClient(app)
            resp = client.post("/admin/systemd/units/analyzer/start")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        actions = [c.args[1] for c in mock_audit.call_args_list]
        assert "systemd_start" in actions

    def test_failed_action_returns_500_and_audits(self):
        from fastapi.testclient import TestClient
        from api.main import app
        with patch("api.routes.admin_systemd._systemd_available", return_value=(True, "Linux")), \
             patch("api.routes.admin_systemd._summarize_unit", return_value={"active": "inactive"}), \
             patch("api.routes.admin_systemd._systemctl_action",
                   return_value=(False, "a password is required")), \
             patch("api.routes.admin_systemd._audit") as mock_audit, \
             _override_admin():
            client = TestClient(app)
            resp = client.post("/admin/systemd/units/analyzer/start")
        assert resp.status_code == 500
        actions = [c.args[1] for c in mock_audit.call_args_list]
        assert "systemd_action_failed" in actions

    def test_enable_uses_timer_unit(self):
        """enable/disable 은 service가 아닌 timer를 대상으로 호출되어야 함"""
        from fastapi.testclient import TestClient
        from api.main import app
        with patch("api.routes.admin_systemd._systemd_available", return_value=(True, "Linux")), \
             patch("api.routes.admin_systemd._summarize_unit", return_value={"enabled": False}), \
             patch("api.routes.admin_systemd._systemctl_action", return_value=(True, "")) as mock_act, \
             patch("api.routes.admin_systemd._audit"), \
             _override_admin():
            client = TestClient(app)
            client.post("/admin/systemd/units/analyzer/enable")
        verb, target = mock_act.call_args[0]
        assert verb == "enable"
        assert target == "investment-advisor-analyzer.timer"
```

- [ ] **6-2. 엔드포인트 추가** (단일 핸들러 + verb 파라미터)

```python
_MUTATION_VERBS = ("start", "stop", "restart", "enable", "disable")


def _resolve_target(unit: dict, verb: str) -> str:
    """verb별 대상 unit 결정. enable/disable은 timer가 있으면 timer."""
    if verb in ("enable", "disable") and unit["timer"]:
        return unit["timer"]
    return unit["service"]


@router.post("/units/{key}/{verb}")
def mutate_unit(key: str, verb: str, admin: UserInDB = Depends(_ADMIN_DEP)):
    if verb not in _MUTATION_VERBS:
        raise HTTPException(400, "invalid verb")
    avail, _ = _systemd_available()
    if not avail:
        raise HTTPException(503, "systemd_unavailable")
    unit = _find_unit(key)
    if not unit:
        _audit(admin, "systemd_invalid_target", key, before={"verb": verb}, reason=f"unknown key: {key}")
        raise HTTPException(400, "invalid unit key")
    if unit["self_protected"]:
        _audit(admin, "systemd_self_protected_violation", key,
               before={"verb": verb}, reason="API service cannot be controlled via web UI")
        raise HTTPException(403, "self-protected unit cannot be controlled here")

    target = _resolve_target(unit, verb)
    before = _summarize_unit(unit)
    ok, err = _systemctl_action(verb, target)
    if not ok:
        _audit(admin, "systemd_action_failed", key,
               before={"verb": verb, "active": before.get("active"), "enabled": before.get("enabled")},
               reason=err or "action failed")
        raise HTTPException(500, f"systemctl {verb} failed: {err}")

    after = _summarize_unit(unit)
    _audit(admin, f"systemd_{verb}", key,
           before={"active": before.get("active"), "enabled": before.get("enabled")},
           after={"active": after.get("active"), "enabled": after.get("enabled")})
    return {"ok": True, "before": before, "after": after}
```

- [ ] **6-3. 테스트 통과 확인**

---

### Task 7: SSE 로그 스트리밍

- [ ] **7-1. 테스트 추가** (구조 검증만 — 실제 streaming은 통합 테스트 외 영역)

```python
class TestLogStreamEndpoint:
    def test_unknown_key_returns_400(self):
        from fastapi.testclient import TestClient
        from api.main import app
        with patch("api.routes.admin_systemd._systemd_available", return_value=(True, "Linux")), \
             _override_admin():
            client = TestClient(app)
            resp = client.get("/admin/systemd/units/nope/logs/stream")
        assert resp.status_code == 400

    def test_unavailable_returns_503(self):
        from fastapi.testclient import TestClient
        from api.main import app
        with patch("api.routes.admin_systemd._systemd_available", return_value=(False, "Windows")), \
             _override_admin():
            client = TestClient(app)
            resp = client.get("/admin/systemd/units/analyzer/logs/stream")
        assert resp.status_code == 503
```

- [ ] **7-2. 엔드포인트 추가**

```python
@router.get("/units/{key}/logs/stream")
def stream_logs(key: str, _admin: UserInDB = Depends(_ADMIN_DEP)):
    avail, _ = _systemd_available()
    if not avail:
        raise HTTPException(503, "systemd_unavailable")
    unit = _find_unit(key)
    if not unit:
        raise HTTPException(400, "invalid unit key")

    def generate():
        proc = subprocess.Popen(
            ["journalctl", "-u", unit["service"], "-n", "100", "-f", "--no-pager", "-o", "short-iso"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1, text=True,
        )
        try:
            for line in iter(proc.stdout.readline, ""):
                yield f"data: {line.rstrip()}\n\n"
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()

    return StreamingResponse(generate(), media_type="text/event-stream")
```

- [ ] **7-3. 테스트 통과 + 커밋**

```bash
git add api/routes/admin_systemd.py tests/test_admin_systemd.py api/main.py
git commit -m "feat(admin): /admin/systemd/* 라우터 + 7개 unit 화이트리스트 + 감사로그"
```

---

## Phase 2 — 기존 admin 통합

### Task 8: admin.py — γ 정책 + admin_page units 컨텍스트

- [ ] **8-1. `admin.py` 상단 import 추가**

```python
from api.routes.admin_systemd import (
    _systemd_available, _summarize_unit, MANAGED_UNITS,
)
```

- [ ] **8-2. `admin_page()` 수정 — units 주입**

기존 `admin_page` 함수 본문을 다음으로 교체:

```python
@router.get("")
def admin_page(ctx: dict = Depends(make_page_ctx("admin"))):
    """관리자 페이지"""
    from fastapi.responses import RedirectResponse
    if ctx["auth_enabled"]:
        if ctx["_user"] is None:
            return RedirectResponse("/auth/login?next=/admin", status_code=302)
        if ctx["_user"].role != "admin":
            raise HTTPException(status_code=403, detail="접근 권한이 없습니다")

    avail, plat = _systemd_available()
    units_dict: dict = {}
    if avail:
        try:
            units_dict = {u["key"]: _summarize_unit(u) for u in MANAGED_UNITS}
        except Exception as e:
            print(f"[admin_page] unit summary failed: {e}")
    ctx["systemd_available"] = avail
    ctx["systemd_platform"] = plat
    ctx["units"] = units_dict
    return templates.TemplateResponse(request=ctx["request"], name="admin.html", context=ctx)
```

- [ ] **8-3. `run_analysis()` γ 분기 추가**

기존 `run_analysis()` 함수 시작 부분에 다음 분기 추가:

```python
@router.post("/run")
def run_analysis(_admin: Optional[UserInDB] = Depends(require_role("admin"))):
    """분석 파이프라인 실행 (SSE 스트리밍) — γ 정책: Linux+systemd → systemctl, 외 → in-process"""
    avail, _ = _systemd_available()
    analyzer_unit_path = "/etc/systemd/system/investment-advisor-analyzer.service"
    if avail and os.path.exists(analyzer_unit_path):
        return _stream_via_systemd_analyzer()
    # 이하 기존 코드 (in-process subprocess)
    ...
```

새 헬퍼 `_stream_via_systemd_analyzer()` 를 `run_analysis` 위에 추가:

```python
def _stream_via_systemd_analyzer():
    """systemctl start + journalctl -f 스트리밍 (γ 정책 Linux 경로)"""
    import subprocess as _sp

    # 1) 기동 명령 (sudo NOPASSWD 가정)
    start_proc = _sp.run(
        ["sudo", "-n", "systemctl", "start", "investment-advisor-analyzer.service"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if start_proc.returncode != 0:
        def err():
            yield f"data: [오류] systemctl start 실패: {start_proc.stderr.strip()}\n\n"
            yield "event: done\ndata: failed\n\n"
        return StreamingResponse(err(), media_type="text/event-stream")

    # 2) journalctl -f 로 로그 follow
    def stream():
        yield "data: [시작] systemctl start 완료. journalctl 추적 중...\n\n"
        proc = _sp.Popen(
            ["journalctl", "-u", "investment-advisor-analyzer.service",
             "-f", "--no-pager", "-o", "cat", "--since", "now"],
            stdout=_sp.PIPE, stderr=_sp.STDOUT, bufsize=1, text=True,
        )
        try:
            for line in iter(proc.stdout.readline, ""):
                yield f"data: {line.rstrip()}\n\n"
                if "[완료]" in line or "분석이 성공적으로 완료" in line:
                    break
        finally:
            proc.terminate()
            try: proc.wait(timeout=2)
            except _sp.TimeoutExpired: proc.kill()
        yield "event: done\ndata: finished\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")
```

- [ ] **8-4. 수동 검증 (Windows 환경)**

```bash
python -c "from api.routes.admin_systemd import _systemd_available; print(_systemd_available())"
# Expected: (False, 'Windows') — 즉 admin_page 가 units={} 컨텍스트로 렌더, 기존 in-process 경로 동작
```

- [ ] **8-5. 커밋**

```bash
git add api/routes/admin.py
git commit -m "feat(admin): admin_page units 컨텍스트 주입 + run_analysis γ 정책 분기"
```

---

## Phase 3 — 프론트엔드 자산

### Task 9: 공용 SSE 컨트롤러 JS

- [ ] **9-1. `api/static/js/sse_log_viewer.js` 작성**

```javascript
/* 공용 SSE 로그 뷰어 — 분석/번역/systemd 카드에서 공유 사용 */
(function () {
  const _conns = new Map();

  function attachSseLog(panelId, url, opts) {
    opts = opts || {};
    const panel = document.getElementById(panelId);
    if (!panel) return;
    detachSseLog(panelId);
    const maxLines = opts.maxLines || 1000;
    const es = new EventSource(url);
    es.onmessage = function (e) {
      const line = document.createElement('div');
      line.textContent = e.data;
      panel.appendChild(line);
      while (panel.childNodes.length > maxLines) panel.removeChild(panel.firstChild);
      panel.scrollTop = panel.scrollHeight;
    };
    es.addEventListener('done', function () { detachSseLog(panelId); });
    es.onerror = function () { /* EventSource 자동 재연결 */ };
    _conns.set(panelId, es);
  }

  function detachSseLog(panelId) {
    const es = _conns.get(panelId);
    if (es) {
      es.close();
      _conns.delete(panelId);
    }
  }

  window.attachSseLog = attachSseLog;
  window.detachSseLog = detachSseLog;
})();
```

- [ ] **9-2. `base.html` 에 스크립트 로드 추가** — `</body>` 직전 또는 head에 다음 한 줄 (이미 다른 정적 JS 로드 구간 있으면 거기에):

```html
<script src="/static/js/sse_log_viewer.js"></script>
```

### Task 10: 추가 CSS

- [ ] **10-1. `api/static/css/admin_extra.css` 작성**

```css
/* admin systemd / 탭 / 상태 뱃지 — 기존 admin-status-{idle,running} 옆 확장 */
.admin-status-active   { background:#1f3d2e; color:#7fd9a3; }
.admin-status-inactive { background:#2a2a2a; color:#888; }
.admin-status-failed   { background:#3d1f1f; color:#e07070; }
.admin-status-disabled { background:#1a1a1a; color:#555; }

.admin-tab-bar {
  display:flex;
  gap:4px;
  border-bottom:1px solid var(--border);
  margin-bottom:16px;
}
.admin-tab {
  padding:10px 18px;
  cursor:pointer;
  color:var(--text-muted);
  border-bottom:2px solid transparent;
  font-size:14px;
  user-select:none;
}
.admin-tab:hover { color:var(--text); }
.admin-tab.active {
  color:var(--text);
  border-bottom-color:var(--accent);
  font-weight:600;
}

.admin-tab-pane { display:none; }
.admin-tab-pane.active { display:block; }

.admin-card-protected { opacity:0.75; }
.admin-card-protected .btn[disabled] { cursor:not-allowed; }

.admin-next-run { font-size:11px; color:var(--text-muted); margin-top:2px; }

.systemd-log-modal {
  position:fixed;
  top:0; left:0; right:0; bottom:0;
  background:rgba(0,0,0,0.75);
  z-index:1000;
  display:none;
  align-items:center;
  justify-content:center;
}
.systemd-log-modal.active { display:flex; }
.systemd-log-modal-inner {
  background:var(--surface);
  border:1px solid var(--border);
  border-radius:8px;
  width:80%;
  max-width:1000px;
  max-height:80vh;
  display:flex;
  flex-direction:column;
}
.systemd-log-modal-header {
  padding:12px 16px;
  display:flex;
  justify-content:space-between;
  align-items:center;
  border-bottom:1px solid var(--border);
}
.systemd-log-modal-body { flex:1; overflow:hidden; padding:0; }
.systemd-log-modal-body pre {
  margin:0;
  height:100%;
  overflow:auto;
  background:#0a0a0a;
  color:#cfcfcf;
  padding:12px;
  font-size:12px;
  line-height:1.5;
  font-family:'Consolas','Courier New',monospace;
}
```

- [ ] **10-2. `base.html` 에 CSS 링크 추가**

```html
<link rel="stylesheet" href="/static/css/admin_extra.css">
```

### Task 11: 매크로 — sse_log_panel + unit_card + tool_card

- [ ] **11-1. `api/templates/_macros.html` 끝부분에 매크로 추가**

```jinja2
{# ───────────────────────────── 공용 SSE 로그 패널 ───────────────────────────── #}
{% macro sse_log_panel(panel_id, height='360px') -%}
<pre id="{{ panel_id }}"
     style="height:{{ height }};overflow:auto;background:#0a0a0a;color:#cfcfcf;
            padding:12px;font-size:12px;line-height:1.5;border-radius:6px;
            font-family:'Consolas','Courier New',monospace;margin:0;"></pre>
{%- endmacro %}


{# ───────────────────────────── systemd unit 카드 ───────────────────────────── #}
{% macro unit_card(unit) -%}
{% set protected = unit.self_protected %}
<div class="card{% if protected %} admin-card-protected{% endif %}"
     data-unit-key="{{ unit.key }}"
     style="padding:14px 18px;margin-bottom:10px;">
  <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;">
    <div style="min-width:240px;">
      <div style="display:flex;align-items:center;gap:8px;">
        <span style="font-size:14px;font-weight:600;">{{ unit.label }}</span>
        <span class="admin-status admin-status-{{ unit.active or 'inactive' }}"
              data-status-badge="{{ unit.key }}">{{ unit.active or 'inactive' }}</span>
      </div>
      <div style="font-size:12px;color:var(--text-muted);margin-top:2px;">{{ unit.description }}</div>
      <div class="admin-next-run">
        <code style="font-size:11px;">{{ unit.service }}</code>
        {% if unit.schedule %}· {{ unit.schedule }}{% endif %}
      </div>
    </div>
    <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
      {% if protected %}
        <span style="font-size:11px;color:var(--text-muted);max-width:280px;">
          이 서비스는 자기 자신을 제어할 수 없습니다 — SSH로 처리하세요
        </span>
      {% else %}
        <button class="btn" data-systemd-action="start"  data-key="{{ unit.key }}">Start</button>
        <button class="btn" data-systemd-action="stop"   data-key="{{ unit.key }}">Stop</button>
        <button class="btn" data-systemd-action="restart" data-key="{{ unit.key }}">Restart</button>
        {% if unit.timer %}
          <button class="btn"
                  data-systemd-action="{{ 'disable' if unit.enabled else 'enable' }}"
                  data-key="{{ unit.key }}"
                  data-toggle-enable="1">
            {{ 'Disable' if unit.enabled else 'Enable' }}
          </button>
        {% endif %}
      {% endif %}
      <button class="btn" data-systemd-log="{{ unit.key }}"
              data-label="{{ unit.label }}">로그</button>
    </div>
  </div>
</div>
{%- endmacro %}
```

> tool_card 매크로는 기존 도구들의 마크업이 제각각이라(폼 입력 / 단순 버튼 / 링크) 강제 추출 시 오히려 복잡해진다. **YAGNI** 적용: 기존 카드들은 admin.html 내부에서 그대로 두고, systemd 카드만 매크로화.

- [ ] **11-2. 매크로 syntax 검증** — admin.html에 import 후 페이지 로드 (다음 태스크에서 같이)

---

### Task 12: admin.html 재구성 — 탭 3개

- [ ] **12-1. admin.html 전면 교체**

`api/templates/admin.html` 파일 전체를 새 버전으로 교체. 기존 분석실행 / 번역 / 진단링크 / 데이터삭제 / 원격복사 카드는 보존하되 **탭 안으로 이동**.

```jinja2
{% extends "base.html" %}
{% from "_macros.html" import unit_card %}
{% block title %}관리자 메뉴 — AlphaSignal{% endblock %}
{% block page_title %}관리자 메뉴{% endblock %}

{% block content %}
<!-- 탭 바 -->
<div class="admin-tab-bar">
  <div class="admin-tab" data-tab="operations">운영</div>
  <div class="admin-tab" data-tab="tools">도구</div>
  <div class="admin-tab" data-tab="danger">위험구역</div>
</div>

{# ─────────────── 운영 탭 ─────────────── #}
<div class="admin-tab-pane" id="tab-operations">
  {% if not systemd_available %}
    <div class="card" style="padding:14px 18px;border-left:3px solid var(--accent);">
      <div style="font-size:13px;color:var(--text-muted);">
        이 환경({{ systemd_platform }})에서는 systemd 제어를 사용할 수 없습니다.
        Linux + systemctl 환경(라즈베리파이 운영기)에서만 동작합니다.
      </div>
    </div>
  {% else %}
    {% for cat, title in [('A','A. 필수 서비스'),('B','B. Universe / OHLCV 자동화'),('C','C. 섹터 분류'),('D','D. 프리마켓 브리핑')] %}
      {% set group = units.values() | selectattr('category','equalto',cat) | list %}
      {% if group %}
        <div class="section-title" style="margin-top:18px;font-size:13px;color:var(--text-muted);">{{ title }}</div>
        {% for u in group %}
          {{ unit_card(u) }}
        {% endfor %}
      {% endif %}
    {% endfor %}
  {% endif %}
</div>

{# ─────────────── 도구 탭 ─────────────── #}
<div class="admin-tab-pane" id="tab-tools">

  <!-- 진단 페이지 링크 -->
  <div class="card" style="padding:16px 20px;margin-bottom:12px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;">
    <div>
      <div style="font-size:15px;font-weight:600;">실행 진단 · AI 쿼리 아카이브</div>
      <div style="font-size:12px;color:var(--text-muted);">
        최근 실행 로그, AI 쿼리 원본(JSON 파싱 실패 재현), 체크포인트, 사건 보고서를 조회합니다.
      </div>
    </div>
    <a href="/admin/diagnostics" class="btn btn-primary" style="text-decoration:none;">진단 열기 →</a>
  </div>

  <!-- 분석 파이프라인 실행 -->
  <div class="card" style="padding:20px;margin-bottom:12px;">
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;">
      <div>
        <div style="font-size:16px;font-weight:600;margin-bottom:4px;">분석 파이프라인 실행</div>
        <div style="font-size:13px;color:var(--text-muted);">
          {% if systemd_available %}systemd analyzer.service 트리거{% else %}python -m analyzer.main (in-process){% endif %}
        </div>
      </div>
      <div style="display:flex;gap:8px;align-items:center;">
        <span id="statusBadge" class="admin-status admin-status-idle">대기</span>
        <button id="btnRun" class="btn btn-primary" onclick="startAnalysis()">분석 실행</button>
        <button id="btnStop" class="btn" onclick="stopAnalysis()" style="display:none;color:var(--red);">중단</button>
      </div>
    </div>
  </div>

  <!-- 뉴스 한글 번역 -->
  <div class="card" style="padding:20px;margin-bottom:12px;">
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;">
      <div>
        <div style="font-size:16px;font-weight:600;margin-bottom:4px;">뉴스 한글 번역</div>
        <div style="font-size:13px;color:var(--text-muted);">
          기존 미번역 뉴스 제목을 한글로 일괄 번역합니다.
          <span id="untranslatedCount" style="color:var(--accent);font-weight:600;"></span>
        </div>
      </div>
      <div style="display:flex;gap:8px;align-items:center;">
        <span id="translateBadge" class="admin-status admin-status-idle">대기</span>
        <button id="btnTranslate" class="btn btn-primary" onclick="startTranslation()">번역 실행</button>
      </div>
    </div>
  </div>

  <!-- 원격 DB 데이터 복사 -->
  <div class="card" style="padding:20px;margin-bottom:12px;">
    <div style="margin-bottom:12px;">
      <div style="font-size:16px;font-weight:600;margin-bottom:4px;">원격 DB 데이터 복사</div>
      <div style="font-size:13px;color:var(--text-muted);">
        다른 PostgreSQL DB의 분석 데이터를 로컬로 복사합니다. 기존 로컬 데이터는 삭제됩니다.
      </div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px;">
      <div>
        <label style="font-size:12px;color:var(--text-muted);display:block;margin-bottom:2px;">호스트</label>
        <input id="remoteHost" type="text" placeholder="예: 192.168.0.10" style="width:100%;padding:6px 10px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);box-sizing:border-box;">
      </div>
      <div>
        <label style="font-size:12px;color:var(--text-muted);display:block;margin-bottom:2px;">포트</label>
        <input id="remotePort" type="number" value="5432" style="width:100%;padding:6px 10px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);box-sizing:border-box;">
      </div>
      <div>
        <label style="font-size:12px;color:var(--text-muted);display:block;margin-bottom:2px;">데이터베이스명</label>
        <input id="remoteDbname" type="text" value="investment_advisor" style="width:100%;padding:6px 10px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);box-sizing:border-box;">
      </div>
      <div>
        <label style="font-size:12px;color:var(--text-muted);display:block;margin-bottom:2px;">사용자</label>
        <input id="remoteUser" type="text" value="postgres" style="width:100%;padding:6px 10px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);box-sizing:border-box;">
      </div>
      <div style="grid-column:1/3;">
        <label style="font-size:12px;color:var(--text-muted);display:block;margin-bottom:2px;">비밀번호</label>
        <input id="remotePassword" type="password" placeholder="비밀번호 입력" style="width:100%;padding:6px 10px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);box-sizing:border-box;">
      </div>
    </div>
    <div style="display:flex;align-items:center;gap:8px;">
      <span id="copyBadge" class="admin-status admin-status-idle">대기</span>
      <button id="btnCopy" class="btn btn-primary" onclick="copyFromRemote()">데이터 복사</button>
    </div>
  </div>

  <!-- 진행률 + 공용 로그 뷰어 -->
  <div id="progressSection" style="display:none;margin-bottom:12px;">
    <div class="admin-progress-bar">
      <div class="admin-progress-fill" id="progressFill"></div>
    </div>
    <div class="admin-progress-label" id="progressLabel"></div>
  </div>

  <div class="section-title" style="display:flex;align-items:center;justify-content:space-between;">
    <span>실행 로그</span>
    <button class="btn" onclick="clearLog()" style="font-size:12px;padding:4px 12px;">지우기</button>
  </div>
  <div class="admin-log-viewer" id="logViewer">
    <div class="admin-log-empty" id="logEmpty">분석/번역/복사 등을 실행하면 로그가 여기에 표시됩니다.</div>
  </div>
</div>

{# ─────────────── 위험구역 탭 ─────────────── #}
<div class="admin-tab-pane" id="tab-danger">
  <div class="card" style="padding:20px;border:1px solid rgba(220,60,60,0.4);">
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;">
      <div>
        <div style="font-size:16px;font-weight:600;margin-bottom:4px;color:#e05555;">전체 데이터 삭제</div>
        <div style="font-size:13px;color:var(--text-muted);">
          모든 분석 세션, 테마, 제안, 뉴스, 추적 데이터를 삭제합니다. <strong>복구할 수 없습니다.</strong>
        </div>
      </div>
      <button class="btn" onclick="resetAllData()"
              style="background:#c0392b;color:#fff;border:none;padding:8px 18px;font-weight:600;cursor:pointer;border-radius:6px;">
        전체 데이터 삭제
      </button>
    </div>
  </div>
</div>

{# ─────────────── systemd 로그 모달 ─────────────── #}
<div class="systemd-log-modal" id="systemdLogModal">
  <div class="systemd-log-modal-inner">
    <div class="systemd-log-modal-header">
      <div id="systemdLogTitle" style="font-weight:600;">systemd journal</div>
      <button class="btn" onclick="closeSystemdLog()" style="font-size:12px;">닫기</button>
    </div>
    <div class="systemd-log-modal-body">
      <pre id="systemdLogPanel"></pre>
    </div>
  </div>
</div>
{% endblock %}
```

- [ ] **12-2. 매크로 import 정상 작동 확인 (페이지 로드)**

---

### Task 13: admin.html scripts 블록 — 탭 + systemd 액션 + 기존 JS 통합

- [ ] **13-1. admin.html `{% block scripts %}` 작성** (한 블록, 기존 JS 보존 + 신규 추가)

기존 `admin.html` 의 모든 `<script>` 코드를 그대로 가져오고, 그 위에 탭 + systemd 동작 코드를 prepend.

```jinja2
{% block scripts %}
<script>
/* ── 탭 동기화 ─────────────────────── */
function activateTab(tabId) {
  if (!tabId) tabId = 'operations';
  document.querySelectorAll('.admin-tab').forEach(t =>
    t.classList.toggle('active', t.dataset.tab === tabId));
  document.querySelectorAll('.admin-tab-pane').forEach(p =>
    p.classList.toggle('active', p.id === 'tab-' + tabId));
  if (location.hash !== '#' + tabId) {
    history.replaceState(null, '', '#' + tabId);
  }
}
window.addEventListener('hashchange', () =>
  activateTab(location.hash.slice(1) || 'operations'));
document.addEventListener('DOMContentLoaded', () => {
  activateTab(location.hash.slice(1) || 'operations');
  document.querySelectorAll('.admin-tab').forEach(t =>
    t.addEventListener('click', () => activateTab(t.dataset.tab)));
});

/* ── systemd unit 액션 ────────────── */
async function systemdAction(key, action) {
  const verbLabel = {start:'시작',stop:'중지',restart:'재시작',enable:'활성화',disable:'비활성화'}[action] || action;
  if (!confirm(`${key} 서비스를 ${verbLabel}하시겠습니까?`)) return;
  try {
    const r = await fetch(`/admin/systemd/units/${key}/${action}`, {method:'POST'});
    const data = await r.json();
    if (!r.ok) {
      alert(`실패: ${data.detail || data.error}`);
    }
  } catch (e) {
    alert(`요청 실패: ${e.message}`);
  }
  refreshUnits();
}

async function refreshUnits() {
  try {
    const r = await fetch('/admin/systemd/units');
    if (!r.ok) return;
    const data = await r.json();
    if (!data.systemd_available) return;
    for (const u of data.units) {
      const badge = document.querySelector(`[data-status-badge="${u.key}"]`);
      if (badge) {
        badge.className = 'admin-status admin-status-' + (u.active || 'inactive');
        badge.textContent = u.active || 'inactive';
      }
      const toggleBtn = document.querySelector(`[data-toggle-enable="1"][data-key="${u.key}"]`);
      if (toggleBtn) {
        toggleBtn.textContent = u.enabled ? 'Disable' : 'Enable';
        toggleBtn.dataset.systemdAction = u.enabled ? 'disable' : 'enable';
      }
    }
  } catch {}
}

/* unit 카드 버튼 위임 */
document.addEventListener('click', (ev) => {
  const actBtn = ev.target.closest('[data-systemd-action]');
  if (actBtn) {
    systemdAction(actBtn.dataset.key, actBtn.dataset.systemdAction);
    return;
  }
  const logBtn = ev.target.closest('[data-systemd-log]');
  if (logBtn) {
    openSystemdLog(logBtn.dataset.systemdLog, logBtn.dataset.label);
  }
});

/* ── systemd 로그 모달 ────────────── */
function openSystemdLog(key, label) {
  document.getElementById('systemdLogTitle').textContent = `journal — ${label || key}`;
  document.getElementById('systemdLogPanel').textContent = '';
  document.getElementById('systemdLogModal').classList.add('active');
  if (window.attachSseLog) {
    window.attachSseLog('systemdLogPanel', `/admin/systemd/units/${key}/logs/stream`);
  }
}
function closeSystemdLog() {
  if (window.detachSseLog) window.detachSseLog('systemdLogPanel');
  document.getElementById('systemdLogModal').classList.remove('active');
}

/* 30초 주기 자동 새로고침 (운영 탭일 때만) */
setInterval(() => {
  const opTabActive = document.querySelector('.admin-tab.active')?.dataset.tab === 'operations';
  if (opTabActive) refreshUnits();
}, 30000);

/* ───── 이하 기존 admin.html scripts 블록 코드 그대로 ───── */
const btnRun = document.getElementById('btnRun');
const btnStop = document.getElementById('btnStop');
const statusBadge = document.getElementById('statusBadge');
const logViewer = document.getElementById('logViewer');
const logEmpty = document.getElementById('logEmpty');
const progressSection = document.getElementById('progressSection');
const progressFill = document.getElementById('progressFill');
const progressLabel = document.getElementById('progressLabel');

let eventSource = null;
const PROGRESS_MAP = [/* ... 기존 그대로 ... */];
function updateProgress(text) { /* ... */ }
function setRunning(running) { /* ... */ }
function appendLog(text, type) { /* ... */ }
function startAnalysis() { /* ... */ }
function stopAnalysis() { /* ... */ }
function clearLog() { /* ... */ }
function connectToStream() { /* ... */ }
function loadTranslateStatus() { /* ... */ }
function startTranslation() { /* ... */ }
function resetAllData() { /* ... */ }
function copyFromRemote() { /* ... */ }
loadTranslateStatus();
fetch('/admin/status').then(/* ... */);
</script>
{% endblock %}
```

> 위 `/* ... */` 자리에는 **기존 admin.html 의 동일 함수 본문을 그대로 복사** — 변경 없음.

- [ ] **13-2. 수동 검증 (Windows 개발 환경)**

```bash
python -m api.main
# 브라우저 → http://localhost:8000/admin
# 체크리스트:
# - 탭 3개(운영/도구/위험구역) 표시
# - 운영 탭: "이 환경에서는 systemd 제어를 사용할 수 없습니다 (Windows)" 안내
# - 도구 탭: 분석 실행/번역/원격복사 카드 정상 동작 (기존 기능)
# - 위험구역 탭: 데이터 삭제 카드
# - URL #operations / #tools / #danger 토글 정상
# - 새로고침 후 같은 탭 유지
```

- [ ] **13-3. 커밋**

```bash
git add api/static/js/sse_log_viewer.js api/static/css/admin_extra.css \
        api/templates/_macros.html api/templates/admin.html api/templates/base.html
git commit -m "feat(admin): /admin 탭 3개 재구성 + systemd 카드 매크로 + 공용 SSE 뷰어"
```

---

## Phase 4 — 문서

### Task 14: deploy/systemd/README.md sudoers 섹션 추가

- [ ] **14-1. README.md 끝에 다음 섹션 추가**

```markdown
## 웹 UI에서 관리하기 (Admin → 운영 탭)

라즈베리파이 SSH 없이 `/admin` 페이지에서 unit 제어가 가능하다 (systemctl start/stop/restart/enable/disable + journalctl).

### 권한 설정 — sudoers NOPASSWD 화이트리스트

API 서비스는 `__SYSTEM_USER__` 권한으로 실행되므로 `sudo systemctl ...` 호출에 비밀번호가 필요하다.
다음 화이트리스트를 등록하면 비밀번호 없이 지정된 명령만 실행 가능하다.

```bash
sudo visudo -f /etc/sudoers.d/investment-advisor-systemd
```

내용 (예시 — `dzp` 자리에 실제 운영 유저 치환):

```
Cmnd_Alias INV_SVC_ACTIONS = \
  /bin/systemctl start   investment-advisor-analyzer.service, \
  /bin/systemctl stop    investment-advisor-analyzer.service, \
  /bin/systemctl restart investment-advisor-analyzer.service, \
  /bin/systemctl start   universe-sync-price.service, \
  /bin/systemctl stop    universe-sync-price.service, \
  /bin/systemctl restart universe-sync-price.service, \
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
  /bin/systemctl restart pre-market-briefing.service

Cmnd_Alias INV_TIMER_ACTIONS = \
  /bin/systemctl enable  --now investment-advisor-analyzer.timer, \
  /bin/systemctl disable --now investment-advisor-analyzer.timer, \
  /bin/systemctl enable  --now universe-sync-price.timer, \
  /bin/systemctl disable --now universe-sync-price.timer, \
  /bin/systemctl enable  --now universe-sync-meta.timer, \
  /bin/systemctl disable --now universe-sync-meta.timer, \
  /bin/systemctl enable  --now ohlcv-cleanup.timer, \
  /bin/systemctl disable --now ohlcv-cleanup.timer, \
  /bin/systemctl enable  --now monthly-sector-refresh.timer, \
  /bin/systemctl disable --now monthly-sector-refresh.timer, \
  /bin/systemctl enable  --now pre-market-briefing.timer, \
  /bin/systemctl disable --now pre-market-briefing.timer

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

- **API 자체 service**(`investment-advisor-api.service`) 는 sudoers에 포함하지 않는다 — 백엔드에서 self_protected 로 차단 (이중 방어).
- 화이트리스트 외 systemctl 명령(`daemon-reload`, `mask`, 임의 unit 등) 절대 추가 금지 — 권한 확장 위험.
- `sudoers.d/*` 파일 권한은 반드시 `0440`. 그렇지 않으면 sudo가 무시한다.

### 미지원 환경 동작

- Windows / non-Linux: 운영 탭에 안내 메시지 노출, 모든 systemd 엔드포인트 503 반환.
- 분석 실행은 systemd 미지원 시 자동으로 in-process subprocess 경로로 fallback (γ 정책).
```

- [ ] **14-2. 커밋**

```bash
git add deploy/systemd/README.md
git commit -m "docs(systemd): 웹 UI 관리용 sudoers NOPASSWD 화이트리스트 + 운영 안내 추가"
```

---

## Phase 5 — 통합 검증

### Task 15: 전체 테스트 + 페이지 로드

- [ ] **15-1. pytest 전체 실행**

```bash
pytest tests/test_admin_systemd.py -v
pytest -x  # 전체. 회귀 없는지 확인
```

- [ ] **15-2. Windows 수동 검증**

```bash
python -m api.main
# /admin 접속 후 운영/도구/위험구역 탭 전환 확인
# 도구 탭에서 분석실행 버튼 누름 → 기존 in-process 동작 확인 (γ 정책 fallback)
```

- [ ] **15-3. 최종 상태 보고**

변경 파일 목록 + Linux 검증 절차 (sudoers 등록 + 라즈베리파이에서 /admin 접속 후 Start 버튼 → analyzer.service 기동 확인) 한 줄씩.

---

## 자체 리뷰 (작성 후 점검)

- 7 unit 화이트리스트, self_protected, sudoers 정책, 탭 구조, γ 정책, 매크로/JS/CSS 추출 — 명세 §2~§12 모두 task로 매핑됨
- 플레이스홀더 `/* ... */` 는 Task 13-1 에서 "기존 admin.html 동일 함수 본문 복사"로 명시 — 실행 시 실제 코드를 가져옴
- 함수명 일관성: `_systemd_available` / `_find_unit` / `_systemctl_show` / `_systemctl_action` / `_summarize_unit` / `_audit` / `attachSseLog` / `detachSseLog` / `systemdAction` / `refreshUnits` / `openSystemdLog` / `closeSystemdLog` — 모두 정의된 곳과 호출하는 곳 일치
