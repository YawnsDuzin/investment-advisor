"""DB 계층 공개 API (A 트랙 분할 진행 중 — 임시 shim).

T2~T11에서 내부 구조가 점진적으로 `shared.db.*` 서브모듈로 이동한다.
T12에서 이 shim을 명시적 re-export + __all__ 선언으로 치환하고
`shared/db_legacy.py`를 삭제한다.
"""
from shared.db_legacy import *  # noqa: F401, F403
