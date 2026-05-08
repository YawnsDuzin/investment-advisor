"""analyzer/macro_observer.py — 매크로 일배치 헬퍼 단위 테스트 (Tier 2 #4)."""
from __future__ import annotations

from analyzer.macro_observer import (
    YFINANCE_VARIABLES,
    MACRO_LABELS_KR,
    MACRO_UNITS,
)


def test_yfinance_variables_complete():
    """5개 핵심 매크로 변수 정의."""
    assert "us_10y_yield" in YFINANCE_VARIABLES
    assert "usdkrw" in YFINANCE_VARIABLES
    assert "wti" in YFINANCE_VARIABLES
    assert "vix" in YFINANCE_VARIABLES
    assert "gold" in YFINANCE_VARIABLES


def test_yfinance_symbols_format():
    """yfinance 심볼은 '^XXX' 또는 'XXX=Y' / 'XX=F' 패턴."""
    for var, sym in YFINANCE_VARIABLES.items():
        assert sym.startswith("^") or "=" in sym, f"{var}: {sym}"


def test_labels_cover_all_variables():
    """라벨이 모든 변수에 매핑되어 있어야 UI 카드 누락 없음."""
    for var in YFINANCE_VARIABLES:
        assert var in MACRO_LABELS_KR, f"라벨 누락: {var}"


def test_units_cover_all_variables():
    for var in YFINANCE_VARIABLES:
        assert var in MACRO_UNITS, f"단위 누락: {var}"


def test_us_10y_yield_unit_is_percent():
    assert MACRO_UNITS["us_10y_yield"] == "%"


def test_usdkrw_unit_is_won():
    assert MACRO_UNITS["usdkrw"] == "₩"


def test_fetch_and_store_returns_empty_dict_when_yfinance_fails(monkeypatch):
    """yfinance 호출 실패 → 빈 dict 그래이스풀 폴백 (DB 미접근)."""
    from analyzer import macro_observer

    # yfinance import 자체를 못 찾는 척
    import builtins
    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "yfinance":
            raise ImportError("yfinance unavailable in test env")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    out = macro_observer.fetch_and_store(db_cfg=None)
    assert out == {}


def test_fetch_and_store_skips_unknown_variables():
    """variables 인자에 미정의 변수가 들어와도 무시 — known 만 처리."""
    from analyzer import macro_observer

    out = macro_observer.fetch_and_store(
        db_cfg=None,
        variables=["nonexistent_var", "another_unknown"],
    )
    assert out == {}
