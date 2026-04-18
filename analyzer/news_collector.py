"""RSS 뉴스 수집 모듈 — 카테고리별 수집 및 구조화"""
import time
import socket
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import feedparser
from shared.config import NewsConfig
from shared.logger import get_logger


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
    import re
    text = re.sub(r'<[^>]+>', ' ', text)
    return " ".join(text.split())


def _parse_published(published: str) -> datetime | None:
    """RSS published 문자열을 datetime으로 파싱 (실패 시 None)"""
    if not published:
        return None
    try:
        return parsedate_to_datetime(published)
    except Exception:
        pass
    # feedparser의 time_struct 포맷 시도
    try:
        return datetime(*time.strptime(published[:25], "%Y-%m-%dT%H:%M:%S")[:6],
                        tzinfo=timezone.utc)
    except Exception:
        return None


def collect_news_structured(cfg: NewsConfig) -> tuple[str, list[dict]]:
    """RSS 피드에서 뉴스를 수집하여 (텍스트, 구조화 리스트)를 반환

    최적화:
    - 최근 24시간 이내 뉴스만 수집 (시간 필터링)
    - 제목 앞 30자 기준 소스 간 교차 중복 제거
    - 요약 300자로 축소 (토큰 절감)

    Returns:
        (news_text, articles)
        - news_text: 분석 파이프라인용 마크다운 텍스트
        - articles: [{"category", "source", "title", "summary", "link", "published"}]
    """
    sections: list[str] = []
    articles: list[dict] = []
    seen_titles: set[str] = set()  # 제목 앞 30자 기준 중복 제거
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    total = 0
    skipped_old = 0
    skipped_dup = 0

    log = get_logger("뉴스")
    # feedparser용 소켓 타임아웃 (기본 무제한 → 30초 제한)
    _orig_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(30)

    for category, feed_urls in cfg.feeds.items():
        label = CATEGORY_LABELS.get(category, category)
        lines: list[str] = []

        for feed_url in feed_urls:
            try:
                feed = feedparser.parse(feed_url)
                source = feed.feed.get("title", feed_url)

                for entry in feed.entries[: cfg.max_articles_per_feed]:
                    title = entry.get("title", "")
                    published = entry.get("published", "")

                    # B2: 시간 필터링 — 24시간 이내 뉴스만
                    pub_dt = _parse_published(published)
                    if pub_dt and pub_dt < cutoff:
                        skipped_old += 1
                        continue

                    # B3: 교차 중복 제거 — 제목 앞 30자 기준
                    title_key = title[:30].strip().lower()
                    if title_key in seen_titles:
                        skipped_dup += 1
                        continue
                    seen_titles.add(title_key)

                    summary = _clean_html(
                        entry.get("summary", entry.get("description", ""))
                    )
                    link = entry.get("link", "")

                    # A2: 프롬프트 입력 텍스트 축약 (토큰 절감)
                    # 날짜 문자열 축약: 불필요한 요일·시간대 정보 제거
                    short_date = ""
                    if pub_dt:
                        short_date = f" ({pub_dt.strftime('%m/%d %H:%M')})"
                    elif published:
                        short_date = f" ({published[:16]})"
                    lines.append(
                        f"  • [{source}]{short_date} {title}\n    {summary[:300]}"
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

            except socket.timeout:
                log.warning(f"{feed_url} 타임아웃 (30초 초과)")
            except Exception as e:
                log.warning(f"{feed_url} 수집 실패: {e}")

        if lines:
            section = f"### [{label}] ({len(lines)}건)\n\n" + "\n\n".join(lines)
            sections.append(section)

    # 소켓 타임아웃 복원
    socket.setdefaulttimeout(_orig_timeout)

    log.info(f"총 {total}건 수집 완료 (카테고리 {len(sections)}개)")
    if skipped_old or skipped_dup:
        log.info(f"필터링: 24시간 초과 {skipped_old}건, 중복 {skipped_dup}건 제외")
    news_text = "\n\n---\n\n".join(sections)
    return news_text, articles


def collect_news(cfg: NewsConfig) -> str:
    """RSS 피드에서 카테고리별 뉴스를 수집하여 구조화된 텍스트로 반환 (하위호환)"""
    news_text, _ = collect_news_structured(cfg)
    return news_text
