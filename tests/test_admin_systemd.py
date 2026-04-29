"""admin_systemd 라우터 단위 테스트.

subprocess / platform / shutil / DB 연결을 mock 처리.
TestClient + dependency_overrides 로 require_role 우회.
"""
from contextlib import contextmanager
from unittest.mock import patch, MagicMock

import pytest


# ── 가짜 DB 객체 ────────────────────────────────────────────
class _FakeCursor:
    def __init__(self):
        self.executed: list[tuple] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class _FakeConn:
    def __init__(self):
        self._cur = _FakeCursor()
        self.committed = False
        self.closed = False

    @property
    def executed(self):
        return self._cur.executed

    def cursor(self):
        return self._cur

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


# ── admin Depends 우회 ─────────────────────────────────────
@contextmanager
def _override_admin():
    from api.main import app
    from api.routes.admin_systemd import _ADMIN_DEP

    fake_admin = type("U", (), {"id": 1, "email": "a@x.com", "role": "admin"})()
    app.dependency_overrides[_ADMIN_DEP] = lambda: fake_admin
    try:
        yield fake_admin
    finally:
        app.dependency_overrides.pop(_ADMIN_DEP, None)


# ─────────────────── 레지스트리 ───────────────────
class TestUnitRegistry:
    def test_managed_units_has_expected_entries(self):
        from api.routes.admin_systemd import MANAGED_UNITS
        keys = {u["key"] for u in MANAGED_UNITS}
        expected = {"api", "analyzer", "sync-price", "sync-indices",
                    "sync-meta", "ohlcv-cleanup", "sector-refresh",
                    "briefing", "fundamentals", "foreign-flow-sync"}
        assert keys == expected, f"MANAGED_UNITS 키 불일치: 예상 {expected} vs 실제 {keys}"
        assert len(MANAGED_UNITS) == len(expected), \
            f"MANAGED_UNITS 길이 {len(MANAGED_UNITS)} != 예상 {len(expected)}"

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


# ─────────────────── OS 가드 ───────────────────
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


# ─────────────────── systemctl 래퍼 ───────────────────
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

    def test_handles_timeout(self):
        import subprocess as _sp
        from api.routes import admin_systemd
        with patch("api.routes.admin_systemd.subprocess.run",
                   side_effect=_sp.TimeoutExpired(cmd="x", timeout=10)):
            result = admin_systemd._systemctl_show("foo.service")
        assert result == {}


