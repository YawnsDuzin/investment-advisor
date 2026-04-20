"""PostgreSQL 데이터베이스 관리 모듈"""
import json
import psycopg2
from psycopg2.extras import execute_values, RealDictCursor
from shared.config import DatabaseConfig
from shared.db.connection import _ensure_database, get_connection, _get_schema_version  # noqa: F401
from shared.db.migrations.seeds import _seed_admin_user, _seed_education_topics  # noqa: F401
from shared.db.migrations.versions import (  # noqa: F401
    _migrate_to_v2, _migrate_to_v3, _migrate_to_v4, _migrate_to_v5,
    _migrate_to_v6, _migrate_to_v7, _migrate_to_v8, _migrate_to_v9,
    _migrate_to_v10, _migrate_to_v11, _migrate_to_v12, _migrate_to_v13,
    _migrate_to_v14, _migrate_to_v15, _migrate_to_v16, _migrate_to_v17,
    _migrate_to_v18, _migrate_to_v19, _migrate_to_v20, _migrate_to_v21,
    _migrate_to_v22, _migrate_to_v23,
)
from shared.db.schema import SCHEMA_VERSION, _create_base_schema, init_db  # noqa: F401
from shared.db.session_repo import save_analysis  # noqa: F401
from shared.db.session_repo import (  # noqa: F401
    _validate_proposal,
    _generate_notifications,
    _normalize_theme_key,
    _resolve_theme_key,
    _update_tracking,
)
from shared.db.news_repo import (  # noqa: F401
    save_news_articles,
    get_untranslated_news,
    update_news_title_ko,
    update_news_translation,
    get_latest_news_titles,
)
from shared.db.query_repo import get_recent_recommendations, get_existing_theme_keys  # noqa: F401
from shared.db.top_picks_repo import save_top_picks, update_top_picks_ai_rerank  # noqa: F401
