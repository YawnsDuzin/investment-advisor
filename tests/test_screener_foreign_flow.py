"""스크리너 외국인 수급 필터 — SQL 생성 검증.

run_screener 의 SQL 빌드 로직만 검증 (실제 DB 호출은 mock).
"""
from unittest.mock import MagicMock, patch

import pytest


def _run(spec, user_id=None):
    """run_screener 실행 후 cur.execute 가 받은 (sql, params) 캡처."""
    from api.routes import screener
    captured = {}
    fake_cur = MagicMock()

    def _capture_execute(sql, params):
        captured["sql"] = sql
        captured["params"] = list(params)

    fake_cur.execute.side_effect = _capture_execute
    fake_cur.fetchall.return_value = []
    fake_conn = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cur

    user = None
    if user_id:
        user = MagicMock()
        user.id = user_id
        user.effective_tier.return_value = "premium"

    screener.run_screener(spec=spec, user=user, conn=fake_conn)
    return captured


def test_no_foreign_keys_skips_join():
    """spec 에 foreign 키 없으면 foreign_flow CTE/JOIN 안 들어감."""
    cap = _run({"sectors": ["semiconductors"]})
    assert "foreign_flow_metrics" not in cap["sql"]
    assert "ff.own_latest" not in cap["sql"]


def test_min_foreign_ownership_pct_clause():
    cap = _run({"min_foreign_ownership_pct": 30.0})
    assert "foreign_flow_metrics" in cap["sql"]
    assert "ff.own_latest >= %s" in cap["sql"]
    assert 30.0 in cap["params"]


def test_delta_filter_with_window_5():
    cap = _run({
        "min_foreign_ownership_delta_pp": 1.5,
        "delta_window_days": 5,
    })
    assert "ff.own_d5" in cap["sql"]
    assert "ff.own_latest - ff.own_d5" in cap["sql"]
    assert 1.5 in cap["params"]


def test_delta_filter_default_window_20():
    cap = _run({"min_foreign_ownership_delta_pp": 1.0})
    assert "ff.own_d20" in cap["sql"]


def test_delta_filter_window_60():
    cap = _run({
        "min_foreign_ownership_delta_pp": 0.5,
        "delta_window_days": 60,
    })
    assert "ff.own_d60" in cap["sql"]


def test_delta_filter_invalid_window_falls_back_to_20():
    cap = _run({
        "min_foreign_ownership_delta_pp": 0.5,
        "delta_window_days": 99,  # invalid
    })
    assert "ff.own_d20" in cap["sql"]
    assert "ff.own_d99" not in cap["sql"]


def test_net_buy_filter_with_window_60():
    cap = _run({
        "min_foreign_net_buy_krw": 1_000_000_000,
        "net_buy_window_days": 60,
    })
    assert "ff.net_buy_60d" in cap["sql"]
    assert 1_000_000_000 in cap["params"]


def test_negative_delta_input_allowed():
    cap = _run({"min_foreign_ownership_delta_pp": -2.0})
    assert -2.0 in cap["params"]


def test_sort_foreign_delta_uses_filter_window():
    """sort=foreign_delta_desc + delta_window_days=5 → ORDER BY 가 own_d5 참조."""
    cap = _run({
        "sort": "foreign_delta_desc",
        "delta_window_days": 5,
    })
    assert "ORDER BY (ff.own_latest - ff.own_d5) DESC" in cap["sql"]


def test_sort_foreign_net_buy_uses_filter_window():
    cap = _run({
        "sort": "foreign_net_buy_desc",
        "net_buy_window_days": 60,
    })
    assert "ORDER BY ff.net_buy_60d DESC" in cap["sql"]


def test_sort_foreign_ownership_simple():
    cap = _run({"sort": "foreign_ownership_desc"})
    assert "ORDER BY ff.own_latest DESC" in cap["sql"]


def test_response_includes_window_metadata():
    """응답 row 에 윈도우 표기를 위해 SELECT 에 *_window_days 필드 포함."""
    cap = _run({
        "min_foreign_ownership_delta_pp": 1.0,
        "delta_window_days": 5,
    })
    assert "foreign_ownership_delta_window_days" in cap["sql"]
