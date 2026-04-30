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


def _fake_feed(source_name: str, entries: list):
    feed = MagicMock()
    feed.feed.get = lambda k, default="": {"title": source_name}.get(k, default)
    feed.entries = entries
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
