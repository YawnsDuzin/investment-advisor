"""FundamentalsConfig dataclass 환경변수 파싱 검증."""
import os
import pytest
from shared.config import FundamentalsConfig, AppConfig


def test_defaults():
    cfg = FundamentalsConfig()
    assert cfg.retention_days == 800
    assert cfg.delisted_retention_days == 400
    assert cfg.sync_enabled is True
    assert cfg.pykrx_batch_size == 200
    assert cfg.yfinance_batch_size == 50
    assert cfg.validation_tolerance_pct == 5.0
    assert cfg.us_max_consecutive_failures == 50
    # health check thresholds
    assert cfg.staleness_days == 2
    assert cfg.missing_threshold_kospi == 5.0
    assert cfg.missing_threshold_kosdaq == 5.0
    assert cfg.missing_threshold_nasdaq == 3.0
    assert cfg.missing_threshold_nyse == 3.0
    # threshold lookup
    assert cfg.missing_pct_threshold("KOSPI") == 5.0
    assert cfg.missing_pct_threshold("nyse") == 3.0  # case-insensitive
    assert cfg.missing_pct_threshold("UNKNOWN") == 10.0  # fallback


def test_env_override(monkeypatch):
    monkeypatch.setenv("FUNDAMENTALS_RETENTION_DAYS", "365")
    monkeypatch.setenv("FUNDAMENTALS_SYNC_ENABLED", "false")
    monkeypatch.setenv("FUNDAMENTALS_PYKRX_BATCH_SIZE", "50")
    cfg = FundamentalsConfig()
    assert cfg.retention_days == 365
    assert cfg.sync_enabled is False
    assert cfg.pykrx_batch_size == 50


def test_appconfig_includes_fundamentals():
    app = AppConfig()
    assert isinstance(app.fundamentals, FundamentalsConfig)
