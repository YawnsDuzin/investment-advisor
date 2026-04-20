# A — `shared/db.py` 분할 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `shared/db.py`(2,348줄)를 `shared/db/` 패키지로 분할하여 7개 역할을 의미 단위 파일로 분리하고, 외부 호출부 무수정(공개 API 13개 심볼 re-export)을 유지한다.

**Architecture:** `shared/db.py` → `shared/db_legacy.py` 리네임 후 단계별로 `shared/db/`(패키지)에 connection / schema / migrations(versions+seeds) / session_repo / news_repo / query_repo / top_picks_repo 로 추출. 마이그레이션은 `_MIGRATIONS` dict + `run_migrations()` loop로 오케스트레이션. 최종적으로 `shared/db_legacy.py`를 삭제하고 `__init__.py`에 공개 13개 심볼만 `__all__`로 고정.

**Tech Stack:** Python 3.10+, psycopg2, PostgreSQL, pytest

**Spec:** `docs/superpowers/specs/2026-04-20-a-shared-db-split-design.md`

---

## Task 1: Baseline 캡처 스크립트 작성 + 실행

공개 API parity 검증용. 분할 전 심볼 시그니처를 JSON으로 저장해두고 각 단계마다 비교.

**Files:**
- Create: `tools/a_db_split/capture_api.py`
- Create: `tools/a_db_split/baseline.json` (스크립트 실행 산출물, git 추적)

- [ ] **Step 1: 스크립트 작성**

Create `tools/a_db_split/capture_api.py`:

```python
"""shared.db 공개 API 스냅샷 — A 트랙 parity 검증용.

사용: python -m tools.a_db_split.capture_api > tools/a_db_split/baseline.json
비교: python -m tools.a_db_split.capture_api | diff - tools/a_db_split/baseline.json
"""
import inspect
import json
import sys

import shared.db as m

snapshot: dict[str, str] = {}
for name in sorted(dir(m)):
    if name.startswith("_"):
        continue
    obj = getattr(m, name)
    if callable(obj):
        try:
            snapshot[name] = f"callable{inspect.signature(obj)}"
        except (TypeError, ValueError):
            snapshot[name] = "callable(?)"
    else:
        snapshot[name] = f"<{type(obj).__name__}>"

json.dump(snapshot, sys.stdout, indent=2, ensure_ascii=False)
sys.stdout.write("\n")
```

- [ ] **Step 2: 분할 전 baseline 생성**

Run:
```bash
python -m tools.a_db_split.capture_api > tools/a_db_split/baseline.json
```

Expected: `baseline.json`에 `SCHEMA_VERSION`, `get_connection`, `init_db`, `save_analysis`, `save_news_articles`, `get_untranslated_news`, `update_news_title_ko`, `update_news_translation`, `get_latest_news_titles`, `get_recent_recommendations`, `get_existing_theme_keys`, `save_top_picks`, `update_top_picks_ai_rerank` 13개 이상의 심볼 기록 (json, psycopg2 등 import된 모듈도 함께 기록될 수 있음 — 이후 비교 시 노이즈 허용).

- [ ] **Step 3: 커밋**

```bash
git add tools/a_db_split/
git commit -m "$(cat <<'EOF'
chore(refactor): A — 공개 API baseline 캡처 스크립트 + 스냅샷

분할 전후 shared.db의 공개 심볼 시그니처를 비교하기 위한 baseline.
분할이 외부 호출부에 영향을 주지 않음을 parity 검증으로 보장.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `shared/db.py` → `shared/db_legacy.py` 리네임 + 패키지 shim 생성

**Files:**
- Move: `shared/db.py` → `shared/db_legacy.py`
- Create: `shared/db/__init__.py`

- [ ] **Step 1: git mv**

Run:
```bash
git mv shared/db.py shared/db_legacy.py
```

- [ ] **Step 2: 패키지 `__init__.py` 작성 (임시 shim)**

Create `shared/db/__init__.py`:

```python
"""DB 계층 공개 API (A 트랙 분할 진행 중 — 임시 shim).

T2~T11에서 내부 구조가 점진적으로 `shared.db.*` 서브모듈로 이동한다.
T12에서 이 shim을 명시적 re-export + __all__ 선언으로 치환하고
`shared/db_legacy.py`를 삭제한다.
"""
from shared.db_legacy import *  # noqa: F401, F403
```

- [ ] **Step 3: 패키지 전환 동작 검증**

Run:
```bash
python -c "from shared.db import get_connection, init_db, save_analysis, save_news_articles, get_recent_recommendations; print('ok')"
```

Expected: `ok` (에러 없음)

- [ ] **Step 4: 공개 API parity 검증 (노이즈 차이 기록)**

Run:
```bash
python -m tools.a_db_split.capture_api > /tmp/after_t2.json
diff tools/a_db_split/baseline.json /tmp/after_t2.json || true
```

Expected: 차이 없음 또는 `shared.db_legacy` 모듈 참조 같은 무해한 차이만. 13개 공개 심볼이 모두 동일 시그니처로 존재해야 함.

- [ ] **Step 5: API 서버 기동 스모크**

Run:
```bash
timeout 5 python -m api.main 2>&1 | head -20 || true
```

Expected: `Uvicorn running on http://0.0.0.0:8000` 라인 출력 (또는 라이브 환경에서 동일한 기동 메시지). `ImportError`, `ModuleNotFoundError` 없음.

- [ ] **Step 6: 커밋**

