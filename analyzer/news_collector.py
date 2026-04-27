"""RSS 뉴스 수집 모듈 — region-tagged feed_sources 기반 (Sprint 1 PR-2).

각 article 에 lang/region/title_original 태그 부착.
news_text 는 region 별 섹션으로 그룹 — Stage 1 프롬프트가 region 단위로 인식.
"""
import time
import socket
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import feedparser
from shared.config import NewsConfig, FeedSpec
from shared.logger import get_logger


# 카테고리 라벨 (article 메타데이터용 — text 그룹은 region 단위)
CATEGORY_LABELS = {
    "global": "글로벌 종합",
    "finance": "경제·금융·시장",
    "technology": "기술·AI·반도체",
    "commodities": "에너지·원자재",
    "korea": "한국 경제",
    "korea_early": "한국 산업·M&A·자본시장",
    "early_signals": "선행 지표·규제·공급망",
    "asia_business": "아시아 비즈니스",
    "china_business": "중국 비즈니스",
    "eu_companies": "유럽 기업",
    "eu_business": "유럽 비즈니스",
}

# region 별 섹션 헤더 (news_text 그룹용)
REGION_LABELS = {
    "KR": "한국 뉴스",
    "US": "미국 뉴스",
    "JP": "일본 뉴스",
    "CN": "중국 뉴스",
    "EU": "유럽 뉴스",
    "GLOBAL": "글로벌 뉴스",
}

# Stage 1 프롬프트 입력에서 region 그룹 출력 순서
REGION_ORDER = ["KR", "US", "JP", "CN", "EU", "GLOBAL"]


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
    try:
        return datetime(*time.strptime(published[:25], "%Y-%m-%dT%H:%M:%S")[:6],
                        tzinfo=timezone.utc)
    except Exception:
        return None


def collect_news_structured(cfg: NewsConfig) -> tuple[str, list[dict]]:
    """RSS 피드에서 뉴스를 수집하여 (region-grouped 텍스트, article 리스트) 반환.

    각 article:
      {
        "category", "source", "title", "summary", "link", "published",
        "lang", "region", "title_original"  # ← Sprint 1 PR-2 추가
      }

    news_text 는 region 별 섹션 (`### [한국 뉴스] (N건)`) 으로 그룹.
    """
    articles: list[dict] = []
    seen_titles: set[str] = set()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    total = 0
    skipped_old = 0
    skipped_dup = 0

    log = get_logger("뉴스")
    _orig_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(30)

    # GLOBAL_NEWS_ENABLED 토글 적용
    feed_specs: list[FeedSpec] = (
        cfg.active_feed_sources() if hasattr(cfg, "active_feed_sources")
        else list(cfg.feed_sources)
    )

    by_region: dict[str, list[dict]] = defaultdict(list)

    for spec in feed_specs:
        try:
            feed = feedparser.parse(spec.url)
            source = feed.feed.get("title", spec.url)

            for entry in feed.entries[: cfg.max_articles_per_feed]:
                title = entry.get("title", "")
                published = entry.get("published", "")

                pub_dt = _parse_published(published)
                if pub_dt and pub_dt < cutoff:
                    skipped_old += 1
                    continue

                title_key = title[:30].strip().lower()
                if title_key in seen_titles:
                    skipped_dup += 1
                    continue
                seen_titles.add(title_key)

                summary = _clean_html(
                    entry.get("summary", entry.get("description", ""))
                )
                link = entry.get("link", "")

                article = {
                    "category": spec.category,
                    "source": source,
                    "title": title,
                    "title_original": title,
                    "summary": summary[:1000],
                    "link": link,
                    "published": published,
                    "lang": spec.lang,
                    "region": spec.region,
                    "_pub_dt": pub_dt,
                }
                articles.append(article)
                by_region[spec.region].append(article)
                total += 1

        except socket.timeout:
            log.warning(f"{spec.url} 타임아웃 (30초 초과)")
        except Exception as e:
            log.warning(f"{spec.url} 수집 실패: {e}")

    socket.setdefaulttimeout(_orig_timeout)

    # ── region 별 섹션 빌드 ────────────────────────────
    sections: list[str] = []
    for region in REGION_ORDER:
        region_articles = by_region.get(region, [])
        if not region_articles:
            continue
        label = REGION_LABELS.get(region, region)
        lines: list[str] = []
        for a in region_articles:
            short_date = ""
            if a["_pub_dt"]:
                short_date = f" ({a['_pub_dt'].strftime('%m/%d %H:%M')})"
            elif a["published"]:
                short_date = f" ({a['published'][:16]})"
            cat_label = CATEGORY_LABELS.get(a["category"], a["category"])
            lines.append(
                f"  • [{a['source']}][{cat_label}]{short_date} {a['title']}\n"
                f"    {a['summary'][:300]}"
            )
        sections.append(f"### [{label}] ({len(lines)}건)\n\n" + "\n\n".join(lines))

    for a in articles:
        a.pop("_pub_dt", None)

    log.info(f"총 {total}건 수집 완료 (region {len(by_region)}개)")
    if skipped_old or skipped_dup:
        log.info(f"필터링: 24시간 초과 {skipped_old}건, 중복 {skipped_dup}건 제외")

    news_text = "\n\n---\n\n".join(sections)
    return news_text, articles


def collect_news(cfg: NewsConfig) -> str:
    """RSS 피드에서 뉴스를 수집하여 region-grouped 텍스트로 반환 (하위호환)."""
    news_text, _ = collect_news_structured(cfg)
    return news_text
