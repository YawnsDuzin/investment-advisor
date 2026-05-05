"""매도/익절 시그널 단위 테스트.

DB 호출은 모두 mock — execute() 인자 캡처로 룰 평가·알림 생성·dedup 갱신을 검증.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock


class _FakeCursor:
    """SQL 별 응답을 dispatch 하는 fake cursor — fetchall 반환을 query 키워드로 결정."""

    def __init__(self, target_rows=None, stop_rows=None, recipient_rows=None):
        self._target_rows = target_rows or []
        self._stop_rows = stop_rows or []
        self._recipient_rows = recipient_rows or []
        self.executed: list[tuple] = []
        # 카운터로 SELECT 단계 분기
        self._select_step = 0

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        # target_hit SELECT → stop_loss SELECT → 그 외 (recipients)
        upper = (self.executed[-1][0] or "").upper()
        if "TARGET_HIT_NOTIFIED_AT IS NULL" in upper:
            return list(self._target_rows)
        if "STOP_LOSS_NOTIFIED_AT IS NULL" in upper:
            return list(self._stop_rows)
        # recipients fan-out
        return list(self._recipient_rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, cur):
        self._cur = cur
        self.committed = False
        self.rolled_back = False

    def cursor(self, **kw):
        return self._cur

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


def _executed_sql(cur):
    return [s.strip() for s, _ in cur.executed]


# ── 룰 평가 ──

def test_target_hit_fires_when_post_return_exceeds_upside():
    from analyzer.exit_signals import evaluate_exit_signals

    target_rows = [{
        "id": 1, "ticker": "AAPL", "market": "NASDAQ", "asset_name": "Apple",
        "upside_pct": 25.0,
        "post_return_1m_pct": 30.5,  # > 25
        "post_return_3m_pct": None,
        "post_return_6m_pct": None,
        "post_return_1y_pct": None,
    }]
    cur = _FakeCursor(target_rows=target_rows, recipient_rows=[(11,), (12,)])
    with patch("analyzer.exit_signals.get_connection", return_value=_FakeConn(cur)):
        out = evaluate_exit_signals(db_cfg=None)

    assert out["target_hit_count"] == 1
    assert out["target_hit_notifications"] == 2  # 두 recipient
    # UPDATE notified_at 호출 확인
    assert any("target_hit_notified_at = NOW()" in s.lower() or
               "target_hit_notified_at = now()" in s.lower()
               for s in _executed_sql(cur))
    # INSERT 알림 호출 확인 (2건)
    insert_calls = [s for s in _executed_sql(cur) if "INSERT INTO user_notifications" in s]
    assert len(insert_calls) == 2


def test_target_hit_uses_default_when_upside_missing():
    from analyzer.exit_signals import evaluate_exit_signals

    target_rows = [{
        "id": 2, "ticker": "MSFT", "market": "NASDAQ", "asset_name": "Microsoft",
        "upside_pct": None,           # default 30 적용
        "post_return_1m_pct": 32.0,   # > default 30
        "post_return_3m_pct": None,
        "post_return_6m_pct": None,
        "post_return_1y_pct": None,
    }]
    cur = _FakeCursor(target_rows=target_rows, recipient_rows=[(99,)])
    with patch("analyzer.exit_signals.get_connection", return_value=_FakeConn(cur)):
        out = evaluate_exit_signals(db_cfg=None, default_target_pct=30.0)

    assert out["target_hit_count"] == 1


def test_target_hit_does_not_fire_when_below_threshold():
    from analyzer.exit_signals import evaluate_exit_signals

    target_rows = [{
        "id": 3, "ticker": "GOOG", "market": "NASDAQ", "asset_name": "Alphabet",
        "upside_pct": 50.0,
        "post_return_1m_pct": 20.0,   # < 50
        "post_return_3m_pct": None,
        "post_return_6m_pct": None,
        "post_return_1y_pct": None,
    }]
    cur = _FakeCursor(target_rows=target_rows)
    with patch("analyzer.exit_signals.get_connection", return_value=_FakeConn(cur)):
        out = evaluate_exit_signals(db_cfg=None)

    assert out["target_hit_count"] == 0
    assert out["target_hit_notifications"] == 0


def test_stop_loss_fires_on_max_drawdown():
    from analyzer.exit_signals import evaluate_exit_signals

    stop_rows = [{
        "id": 4, "ticker": "TSLA", "market": "NASDAQ", "asset_name": "Tesla",
        "max_drawdown_pct": -22.0,    # <= -15
        "post_return_1m_pct": -5.0,
        "post_return_3m_pct": None,
        "post_return_6m_pct": None,
        "post_return_1y_pct": None,
    }]
    cur = _FakeCursor(stop_rows=stop_rows, recipient_rows=[(7,)])
    with patch("analyzer.exit_signals.get_connection", return_value=_FakeConn(cur)):
        out = evaluate_exit_signals(db_cfg=None, stop_loss_pct=-15.0)

    assert out["stop_loss_count"] == 1
    assert out["stop_loss_notifications"] == 1


def test_stop_loss_fires_on_post_return_when_drawdown_missing():
    from analyzer.exit_signals import evaluate_exit_signals

    stop_rows = [{
        "id": 5, "ticker": "X", "market": "NYSE", "asset_name": "X Corp",
        "max_drawdown_pct": None,
        "post_return_1m_pct": -18.0,  # <= -15
        "post_return_3m_pct": None,
        "post_return_6m_pct": None,
        "post_return_1y_pct": None,
    }]
    cur = _FakeCursor(stop_rows=stop_rows, recipient_rows=[(8,)])
    with patch("analyzer.exit_signals.get_connection", return_value=_FakeConn(cur)):
        out = evaluate_exit_signals(db_cfg=None, stop_loss_pct=-15.0)

    assert out["stop_loss_count"] == 1


def test_stop_loss_skipped_when_above_threshold():
    from analyzer.exit_signals import evaluate_exit_signals

    stop_rows = [{
        "id": 6, "ticker": "Y", "market": "NYSE", "asset_name": "Y Co",
        "max_drawdown_pct": -10.0,
        "post_return_1m_pct": -8.0,
        "post_return_3m_pct": None,
        "post_return_6m_pct": None,
        "post_return_1y_pct": None,
    }]
    cur = _FakeCursor(stop_rows=stop_rows)
    with patch("analyzer.exit_signals.get_connection", return_value=_FakeConn(cur)):
        out = evaluate_exit_signals(db_cfg=None, stop_loss_pct=-15.0)

    assert out["stop_loss_count"] == 0


def test_no_recipients_no_notifications_but_still_marks_dedup():
    """워치/구독자 0명이라도 notified_at 은 갱신 (재평가 무한 반복 방지)."""
    from analyzer.exit_signals import evaluate_exit_signals

    target_rows = [{
        "id": 7, "ticker": "Z", "market": "NYSE", "asset_name": "Z Co",
        "upside_pct": 10.0,
        "post_return_1m_pct": 15.0,
        "post_return_3m_pct": None,
        "post_return_6m_pct": None,
        "post_return_1y_pct": None,
    }]
    cur = _FakeCursor(target_rows=target_rows, recipient_rows=[])
    with patch("analyzer.exit_signals.get_connection", return_value=_FakeConn(cur)):
        out = evaluate_exit_signals(db_cfg=None)

    assert out["target_hit_count"] == 1
    assert out["target_hit_notifications"] == 0
    # UPDATE notified_at 가 여전히 호출됐는지
    update_calls = [s for s in _executed_sql(cur) if "target_hit_notified_at = NOW()" in s]
    assert len(update_calls) == 1


def test_zero_or_negative_upside_falls_back_to_default():
    """proposal.upside_pct 가 None/0/음수일 때 default 적용."""
    from analyzer.exit_signals import evaluate_exit_signals

    target_rows = [{
        "id": 8, "ticker": "A", "market": "NYSE", "asset_name": "A Co",
        "upside_pct": -5.0,            # 비정상 — default 사용
        "post_return_1m_pct": 32.0,    # default 30 초과
        "post_return_3m_pct": None,
        "post_return_6m_pct": None,
        "post_return_1y_pct": None,
    }]
    cur = _FakeCursor(target_rows=target_rows, recipient_rows=[(1,)])
    with patch("analyzer.exit_signals.get_connection", return_value=_FakeConn(cur)):
        out = evaluate_exit_signals(db_cfg=None, default_target_pct=30.0)

    assert out["target_hit_count"] == 1


def test_notification_title_format():
    """알림 INSERT 호출의 title 인자에 한글 시그널 prefix + 수익률 포함."""
    from analyzer.exit_signals import evaluate_exit_signals

    target_rows = [{
        "id": 9, "ticker": "AAPL", "market": "NASDAQ", "asset_name": "Apple Inc",
        "upside_pct": 25.0,
        "post_return_1m_pct": 31.5,
        "post_return_3m_pct": None,
        "post_return_6m_pct": None,
        "post_return_1y_pct": None,
    }]
    cur = _FakeCursor(target_rows=target_rows, recipient_rows=[(42,)])
    with patch("analyzer.exit_signals.get_connection", return_value=_FakeConn(cur)):
        evaluate_exit_signals(db_cfg=None)

    insert_call = next(
        (params for sql, params in cur.executed if "INSERT INTO user_notifications" in (sql or "")),
        None,
    )
    assert insert_call is not None
    user_id, title, detail, link = insert_call
    assert user_id == 42
    assert "익절시그널" in title
    assert "Apple Inc" in title
    assert "+31.5%" in title
    assert link == "/stocks/AAPL"