```bash
git add shared/
git commit -m "$(cat <<'EOF'
refactor(db): A — 패키지 스캐폴드 (shared.db → shared.db_legacy + shim)

shared/db.py를 shared/db_legacy.py로 리네임하고 shared/db/__init__.py에
`from shared.db_legacy import *` shim 추가. 외부 호출부 무수정 유지.
T3~T11에서 기능별로 서브모듈로 이동, T12에서 legacy 파일 삭제 및
__init__.py를 명시적 re-export로 치환.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `connection.py` 추출

**Files:**
- Create: `shared/db/connection.py`
- Modify: `shared/db_legacy.py` (상단 — `_ensure_database`, `get_connection`, `_get_schema_version` 제거 후 re-import)

- [ ] **Step 1: `shared/db/connection.py` 생성**

Create `shared/db/connection.py` with the following content (원본 `shared/db_legacy.py:11-47` 범위를 그대로 복사):

```python
"""PostgreSQL 연결 관리 — DB 자동 생성 + 연결 + 스키마 버전 조회."""
import psycopg2

from shared.config import DatabaseConfig


def _ensure_database(cfg: DatabaseConfig) -> None:
    """데이터베이스가 없으면 자동 생성"""
    conn = psycopg2.connect(
        host=cfg.host, port=cfg.port,
        dbname="postgres", user=cfg.user, password=cfg.password,
    )
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (cfg.dbname,)
            )
            if not cur.fetchone():
                cur.execute(f'CREATE DATABASE "{cfg.dbname}"')
                print(f"[DB] 데이터베이스 '{cfg.dbname}' 생성 완료")
    finally:
        conn.close()


def get_connection(cfg: DatabaseConfig):
    """DB 커넥션 반환"""
    return psycopg2.connect(cfg.dsn)


def _get_schema_version(cur) -> int:
    """현재 스키마 버전 조회 (테이블 없으면 0)"""
    cur.execute("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'schema_version'
        )
    """)
    if not cur.fetchone()[0]:
        return 0
    cur.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1")
    row = cur.fetchone()
    return row[0] if row else 0
```

- [ ] **Step 2: `shared/db_legacy.py`에서 이동된 3개 함수 제거**

Edit `shared/db_legacy.py`:
- `_ensure_database(cfg: DatabaseConfig) -> None:` 함수 전체 삭제
- `get_connection(cfg: DatabaseConfig):` 함수 전체 삭제
- `_get_schema_version(cur) -> int:` 함수 전체 삭제

그 자리에 상단 import 블록 아래 (SCHEMA_VERSION 상수 위 또는 아래 — 현재 구조에 맞춰) 다음 re-import 라인을 추가:

```python
from shared.db.connection import _ensure_database, get_connection, _get_schema_version  # noqa: F401
```

- [ ] **Step 3: 검증 — import 및 동작 확인**

Run:
```bash
python -c "from shared.db import get_connection; from shared.db.connection import _ensure_database, _get_schema_version; print('ok')"
```

Expected: `ok`

- [ ] **Step 4: API parity 재확인**

Run:
```bash
python -m tools.a_db_split.capture_api > /tmp/after_t3.json
diff tools/a_db_split/baseline.json /tmp/after_t3.json || true
```

Expected: 공개 13개 심볼 시그니처 동일.

- [ ] **Step 5: API 서버 기동 스모크**

Run:
```bash
timeout 5 python -m api.main 2>&1 | head -20 || true
```

Expected: `Uvicorn running` — `ImportError` 없음.

- [ ] **Step 6: 커밋**

```bash
git add shared/db/ shared/db_legacy.py
git commit -m "$(cat <<'EOF'
refactor(db): A — connection.py 추출

_ensure_database, get_connection, _get_schema_version을
shared/db/connection.py로 이동. 기존 import 경로 호환을 위해
db_legacy.py에서 re-import 유지.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `migrations/seeds.py` 추출

**Files:**
- Create: `shared/db/migrations/__init__.py` (빈 파일)
- Create: `shared/db/migrations/seeds.py`
- Modify: `shared/db_legacy.py` (`_seed_admin_user`, `_seed_education_topics` 제거 + re-import)

- [ ] **Step 1: `shared/db/migrations/__init__.py` 생성 (빈 placeholder)**

Create `shared/db/migrations/__init__.py`:

```python
"""DB 마이그레이션 패키지 — T6에서 오케스트레이터 추가."""
```

- [ ] **Step 2: `shared/db/migrations/seeds.py` 생성**

Create `shared/db/migrations/seeds.py`. 원본 `shared/db_legacy.py`의 다음 함수 2개를 **그대로** 옮긴다:

- `_seed_admin_user(cur)` (shared/db_legacy.py:397 부근, ~22줄)
- `_seed_education_topics(cur)` (shared/db_legacy.py:1077 부근, ~488줄)

파일 상단:
```python
"""DB 초기 데이터 시드 — admin 계정 + 투자 교육 토픽 12개."""
```

함수 본문에서 `from shared.config import ...` 같은 외부 의존이 있으면 유지. 함수 시그니처·본문 변경 금지.

- [ ] **Step 3: `shared/db_legacy.py`에서 이동된 2개 함수 제거**

Edit `shared/db_legacy.py`:
- `_seed_admin_user(cur)` 함수 전체 삭제
- `_seed_education_topics(cur)` 함수 전체 삭제

상단 re-import 블록에 추가:
```python
from shared.db.migrations.seeds import _seed_admin_user, _seed_education_topics  # noqa: F401
```

- [ ] **Step 4: 검증 — 심볼 import 확인**

Run:
```bash
python -c "from shared.db.migrations.seeds import _seed_admin_user, _seed_education_topics; print('ok')"
```

Expected: `ok`

- [ ] **Step 5: 마이그레이션 함수가 seed를 호출하는지 확인**

`_migrate_to_v11`과 `_migrate_to_v21`이 `_seed_admin_user`, `_seed_education_topics`를 호출하는지 확인. 이 시점에서 `_migrate_to_vN` 함수들은 여전히 `shared/db_legacy.py`에 있으며, 같은 모듈 내부에서 `_seed_admin_user` 이름이 re-import로 노출되므로 호출 동작 유지.

Run:
```bash
python -c "from shared.db_legacy import _migrate_to_v11, _migrate_to_v21, _seed_admin_user, _seed_education_topics; print('ok')"
```

Expected: `ok`

- [ ] **Step 6: API 서버 기동 스모크**

Run:
```bash
timeout 5 python -m api.main 2>&1 | head -20 || true
```

Expected: `Uvicorn running` — `ImportError` 없음.

- [ ] **Step 7: 커밋**

```bash
git add shared/db/migrations/ shared/db_legacy.py
git commit -m "$(cat <<'EOF'
refactor(db): A — migrations/seeds.py 추출 (admin + education 12토픽)

_seed_admin_user(~22줄), _seed_education_topics(~488줄)을
shared/db/migrations/seeds.py로 이동. 마이그레이션 함수(v11, v21)가
호출하는 이름은 db_legacy.py의 re-import로 유지.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `migrations/versions.py` 추출

**Files:**
- Create: `shared/db/migrations/versions.py`
- Modify: `shared/db_legacy.py` (22개 `_migrate_to_vN` 제거 + re-import)

- [ ] **Step 1: `shared/db/migrations/versions.py` 생성**

Create `shared/db/migrations/versions.py`. 원본 `shared/db_legacy.py`에서 다음 22개 함수를 **순서 유지**하며 그대로 옮긴다:

- `_migrate_to_v2(cur)` ~ `_migrate_to_v23(cur)` (22개)

파일 상단:
```python
"""스키마 마이그레이션 v2 ~ v23.

