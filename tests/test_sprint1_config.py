"""Sprint1Config — 9개 신규 env 파싱 + 기본값 검증.

env 가 설정되지 않으면 spec 의 기본값을 따른다.
Spec: _docs/20260427055258_sprint1-design.md §4.1
"""
import os
from unittest.mock import patch

import pytest


class TestSprint1ConfigDefaults:
    """env 없을 때 기본값 — spec 과 일치해야 한다."""

    def setup_method(self):
        # 기존 env 격리 — Sprint1 키만 제거
        self._removed = {}
        for key in (
            "NL_SEARCH_ENABLED", "NL_SEARCH_TIMEOUT_SEC", "NL_SEARCH_RESULT_LIMIT",
            "NL_SEARCH_READONLY_DSN", "ENABLE_RED_TEAM", "RED_TEAM_FOR_TIER",
            "GLOBAL_NEWS_ENABLED", "CHART_VISION_ENABLED", "CHART_VISION_MAX_BYTES",
            "CHART_VISION_ANON_LIMIT",
        ):
            if key in os.environ:
                self._removed[key] = os.environ.pop(key)

    def teardown_method(self):
        # 복원
        for key, val in self._removed.items():
            os.environ[key] = val

    def test_defaults(self):
        from shared.config import Sprint1Config
        c = Sprint1Config()
        assert c.nl_search_enabled is True
        assert c.nl_search_timeout_sec == 10
        assert c.nl_search_result_limit == 100
        assert c.nl_search_readonly_dsn == ""
        assert c.enable_red_team is False
        assert c.red_team_for_tier == "premium"
        assert c.global_news_enabled is True
        assert c.chart_vision_enabled is True
        assert c.chart_vision_max_bytes == 5_242_880
        assert c.chart_vision_anon_limit == 1


class TestSprint1ConfigEnvOverride:
    """env 가 설정되면 그 값을 따른다."""

    def test_bool_env_parsed(self):
        from shared.config import Sprint1Config
        with patch.dict(os.environ, {
            "NL_SEARCH_ENABLED": "false",
            "ENABLE_RED_TEAM": "true",
            "GLOBAL_NEWS_ENABLED": "0",
            "CHART_VISION_ENABLED": "yes",
        }):
            c = Sprint1Config()
            assert c.nl_search_enabled is False
            assert c.enable_red_team is True
            assert c.global_news_enabled is False
            assert c.chart_vision_enabled is True

    def test_int_env_parsed(self):
        from shared.config import Sprint1Config
        with patch.dict(os.environ, {
            "NL_SEARCH_TIMEOUT_SEC": "30",
            "NL_SEARCH_RESULT_LIMIT": "250",
            "CHART_VISION_MAX_BYTES": "1048576",
            "CHART_VISION_ANON_LIMIT": "3",
        }):
            c = Sprint1Config()
            assert c.nl_search_timeout_sec == 30
            assert c.nl_search_result_limit == 250
            assert c.chart_vision_max_bytes == 1_048_576
            assert c.chart_vision_anon_limit == 3

    def test_string_env_parsed(self):
        from shared.config import Sprint1Config
        with patch.dict(os.environ, {
            "NL_SEARCH_READONLY_DSN": "postgresql://nl_reader:pw@host/db",
            "RED_TEAM_FOR_TIER": "premium,pro",
        }):
            c = Sprint1Config()
            assert c.nl_search_readonly_dsn == "postgresql://nl_reader:pw@host/db"
            assert c.red_team_for_tier == "premium,pro"


class TestAppConfigIntegration:
    """AppConfig 가 sprint1 필드를 노출."""

    def test_appconfig_has_sprint1(self):
        from shared.config import AppConfig
        cfg = AppConfig()
        assert hasattr(cfg, "sprint1")
        assert cfg.sprint1.nl_search_enabled is True  # 기본값
