"""news_collector — Sprint 1 PR-2 region 태깅 + 그룹 text.

각 article 에 lang/region/title_original 태그 부착 + region 별 섹션 그룹.
feedparser 는 conftest.py 에서 mock 처리되어 실제 RSS 호출 없음.

Spec: _docs/20260427055258_sprint1-design.md §6
"""
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta, timezone
import os

import pytest


def _recent_published() -> str:
    """24h cutoff 안에 들어가도록 항상 1시간 전으로 동적 생성."""
    dt = datetime.now(timezone.utc) - timedelta(hours=1)
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


def _fake_entry(title: str, summary: str, published: str | None = None):
    if published is None:
        published = _recent_published()
    e = MagicMock()
    e.get = lambda k, default="": {
        "title": title, "summary": summary, "description": summary,
        "link": "http://x", "published": published,
    }.get(k, default)
    return e


def _fake_feed(source_name: str, entries: list, bozo: int = 0):
    feed = MagicMock()
    feed.feed.get = lambda k, default="": {"title": source_name}.get(k, default)
    feed.entries = entries
    feed.bozo = bozo  # 0 = 정상, 1 = 파싱 에러
    feed.bozo_exception = None
    return feed


class TestArticleTagging:
    def test_kr_article_tagged_ko_kr(self):
        from shared.config import NewsConfig, FeedSpec
        from analyzer.news_collector import collect_news_structured

        cfg = NewsConfig.__new__(NewsConfig)
        cfg.feed_sources = [FeedSpec("http://kr.example/rss", "ko", "KR", "korea")]
        cfg.max_articles_per_feed = 5

        with patch("analyzer.news_collector.feedparser.parse") as fp:
            fp.return_value = _fake_feed("한국경제",
                [_fake_entry("삼성전자 신제품 출시", "본문 요약")])
            _, articles = collect_news_structured(cfg)

        assert len(articles) == 1
        a = articles[0]
        assert a["lang"] == "ko"
        assert a["region"] == "KR"
        assert a["title_original"] == "삼성전자 신제품 출시"
        assert a["category"] == "korea"

    def test_us_article_tagged_en_us(self):
        from shared.config import NewsConfig, FeedSpec
        from analyzer.news_collector import collect_news_structured

        cfg = NewsConfig.__new__(NewsConfig)
        cfg.feed_sources = [FeedSpec("http://us.example/rss", "en", "US", "finance")]
        cfg.max_articles_per_feed = 5

        with patch("analyzer.news_collector.feedparser.parse") as fp:
            fp.return_value = _fake_feed("Reuters",
                [_fake_entry("Fed signals rate cut", "summary text")])
            _, articles = collect_news_structured(cfg)

        assert articles[0]["lang"] == "en"
        assert articles[0]["region"] == "US"
        assert articles[0]["title_original"] == "Fed signals rate cut"
        assert articles[0]["category"] == "finance"

    def test_jp_article_tagged_jp(self):
        from shared.config import NewsConfig, FeedSpec
        from analyzer.news_collector import collect_news_structured

        cfg = NewsConfig.__new__(NewsConfig)
        cfg.feed_sources = [FeedSpec("http://nikkei.example/rss", "en", "JP", "asia_business")]
        cfg.max_articles_per_feed = 5

        with patch("analyzer.news_collector.feedparser.parse") as fp:
            fp.return_value = _fake_feed("Nikkei Asia",
                [_fake_entry("Toyota beats forecast", "summary")])
            _, articles = collect_news_structured(cfg)

        assert articles[0]["region"] == "JP"


class TestRegionGroupedText:
    def _build_cfg(self, *specs):
        from shared.config import NewsConfig
        cfg = NewsConfig.__new__(NewsConfig)
        cfg.feed_sources = list(specs)
        cfg.max_articles_per_feed = 5
        return cfg

    def test_kr_us_grouped_separately(self):
        from shared.config import FeedSpec
        from analyzer.news_collector import collect_news_structured

        cfg = self._build_cfg(
            FeedSpec("http://kr/rss", "ko", "KR", "korea"),
            FeedSpec("http://us/rss", "en", "US", "finance"),
        )
        with patch("analyzer.news_collector.feedparser.parse") as fp:
            def parse_side(url):
                if "kr" in url:
                    return _fake_feed("한경", [_fake_entry("삼성", "내용")])
                return _fake_feed("Reuters", [_fake_entry("Apple", "content")])
            fp.side_effect = parse_side
            news_text, _ = collect_news_structured(cfg)

        assert "한국" in news_text or "[KR" in news_text or "[KOREA" in news_text.upper()
        assert "미국" in news_text or "[US" in news_text.upper()
        assert "삼성" in news_text
        assert "Apple" in news_text

    def test_global_section_for_jp_cn_eu(self):
        from shared.config import FeedSpec
        from analyzer.news_collector import collect_news_structured

        cfg = self._build_cfg(
            FeedSpec("http://jp/rss", "en", "JP", "asia_business"),
            FeedSpec("http://cn/rss", "en", "CN", "china_business"),
            FeedSpec("http://eu/rss", "en", "EU", "eu_companies"),
        )
        with patch("analyzer.news_collector.feedparser.parse") as fp:
            def parse_side(url):
                name = "Nikkei" if "jp" in url else ("Caixin" if "cn" in url else "FT")
                title = f"{name} headline"
                return _fake_feed(name, [_fake_entry(title, "content")])
            fp.side_effect = parse_side
            news_text, articles = collect_news_structured(cfg)

        assert len(articles) == 3
        assert "Nikkei" in news_text
        assert "Caixin" in news_text
        assert "FT" in news_text


