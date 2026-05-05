"""이상 시그널 — 시장별 latest_date 분기 회귀 테스트.

원래 `analyzer/signals.py:_DETECT_SQL` 가 글로벌 `MAX(trade_date)` 단일 필터를
사용해, 한국 단독 휴장(어린이날·설·추석 등) 시 KR 종목이 통째로 누락되고
대시보드 "오늘의 이상 시그널" 카드에 미국 종목만 노출되는 결함이 있었다.

수정 후엔 시장별 자체 latest_date 로 필터하여 KR/US 둘 다 처리된다.
이 테스트는 다음을 가드한다.

  1. _DETECT_SQL 에 시장별 latest 분기(`market_latest`) CTE 가 포함된다.
  2. fetchall 이 KR + US 양쪽 row 를 반환할 때 양쪽 모두 UPSERT 큐에 push 된다.
  3. /api/signals/today 응답이 시장별 latest signal_date 맵을 포함한다.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from unittest.mock import MagicMock, patch


# ─────────────────────────────────────────────
# 1) SQL 토큰 회귀 가드
# ─────────────────────────────────────────────

def test_detect_sql_uses_market_specific_latest():
    """시장별 latest_date 분기 — 글로벌 MAX 단일 필터 회귀 방지."""
    from analyzer import signals
    sql_lower = signals._DETECT_SQL.lower()
    # CTE 이름 또는 LATERAL/per-market max 가 들어있어야 한다
    assert "market_latest" in sql_lower, (
        "_DETECT_SQL 에 'market_latest' CTE 가 없음 — 시장별 latest_date 분기 "
        "누락. 한국 단독 휴장 시 KR 종목 통째 누락 회귀 위험."
    )


# ─────────────────────────────────────────────
# 2) detect_signals — KR + US 양쪽 UPSERT
# ─────────────────────────────────────────────

def _make_row(
    ticker: str, market: str, latest_date: date,
    *, close: float, high_252d: float, low_252d: float,
    volume: int = 1_000_000, avg_volume: float = 500_000.0,
):
    """_DETECT_SQL 의 컬럼 순서와 일치하는 fake row 생성."""
    return (
        ticker, market, latest_date,
        close,        # close_latest
        close - 1,    # open_latest
        volume,       # volume_latest
        close - 2,    # close_prev
        high_252d,    # high_252d
        low_252d,     # low_252d
        close - 5,    # ma200_latest
        close - 5,    # ma200_prev
        avg_volume,   # volume_avg_20
    )


def test_detect_signals_processes_both_kr_and_us_when_calendar_diverges():
    """KR latest=5/1, US latest=5/4 시나리오 — 양쪽 모두 시그널 push 되어야 함.

    Mock 환경이라 SQL 자체는 실행되지 않는다. fetchall 이 KR+US row 를
    동시에 반환하면 detect_signals 가 양쪽을 동등하게 처리하는지 검증한다.
    """
    from analyzer import signals

    kr_row = _make_row(
        "005930", "KOSPI", date(2026, 5, 1),
        close=80000.0, high_252d=80000.0, low_252d=50000.0,  # 신고가
    )
    us_row = _make_row(
        "AAPL", "NASDAQ", date(2026, 5, 4),
        close=250.0, high_252d=250.0, low_252d=150.0,  # 신고가
    )

    cur = MagicMock()
    cur.fetchall.return_value = [kr_row, us_row]
    # fix 후 _DETECT_SQL 이 시장별 latest 를 자체 처리하므로 fetchone 은
    # 호출되지 않을 수 있지만, 호출돼도 안전하도록 stub
    cur.fetchone.return_value = (date(2026, 5, 4),)

    @contextmanager
    def _ctx(*args, **kwargs):
        yield cur

    conn = MagicMock()
    conn.cursor = _ctx

    captured: dict = {}

    def _capture_execute_values(c, sql, rows, page_size=1000):
        captured["rows"] = rows

    with patch.object(signals, "get_connection", return_value=conn), \
         patch("psycopg2.extras.execute_values", side_effect=_capture_execute_values):
        counts = signals.detect_signals(MagicMock())

    rows = captured.get("rows") or []
    markets = {r[3] for r in rows}  # (latest_date, signal_type, ticker, market, metric)
    assert "KOSPI" in markets, f"KR 시그널 누락 — markets={markets}"
    assert "NASDAQ" in markets, f"US 시그널 누락 — markets={markets}"

    # 두 종목 모두 신고가
    assert counts.get("new_52w_high", 0) >= 2, (
        f"new_52w_high 누락 — counts={counts}"
    )


# ─────────────────────────────────────────────
# 3) /api/signals/today 응답 — 시장별 signal_date
# ─────────────────────────────────────────────

def test_today_signals_response_includes_per_market_signal_dates():
    """대시보드 라벨이 시장별로 분리 표기되도록 markets→signal_date 맵을 반환해야 한다."""
    from api.routes import signals as routes_signals

    rows = [
        {"signal_type": "new_52w_high", "ticker": "005930",
         "market": "KOSPI", "metric": {}},
        {"signal_type": "new_52w_high", "ticker": "AAPL",
         "market": "NASDAQ", "metric": {}},
    ]
    market_dates = [
        {"market": "KOSPI", "d": date(2026, 5, 1)},
        {"market": "NASDAQ", "d": date(2026, 5, 4)},
    ]

    # 호출 순서 (api/routes/signals.py:get_today_signals):
    #   1) cur.fetchone  → MAX(signal_date) 단일 (전체 latest, 호환성용)
    #   2) cur.fetchall  → 시장별 MAX(signal_date)
    #   3) cur.fetchall  → (market, signal_date) IN 페어 union 시그널 row
    fetchone_seq = [{"d": date(2026, 5, 4)}]
    fetchall_seq = [market_dates, rows]

    cur = MagicMock()
    fetchone_idx = {"n": 0}
    fetchall_idx = {"n": 0}

    def _fone(*a, **kw):
        v = fetchone_seq[fetchone_idx["n"]]
        fetchone_idx["n"] += 1
        return v

    def _fall(*a, **kw):
        v = fetchall_seq[fetchall_idx["n"]]
        fetchall_idx["n"] += 1
        return v

    cur.fetchone.side_effect = _fone
    cur.fetchall.side_effect = _fall

    @contextmanager
    def _ctx(**kwargs):
        yield cur

    conn = MagicMock()
    conn.cursor = _ctx

    res = routes_signals.get_today_signals(conn=conn, limit=12)

    sd_map = res.get("signal_dates_by_market")
    assert sd_map, (
        "응답에 'signal_dates_by_market' 키 없음 — UI 가 시장별 라벨 분리 표기 불가"
    )
    assert sd_map.get("KOSPI") == "2026-05-01"
    assert sd_map.get("NASDAQ") == "2026-05-04"
