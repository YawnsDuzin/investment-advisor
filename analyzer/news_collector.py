"""RSS 뉴스 수집 모듈 — 카테고리별 수집 및 구조화"""
import feedparser
from shared.config import NewsConfig


def collect_news(cfg: NewsConfig) -> str:
    """RSS 피드에서 카테고리별 뉴스를 수집하여 구조화된 텍스트로 반환"""
    sections: list[str] = []
    total = 0

    category_labels = {
        "global": "글로벌 종합",
        "finance": "경제·금융·시장",
        "technology": "기술·AI·반도체",
        "commodities": "에너지·원자재",
        "korea": "한국 경제",
        "early_signals": "선행 지표·규제·공급망",
        "korea_early": "한국 산업·M&A·자본시장",
    }

    for category, feed_urls in cfg.feeds.items():
        label = category_labels.get(category, category)
        articles: list[str] = []

        for feed_url in feed_urls:
            try:
                feed = feedparser.parse(feed_url)
                source = feed.feed.get("title", feed_url)

                for entry in feed.entries[: cfg.max_articles_per_feed]:
                    title = entry.get("title", "")
                    summary = entry.get("summary", entry.get("description", ""))
                    for tag in ["<p>", "</p>", "<br>", "<br/>", "<b>", "</b>",
                                "<i>", "</i>", "<em>", "</em>", "<strong>", "</strong>"]:
                        summary = summary.replace(tag, " ")
                    summary = " ".join(summary.split())

                    published = entry.get("published", "")
                    date_str = f" ({published})" if published else ""

                    articles.append(
                        f"  • [{source}]{date_str} {title}\n    {summary[:500]}"
                    )
                    total += 1

            except Exception as e:
                print(f"[뉴스] {feed_url} 수집 실패: {e}")

        if articles:
            section = f"### [{label}] ({len(articles)}건)\n\n" + "\n\n".join(articles)
            sections.append(section)

    print(f"[뉴스] 총 {total}건 수집 완료 (카테고리 {len(sections)}개)")
    return "\n\n---\n\n".join(sections)