class TestRegionDedup:
    """region 별 분리 dedup — 동일 헤드라인이 다른 region 에 등장하면 둘 다 보존."""

    def test_same_title_different_regions_both_preserved(self):
        from shared.config import NewsConfig, FeedSpec
        from analyzer.news_collector import collect_news_structured

        cfg = NewsConfig.__new__(NewsConfig)
        cfg.feed_sources = [
            FeedSpec("http://us/rss", "en", "US", "finance"),
            FeedSpec("http://kr/rss", "ko", "KR", "korea"),
        ]
        cfg.max_articles_per_feed = 5

        # 동일 헤드라인 (prefix 30 동일)
        title = "Fed holds rates steady at 5.25%"

        with patch("analyzer.news_collector.feedparser.parse") as fp:
            def parse_side(url):
                src = "Bloomberg" if "us" in url else "한경"
                return _fake_feed(src, [_fake_entry(title, "내용")])
            fp.side_effect = parse_side
            _, articles = collect_news_structured(cfg)

        # region 별 분리 dedup → US + KR 각각 1건씩 = 2건
        assert len(articles) == 2
        regions = {a["region"] for a in articles}
        assert regions == {"US", "KR"}

    def test_same_title_same_region_deduped(self):
        from shared.config import NewsConfig, FeedSpec
        from analyzer.news_collector import collect_news_structured

        cfg = NewsConfig.__new__(NewsConfig)
        cfg.feed_sources = [
            FeedSpec("http://us1/rss", "en", "US", "finance"),
            FeedSpec("http://us2/rss", "en", "US", "finance"),
        ]
        cfg.max_articles_per_feed = 5

        title = "Fed holds rates steady at 5.25%"
        with patch("analyzer.news_collector.feedparser.parse") as fp:
            fp.return_value = _fake_feed("src", [_fake_entry(title, "내용")])
            _, articles = collect_news_structured(cfg)

        # 동일 region 내 중복은 dedup 됨
        assert len(articles) == 1


class TestHealthLabels:
    """health check 3계층 — dead / stale / parse_error."""

    def test_dead_feed_logs_warning(self, caplog):
        import logging
        from shared.config import NewsConfig, FeedSpec
        from analyzer.news_collector import collect_news_structured

        cfg = NewsConfig.__new__(NewsConfig)
        cfg.feed_sources = [FeedSpec("http://dead/rss", "en", "US", "finance")]
        cfg.max_articles_per_feed = 5

        caplog.set_level(logging.WARNING)
        with patch("analyzer.news_collector.feedparser.parse") as fp:
            fp.return_value = _fake_feed("dead_src", [])  # entries 비어있음
            _, articles = collect_news_structured(cfg)

        assert len(articles) == 0
        assert any("피드 0건" in rec.message for rec in caplog.records)

    def test_stale_feed_logs_warning(self, caplog):
        """entries > 0 but 24h fresh == 0 → stale 라벨."""
        import logging
        from shared.config import NewsConfig, FeedSpec
        from analyzer.news_collector import collect_news_structured

        cfg = NewsConfig.__new__(NewsConfig)
        cfg.feed_sources = [FeedSpec("http://stale/rss", "en", "US", "finance")]
        cfg.max_articles_per_feed = 5

        # 7일 전 published — 24h cutoff 위반
        old_published = "Mon, 01 Apr 2026 10:00:00 +0000"

        caplog.set_level(logging.WARNING)
        with patch("analyzer.news_collector.feedparser.parse") as fp:
            fp.return_value = _fake_feed("stale_src",
                [_fake_entry("old headline", "내용", published=old_published)])
            _, articles = collect_news_structured(cfg)

        assert len(articles) == 0
        assert any("피드 stale" in rec.message for rec in caplog.records)

    def test_parse_error_feed_logs_warning(self, caplog):
        """feedparser bozo 플래그 + entries 0 → parse_error."""
        import logging
        from shared.config import NewsConfig, FeedSpec
        from analyzer.news_collector import collect_news_structured

        cfg = NewsConfig.__new__(NewsConfig)
        cfg.feed_sources = [FeedSpec("http://broken/rss", "en", "US", "finance")]
        cfg.max_articles_per_feed = 5

        broken_feed = MagicMock()
        broken_feed.feed.get = lambda k, default="": default
        broken_feed.entries = []
        broken_feed.bozo = 1
        broken_feed.bozo_exception = Exception("XML 파싱 실패")

        caplog.set_level(logging.WARNING)
        with patch("analyzer.news_collector.feedparser.parse") as fp:
            fp.return_value = broken_feed
            _, articles = collect_news_structured(cfg)

        assert len(articles) == 0
        assert any("파싱 에러" in rec.message for rec in caplog.records)


class TestGlobalNewsEnabledIntegration:
    def test_disabled_excludes_jp_cn_eu(self, monkeypatch):
        from shared.config import NewsConfig
        from analyzer.news_collector import collect_news_structured

        monkeypatch.setenv("GLOBAL_NEWS_ENABLED", "false")
        cfg = NewsConfig()

        with patch("analyzer.news_collector.feedparser.parse") as fp:
            fp.return_value = _fake_feed("src", [])
            _, articles = collect_news_structured(cfg)

        called_urls = [c.args[0] for c in fp.call_args_list]
        kr_urls = [u for u in called_urls if "hankyung" in u or "etnews" in u or "thebell" in u]
        global_only = [u for u in called_urls if "nikkei" in u.lower() or "caixin" in u or "yicai" in u]
        assert len(kr_urls) > 0
        assert len(global_only) == 0