새 마이그레이션 추가 시:
1. 이 파일에 `_migrate_to_vN(cur)` 함수 추가
2. `shared/db/migrations/__init__.py`의 `_MIGRATIONS` dict에 한 줄 추가
3. `shared/db/schema.py`의 `SCHEMA_VERSION` 상수 증가
"""
from shared.db.migrations.seeds import _seed_admin_user, _seed_education_topics  # noqa: F401
```

`_seed_admin_user`, `_seed_education_topics`의 호출은 상대 import로 해결.

- [ ] **Step 2: `shared/db_legacy.py`에서 22개 함수 제거**

Edit `shared/db_legacy.py`:
- `_migrate_to_v2` ~ `_migrate_to_v23` 함수 22개 전체 삭제

상단 re-import 블록에 추가:
```python
from shared.db.migrations.versions import (
    _migrate_to_v2, _migrate_to_v3, _migrate_to_v4, _migrate_to_v5,
    _migrate_to_v6, _migrate_to_v7, _migrate_to_v8, _migrate_to_v9,
    _migrate_to_v10, _migrate_to_v11, _migrate_to_v12, _migrate_to_v13,
    _migrate_to_v14, _migrate_to_v15, _migrate_to_v16, _migrate_to_v17,
    _migrate_to_v18, _migrate_to_v19, _migrate_to_v20, _migrate_to_v21,
    _migrate_to_v22, _migrate_to_v23,
)  # noqa: F401
```

- [ ] **Step 3: 검증 — 전체 마이그레이션 함수 import**

Run:
```bash
python -c "
from shared.db.migrations.versions import _migrate_to_v2, _migrate_to_v23
from shared.db_legacy import init_db, _migrate_to_v11, _migrate_to_v21
print('ok')
"
```

Expected: `ok`

- [ ] **Step 4: `init_db` 동작 확인 — 기존 DB에 재실행 시 idempotent**

Run:
```bash
python -c "
from shared.config import DatabaseConfig
from shared.db import init_db
init_db(DatabaseConfig())
print('ok')
"
```

Expected: `[DB] 테이블 초기화 완료` 출력. 에러 없음. (이미 최신 DB라 추가 마이그레이션은 실행되지 않음.)

- [ ] **Step 5: API 서버 기동 스모크**

Run:
```bash
timeout 5 python -m api.main 2>&1 | head -20 || true
```

Expected: `Uvicorn running` — `ImportError` 없음.

- [ ] **Step 6: 커밋**

```bash
git add shared/db/migrations/versions.py shared/db_legacy.py
git commit -m "$(cat <<'EOF'
refactor(db): A — migrations/versions.py 추출 (v2~v23 22개)

22개 _migrate_to_vN 함수를 shared/db/migrations/versions.py로 이동.
seed 함수 호출은 같은 서브패키지 상대 import로 해결. db_legacy.py는
re-import로 하위호환 유지.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: `migrations/__init__.py` 오케스트레이터 + `init_db` 리팩토링

기존 `init_db()`의 22줄 if 체인을 dict 기반 loop로 치환.

**Files:**
- Modify: `shared/db/migrations/__init__.py` (`_MIGRATIONS` dict + `run_migrations()` 추가)
- Modify: `shared/db_legacy.py` (`init_db()` 본문의 if 체인 → `run_migrations()` 호출)

- [ ] **Step 1: `shared/db/migrations/__init__.py` 본문 작성**

Replace `shared/db/migrations/__init__.py` with:

```python
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
```

- [ ] **Step 2: `init_db()` 본문 리팩토링**

Edit `shared/db_legacy.py`. `init_db` 함수 본문에서 22줄 if 체인을 `run_migrations()` 한 줄로 치환.

상단에 import 추가:
```python
from shared.db.migrations import run_migrations
```

`init_db` 본문 변경 — 기존:
```python
    _ensure_database(cfg)
    conn = get_connection(cfg)
    try:
        with conn.cursor() as cur:
            current = _get_schema_version(cur)

            if current < 1:
                _create_base_schema(cur)
                print("[DB] v1 기본 스키마 생성 완료")

            if current < 2:
                _migrate_to_v2(cur)
            # ... 22줄 if 체인 ...
            if current < 23:
                _migrate_to_v23(cur)

        conn.commit()
        print("[DB] 테이블 초기화 완료")
    finally:
        conn.close()
```

변경 후:
```python
    _ensure_database(cfg)
    conn = get_connection(cfg)
    try:
        with conn.cursor() as cur:
            current = _get_schema_version(cur)

            if current < 1:
                _create_base_schema(cur)
                print("[DB] v1 기본 스키마 생성 완료")
                current = 1

            run_migrations(cur, current, SCHEMA_VERSION)

        conn.commit()
        print("[DB] 테이블 초기화 완료")
    finally:
        conn.close()
```

주의: `current = 1` 라인 추가 — 기존 암묵적 흐름(v1 생성 후 `if current < 2`는 항상 true)이 명시적으로 되도록.

- [ ] **Step 3: `init_db()` idempotent 검증**

Run:
```bash
python -c "
from shared.config import DatabaseConfig
from shared.db import init_db
init_db(DatabaseConfig())
init_db(DatabaseConfig())
print('ok')
"
```

Expected: 두 번 실행해도 `[DB] 테이블 초기화 완료` 출력. 에러 없음. (이미 최신 DB에서 `run_migrations(cur, 23, 23)`는 range가 빈 루프라 no-op.)

- [ ] **Step 4: `run_migrations()` 누락 버전 감지 테스트**

Run:
```bash
python -c "
from shared.db.migrations import run_migrations, _MIGRATIONS
assert set(_MIGRATIONS.keys()) == set(range(2, 24)), f'expected 2~23, got {sorted(_MIGRATIONS.keys())}'
print('ok')
"
```

Expected: `ok`

- [ ] **Step 5: API 서버 기동 스모크**

Run:
```bash
timeout 5 python -m api.main 2>&1 | head -20 || true
```

Expected: `Uvicorn running`

- [ ] **Step 6: 커밋**

```bash
git add shared/db/migrations/__init__.py shared/db_legacy.py
git commit -m "$(cat <<'EOF'
refactor(db): A — migrations 오케스트레이터 도입 (dict + run_migrations)

_MIGRATIONS dict + run_migrations() loop로 init_db()의 22줄 if 체인을
제거. 새 마이그레이션 추가 시 dict 한 줄 + SCHEMA_VERSION 증가만
필요해 관리 비용 절감.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: `schema.py` 추출

`SCHEMA_VERSION`, `_create_base_schema`, `init_db`를 `shared/db/schema.py`로 이동.

**Files:**
- Create: `shared/db/schema.py`
- Modify: `shared/db_legacy.py` (위 3개 제거 + re-import)

- [ ] **Step 1: `shared/db/schema.py` 생성**

Create `shared/db/schema.py`:

```python
"""스키마 버전 상수 + 기본 스키마 생성 + init_db 오케스트레이터."""
from shared.config import DatabaseConfig
from shared.db.connection import (
    _ensure_database,
    get_connection,
    _get_schema_version,
)
from shared.db.migrations import run_migrations


# ── 스키마 버전 관리 ──────────────────────────────
SCHEMA_VERSION = 23  # v23: ai_query_archive + app_logs.context + incident_reports


def _create_base_schema(cur) -> None:
    # 원본 shared/db_legacy.py의 _create_base_schema 본문을 그대로 복사
    ...


def init_db(cfg: DatabaseConfig) -> None:
    """PostgreSQL 설치 확인 → 데이터베이스 생성 → 스키마 마이그레이션"""
    from shared.pg_setup import ensure_postgresql
    if not ensure_postgresql(cfg.host, cfg.port):
        raise RuntimeError("PostgreSQL을 사용할 수 없습니다. 설치 후 다시 실행하세요.")
    _ensure_database(cfg)
    conn = get_connection(cfg)
    try:
        with conn.cursor() as cur:
            current = _get_schema_version(cur)

            if current < 1:
                _create_base_schema(cur)
                print("[DB] v1 기본 스키마 생성 완료")
                current = 1

            run_migrations(cur, current, SCHEMA_VERSION)

        conn.commit()
        print("[DB] 테이블 초기화 완료")
    finally:
        conn.close()
```

`_create_base_schema` 함수 본문은 원본 `shared/db_legacy.py:50-107` 범위를 **그대로 복사**.

- [ ] **Step 2: `shared/db_legacy.py`에서 이동된 심볼 제거**

Edit `shared/db_legacy.py`:
- `SCHEMA_VERSION = 23` 라인 삭제
- `_create_base_schema(cur)` 함수 전체 삭제
- `init_db(cfg)` 함수 전체 삭제
- Task 6에서 추가한 `from shared.db.migrations import run_migrations` 라인 삭제 (더이상 db_legacy.py에서 직접 쓰지 않음)

re-import 블록에 추가:
```python
from shared.db.schema import SCHEMA_VERSION, _create_base_schema, init_db  # noqa: F401
```

- [ ] **Step 3: 검증 — 공개 심볼 import**

Run:
```bash
python -c "from shared.db import SCHEMA_VERSION, init_db; print(SCHEMA_VERSION)"
```

Expected: `23`

- [ ] **Step 4: `init_db` 재실행 idempotent**

Run:
```bash
python -c "
from shared.config import DatabaseConfig
from shared.db import init_db
init_db(DatabaseConfig())
print('ok')
"
```

Expected: `ok` — 에러 없음

- [ ] **Step 5: API 서버 기동 스모크**

Run:
```bash
timeout 5 python -m api.main 2>&1 | head -20 || true
```

Expected: `Uvicorn running`

- [ ] **Step 6: 커밋**

```bash
git add shared/db/schema.py shared/db_legacy.py
git commit -m "$(cat <<'EOF'
refactor(db): A — schema.py 추출 (SCHEMA_VERSION + _create_base_schema + init_db)

스키마 상수와 기본 스키마 생성 + init_db 오케스트레이션을
shared/db/schema.py로 이동. migrations 오케스트레이터와의 조합을
하나의 파일에서 명확히 표현.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: `session_repo.py` 추출

`save_analysis` + 관련 private 유틸 5개를 묶어서 이동.

**Files:**
- Create: `shared/db/session_repo.py`
- Modify: `shared/db_legacy.py` (위 6개 제거 + re-import)

- [ ] **Step 1: `shared/db/session_repo.py` 생성**

Create `shared/db/session_repo.py`. 다음 6개 함수를 `shared/db_legacy.py`에서 **그대로** 옮긴다:

- `_validate_proposal(proposal)` (`shared/db_legacy.py:1654` 부근, ~53줄)
- `save_analysis(cfg, analysis_date, result)` (`shared/db_legacy.py:1707` 부근, ~181줄)
- `_generate_notifications(cur, session_id, themes)` (`shared/db_legacy.py:2072` 부근, ~59줄)
- `_normalize_theme_key(name)` (`shared/db_legacy.py:2133` 부근, ~8줄)
- `_resolve_theme_key(theme)` (`shared/db_legacy.py:2141` 부근, ~9줄)
- `_update_tracking(cur, analysis_date, themes, session_id)` (`shared/db_legacy.py:2150` 부근, ~95줄)

파일 상단:
```python
"""분석 세션 저장 pipeline — save_analysis + 검증/알림/추적 private 유틸."""
import json

from psycopg2.extras import execute_values

from shared.config import DatabaseConfig
from shared.db.connection import get_connection
```

(원본 함수가 쓰는 추가 import가 있다면 그대로 유지. `json`, `execute_values`, `DatabaseConfig`, `get_connection`이 주요.)

- [ ] **Step 2: `shared/db_legacy.py`에서 이동된 6개 제거**

Edit `shared/db_legacy.py`. 위 6개 함수 전체 삭제.

re-import 블록에 추가:
```python
from shared.db.session_repo import save_analysis  # noqa: F401
from shared.db.session_repo import (  # noqa: F401
    _validate_proposal,
    _generate_notifications,
    _normalize_theme_key,
    _resolve_theme_key,
    _update_tracking,
)
```

- [ ] **Step 3: 검증 — import 확인**

Run:
```bash
python -c "from shared.db import save_analysis; from shared.db.session_repo import _validate_proposal, _update_tracking; print('ok')"
```

Expected: `ok`

- [ ] **Step 4: API 서버 기동 + 주요 라우트 스모크**

Run:
```bash
timeout 5 python -m api.main 2>&1 | head -20 || true
```

Expected: `Uvicorn running`

라이브 환경에서 실행 가능하면 추가로:
```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/sessions
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/proposals
```
Expected: 각각 `200` 또는 `302`(로그인 리다이렉트 — auth_enabled일 때)

- [ ] **Step 5: 커밋**

```bash
git add shared/db/session_repo.py shared/db_legacy.py
git commit -m "$(cat <<'EOF'
refactor(db): A — session_repo.py 추출 (save_analysis + 5개 유틸)

save_analysis(~181줄) + _validate_proposal / _generate_notifications /
_normalize_theme_key / _resolve_theme_key / _update_tracking 를 묶어
shared/db/session_repo.py로 이동. 모두 save_analysis 내부에서만
호출되는 강결합 유틸이라 동일 파일로 응집성 유지.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: `news_repo.py` 추출

**Files:**
- Create: `shared/db/news_repo.py`
- Modify: `shared/db_legacy.py` (5개 함수 제거 + re-import)

- [ ] **Step 1: `shared/db/news_repo.py` 생성**

Create `shared/db/news_repo.py`. 다음 5개 함수를 그대로 이동:

- `save_news_articles(cfg, session_id, articles)` (`shared/db_legacy.py:1890` 부근)
- `get_untranslated_news(cfg)` (`shared/db_legacy.py:1921` 부근)
- `update_news_title_ko(cfg, updates)` (`shared/db_legacy.py:1936` 부근)
- `update_news_translation(cfg, ...)` (`shared/db_legacy.py:1961` 부근)
- `get_latest_news_titles(cfg)` (`shared/db_legacy.py:2024` 부근)

파일 상단:
```python
"""뉴스 기사 저장/조회/번역 업데이트."""
from psycopg2.extras import RealDictCursor, execute_values

from shared.config import DatabaseConfig
from shared.db.connection import get_connection
```

- [ ] **Step 2: `shared/db_legacy.py`에서 이동된 5개 제거 + re-import**

Edit `shared/db_legacy.py`. 위 5개 함수 삭제.

re-import 블록에 추가:
```python
from shared.db.news_repo import (  # noqa: F401
    save_news_articles,
    get_untranslated_news,
    update_news_title_ko,
    update_news_translation,
    get_latest_news_titles,
)
```

- [ ] **Step 3: 검증**

Run:
```bash
python -c "from shared.db import save_news_articles, get_untranslated_news, update_news_title_ko, update_news_translation, get_latest_news_titles; print('ok')"
```

Expected: `ok`

- [ ] **Step 4: 커밋**

```bash
git add shared/db/news_repo.py shared/db_legacy.py
git commit -m "$(cat <<'EOF'
refactor(db): A — news_repo.py 추출 (뉴스 저장/조회/번역 5개)

save_news_articles / get_untranslated_news / update_news_title_ko /
update_news_translation / get_latest_news_titles 를
shared/db/news_repo.py로 이동.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: `query_repo.py` 추출

**Files:**
- Create: `shared/db/query_repo.py`
- Modify: `shared/db_legacy.py` (2개 제거 + re-import)

- [ ] **Step 1: `shared/db/query_repo.py` 생성**

Create `shared/db/query_repo.py`. 다음 2개를 그대로 이동:

- `get_recent_recommendations(cfg, days=7)` (`shared/db_legacy.py:1987` 부근)
- `get_existing_theme_keys(cfg)` (`shared/db_legacy.py:2046` 부근)

파일 상단:
```python
"""읽기 전용 쿼리 헬퍼 — 최근 추천 이력 + 기존 테마 키 조회."""
from psycopg2.extras import RealDictCursor

from shared.config import DatabaseConfig
from shared.db.connection import get_connection
```

- [ ] **Step 2: `shared/db_legacy.py`에서 제거 + re-import**

re-import 블록에 추가:
```python
from shared.db.query_repo import get_recent_recommendations, get_existing_theme_keys  # noqa: F401
```

- [ ] **Step 3: 검증**

Run:
```bash
python -c "from shared.db import get_recent_recommendations, get_existing_theme_keys; print('ok')"
```

Expected: `ok`

- [ ] **Step 4: 커밋**

```bash
git add shared/db/query_repo.py shared/db_legacy.py
git commit -m "$(cat <<'EOF'
refactor(db): A — query_repo.py 추출 (조회 헬퍼 2개)

get_recent_recommendations, get_existing_theme_keys를
shared/db/query_repo.py로 이동.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: `top_picks_repo.py` 추출

**Files:**
- Create: `shared/db/top_picks_repo.py`
- Modify: `shared/db_legacy.py` (2개 제거 + re-import)

- [ ] **Step 1: `shared/db/top_picks_repo.py` 생성**

Create `shared/db/top_picks_repo.py`. 다음 2개를 그대로 이동:

- `save_top_picks(...)` (`shared/db_legacy.py:2247` 부근)
- `update_top_picks_ai_rerank(...)` (`shared/db_legacy.py:2292` 부근)

파일 상단:
```python
"""Top Picks 저장 + AI 재정렬 갱신."""
from psycopg2.extras import execute_values

from shared.config import DatabaseConfig
from shared.db.connection import get_connection
```

- [ ] **Step 2: `shared/db_legacy.py`에서 제거 + re-import**

re-import 블록에 추가:
```python
from shared.db.top_picks_repo import save_top_picks, update_top_picks_ai_rerank  # noqa: F401
```

- [ ] **Step 3: 검증**

Run:
```bash
python -c "from shared.db import save_top_picks, update_top_picks_ai_rerank; print('ok')"
```

Expected: `ok`

- [ ] **Step 4: 이 시점에서 `shared/db_legacy.py`는 거의 비어있어야 함**

Run:
```bash
wc -l shared/db_legacy.py
```

Expected: ~100줄 미만 (주석 + re-import 블록만 남음). 만약 남은 함수 정의가 있다면 어떤 것이 누락됐는지 확인 — 이 시점에서 `shared/db_legacy.py`에는 **함수 정의가 하나도 없어야** 하고 re-import만 남아있어야 함.

Run:
```bash
grep -n "^def \|^class " shared/db_legacy.py
```

Expected: 출력 없음 (모든 함수 이동 완료)

- [ ] **Step 5: 커밋**

```bash
git add shared/db/top_picks_repo.py shared/db_legacy.py
git commit -m "$(cat <<'EOF'
refactor(db): A — top_picks_repo.py 추출 (Top Picks 2개)

save_top_picks, update_top_picks_ai_rerank를
shared/db/top_picks_repo.py로 이동. 이 시점에서 shared/db_legacy.py에는
re-import만 남음 (함수 정의 0개).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: `__init__.py` 명시적 re-export로 치환 + `db_legacy.py` 삭제

가장 중요한 통합 단계. 실수 시 ImportError 위험이 있으므로 검증 강화.

**Files:**
- Modify: `shared/db/__init__.py` (shim → 명시적 re-export + `__all__`)
- Delete: `shared/db_legacy.py`

- [ ] **Step 1: 분할 후 API parity 사전 캡처**

Run:
```bash
python -m tools.a_db_split.capture_api > /tmp/before_t12.json
diff tools/a_db_split/baseline.json /tmp/before_t12.json || true
```

결과를 확인. 13개 공개 심볼이 모두 같은 시그니처로 존재해야 함. 차이가 있다면 그 원인을 먼저 해결.

- [ ] **Step 2: `shared/db/__init__.py` 치환**

Replace `shared/db/__init__.py` with:

```python
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
]
```

- [ ] **Step 3: `shared/db_legacy.py` 삭제**

Run:
```bash
git rm shared/db_legacy.py
```

- [ ] **Step 4: 공개 심볼 import 검증**

Run:
```bash
python -c "
from shared.db import (
    SCHEMA_VERSION, get_connection, init_db, save_analysis,
    save_news_articles, get_untranslated_news, update_news_title_ko,
    update_news_translation, get_latest_news_titles,
    get_recent_recommendations, get_existing_theme_keys,
    save_top_picks, update_top_picks_ai_rerank,
)
print(f'SCHEMA_VERSION={SCHEMA_VERSION}')
print('ok')
"
```

Expected: `SCHEMA_VERSION=23\nok`

- [ ] **Step 5: 외부 호출부(analyzer/api/logger)가 여전히 import되는지 검증**

Run:
```bash
python -c "
import analyzer.main
import analyzer.analyzer
import analyzer.price_tracker
import analyzer.validators
import api.main
import api.deps
import api.routes.admin
import api.auth.dependencies
import shared.logger
print('ok')
"
```

Expected: `ok` — 모든 모듈 import 성공

- [ ] **Step 6: API 서버 기동 + 5개 주요 페이지 스모크**

Run:
```bash
timeout 5 python -m api.main 2>&1 | head -20 || true
```

Expected: `Uvicorn running`

라이브 환경에서 실행 가능하면:
```bash
for path in / /sessions /themes /proposals /admin; do
  code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:8000${path}")
  echo "${path} → ${code}"
done
```

Expected: 각 경로 200 또는 302 (로그인 리다이렉트). 500 에러 없음.

- [ ] **Step 7: 최종 API parity 확인**

Run:
```bash
python -m tools.a_db_split.capture_api > /tmp/after_t12.json
# 공개 13개 심볼이 모두 존재하고 시그니처가 동일한지 비교
python -c "
import json
base = {k: v for k, v in json.load(open('tools/a_db_split/baseline.json')).items() if not k.startswith('_') and not k in ('DatabaseConfig', 'psycopg2', 'json', 'execute_values', 'RealDictCursor')}
after = {k: v for k, v in json.load(open('/tmp/after_t12.json')).items() if not k.startswith('_') and not k in ('DatabaseConfig', 'psycopg2', 'json', 'execute_values', 'RealDictCursor')}
public_13 = {'SCHEMA_VERSION','get_connection','init_db','save_analysis','save_news_articles','get_untranslated_news','update_news_title_ko','update_news_translation','get_latest_news_titles','get_recent_recommendations','get_existing_theme_keys','save_top_picks','update_top_picks_ai_rerank'}
for name in public_13:
    assert name in base, f'baseline missing {name}'
    assert name in after, f'after missing {name} — __init__.py re-export 누락'
    assert base[name] == after[name], f'signature mismatch for {name}: {base[name]} -> {after[name]}'
print('all 13 public symbols parity OK')
"
```

Expected: `all 13 public symbols parity OK`

- [ ] **Step 8: 커밋**

```bash
git add shared/db/__init__.py
git commit -m "$(cat <<'EOF'
refactor(db): A — 명시적 re-export + db_legacy.py 삭제

shared/db/__init__.py를 `from shared.db_legacy import *` shim에서
명시적 re-export(13개 public 심볼 + __all__)로 치환. db_legacy.py
삭제로 분할 완료. 공개 API parity 검증 완료.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: 테스트의 private import 경로 업데이트

`_migrate_to_vN` 같은 private 심볼을 직접 import하던 테스트 2개를 새 경로로 변경.

**Files:**
- Modify: `tests/test_tier_limits.py:169,175`
- Modify: `tests/test_admin_tier_audit.py:23,27`

- [ ] **Step 1: `tests/test_tier_limits.py` 수정**

Edit `tests/test_tier_limits.py`:
- Line ~169: `from shared.db import _migrate_to_v16` → `from shared.db.migrations.versions import _migrate_to_v16`
- Line ~175: 같은 변경

- [ ] **Step 2: `tests/test_admin_tier_audit.py` 수정**

Edit `tests/test_admin_tier_audit.py`:
- Line ~23: `from shared.db import _migrate_to_v17` → `from shared.db.migrations.versions import _migrate_to_v17`
- Line ~27: 같은 변경

- [ ] **Step 3: 테스트 실행**

Run:
```bash
pytest tests/test_tier_limits.py tests/test_admin_tier_audit.py -v
```

Expected: 모든 테스트 통과 (기존 동작 유지).

- [ ] **Step 4: 전체 테스트 스위트 실행**

Run:
```bash
pytest tests/ -v
```

Expected: 전체 통과. 실패 시 import 경로 문제일 가능성 높음 — 해당 테스트의 import를 검토.

- [ ] **Step 5: 커밋**

```bash
git add tests/test_tier_limits.py tests/test_admin_tier_audit.py
git commit -m "$(cat <<'EOF'
refactor(db): A — 테스트의 private _migrate_to_vN import 경로 업데이트

tests/test_tier_limits.py, tests/test_admin_tier_audit.py가
`from shared.db import _migrate_to_vN`로 private 심볼을 가져오던
것을 `from shared.db.migrations.versions import _migrate_to_vN`로
수정. public API는 `shared.db` 최상위에서만 노출하고, private는
명시적 경로로 분리.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: Migration parity 검증 + 최종 스모크

Fresh DB에서 분할 전후 `pg_dump --schema-only` diff가 공백인지 확인.

- [ ] **Step 1: 테스트용 임시 DB 생성 스크립트**

테스트용 DB를 별도 이름(`investment_advisor_parity_test`)으로 생성하고 `init_db()` 실행 → schema-only dump.

Run:
```bash
python -c "
import os
os.environ['DB_NAME'] = 'investment_advisor_parity_test'
from shared.config import DatabaseConfig
from shared.db import init_db
init_db(DatabaseConfig())
print('parity test DB 초기화 완료')
"
```

Expected: `[DB] 데이터베이스 'investment_advisor_parity_test' 생성 완료` + `[DB] v1 기본 스키마 생성 완료` + `[DB] 테이블 초기화 완료` + `parity test DB 초기화 완료`

에러 발생 시: 기존 `investment_advisor_parity_test` DB가 있을 수 있음 → `psql -U postgres -c "DROP DATABASE investment_advisor_parity_test"` 후 재시도.

- [ ] **Step 2: schema-only dump**

Run:
```bash
pg_dump --schema-only --no-owner --no-acl -U postgres -d investment_advisor_parity_test > /tmp/schema_after.sql
wc -l /tmp/schema_after.sql
```

Expected: 수백~수천 줄의 DDL 출력. 에러 없음.

- [ ] **Step 3: 기존 원본(분할 전) 브랜치 대조 — 대안 A: git checkout**

분할 전 main 기준 dump가 이미 저장돼 있지 않다면, 직전 커밋(T1 baseline 시점)에서 같은 절차 수행:

```bash
# 현재 브랜치 저장
CURRENT=$(git rev-parse HEAD)

# 분할 시작 직전(= baseline 커밋)의 부모 커밋으로 체크아웃
git checkout HEAD~13  # Task 1~12의 커밋 개수만큼, 실제 실행 시 조정

# 기존 DB 삭제 후 재생성
python -c "
import psycopg2
conn = psycopg2.connect(host='localhost', dbname='postgres', user='postgres', password='postgres')
conn.autocommit = True
conn.cursor().execute('DROP DATABASE IF EXISTS investment_advisor_parity_test')
"

python -c "
import os
os.environ['DB_NAME'] = 'investment_advisor_parity_test'
from shared.config import DatabaseConfig
from shared.db import init_db
init_db(DatabaseConfig())
"

pg_dump --schema-only --no-owner --no-acl -U postgres -d investment_advisor_parity_test > /tmp/schema_before.sql

# 원래 브랜치로 복귀
git checkout "$CURRENT"
```

**중요:** 위의 `HEAD~13`은 Task 1~12의 실제 커밋 수에 맞춰야 함. `git log --oneline`으로 baseline 커밋(`chore(refactor): A — 공개 API baseline 캡처`)를 찾아 해시를 직접 사용:
```bash
git log --oneline | grep "baseline 캡처"
# 찾은 해시의 직전 커밋 (HEAD^)으로 체크아웃
```

- [ ] **Step 4: DDL diff**

Run:
```bash
diff /tmp/schema_before.sql /tmp/schema_after.sql
```

Expected: 출력 없음 (DDL 완전 동일)

차이가 있다면 각 차이가 무해한 것(공백, 주석 등)인지 확인. 실제 스키마 차이가 있으면 해당 마이그레이션 함수의 이동 과정에서 문제가 발생했을 가능성 → `git diff HEAD~<n> shared/db/migrations/versions.py` 로 비교.

- [ ] **Step 5: 테스트 DB 정리**

Run:
```bash
python -c "
import psycopg2
conn = psycopg2.connect(host='localhost', dbname='postgres', user='postgres', password='postgres')
conn.autocommit = True
conn.cursor().execute('DROP DATABASE IF EXISTS investment_advisor_parity_test')
print('cleanup ok')
"
```

- [ ] **Step 6: 전체 pytest + API 서버 + 주요 페이지 최종 스모크**

Run:
```bash
pytest tests/ -v
```
Expected: 전체 통과

Run:
```bash
timeout 5 python -m api.main 2>&1 | head -30 || true
```
Expected: `Uvicorn running`

라이브 환경에서:
```bash
for path in / /sessions /themes /proposals /admin /api/watchlist; do
  code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:8000${path}")
  echo "${path} → ${code}"
done
```
Expected: 모두 200/302/401 — 500 없음

- [ ] **Step 7: 커밋 없음 (검증만)**

이 태스크는 커밋 산출물이 없음 — 검증 결과를 Task 15 메모에 기록.

---

## Task 15: 검증 완료 메모 문서 작성

**Files:**
- Create: `_docs/20260420_A_db_split_검증완료.md`

- [ ] **Step 1: 메모 문서 작성**

B 시리즈와 동일한 패턴(예: `_docs/*` 내 B3 검증 메모 참조)을 따라 다음 내용 기록:

Create `_docs/20260420_A_db_split_검증완료.md`:

```markdown
# A — shared/db.py 분할 검증 완료 메모

- **일자**: 2026-04-20
- **스펙**: `docs/superpowers/specs/2026-04-20-a-shared-db-split-design.md`
- **플랜**: `docs/superpowers/plans/2026-04-20-a-shared-db-split.md`

## 결과 요약

| 항목 | 분할 전 | 분할 후 |
|---|---|---|
| 파일 수 | 1 (`shared/db.py`) | 9 (`shared/db/` 패키지) |
| 최대 파일 라인 | 2,348 (`db.py`) | (실측 — `wc -l shared/db/*.py shared/db/migrations/*.py` 결과 기록) |
| 공개 API | 13개 심볼 | 13개 (동일, `__all__`로 고정) |
| 마이그레이션 추가 비용 | 3곳 수정 (함수 정의 + if 체인 + SCHEMA_VERSION) | 2곳 수정 (함수 + dict + SCHEMA_VERSION) |

## 검증 통과 항목

- [x] 공개 API parity: 13개 심볼 시그니처 완전 일치
- [x] `analyzer/*`, `api/*`, `shared/logger.py` 무수정 동작
- [x] `pytest tests/` 전체 통과
- [x] Fresh DB에서 `init_db()` 실행 후 `pg_dump --schema-only` diff 공백
- [x] API 서버 기동 + 5개 주요 페이지 200 응답

## 남은 관심사 (추후 트랙)

- `analyzer/validators.py`와 `session_repo.py:_validate_proposal`의 의미적 중복 — 별도 정리 필요 (이번 스코프 외)
- `shared/logger.py`의 함수 내부 lazy import 10곳 — 순환 참조 해소가 가능한지 검토 (현재는 동작 유지)

## 다음 단계

로드맵 "B → C 병렬 → A → D"에서 **C 트랙(템플릿·UX 개편)** 진행.
```

(실제 수치는 실행 후 채워넣음)

- [ ] **Step 2: 커밋**

```bash
git add _docs/20260420_A_db_split_검증완료.md
git commit -m "$(cat <<'EOF'
docs(refactor): A 검증 완료 메모 — shared/db.py 분할

13개 공개 API parity + DDL parity + 전체 테스트 통과 + API 서버
스모크 확인 기록. 다음 단계는 C 트랙(템플릿·UX 개편).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## 완료 확인 체크리스트

- [ ] Task 1: baseline 캡처
- [ ] Task 2: 패키지 scaffold (db.py → db_legacy.py)
- [ ] Task 3: connection.py 추출
- [ ] Task 4: migrations/seeds.py 추출
- [ ] Task 5: migrations/versions.py 추출
- [ ] Task 6: migrations/__init__.py 오케스트레이터
- [ ] Task 7: schema.py 추출
- [ ] Task 8: session_repo.py 추출
- [ ] Task 9: news_repo.py 추출
- [ ] Task 10: query_repo.py 추출
- [ ] Task 11: top_picks_repo.py 추출
- [ ] Task 12: __init__.py 명시적 re-export + db_legacy.py 삭제
- [ ] Task 13: 테스트 private import 경로 업데이트
- [ ] Task 14: Migration parity 검증
- [ ] Task 15: 검증 완료 메모

**후속:** C 트랙(템플릿·UX 개편) 브레인스토밍 시작.
