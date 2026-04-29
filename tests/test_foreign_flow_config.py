"""ForeignFlowConfig 환경변수 파싱 테스트."""
import os
from unittest.mock import patch

from shared.config import AppConfig, ForeignFlowConfig


def test_default_values():
    """미설정 시 default 값 적용."""
    env_clean = {k: v for k, v in os.environ.items() if not k.startswith("FOREIGN_FLOW_")}
    with patch.dict(os.environ, env_clean, clear=True):
        cfg = ForeignFlowConfig()
    assert cfg.sync_enabled is True
    assert cfg.retention_days == 400
    assert cfg.delisted_retention_days == 200
    assert cfg.max_consecutive_failures == 50
    assert cfg.staleness_days == 2
    assert cfg.missing_threshold_kospi == 5.0
    assert cfg.missing_threshold_kosdaq == 10.0


def test_env_override():
    """환경변수 설정 시 override."""
    overrides = {
        "FOREIGN_FLOW_SYNC_ENABLED": "false",
        "FOREIGN_FLOW_RETENTION_DAYS": "180",
        "FOREIGN_FLOW_MISSING_THRESHOLD_KOSPI": "3.5",
    }
    with patch.dict(os.environ, overrides):
        cfg = ForeignFlowConfig()
    assert cfg.sync_enabled is False
    assert cfg.retention_days == 180
    assert cfg.missing_threshold_kospi == 3.5


def test_missing_pct_threshold_dispatch():
    """시장별 임계 조회 — 미정의 시장은 fallback."""
    cfg = ForeignFlowConfig()
    assert cfg.missing_pct_threshold("KOSPI") == cfg.missing_threshold_kospi
    assert cfg.missing_pct_threshold("KOSDAQ") == cfg.missing_threshold_kosdaq
    assert cfg.missing_pct_threshold("NASDAQ") == 100.0  # KRX 외 = 자연 제외 의미


def test_app_config_exposes_foreign_flow():
    cfg = AppConfig()
    assert isinstance(cfg.foreign_flow, ForeignFlowConfig)
