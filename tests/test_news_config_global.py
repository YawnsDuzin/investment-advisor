"""NewsConfig — Sprint 1 PR-2 region-tagged feed sources.

기존 `feeds: dict[str, list[str]]` 를 `feed_sources: list[FeedSpec]` 로 리팩토.
각 FeedSpec 에 url/lang/region/category 메타데이터 부착.
GLOBAL_NEWS_ENABLED env 토글로 KR+US 외 region 활성화 제어.

Spec: _docs/20260427055258_sprint1-design.md §6
"""
import os
from unittest.mock import patch


class TestFeedSpec:
    def test_feedspec_required_fields(self):
        from shared.config import FeedSpec
        spec = FeedSpec(
            url="https://example.com/rss",
            lang="en",
            region="US",
            category="finance",
        )
        assert spec.url == "https://example.com/rss"
        assert spec.lang == "en"
        assert spec.region == "US"
        assert spec.category == "finance"


class TestNewsConfigFeedSources:
    def test_feed_sources_present(self):
        from shared.config import NewsConfig
        cfg = NewsConfig()
        assert hasattr(cfg, "feed_sources")
        assert isinstance(cfg.feed_sources, list)
        assert len(cfg.feed_sources) > 0

    def test_all_feeds_have_metadata(self):
        from shared.config import NewsConfig, FeedSpec
        cfg = NewsConfig()
        for spec in cfg.feed_sources:
            assert isinstance(spec, FeedSpec)
            assert spec.url
            assert spec.lang in ("ko", "en", "ja", "zh")
            assert spec.region in ("KR", "US", "JP", "CN", "EU", "GLOBAL")
            assert spec.category

    def test_kr_feeds_exist(self):
        from shared.config import NewsConfig
        cfg = NewsConfig()
        kr = [f for f in cfg.feed_sources if f.region == "KR"]
        assert len(kr) >= 2
        for f in kr:
            assert f.lang == "ko"

    def test_us_feeds_exist(self):
        from shared.config import NewsConfig
        cfg = NewsConfig()
        us = [f for f in cfg.feed_sources if f.region == "US"]
        assert len(us) >= 3
        for f in us:
            assert f.lang == "en"

    def test_jp_feed_exists(self):
        from shared.config import NewsConfig
        cfg = NewsConfig()
        jp = [f for f in cfg.feed_sources if f.region == "JP"]
        assert len(jp) >= 1
        for f in jp:
            assert f.lang in ("en", "ja")

    def test_cn_feed_exists(self):
        from shared.config import NewsConfig
        cfg = NewsConfig()
        cn = [f for f in cfg.feed_sources if f.region == "CN"]
        assert len(cn) >= 1

    def test_eu_feed_exists(self):
        from shared.config import NewsConfig
        cfg = NewsConfig()
        eu = [f for f in cfg.feed_sources if f.region == "EU"]
        assert len(eu) >= 1

    def test_legacy_feeds_dict_still_available(self):
        from shared.config import NewsConfig
        cfg = NewsConfig()
        assert hasattr(cfg, "feeds")
        assert isinstance(cfg.feeds, dict)
        for cat, urls in cfg.feeds.items():
            for url in urls:
                matched = [f for f in cfg.feed_sources if f.url == url]
                assert matched, f"feeds dict 의 {url} 이 feed_sources 에 없음"


class TestGlobalNewsEnabledToggle:
    def test_default_includes_global(self):
        from shared.config import NewsConfig
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GLOBAL_NEWS_ENABLED", None)
            cfg = NewsConfig()
            regions = {f.region for f in cfg.active_feed_sources()}
            assert "JP" in regions
            assert "CN" in regions
            assert "EU" in regions

    def test_disabled_excludes_global(self):
        from shared.config import NewsConfig
        with patch.dict(os.environ, {"GLOBAL_NEWS_ENABLED": "false"}):
            cfg = NewsConfig()
            regions = {f.region for f in cfg.active_feed_sources()}
            assert "KR" in regions
            assert "US" in regions
            assert "JP" not in regions
            assert "CN" not in regions
            assert "EU" not in regions

    def test_active_returns_all_when_enabled(self):
        from shared.config import NewsConfig
        with patch.dict(os.environ, {"GLOBAL_NEWS_ENABLED": "true"}):
            cfg = NewsConfig()
            assert len(cfg.active_feed_sources()) == len(cfg.feed_sources)
