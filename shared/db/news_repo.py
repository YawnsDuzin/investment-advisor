"""뉴스 기사 저장/조회/번역 업데이트."""
from psycopg2.extras import RealDictCursor, execute_values

from shared.config import DatabaseConfig
from shared.db.connection import get_connection


def save_news_articles(cfg: DatabaseConfig, session_id: int, articles: list[dict]) -> int:
    """수집된 뉴스 기사를 DB에 저장

    Args:
        articles: [{"category", "source", "title", "title_ko",
                     "summary", "summary_ko", "link", "published"}]
    Returns:
        저장된 기사 수
    """
    if not articles:
        return 0

    conn = get_connection(cfg)
    try:
        with conn.cursor() as cur:
            for a in articles:
                cur.execute(
                    """INSERT INTO news_articles
                       (session_id, category, source, title, title_ko, summary, summary_ko, link, published)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (session_id, a.get("category"), a.get("source"),
                     a.get("title"), a.get("title_ko"),
                     a.get("summary"), a.get("summary_ko"),
                     a.get("link"), a.get("published"))
                )
        conn.commit()
        return len(articles)
    finally:
        conn.close()


def get_untranslated_news(cfg: DatabaseConfig) -> list[dict]:
    """title_ko 또는 summary_ko가 NULL인 뉴스 기사 조회"""
    conn = get_connection(cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, title, summary FROM news_articles
                WHERE title_ko IS NULL OR summary_ko IS NULL
                ORDER BY id
            """)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def update_news_title_ko(cfg: DatabaseConfig, updates: list[tuple[int, str]]) -> int:
    """뉴스 기사 제목 한글 번역 일괄 업데이트

    Args:
        updates: [(article_id, title_ko), ...]
    Returns:
        업데이트된 건수
    """
    if not updates:
        return 0

    conn = get_connection(cfg)
    try:
        with conn.cursor() as cur:
            for article_id, title_ko in updates:
                cur.execute(
                    "UPDATE news_articles SET title_ko = %s WHERE id = %s",
                    (title_ko, article_id)
                )
        conn.commit()
        return len(updates)
    finally:
        conn.close()


def update_news_translation(cfg: DatabaseConfig,
                            updates: list[tuple[int, str, str]]) -> int:
    """뉴스 기사 제목+요약 한글 번역 일괄 업데이트

    Args:
        updates: [(article_id, title_ko, summary_ko), ...]
    Returns:
        업데이트된 건수
    """
    if not updates:
        return 0

    conn = get_connection(cfg)
    try:
        with conn.cursor() as cur:
            for article_id, title_ko, summary_ko in updates:
                cur.execute(
                    "UPDATE news_articles SET title_ko = %s, summary_ko = %s WHERE id = %s",
                    (title_ko, summary_ko, article_id)
                )
        conn.commit()
        return len(updates)
    finally:
        conn.close()


def get_latest_news_titles(cfg: DatabaseConfig) -> list[str]:
    """최근 세션의 뉴스 제목 목록 조회 (뉴스 세트 지문 비교용)"""
    conn = get_connection(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT na.title
                FROM news_articles na
                JOIN analysis_sessions s ON na.session_id = s.id
                WHERE s.analysis_date = (
                    SELECT MAX(analysis_date) FROM analysis_sessions
                )
                ORDER BY na.title
            """)
            return [row[0] for row in cur.fetchall()]
    except Exception as e:
        print(f"[DB] 최근 뉴스 제목 조회 실패: {e}")
        return []
    finally:
        conn.close()