class TestSystemctlAction:
    def test_start_uses_sudo_n(self):
        from api.routes import admin_systemd
        with patch("api.routes.admin_systemd.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stderr = ""
            ok, _err = admin_systemd._systemctl_action("start", "foo.service")
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
        # ["sudo", "-n", "systemctl", "enable", "--now", "foo.timer"]
        assert "--now" in cmd
        assert cmd.index("--now") == cmd.index("enable") + 1
        assert cmd[-1] == "foo.timer"

    def test_failure_returns_stderr(self):
        from api.routes import admin_systemd
        with patch("api.routes.admin_systemd.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stderr = "a password is required\n"
            ok, err = admin_systemd._systemctl_action("stop", "foo.service")
        assert ok is False
        assert "password" in err

    def test_timeout_returns_false(self):
        import subprocess as _sp
        from api.routes import admin_systemd
        with patch("api.routes.admin_systemd.subprocess.run",
                   side_effect=_sp.TimeoutExpired(cmd="x", timeout=15)):
            ok, err = admin_systemd._systemctl_action("start", "foo.service")
        assert ok is False
        assert err == "timeout"


# ─────────────────── 감사 로그 ───────────────────
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
        # params: (actor_id, actor_email, action, before_state_json, after_state_json, reason)
        # SQL 자체에 NULL 두 개가 인라인 박혀있으므로 params는 6개만
        assert params[0] == 1                   # actor_id
        assert params[1] == "admin@x.com"       # actor_email
        assert params[2] == "systemd_start"     # action
        assert "inactive" in params[3]          # before_state JSON
        assert "active" in params[4]            # after_state JSON
        assert fake_conn.committed is True
        assert fake_conn.closed is True

    def test_audit_swallows_exceptions(self):
        from api.routes import admin_systemd
        actor = type("U", (), {"id": 1, "email": "x@x.com"})()
        with patch("api.routes.admin_systemd.get_connection",
                   side_effect=RuntimeError("boom")), \
             patch("api.routes.admin_systemd.get_db_cfg", return_value=None):
            # should NOT raise
            admin_systemd._audit(actor, "systemd_start", "analyzer")


# ─────────────────── 엔드포인트: GET /units ───────────────────
def _summary_stub(u):
    return {"key": u["key"], "active": "inactive", "enabled": False,
            "label": u["label"], "category": u["category"],
            "service": u["service"], "timer": u["timer"],
            "self_protected": u["self_protected"],
            "schedule": u["schedule"], "description": u["description"],
            "sub_state": "", "next_trigger_usec": "0", "last_trigger_usec": "0"}


class TestUnitsListEndpoint:
    def test_returns_ten_units_when_systemd_available(self):
        from fastapi.testclient import TestClient
        from api.main import app
        with patch("api.routes.admin_systemd._systemd_available",
                   return_value=(True, "Linux")), \
             patch("api.routes.admin_systemd._summarize_unit",
                   side_effect=_summary_stub), \
             _override_admin():
            client = TestClient(app)
            resp = client.get("/admin/systemd/units")
        assert resp.status_code == 200
        body = resp.json()
        assert body["systemd_available"] is True
        assert len(body["units"]) == 10

    def test_returns_empty_when_unavailable(self):
        from fastapi.testclient import TestClient
        from api.main import app
        with patch("api.routes.admin_systemd._systemd_available",
                   return_value=(False, "Windows")), \
             _override_admin():
            client = TestClient(app)
            resp = client.get("/admin/systemd/units")
        assert resp.status_code == 200
        body = resp.json()
        assert body["systemd_available"] is False
        assert body["platform"] == "Windows"
        assert body["units"] == []


# ─────────────────── 엔드포인트: GET /units/{key} ───────────────────
class TestUnitDetailEndpoint:
    def test_unknown_key_returns_400(self):
        from fastapi.testclient import TestClient
        from api.main import app
        with patch("api.routes.admin_systemd._systemd_available",
                   return_value=(True, "Linux")), \
             _override_admin():
            client = TestClient(app)
            resp = client.get("/admin/systemd/units/nope")
        assert resp.status_code == 400

    def test_unavailable_returns_503(self):
        from fastapi.testclient import TestClient
        from api.main import app
        with patch("api.routes.admin_systemd._systemd_available",
                   return_value=(False, "Windows")), \
             _override_admin():
            client = TestClient(app)
            resp = client.get("/admin/systemd/units/analyzer")
        assert resp.status_code == 503

    def test_returns_unit_and_journal(self):
        from fastapi.testclient import TestClient
        from api.main import app
        fake_journal = "line1\nline2\nline3\n"
        with patch("api.routes.admin_systemd._systemd_available",
                   return_value=(True, "Linux")), \
             patch("api.routes.admin_systemd._summarize_unit",
                   side_effect=_summary_stub), \
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


# ─────────────────── 엔드포인트: POST mutation ───────────────────
class TestMutationEndpoints:
    def test_invalid_verb_returns_400(self):
        from fastapi.testclient import TestClient
        from api.main import app
        with patch("api.routes.admin_systemd._systemd_available",
                   return_value=(True, "Linux")), \
             _override_admin():
            client = TestClient(app)
            resp = client.post("/admin/systemd/units/analyzer/explode")
        assert resp.status_code == 400

    def test_invalid_key_returns_400_and_audits(self):
        from fastapi.testclient import TestClient
        from api.main import app
        with patch("api.routes.admin_systemd._systemd_available",
                   return_value=(True, "Linux")), \
             patch("api.routes.admin_systemd._audit") as mock_audit, \
             _override_admin():
            client = TestClient(app)
            resp = client.post("/admin/systemd/units/nope/start")
        assert resp.status_code == 400
        actions = [c.args[1] for c in mock_audit.call_args_list]
        assert "systemd_invalid_target" in actions

    def test_self_protected_mutation_blocked(self):
        from fastapi.testclient import TestClient
        from api.main import app
        with patch("api.routes.admin_systemd._systemd_available",
                   return_value=(True, "Linux")), \
             patch("api.routes.admin_systemd._audit") as mock_audit, \
             _override_admin():
            client = TestClient(app)
            resp = client.post("/admin/systemd/units/api/stop")
        assert resp.status_code == 403
        actions = [c.args[1] for c in mock_audit.call_args_list]
        assert "systemd_self_protected_violation" in actions

    def test_unavailable_returns_503(self):
        from fastapi.testclient import TestClient
        from api.main import app
        with patch("api.routes.admin_systemd._systemd_available",
                   return_value=(False, "Windows")), \
             _override_admin():
            client = TestClient(app)
            resp = client.post("/admin/systemd/units/analyzer/start")
        assert resp.status_code == 503

    def test_successful_start_audits(self):
        from fastapi.testclient import TestClient
        from api.main import app
        with patch("api.routes.admin_systemd._systemd_available",
                   return_value=(True, "Linux")), \
             patch("api.routes.admin_systemd._summarize_unit",
                   side_effect=[{"active": "inactive", "enabled": False},
                                {"active": "active", "enabled": False}]), \
             patch("api.routes.admin_systemd._systemctl_action",
                   return_value=(True, "")), \
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
        with patch("api.routes.admin_systemd._systemd_available",
                   return_value=(True, "Linux")), \
             patch("api.routes.admin_systemd._summarize_unit",
                   return_value={"active": "inactive", "enabled": False}), \
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
        """enable/disable 은 service가 아닌 timer를 대상으로 호출되어야 함."""
        from fastapi.testclient import TestClient
        from api.main import app
        with patch("api.routes.admin_systemd._systemd_available",
                   return_value=(True, "Linux")), \
             patch("api.routes.admin_systemd._summarize_unit",
                   return_value={"enabled": False, "active": "inactive"}), \
             patch("api.routes.admin_systemd._systemctl_action",
                   return_value=(True, "")) as mock_act, \
             patch("api.routes.admin_systemd._audit"), \
             _override_admin():
            client = TestClient(app)
            client.post("/admin/systemd/units/analyzer/enable")
        verb, target = mock_act.call_args[0]
        assert verb == "enable"
        assert target == "investment-advisor-analyzer.timer"


# ─────────────────── 엔드포인트: SSE stream ───────────────────
class TestLogStreamEndpoint:
    def test_unknown_key_returns_400(self):
        from fastapi.testclient import TestClient
        from api.main import app
        with patch("api.routes.admin_systemd._systemd_available",
                   return_value=(True, "Linux")), \
             _override_admin():
            client = TestClient(app)
            resp = client.get("/admin/systemd/units/nope/logs/stream")
        assert resp.status_code == 400

    def test_unavailable_returns_503(self):
        from fastapi.testclient import TestClient
        from api.main import app
        with patch("api.routes.admin_systemd._systemd_available",
                   return_value=(False, "Windows")), \
             _override_admin():
            client = TestClient(app)
            resp = client.get("/admin/systemd/units/analyzer/logs/stream")
        assert resp.status_code == 503
