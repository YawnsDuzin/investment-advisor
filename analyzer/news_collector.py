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
    # region 별 분리 dedup — 동일 헤드라인이 KR/US/CN 매체에서 등장하면 region 별 1회 보존.
    # 기존: 전 피드 단일 set → 첫 등장 매체만 살아남음 (US 우선 등록 → KR 한국어 매체 dedup).
    seen_titles: dict[str, set[str]] = defaultdict(set)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    total = 0
    skipped_old = 0
    skipped_dup = 0

    log = get_logger("뉴스")
    _orig_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(30)

    # 피드별 health 통계 — feed_health_repo UPSERT (다음 PR) 및 admin UI 의 데이터 소스
    feed_stats: list[dict] = []

    try:
        # GLOBAL_NEWS_ENABLED 토글 적용
        feed_specs: list[FeedSpec] = (
            cfg.active_feed_sources() if hasattr(cfg, "active_feed_sources")
            else list(cfg.feed_sources)
        )

        by_region: dict[str, list[dict]] = defaultdict(list)

        dead_feeds: list[str] = []
        stale_feeds: list[str] = []
        parse_errors: list[str] = []

        for spec in feed_specs:
            stat: dict = {
                "url": spec.url, "region": spec.region, "category": spec.category,
                "raw_entries": 0, "fresh_articles": 0, "stored_articles": 0,
                "status": "ok", "bozo": False, "bozo_exception": None,
                "latest_pub_at": None,
            }
            try:
                feed = feedparser.parse(spec.url)
                source = feed.feed.get("title", spec.url)
                stat["raw_entries"] = len(feed.entries)
                # feedparser 가 파싱 에러 감지 시 bozo=1 + bozo_exception 객체 저장
                if getattr(feed, "bozo", 0):
                    stat["bozo"] = True
                    exc = getattr(feed, "bozo_exception", None)
                    stat["bozo_exception"] = str(exc) if exc else None

                # latest_pub_at 추적 (abandoned feed 식별용)
                latest_dt = None
                for e in feed.entries:
                    dt = _parse_published(e.get("published", ""))
                    if dt and (latest_dt is None or dt > latest_dt):
                        latest_dt = dt
                stat["latest_pub_at"] = latest_dt

                # ── 3단 health 라벨 ───────────────────────────
                # parse_error: feedparser bozo + entries 0
                # dead       : entries 0
                # stale      : entries > 0 but 24h fresh == 0 (아래 loop 후 재평가)
                # ok         : 정상
                if not feed.entries:
                    if stat["bozo"]:
                        stat["status"] = "parse_error"
                        parse_errors.append(f"[{spec.region}/{spec.category}] {spec.url} - {stat['bozo_exception']}")
                        log.warning(
                            f"피드 파싱 에러: [{spec.region}/{spec.category}] {spec.url} - {stat['bozo_exception']}",
                            extra={"context": {"feed_url": spec.url, "region": spec.region, "bozo": True}},
                        )
                    else:
                        stat["status"] = "dead"
                        dead_feeds.append(f"[{spec.region}/{spec.category}] {spec.url}")
                        log.warning(
                            f"피드 0건: [{spec.region}/{spec.category}] {spec.url}",
                            extra={"context": {"feed_url": spec.url, "region": spec.region, "category": spec.category}},
                        )
                    feed_stats.append(stat)
                    continue

                for entry in feed.entries[: cfg.max_articles_per_feed]:
                    title = entry.get("title", "")
                    published = entry.get("published", "")

                    pub_dt = _parse_published(published)
                    if pub_dt and pub_dt < cutoff:
                        skipped_old += 1
                        continue
                    stat["fresh_articles"] += 1

                    title_key = title[:30].strip().lower()
                    if title_key in seen_titles[spec.region]:
                        skipped_dup += 1
                        continue
                    seen_titles[spec.region].add(title_key)

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
                    stat["stored_articles"] += 1

                # entries 가 있었지만 fresh 가 0이면 stale (예: SCMP 318208 패턴)
                if stat["fresh_articles"] == 0 and stat["status"] == "ok":
                    stat["status"] = "stale"
                    stale_feeds.append(f"[{spec.region}/{spec.category}] {spec.url} (entries={stat['raw_entries']}, fresh=0)")
                    log.warning(
                        f"피드 stale: [{spec.region}/{spec.category}] entries={stat['raw_entries']} fresh=0 — {spec.url}",
                        extra={"context": {"feed_url": spec.url, "region": spec.region, "raw_entries": stat["raw_entries"]}},
                    )

                feed_stats.append(stat)

            except socket.timeout:
                stat["status"] = "timeout"
                feed_stats.append(stat)
                log.warning(f"{spec.url} 타임아웃 (30초 초과)")
            except Exception as e:
                stat["status"] = "error"
                stat["bozo_exception"] = str(e)
                feed_stats.append(stat)
                log.warning(f"{spec.url} 수집 실패: {e}")
    finally:
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
        log.info(f"필터링: 24시간 초과 {skipped_old}건, 중복 {skipped_dup}건 제외 (region 별 분리 dedup)")
    bad_summary: list[str] = []
    if dead_feeds:
        bad_summary.append(f"dead {len(dead_feeds)}건:\n  - " + "\n  - ".join(dead_feeds))
    if stale_feeds:
        bad_summary.append(f"stale {len(stale_feeds)}건 (entries>0, fresh=0):\n  - " + "\n  - ".join(stale_feeds))
    if parse_errors:
        bad_summary.append(f"parse_error {len(parse_errors)}건:\n  - " + "\n  - ".join(parse_errors))
    if bad_summary:
        log.warning(
            f"피드 health 이슈 {len(dead_feeds) + len(stale_feeds) + len(parse_errors)}/{len(feed_specs)}건 — 신뢰도 저하:\n"
            + "\n".join(bad_summary)
        )

    news_text = "\n\n---\n\n".join(sections)
    return news_text, articles


def collect_news(cfg: NewsConfig) -> str:
    """RSS 피드에서 뉴스를 수집하여 region-grouped 텍스트로 반환 (하위호환)."""
    news_text, _ = collect_news_structured(cfg)
    return news_text
