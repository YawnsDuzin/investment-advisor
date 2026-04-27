"""news_repo.save_news_articles — v41 lang/region/title_original 컬럼 INSERT 검증.

psycopg2 mock 으로 cur.execute 의 SQL + 파라미터를 캡처하여 검증.
Spec: _docs/20260427055258_sprint1-design.md §6 + §3 (v41)
"""
from unittest.mock import MagicMock, patch


def _make_article(**overrides):
    base = {
        "category": "finance",
        "source": "Reuters",
        "title": "Fed signals rate cut",
        "title_ko": "연준 금리 인하 시사",
        "summary": "Fed officials signaled...",
        "summary_ko": "연준 관계자들이...",
        "link": "http://x",
        "published": "Mon, 27 Apr 2026 10:00:00 +0000",
        "lang": "en",
        "region": "US",
        "title_original": "Fed signals rate cut",
    }
    base.update(overrides)
    return base


class TestSaveNewsArticlesWithV41Columns:
    def test_insert_includes_lang_region_title_original(self):
        from shared.config import DatabaseConfig
        from shared.db.news_repo import save_news_articles

        cur = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur

        with patch("shared.db.news_repo.get_connection", return_value=conn):
            save_news_articles(DatabaseConfig(), session_id=1, articles=[_make_article()])

        last_call = cur.execute.call_args
        sql, params = last_call.args[0], last_call.args[1]
        assert "INSERT INTO news_articles" in sql
        assert "lang" in sql
        assert "region" in sql
        assert "title_original" in sql
        assert "en" in params
        assert "US" in params
        assert "Fed signals rate cut" in params

    def test_missing_v41_fields_default_to_none(self):
        """기존 article (PR-1 이전 형식) 도 깨지지 않게 — 누락 키는 None."""
        from shared.config import DatabaseConfig
        from shared.db.news_repo import save_news_articles

        cur = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur

        legacy = {
            "category": "finance", "source": "Reuters",
            "title": "x", "title_ko": "x",
            "summary": "y", "summary_ko": "y",
            "link": "http://x", "published": "",
        }

        with patch("shared.db.news_repo.get_connection", return_value=conn):
            save_news_articles(DatabaseConfig(), session_id=1, articles=[legacy])

        assert cur.execute.called
        params = cur.execute.call_args.args[1]
        assert None in params

    def test_returns_count(self):
        from shared.config import DatabaseConfig
        from shared.db.news_repo import save_news_articles

        cur = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur

        with patch("shared.db.news_repo.get_connection", return_value=conn):
            n = save_news_articles(
                DatabaseConfig(), session_id=1,
                articles=[_make_article(), _make_article(title="another")]
            )
        assert n == 2
