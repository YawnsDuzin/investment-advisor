"""관리자 — systemd unit 제어 라우터.

deploy/systemd/README.md 의 9개 unit을 웹에서 제어한다:
  start/stop/restart/enable/disable + journalctl SSE 스트리밍.
API 자체 service는 self_protected (mutation 차단). sudo NOPASSWD 화이트리스트 가정.
"""
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


# ── 관리 대상 unit 화이트리스트 (단일 진실) ──────────────────
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
        "key": "sync-indices", "category": "B", "label": "시장 지수 OHLCV sync",
        "service": "universe-sync-indices.service",
        "timer": "universe-sync-indices.timer",
        "self_protected": False,
        "schedule": "매일 06:30 KST",
        "description": "KOSPI/KOSDAQ/SP500/NDX100 일별 sync — market_regime 입력",
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
    {
        "key": "fundamentals", "category": "B", "label": "펀더멘털 PIT sync",
        "service": "investment-advisor-fundamentals.service",
        "timer": "investment-advisor-fundamentals.timer",
        "self_protected": False,
        "schedule": "매일 06:35 KST",
        "description": "stock_universe_fundamentals 일별 sync (pykrx KR + yfinance.info US, B-Lite)",
    },
    {
        "key": "foreign-flow-sync", "category": "B", "label": "외국인/기관/개인 수급 sync",
        "service": "investment-advisor-foreign-flow-sync.service",
        "timer": "investment-advisor-foreign-flow-sync.timer",
        "self_protected": False,
        "schedule": "매일 06:40 KST",
        "description": "외국인/기관/개인 수급 PIT 일배치 sync (KRX KOSPI+KOSDAQ, stock_universe_foreign_flow v44)",
    },
]


# ── OS / systemctl 가용성 가드 ──────────────────────────
def _systemd_available() -> tuple[bool, str]:
    """(가능 여부, 플랫폼명). Windows / macOS / systemctl 누락 시 False."""
    if platform.system() != "Linux":
        return False, platform.system()
    if not shutil.which("systemctl"):
        return False, "Linux (systemctl not found)"
    return True, "Linux"


def _find_unit(key: str) -> Optional[dict]:
    return next((u for u in MANAGED_UNITS if u["key"] == key), None)


# ── systemctl 래퍼 (읽기 전용 + 쓰기 NOPASSWD sudo) ────────
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
    """sudo NOPASSWD 화이트리스트 가정. (성공 여부, stderr)."""
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


# ── 감사 로그 ─────────────────────────────────────────
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


# ── 모듈 레벨 의존성 (TestClient dependency_overrides 가능) ──
_ADMIN_DEP = require_role("admin")


# ── 엔드포인트 ────────────────────────────────────────
@router.get("/units")
def list_units(_admin: UserInDB = Depends(_ADMIN_DEP)):
    """7개 관리 unit 일괄 상태 조회."""
    avail, plat = _systemd_available()
    if not avail:
        return {"systemd_available": False, "platform": plat, "units": []}
    return {
        "systemd_available": True,
        "platform": plat,
        "units": [_summarize_unit(u) for u in MANAGED_UNITS],
    }


@router.get("/units/{key}")
def get_unit(key: str, _admin: UserInDB = Depends(_ADMIN_DEP)):
    """단일 unit 상세 + 최근 journal 100줄."""
    avail, _ = _systemd_available()
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


_MUTATION_VERBS = ("start", "stop", "restart", "enable", "disable")


def _resolve_target(unit: dict, verb: str) -> str:
    """verb별 대상 unit 결정. enable/disable은 timer가 있으면 timer."""
    if verb in ("enable", "disable") and unit["timer"]:
        return unit["timer"]
    return unit["service"]


@router.post("/units/{key}/{verb}")
def mutate_unit(key: str, verb: str, admin: UserInDB = Depends(_ADMIN_DEP)):
    """systemctl mutation — 화이트리스트 + self_protected 검증 + 감사 로그."""
    if verb not in _MUTATION_VERBS:
        raise HTTPException(400, "invalid verb")
    avail, _ = _systemd_available()
    if not avail:
        raise HTTPException(503, "systemd_unavailable")
    unit = _find_unit(key)
    if not unit:
        _audit(admin, "systemd_invalid_target", key,
               before={"verb": verb}, reason=f"unknown key: {key}")
        raise HTTPException(400, "invalid unit key")
    if unit["self_protected"]:
        _audit(admin, "systemd_self_protected_violation", key,
               before={"verb": verb},
               reason="API service cannot be controlled via web UI")
        raise HTTPException(403, "self-protected unit cannot be controlled here")

    target = _resolve_target(unit, verb)
    before = _summarize_unit(unit)
    ok, err = _systemctl_action(verb, target)
    if not ok:
        _audit(admin, "systemd_action_failed", key,
               before={"verb": verb, "active": before.get("active"),
                       "enabled": before.get("enabled")},
               reason=err or "action failed")
        raise HTTPException(500, f"systemctl {verb} failed: {err}")

    after = _summarize_unit(unit)
    _audit(admin, f"systemd_{verb}", key,
           before={"active": before.get("active"), "enabled": before.get("enabled")},
           after={"active": after.get("active"), "enabled": after.get("enabled")})
    return {"ok": True, "before": before, "after": after}


@router.get("/units/{key}/logs/stream")
def stream_logs(key: str, _admin: UserInDB = Depends(_ADMIN_DEP)):
    """journalctl -f SSE 스트리밍. 클라이언트 disconnect 시 subprocess terminate."""
    avail, _ = _systemd_available()
    if not avail:
        raise HTTPException(503, "systemd_unavailable")
    unit = _find_unit(key)
    if not unit:
        raise HTTPException(400, "invalid unit key")

    def generate():
        proc = subprocess.Popen(
            ["journalctl", "-u", unit["service"], "-n", "100", "-f",
             "--no-pager", "-o", "short-iso"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=1, text=True,
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
