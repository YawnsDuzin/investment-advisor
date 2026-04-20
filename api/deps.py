"""공통 FastAPI dependency 팩토리 (B2).

B2.5에서 `get_db_conn`(컨텍스트 매니저 dependency), `get_page_context` 등이
여기에 추가될 예정. 본 spec에서는 `_get_cfg` 중복 제거만 담당.
"""
from shared.config import DatabaseConfig


def get_db_cfg() -> DatabaseConfig:
    """DatabaseConfig 인스턴스 반환 — 라우트 기존 `_get_cfg()` 대체."""
    return DatabaseConfig()
