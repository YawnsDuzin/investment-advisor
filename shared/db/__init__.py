"""DB 계층 공개 API.

외부 모듈은 이 패키지에서만 import할 것. 내부 구조(connection/schema/migrations/
session_repo/news_repo/query_repo/top_picks_repo)는 구현 디테일이며 호출부는
신경쓰지 않아도 된다.

테스트에서 `_migrate_to_vN` 같은 private 심볼이 필요하면
`shared.db.migrations.versions`에서 직접 import한다.
"""
from shared.db.connection import get_connection
from shared.db.schema import SCHEMA_VERSION, init_db
from shared.db.session_repo import save_analysis
from shared.db.news_repo import (
    save_news_articles,
    get_untranslated_news,
    update_news_title_ko,
    update_news_translation,
    get_latest_news_titles,
)
from shared.db.query_repo import (
    get_recent_recommendations,
    get_existing_theme_keys,
)
from shared.db.top_picks_repo import (
    save_top_picks,
    update_top_picks_ai_rerank,
)
from shared.db.feed_health_repo import (
    upsert_feed_health,
    list_recent_feed_health,
    detect_chronic_failures,
)


__all__ = [
    "SCHEMA_VERSION",
    "get_connection",
    "init_db",
    "save_analysis",
    "save_news_articles",
    "get_untranslated_news",
    "update_news_title_ko",
    "update_news_translation",
    "get_latest_news_titles",
    "get_recent_recommendations",
    "get_existing_theme_keys",
    "save_top_picks",
    "update_top_picks_ai_rerank",
    "upsert_feed_health",
    "list_recent_feed_health",
    "detect_chronic_failures",
]
