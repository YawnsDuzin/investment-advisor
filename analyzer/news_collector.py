"""RSS 뉴스 수집 모듈 — 카테고리별 수집 및 구조화"""
import feedparser
from shared.config import NewsConfig


CATEGORY_LABELS = {
    "global": "글로벌 종합",
    "finance": "경제·금융·시장",
    "technology": "기술·AI·반도체",
    "commodities": "에너지·원자재",
    "korea": "한국 경제",
    "early_signals": "선행 지표·규제·공급망",
    "korea_early": "한국 산업·M&A·자본시장",
}


def _clean_html(text: str) -> str:
    """HTML 태그 간이 제거"""
    for tag in ["<p>", "</p>", "<br>", "<br/>", "<b>", "</b>",
                "<i>", "</i>", "<em>", "</em>", "<strong>", "</strong>"]:
        text = text.replace(tag, " ")
    return " ".join(text.split())


def collect_news_structured(cfg: NewsConfig) -> tuple[str, list[dict]]:
    """RSS 피드에서 뉴스를 수집하여 (텍스트, 구조화 리스트)를 반환

    Returns:
        (news_text, articles)
        - news_text: 분석 파이프라인용 마크다운 텍스트
        - articles: [{"category", "source", "title", "summary", "link", "published"}]
    """
    sections: list[str] = []
    articles: list[dict] = []
    total = 0

    for category, feed_urls in cfg.feeds.items():
        label = CATEGORY_LABELS.get(category, category)
        lines: list[str] = []

        for feed_url in feed_urls:
            try:
                feed = feedparser.parse(feed_url)
                source = feed.feed.get("title", feed_url)

                for entry in feed.entries[: cfg.max_articles_per_feed]:
                    title = entry.get("title", "")
                    summary = _clean_html(
                        entry.get("summary", entry.get("description", ""))
                    )
                    published = entry.get("published", "")
                    link = entry.get("link", "")

                    date_str = f" ({published})" if published else ""
                    lines.append(
                        f"  • [{source}]{date_str} {title}\n    {summary[:500]}"
                    )

                    articles.append({
                        "category": category,
                        "source": source,
                        "title": title,
                        "summary": summary[:1000],
                        "link": link,
                        "published": published,
                    })
                    total += 1

            except Exception as e:
                print(f"[뉴스] {feed_url} 수집 실패: {e}")

        if lines:
            section = f"### [{label}] ({len(lines)}건)\n\n" + "\n\n".join(lines)
            sections.append(section)

    print(f"[뉴스] 총 {total}건 수집 완료 (카테고리 {len(sections)}개)")
    news_text = "\n\n---\n\n".join(sections)
    return news_text, articles


def collect_news(cfg: NewsConfig) -> str:
    """RSS 피드에서 카테고리별 뉴스를 수집하여 구조화된 텍스트로 반환 (하위호환)"""
    news_text, _ = collect_news_structured(cfg)
    return news_text
