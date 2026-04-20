# A Design — `shared/db.py` 분할

- **작성일**: 2026-04-20
- **작성자**: Claude (brainstorming skill, opus-4-7)
- **상위 컨텍스트**: "B → C 병렬 → A → D" 로드맵 중 A 트랙. B 시리즈(B1·B2·B2.5·B3) 완료 후 이어짐
- **다음 단계**: writing-plans skill 호출 → 구현 계획서 작성

---

## 0. 컨텍스트

`shared/db.py`는 2,348줄 단일 파일로 다음 7개 역할이 섞여 있다:

| 역할 | 대표 심볼 | 라인 |
|---|---|---|
| Connection / Schema | `_ensure_database`, `get_connection`, `_get_schema_version` | ~60 |
| Base schema + init 오케스트레이터 | `SCHEMA_VERSION`, `_create_base_schema`, `init_db` | ~150 |
| Migrations | `_migrate_to_v2` ~ `_migrate_to_v23` (22개) | ~1,300 |
| Seed 데이터 | `_seed_admin_user`, `_seed_education_topics`(488줄) | ~510 |
| Save pipeline | `save_analysis`, `_validate_proposal`, `_update_tracking`, `_generate_notifications`, `_normalize_theme_key`, `_resolve_theme_key` | ~450 |
| News ops | `save_news_articles`, `get_untranslated_news`, `update_news_title_ko`, `update_news_translation`, `get_latest_news_titles` | ~130 |
| Query helpers | `get_recent_recommendations`, `get_existing_theme_keys` | ~60 |
| Top Picks | `save_top_picks`, `update_top_picks_ai_rerank` | ~100 |

외부 import 사용처:

- `analyzer/main.py`, `analyzer/analyzer.py`, `analyzer/price_tracker.py`, `analyzer/validators.py`
- `api/main.py`, `api/deps.py`, `api/routes/admin.py`, `api/auth/dependencies.py`
- `shared/logger.py` (함수 내부 lazy import 10곳 — 순환 참조 회피 목적)
- `tests/test_tier_limits.py`, `tests/test_admin_tier_audit.py` (private `_migrate_to_vN` 직접 import)

**현재 상태의 문제:**

1. 파일 하나에서 스키마 관리 / 저장 / 조회 / 알림 / 추적 / 시드를 모두 담당 — 단일 책임 원칙 위반
2. 새 마이그레이션 추가 시 2,000+ 줄 파일 끝부분에 함수를 덧붙이고 `init_db()` 중간의 22줄짜리 if 체인도 수정 필요
3. `_seed_education_topics`의 488줄 데이터 블록이 로직 파일에 혼재 → 데이터 변경이 로직 diff처럼 보임
4. 테스트가 private 심볼(`_migrate_to_vN`)을 `shared.db`에서 직접 import — 공개/비공개 경계가 모호

## 1. Goals

1. **모듈화** — 7개 역할을 의미 단위로 파일 분리, 가장 큰 파일도 800줄 이내로 유지
2. **공개 API 경계 명시화** — `shared/db/__init__.py`에서 실제로 외부가 사용하는 13개 심볼만 `__all__`로 노출
3. **외부 호출부 무수정** — `analyzer/*`, `api/*`, `shared/logger.py`의 기존 `from shared.db import ...` 경로가 그대로 작동
4. **마이그레이션 오케스트레이션 테이블화** — `init_db()` 내부의 22줄 if 체인을 버전→함수 dict 기반 loop로 치환
5. **새 마이그레이션 추가 비용 감소** — 새 버전 추가 시 `versions.py` 함수 + `migrations/__init__.py` dict 한 줄만 수정

## 2. Non-goals (B / C / D)

- **B 시리즈**: 이미 완료 (B1 pages.py 분할, B2 보일러플레이트, B2.5 DI, B3 응답 일관성)
- **C**: 템플릿·UX 개편
- **D**: `analyzer/` 파이프라인 분해
- **ORM 도입**: psycopg2 raw SQL 유지 — 이번 작업은 파일 분할만 수행
- **트랜잭션 정책 변경**: 기존 `try/finally + commit/rollback` 패턴 유지
- **DDL 튜닝**: 기존 스키마/인덱스는 그대로 — 기능·동작 변경 0
- **`analyzer/validators.py`와의 통합**: 별도 관심사 (파이프라인 결과 검증 vs 저장 시점 정규화) — 이번 범위 외

