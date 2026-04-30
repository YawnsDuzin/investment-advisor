"""RSS 피드 health 시계열 저장/조회 (v45).

`analyzer.news_collector` 가 매 실행마다 피드별 stat 을 UPSERT.
`api.routes.admin_news_feeds` 가 admin UI 데이터 소스로 사용.
"""
from datetime import date, datetime, timedelta
from psycopg2.extras import RealDictCursor

from shared.config import DatabaseConfig
from shared.db.connection import get_connection


def upsert_feed_health(cfg: DatabaseConfig, stats: list[dict]) -> int:
    """피드별 health stat 을 (url, check_date) UNIQUE 로 UPSERT.

    Args:
        stats: news_collector 가 누적한 dict 리스트:
            {url, region, category, raw_entries, fresh_articles, stored_articles,
             status, bozo, bozo_exception, latest_pub_at, elapsed_ms?}
    Returns:
        UPSERT 적용 row 수
    """
    if not stats:
        return 0

    conn = get_connection(cfg)
    try:
        with conn.cursor() as cur:
            for s in stats:
                cur.execute(
                    """INSERT INTO news_feed_health
                       (url, region, category, check_date,
                        raw_entries, fresh_articles, stored_articles,
                        status, bozo, bozo_exception, latest_pub_at, elapsed_ms)
                       VALUES (%s, %s, %s, CURRENT_DATE, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (url, check_date) DO UPDATE SET
                           region          = EXCLUDED.region,
                           category        = EXCLUDED.category,
                           raw_entries     = EXCLUDED.raw_entries,
                           fresh_articles  = EXCLUDED.fresh_articles,
                           stored_articles = EXCLUDED.stored_articles,
                           status          = EXCLUDED.status,
                           bozo            = EXCLUDED.bozo,
                           bozo_exception  = EXCLUDED.bozo_exception,
                           latest_pub_at   = EXCLUDED.latest_pub_at,
                           elapsed_ms      = EXCLUDED.elapsed_ms,
                           checked_at      = NOW()
                    """,
                    (
                        s.get("url"), s.get("region"), s.get("category"),
                        s.get("raw_entries"), s.get("fresh_articles"), s.get("stored_articles"),
                        s.get("status"), bool(s.get("bozo")), s.get("bozo_exception"),
                        s.get("latest_pub_at"), s.get("elapsed_ms"),
                    ),
                )
        conn.commit()
        return len(stats)
    finally:
        conn.close()


def list_recent_feed_health(cfg: DatabaseConfig, days: int = 14) -> list[dict]:
    """최근 N일 피드별 14일 rolling 집계 + 어제·오늘 status.

    각 피드(url) 별로:
      - latest_status: 가장 최근 row 의 status
      - latest_check_date / fresh_today / stored_today
      - avg_stored_14d: 14일 평균 stored_articles
      - bad_days_14d: 14일 중 dead/stale/parse_error/timeout/error 일수
      - history: 최근 N일 일별 stored 카운트 + status (sparkline 용)
    """
    since = date.today() - timedelta(days=days)
    conn = get_connection(cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT url, region, category, check_date,
                          raw_entries, fresh_articles, stored_articles, status,
                          latest_pub_at, bozo_exception
                   FROM news_feed_health
                   WHERE check_date >= %s
                   ORDER BY url, check_date DESC""",
                (since,),
            )
            rows = cur.fetchall()

        # url 별로 집계
        by_url: dict[str, dict] = {}
        for r in rows:
            url = r["url"]
            agg = by_url.setdefault(url, {
                "url": url,
                "region": r["region"],
                "category": r["category"],
                "latest_status": None,
                "latest_check_date": None,
                "fresh_today": None,
                "stored_today": None,
                "raw_today": None,
                "latest_pub_at": r["latest_pub_at"],
                "latest_exception": r["bozo_exception"],
                "history": [],
                "stored_sum": 0,
                "history_count": 0,
                "bad_days_14d": 0,
            })
            # 가장 최근 row (ORDER BY DESC 첫 번째)
            if agg["latest_status"] is None:
                agg["latest_status"] = r["status"]
                agg["latest_check_date"] = r["check_date"]
                agg["fresh_today"] = r["fresh_articles"]
                agg["stored_today"] = r["stored_articles"]
                agg["raw_today"] = r["raw_entries"]

            agg["history"].append({
                "date": r["check_date"].isoformat(),
                "stored": r["stored_articles"] or 0,
                "status": r["status"],
            })
            agg["stored_sum"] += r["stored_articles"] or 0
            agg["history_count"] += 1
            if r["status"] in ("dead", "stale", "parse_error", "timeout", "error"):
                agg["bad_days_14d"] += 1

        # avg_stored_14d 계산 + 정렬용 키
        out = []
        for agg in by_url.values():
            agg["avg_stored_14d"] = (
                round(agg["stored_sum"] / agg["history_count"], 1) if agg["history_count"] else 0
            )
            # history 를 시간순(과거→현재) 으로 뒤집기 — sparkline 용
            agg["history"].reverse()
            out.append(agg)

        # 정렬: 문제 있는 피드 먼저, 그 다음 region/url
        out.sort(key=lambda x: (-x["bad_days_14d"], x["region"], x["url"]))
        return out
    finally:
        conn.close()


def detect_chronic_failures(cfg: DatabaseConfig, threshold_days: int = 7) -> list[dict]:
    """N일 연속 dead/stale/parse_error 인 피드 검출 (만성 실패 알림용)."""
    conn = get_connection(cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT url, region, category,
                          COUNT(*) FILTER (WHERE status IN ('dead','stale','parse_error','timeout','error')) AS bad_days,
                          COUNT(*) AS total_days,
                          MAX(check_date) AS last_check
                   FROM news_feed_health
                   WHERE check_date >= CURRENT_DATE - %s::int
                   GROUP BY url, region, category
                   HAVING COUNT(*) FILTER (WHERE status IN ('dead','stale','parse_error','timeout','error')) >= %s::int
                   ORDER BY bad_days DESC, url""",
                (threshold_days, threshold_days),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
