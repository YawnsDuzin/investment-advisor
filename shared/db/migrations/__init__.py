"""DB 마이그레이션 오케스트레이터.

versions.py의 `_migrate_to_vN` 함수들을 버전→함수 dict로 테이블화하고
run_migrations()가 current_version 이후부터 target_version까지 순차 적용한다.

새 마이그레이션 추가 시:
1. versions.py에 `_migrate_to_vN(cur)` 함수 추가
2. 아래 _MIGRATIONS dict에 `N: v._migrate_to_vN,` 한 줄 추가
3. shared/db/schema.py의 SCHEMA_VERSION 상수 증가
"""
from shared.db.migrations import versions as _v


_MIGRATIONS = {
    2: _v._migrate_to_v2,
    3: _v._migrate_to_v3,
    4: _v._migrate_to_v4,
    5: _v._migrate_to_v5,
    6: _v._migrate_to_v6,
    7: _v._migrate_to_v7,
    8: _v._migrate_to_v8,
    9: _v._migrate_to_v9,
    10: _v._migrate_to_v10,
    11: _v._migrate_to_v11,
    12: _v._migrate_to_v12,
    13: _v._migrate_to_v13,
    14: _v._migrate_to_v14,
    15: _v._migrate_to_v15,
    16: _v._migrate_to_v16,
    17: _v._migrate_to_v17,
    18: _v._migrate_to_v18,
    19: _v._migrate_to_v19,
    20: _v._migrate_to_v20,
    21: _v._migrate_to_v21,
    22: _v._migrate_to_v22,
    23: _v._migrate_to_v23,
    24: _v._migrate_to_v24,
    25: _v._migrate_to_v25,
    26: _v._migrate_to_v26,
    27: _v._migrate_to_v27,
    28: _v._migrate_to_v28,
    29: _v._migrate_to_v29,
    30: _v._migrate_to_v30,
    31: _v._migrate_to_v31,
    32: _v._migrate_to_v32,
    33: _v._migrate_to_v33,
    34: _v._migrate_to_v34,
    35: _v._migrate_to_v35,
    36: _v._migrate_to_v36,
    37: _v._migrate_to_v37,
    38: _v._migrate_to_v38,
    39: _v._migrate_to_v39,
    40: _v._migrate_to_v40,
    41: _v._migrate_to_v41,
    42: _v._migrate_to_v42,
    43: _v._migrate_to_v43,
    44: _v._migrate_to_v44,
    45: _v._migrate_to_v45,
}


def run_migrations(cur, current_version: int, target_version: int) -> None:
    """current_version+1 부터 target_version까지 순차 적용.

    Raises:
        RuntimeError: target 버전에 해당하는 마이그레이션이 dict에 없을 때.
    """
    for ver in range(current_version + 1, target_version + 1):
        migrate = _MIGRATIONS.get(ver)
        if migrate is None:
            raise RuntimeError(f"Missing migration for v{ver}")
        migrate(cur)