## 3. 분할 후 파일 구조

```
shared/db/
├── __init__.py            # 공개 API re-export + __all__
├── connection.py          # _ensure_database, get_connection, _get_schema_version
├── schema.py              # SCHEMA_VERSION, _create_base_schema, init_db
├── migrations/
│   ├── __init__.py        # _MIGRATIONS dict + run_migrations() 오케스트레이터
│   ├── versions.py        # _migrate_to_v2 ~ _migrate_to_v23 (22개 함수)
│   └── seeds.py           # _seed_admin_user, _seed_education_topics
├── session_repo.py        # save_analysis + _validate_proposal + _generate_notifications
│                          #   + _update_tracking + _normalize_theme_key + _resolve_theme_key
├── news_repo.py           # save_news_articles, get_untranslated_news, update_news_title_ko,
│                          #   update_news_translation, get_latest_news_titles
├── query_repo.py          # get_recent_recommendations, get_existing_theme_keys
└── top_picks_repo.py      # save_top_picks, update_top_picks_ai_rerank
```

### 파일별 예상 라인수

| 파일 | 예상 라인 | 비고 |
|---|---|---|
| `connection.py` | ~60 | `get_connection` + DB 자동 생성 로직 |
| `schema.py` | ~110 | `SCHEMA_VERSION` 상수 + `_create_base_schema` + `init_db` (오케스트레이터) |
| `migrations/__init__.py` | ~40 | `_MIGRATIONS` dict + `run_migrations()` |
| `migrations/versions.py` | ~800 | 22개 `_migrate_to_vN` 함수 (seed 호출은 상대 import) |
| `migrations/seeds.py` | ~510 | `_seed_admin_user` + `_seed_education_topics`(대용량 데이터) |
| `session_repo.py` | ~450 | `save_analysis` + 관련 private 유틸 5개 |
| `news_repo.py` | ~130 | 뉴스 저장/조회/번역 업데이트 5개 |
| `query_repo.py` | ~60 | 최근 추천 이력 + 테마 키 조회 |
| `top_picks_repo.py` | ~100 | Top Picks 저장 + AI 재정렬 |
| `__init__.py` | ~30 | re-export + `__all__` |

## 4. 공개 API 경계 (`shared/db/__init__.py`)

현재 외부에서 import되는 심볼을 grep으로 조사한 결과 다음 13개만 사용 중:

