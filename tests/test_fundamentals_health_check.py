"""펀더 결측률 health check — 시장별 결측 비율 + 마지막 sync 시각."""
from datetime import datetime, date, timedelta, timezone
from unittest.mock import MagicMock


def test_compute_missing_rate_per_market():
    """stock_universe 활성 종목 vs 최근 7일 내 펀더 row 보유 종목 비교."""
    fake_conn = MagicMock()
    fake_cur = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cur
    fake_cur.fetchall.return_value = [
        ("KOSPI",  1000, 988, datetime(2026, 4, 25, 6, 35, tzinfo=timezone.utc)),
        ("KOSDAQ", 1500, 1490, datetime(2026, 4, 25, 6, 35, tzinfo=timezone.utc)),
        ("NASDAQ", 600, 598, datetime(2026, 4, 25, 6, 35, tzinfo=timezone.utc)),
        ("NYSE",   400, 0, None),  # 백필 미완 → 결측 100%
    ]
    from tools.fundamentals_health_check import compute_missing_rate
    out = compute_missing_rate(fake_conn)
    by_market = {r["market"]: r for r in out}
    assert abs(by_market["KOSPI"]["missing_pct"] - 1.2) < 0.01
    assert abs(by_market["KOSDAQ"]["missing_pct"] - 0.667) < 0.01
    assert by_market["NYSE"]["missing_pct"] == 100.0
    assert by_market["NYSE"]["last_fetched_at"] is None


def test_compute_missing_rate_zero_total_safe():
    """활성 종목 0개 시장 → division by zero 방지."""
    fake_conn = MagicMock()
    fake_cur = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cur
    fake_cur.fetchall.return_value = [("KONEX", 0, 0, None)]
    from tools.fundamentals_health_check import compute_missing_rate
    out = compute_missing_rate(fake_conn)
    assert out[0]["missing_pct"] == 0.0
