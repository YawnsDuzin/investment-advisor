"""펀더 sync unit이 MANAGED_UNITS에 등록되었는지 검증.

웹 UI에서 start/stop/journalctl 제어 가능하려면 화이트리스트에 들어야 함.
"""
from api.routes.admin_systemd import MANAGED_UNITS, _find_unit


def test_fundamentals_unit_registered():
    unit = _find_unit("fundamentals")
    assert unit is not None, "fundamentals unit not in MANAGED_UNITS"
    assert unit["service"] == "investment-advisor-fundamentals.service"
    assert unit["timer"] == "investment-advisor-fundamentals.timer"
    assert unit["self_protected"] is False, (
        "self_protected=True면 웹 UI에서 제어 불가 — 펀더 sync는 운영자가 수동 트리거 가능해야 함"
    )


def test_fundamentals_unit_has_descriptive_metadata():
    unit = _find_unit("fundamentals")
    assert unit["label"], "label 비어 있음 — UI 카드 제목 누락"
    assert unit["description"], "description 비어 있음 — UI 부제 누락"
    assert "06:35" in unit["schedule"], "schedule 표기 누락 (KST 06:35)"


def test_no_duplicate_unit_keys():
    """MANAGED_UNITS의 key가 모두 unique."""
    keys = [u["key"] for u in MANAGED_UNITS]
    assert len(keys) == len(set(keys)), f"중복 key 발견: {keys}"


def test_foreign_flow_unit_registered():
    unit = _find_unit("foreign-flow-sync")
    assert unit is not None, "foreign-flow-sync unit not in MANAGED_UNITS"
    assert "foreign-flow-sync.service" in unit["service"]
    assert "foreign-flow-sync.timer" in unit["timer"]
    assert unit["self_protected"] is False, (
        "self_protected=True면 웹 UI에서 제어 불가 — 외국인 수급 sync는 운영자가 수동 트리거 가능해야 함"
    )


def test_foreign_flow_unit_has_descriptive_metadata():
    unit = _find_unit("foreign-flow-sync")
    assert unit["label"], "label 비어 있음 — UI 카드 제목 누락"
    assert unit["description"], "description 비어 있음 — UI 부제 누락"
    assert "06:40" in unit["schedule"], "schedule 표기 누락 (KST 06:40)"
