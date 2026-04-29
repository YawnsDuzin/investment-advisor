"""foreign_flow 결측률 진단 도구 — DB 모킹 단위 테스트."""
from datetime import datetime
from unittest.mock import MagicMock, patch


def test_compute_missing_rate_kospi_clean():
    from tools.foreign_flow_health_check import compute_missing_rate
    fake_conn = MagicMock()
    fake_cur = MagicMock()
    fake_cur.fetchall.return_value = [
        ("KOSPI", 100, 98, datetime(2026, 4, 30, 6, 40)),
        ("KOSDAQ", 200, 180, datetime(2026, 4, 30, 6, 40)),
    ]
    fake_conn.cursor.return_value.__enter__.return_value = fake_cur

    result = compute_missing_rate(fake_conn, staleness_days=2)
    assert len(result) == 2
    assert result[0]["market"] == "KOSPI"
    assert result[0]["missing_pct"] == 2.0
    assert result[1]["missing_pct"] == 10.0


def test_main_returns_nonzero_when_threshold_exceeded():
    from tools.foreign_flow_health_check import main
    from shared.config import DatabaseConfig, ForeignFlowConfig

    fake_conn = MagicMock()
    fake_cur = MagicMock()
    fake_cur.fetchall.return_value = [
        ("KOSPI", 100, 98, datetime(2026, 4, 30)),   # 2.0% — under 5.0
        ("KOSDAQ", 100, 70, datetime(2026, 4, 30)),  # 30.0% — over 10.0
    ]
    fake_conn.cursor.return_value.__enter__.return_value = fake_cur

    with patch("tools.foreign_flow_health_check.get_connection", return_value=fake_conn):
        exit_code = main(DatabaseConfig(), ForeignFlowConfig())
    assert exit_code == 1


def test_main_returns_zero_when_clean():
    from tools.foreign_flow_health_check import main
    from shared.config import DatabaseConfig, ForeignFlowConfig

    fake_conn = MagicMock()
    fake_cur = MagicMock()
    fake_cur.fetchall.return_value = [
        ("KOSPI", 100, 98, datetime(2026, 4, 30)),
        ("KOSDAQ", 100, 95, datetime(2026, 4, 30)),
    ]
    fake_conn.cursor.return_value.__enter__.return_value = fake_cur

    with patch("tools.foreign_flow_health_check.get_connection", return_value=fake_conn):
        exit_code = main(DatabaseConfig(), ForeignFlowConfig())
    assert exit_code == 0