```python
# shared/db/__init__.py
"""DB 계층 공개 API.

외부 모듈은 이 패키지에서만 import할 것. 내부 구조(connection/schema/migrations/
*_repo)는 구현 디테일이며 호출부는 신경쓰지 않아도 된다.
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

**결과:** `analyzer/*`, `api/*`, `shared/logger.py`의 기존 import 문 전부 무수정으로 동작.

**예외:** `tests/test_tier_limits.py`와 `tests/test_admin_tier_audit.py`의 `from shared.db import _migrate_to_vN`은 private 심볼이므로 새 경로(`from shared.db.migrations.versions import _migrate_to_vN`)로 업데이트. → private은 private 경로로.

## 5. 마이그레이션 오케스트레이션

### 현재 구조 (문제점)

`shared/db.py`의 `init_db()` 내부:

```python
if current_version < 2:
    _migrate_to_v2(cur)
    current_version = 2
if current_version < 3:
    _migrate_to_v3(cur)
    current_version = 3
...
if current_version < 23:
    _migrate_to_v23(cur)
    current_version = 23
```

→ 마이그레이션 추가 시마다 3곳 수정 필요 (함수 정의, if 블록, `SCHEMA_VERSION` 상수).

### 개선 (dict 기반 loop)

```python
# shared/db/migrations/__init__.py
from shared.db.migrations import versions as _v

_MIGRATIONS = {
    2: _v._migrate_to_v2,
    3: _v._migrate_to_v3,
    4: _v._migrate_to_v4,
    # ... 순서대로
    23: _v._migrate_to_v23,
}


def run_migrations(cur, current_version: int, target_version: int) -> None:
    """current_version 이후부터 target_version까지 순차 적용."""
    for ver in range(current_version + 1, target_version + 1):
        migrate = _MIGRATIONS.get(ver)
        if migrate is None:
            raise RuntimeError(f"Missing migration for v{ver}")
        migrate(cur)
```

```python
# shared/db/schema.py (init_db 본문)
from shared.db.migrations import run_migrations

def init_db(cfg: DatabaseConfig) -> None:
    conn = get_connection(cfg)
    try:
        with conn.cursor() as cur:
            current = _get_schema_version(cur)
            if current == 0:
                _create_base_schema(cur)
                current = 1
            run_migrations(cur, current, SCHEMA_VERSION)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```

→ 새 마이그레이션 추가 시: `versions.py`에 함수 하나, `migrations/__init__.py`의 `_MIGRATIONS` dict에 한 줄, `schema.py`의 `SCHEMA_VERSION` 상수 증가. **if 체인 수정 불필요.**

## 6. 단계별 커밋 전략

B1·B2·B2.5·B3과 동일하게 여러 작은 커밋으로 분할해 각 단계 검증:

| # | 커밋 | 내용 | 검증 |
|---|---|---|---|
| T1 | `refactor(db): A — 패키지 스캐폴드 + connection 추출` | `shared/db.py` → `shared/db_legacy.py` 리네임. `shared/db/__init__.py` 생성하고 `from shared.db_legacy import *` 임시 shim으로 기존 심볼 노출. `connection.py` 생성하고 `_ensure_database`, `get_connection`, `_get_schema_version` 이동 후 `db_legacy.py`에서 `from shared.db.connection import ...` re-import | `python -c "from shared.db import get_connection, init_db"` 성공 + API 서버 기동 |
| T2 | `refactor(db): A — migrations/versions.py + seeds.py 추출` | 22개 `_migrate_to_vN` 함수를 `versions.py`로, `_seed_admin_user`·`_seed_education_topics`를 `seeds.py`로 이동. 원본 파일에서는 제거 후 re-import | fresh DB에 `init_db()` 실행 → v0→v23 순차 마이그레이션 성공 |
| T3 | `refactor(db): A — migrations/__init__.py 오케스트레이터 추가` | `_MIGRATIONS` dict + `run_migrations()` 도입. `init_db()`의 22줄 if 체인 제거 | idempotent init_db() 재실행 시 no-op 확인 |
| T4 | `refactor(db): A — schema.py 추출 + session_repo 추출` | `SCHEMA_VERSION`, `_create_base_schema`, `init_db`를 `schema.py`로. `save_analysis`와 관련 private 유틸 5개를 `session_repo.py`로 | save_analysis 경로 E2E 스모크 (배치 실행 또는 /admin 트리거) |
| T5 | `refactor(db): A — news_repo/query_repo/top_picks_repo 추출 + 레거시 파일 제거` | 나머지 함수를 각 `*_repo.py`로 이동 후 `shared/db_legacy.py` 삭제. `__init__.py`의 `*` import shim을 명시적 public 심볼 13개 re-export로 치환, `__all__` 선언 | `/sessions`, `/themes`, `/proposals`, `/admin` 200 응답 + `pytest tests/` 전체 성공 |
| T6 | `refactor(db): A — 테스트 private import 경로 업데이트` | `tests/test_tier_limits.py`, `tests/test_admin_tier_audit.py`의 `from shared.db import _migrate_to_vN` → `from shared.db.migrations.versions import _migrate_to_vN` | `pytest tests/test_tier_limits.py tests/test_admin_tier_audit.py` 성공 |
| T7 | `docs(refactor): A 검증 완료 메모` | `_docs/`에 결과 요약 + baseline 비교 결과 기록 | — |

**원자성:** 각 커밋 직후 `python -c "from shared.db import *"` + API 서버 기동 확인. 문제 시 해당 커밋만 revert해 직전 상태로 복귀.

## 7. 검증 전략

### 7.1 Public API Parity (baseline 캡처)

분할 전후로 `shared.db.*` 의 공개 심볼 목록과 시그니처가 동일한지 스크립트로 비교:

```python
# tools/a_db_split/capture_api.py (T1 이전 실행)
import inspect
import shared.db as m

baseline = {}
for name in sorted(dir(m)):
    if name.startswith("_"):
        continue
    obj = getattr(m, name)
    if callable(obj):
        baseline[name] = str(inspect.signature(obj))
    else:
        baseline[name] = f"<{type(obj).__name__}>"

# baseline을 JSON으로 저장, 각 커밋 후 diff
```

→ 공개 13개 심볼이 모두 같은 시그니처로 남아있어야 pass.

### 7.2 Migration Parity

Fresh PostgreSQL DB에서:

1. 분할 **전** 브랜치로 `init_db()` 실행 → `pg_dump --schema-only -d test_before`
2. 분할 **후** 브랜치로 `init_db()` 실행 → `pg_dump --schema-only -d test_after`
3. `diff` 결과가 공백뿐이어야 pass

### 7.3 런타임 스모크

T4, T5, T6 이후 각각:

- `python -m api.main` 기동 → `/`, `/sessions`, `/themes`, `/proposals`, `/admin`, `/api/watchlist` GET 200
- `pytest tests/` 전체 통과
- 배치 드라이런 가능하면 `python -m analyzer.main` (또는 부분 실행)

## 8. 리스크 및 대응

| # | 리스크 | 가능성 | 영향 | 대응 |
|---|---|---|---|---|
| R1 | `shared/logger.py`의 함수 내부 lazy import (10곳, `from shared.db import get_connection`)가 패키지 전환 후 순환 참조를 유발 | 낮음 | 중 | `shared/db/__init__.py`의 re-export가 모듈 로드 시점에 해소되므로 lazy import 동작 동일. T1 직후 `python -c "import shared.logger; import shared.db"` 확인. |
| R2 | `versions.py` 내부 마이그레이션 함수 간 상호 호출 (예: `_migrate_to_v11` → `_seed_admin_user`) | 중 | 고 | 같은 서브패키지로 이동 → `from shared.db.migrations.seeds import _seed_admin_user` 상대 import. T2 직후 v0→v23 full migration 테스트. |
| R3 | T5에서 원본 `shared/db.py` 삭제 후 `__init__.py`의 re-export가 누락된 심볼이 있을 경우 런타임 ImportError | 낮음 | 고 | 7.1의 baseline 스크립트가 T5 직전/직후 실행되어 차이를 사전 감지. 누락 시 `__init__.py`에 추가. |
| R4 | `_update_tracking`이 `save_analysis` 외 다른 경로에서도 호출되고 있을 가능성 | 낮음 | 중 | 이동 전 grep으로 호출부 검증. 현재 `shared/db.py` 내부에서만 호출됨을 확인 완료. |
| R5 | `_migrate_to_vN` 함수가 테스트 외에도 외부에서 쓰일 가능성 | 낮음 | 저 | 이동 전 전체 grep. 현재 2개 테스트 파일만 사용 확인. 공개 API 아님을 스펙에 명시. |
| R6 | 커밋 중간에 다른 트랙(C)이 시작되어 충돌 | 중 | 저 | A 트랙은 dev 브랜치에서 단일 흐름으로 먼저 완료 후 C 진행. B 시리즈와 동일한 순차 정책. |

## 9. 성공 기준

- [ ] `shared/db/` 패키지로 7개 기능 그룹이 의미 단위 파일로 분리됨 (단일 파일 800줄 이내)
- [ ] 외부 호출부(`analyzer/*`, `api/*`, `shared/logger.py`) 무수정 — baseline 공개 API 13개 심볼 parity 통과
- [ ] 테스트(`tests/`) 전체 통과 — private import는 새 경로로 업데이트
- [ ] Fresh DB에서 `init_db()` 실행 시 분할 전후 `pg_dump --schema-only` diff가 공백
- [ ] `init_db()`의 22줄 if 체인 → dict 기반 loop 치환 완료
- [ ] API 서버 기동 + 5개 주요 페이지 200 응답

---

## 부록 A — 공개 API 근거 (grep 결과)

```
analyzer/main.py:18         from shared.db import (...)           # 여러 개
analyzer/analyzer.py:25     get_recent_recommendations, get_existing_theme_keys
analyzer/price_tracker.py:11 get_connection
analyzer/validators.py:246   get_connection (함수 내부)
api/main.py:8               init_db
api/deps.py:6               get_connection
api/routes/admin.py:15      get_untranslated_news, update_news_title_ko, update_news_translation, get_connection
api/auth/dependencies.py:5  get_connection
shared/logger.py (10곳)     get_connection (함수 내부 lazy)
tests/test_tier_limits.py:169,175     _migrate_to_v16 (private, 새 경로 이전 대상)
tests/test_admin_tier_audit.py:23,27  _migrate_to_v17 (private, 새 경로 이전 대상)
tests/test_pages_new.py:43  shared.db.init_db (monkeypatch — 경로 문자열, 작동 유지)
```

→ public 13개 심볼로 충분. `_migrate_to_vN`은 private으로 분류하고 테스트만 새 경로 사용.
