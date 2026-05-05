"""섹터 로테이션 모듈 단위 테스트.

DB 호출은 _compute_group 을 patch 로 가짜화하여 format/infer 함수의
프롬프트 출력만 검증한다.
"""
from __future__ import annotations

from unittest.mock import patch


def _sample_group(prefix: str = "krx") -> dict:
    """KRX 또는 US 그룹용 샘플 dict — sectors는 내림차순 정렬됨."""
    sectors = [
        {"sector": f"{prefix}_semiconductors", "sample_size": 25,
         "r1m_avg_pct": 8.5, "r3m_avg_pct": 18.2, "r6m_avg_pct": 30.0,
         "breadth_20d_pct": 72.0},
        {"sector": f"{prefix}_biotech", "sample_size": 20,
         "r1m_avg_pct": 5.1, "r3m_avg_pct": 11.0, "r6m_avg_pct": 15.0,
         "breadth_20d_pct": 60.0},
        {"sector": f"{prefix}_financials", "sample_size": 15,
         "r1m_avg_pct": 1.2, "r3m_avg_pct": 4.0, "r6m_avg_pct": 8.0,
         "breadth_20d_pct": 50.0},
        {"sector": f"{prefix}_energy", "sample_size": 10,
         "r1m_avg_pct": -2.0, "r3m_avg_pct": -3.5, "r6m_avg_pct": -5.0,
         "breadth_20d_pct": 35.0},
        {"sector": f"{prefix}_real_estate", "sample_size": 8,
         "r1m_avg_pct": -7.0, "r3m_avg_pct": -12.0, "r6m_avg_pct": -18.0,
         "breadth_20d_pct": 20.0},
    ]
    return {
        "sector_count": len(sectors),
        "sectors": sectors,
        "leading_sectors": [s["sector"] for s in sectors[:3]],
        "lagging_sectors": [s["sector"] for s in list(reversed(sectors))[:3]],
    }


def test_compute_returns_groups_when_data_exists():
    from analyzer.sector_rotation import compute_sector_rotation

    with patch("analyzer.sector_rotation._compute_group") as m:
        m.side_effect = [_sample_group("krx"), _sample_group("us")]
        snap = compute_sector_rotation(db_cfg=None)

    assert "groups" in snap
    assert set(snap["groups"].keys()) == {"KRX", "US"}
    assert snap["groups"]["KRX"]["sector_count"] == 5
    assert "computed_at" in snap


def test_compute_returns_empty_when_no_data():
    from analyzer.sector_rotation import compute_sector_rotation

    with patch("analyzer.sector_rotation._compute_group", return_value=None):
        snap = compute_sector_rotation(db_cfg=None)

    assert snap == {}


def test_format_text_includes_leading_and_lagging():
    from analyzer.sector_rotation import format_sector_rotation_text

    snap = {
        "computed_at": "2026-05-05T06:30:00+09:00",
        "groups": {
            "KRX": _sample_group("krx"),
            "US": _sample_group("us"),
        },
    }
    text = format_sector_rotation_text(snap)
    # 시장 라벨
    assert "한국" in text and "(KRX)" in text
    assert "미국" in text and "(US)" in text
    # 강세/약세 헤더
    assert "강세 섹터" in text
    assert "약세 섹터" in text
    # 강세 섹터 내용
    assert "krx_semiconductors" in text
    # 약세 섹터 내용
    assert "krx_real_estate" in text
    # 수익률 표기
    assert "+8.50%" in text
    assert "-7.00%" in text
    # breadth
    assert "72%" in text  # 20D 상승비율


def test_format_text_empty_for_empty_snap():
    from analyzer.sector_rotation import format_sector_rotation_text
    assert format_sector_rotation_text({}) == ""
    assert format_sector_rotation_text({"groups": {}}) == ""


def test_infer_hint_summarizes_leading_and_lagging():
    from analyzer.sector_rotation import infer_rotation_hint

    snap = {"groups": {
        "KRX": _sample_group("krx"),
        "US": _sample_group("us"),
    }}
    hint = infer_rotation_hint(snap)
    assert "한국 강세" in hint
    assert "미국 강세" in hint
    assert "krx_semiconductors" in hint
    # leading 3개만 노출
    assert hint.count("|") == 1  # KRX | US 구분자


def test_infer_hint_empty_for_empty_snap():
    from analyzer.sector_rotation import infer_rotation_hint
    assert infer_rotation_hint({}) == ""


def test_compute_group_sql_uses_sector_norm_filter():
    """SQL 에 sector_norm IS NOT NULL 가드와 listed=TRUE 가 들어가야 한다."""
    from analyzer.sector_rotation import _compute_group

    captured = {}

    class _Cur:
        def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = list(params)
        def fetchall(self):
            return []
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass

    class _Conn:
        def cursor(self, **kw):
            return _Cur()
        def close(self):
            pass

    with patch("analyzer.sector_rotation.get_connection", return_value=_Conn()):
        out = _compute_group(None, ("KOSPI", "KOSDAQ"), window_days=200)

    assert out is None  # 결과 없음 → None
    assert "sector_norm IS NOT NULL" in captured["sql"]
    assert "u.listed = TRUE" in captured["sql"]
    assert "HAVING COUNT(*) >= " in captured["sql"]
    # min sample size 가 params 마지막에 들어가야 함
    assert captured["params"][-1] == 5  # _MIN_SAMPLE_SIZE
