"""읽기 전용 쿼리 헬퍼 — 최근 추천 이력 + 기존 테마 키 조회."""
from psycopg2.extras import RealDictCursor

from shared.config import DatabaseConfig
from shared.db.connection import get_connection


def get_recent_recommendations(cfg: DatabaseConfig, days: int = 7) -> list[dict]:
    """최근 N일간 추천된 종목 이력 조회 (중복 제거 피드백용)

    Returns:
        [{"ticker": "005930", "asset_name": "삼성전자", "theme_name": "AI 반도체",
          "action": "buy", "conviction": "high", "count": 3,
          "first_date": "2026-04-07", "last_date": "2026-04-13"}]
    """
    conn = get_connection(cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    p.ticker,
                    p.asset_name,
                    t.theme_name,
                    p.action,
                    p.conviction,
                    COUNT(*) as count,
                    MIN(s.analysis_date)::text as first_date,
                    MAX(s.analysis_date)::text as last_date
                FROM investment_proposals p
                JOIN investment_themes t ON p.theme_id = t.id
                JOIN analysis_sessions s ON t.session_id = s.id
                WHERE s.analysis_date >= CURRENT_DATE - %s
                  AND p.ticker IS NOT NULL
                GROUP BY p.ticker, p.asset_name, t.theme_name, p.action, p.conviction
                ORDER BY count DESC, p.ticker
            """, (days,))
            return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        print(f"[DB] 최근 추천 이력 조회 실패: {e}")
        return []
    finally:
        conn.close()


def get_existing_theme_keys(cfg: DatabaseConfig) -> list[dict]:
    """기존 theme_key 목록 조회 (AI 프롬프트 피드백용 — 키 재사용 유도)

    Returns:
        [{"theme_key": "secondary_battery_oversupply",
          "theme_name": "2차전지 공급과잉",
          "last_seen_date": "2026-04-15",
          "appearances": 5}]
    """
    conn = get_connection(cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT theme_key, theme_name, last_seen_date::text, appearances
                FROM theme_tracking
                ORDER BY last_seen_date DESC
                LIMIT 100
            """)
            return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        print(f"[DB] 기존 theme_key 조회 실패: {e}")
        return []
    finally:
        conn.close()
