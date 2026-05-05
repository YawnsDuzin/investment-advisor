# 알림 회사명·테마 보강 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ticker 구독 알림에 회사명을 보강하고, 등장 테마명을 detail 라인에 노출. 기존 알림은 v47 마이그레이션으로 일괄 backfill.

**Architecture:**
1. 신규 알림 — `_generate_notifications()` 가 `stock_universe.asset_name` 일괄 조회 + `ticker_themes` dict 구축 후 단일 포맷터(`_format_ticker_notification`)로 title/detail 생성.
2. 기존 알림 — v47 마이그레이션이 동일 포맷터를 import 해 backfill (단일 진실 소스).

**Tech Stack:** Python 3.10+, psycopg2 (raw cursor), PostgreSQL. UI 변경 없음 ([api/templates/notifications.html](api/templates/notifications.html) 가 이미 `n.detail` 분기 보유).

**Spec:** [_docs/20260505174924_notifications-ticker-enrichment-design.md](_docs/20260505174924_notifications-ticker-enrichment-design.md)

---

## File Structure

| 변경 종류 | 경로 | 책임 |
|---|---|---|
| Modify | [shared/db/session_repo.py](shared/db/session_repo.py) | `_format_ticker_notification` 신규 + `_fetch_company_names` 신규 + `_generate_notifications` 보강 + INSERT 에 detail 추가 |
| Modify | [shared/db/migrations/versions.py](shared/db/migrations/versions.py) | `_migrate_to_v47` 신규 (backfill) |
| Modify | [shared/db/migrations/__init__.py](shared/db/migrations/__init__.py) | `_MIGRATIONS` dict 에 `47` 항목 추가 |
| Modify | [shared/db/schema.py](shared/db/schema.py) | `SCHEMA_VERSION = 47` 로 증가 |
| Create | [tests/test_notification_formatting.py](tests/test_notification_formatting.py) | `_format_ticker_notification` 4 케이스 단위 테스트 |
| Modify | [CLAUDE.md](CLAUDE.md) | DB Schema 절 v47 한 줄 + Key Conventions 알림 보강 1줄 |

---

## Task 1: 포맷터 함수 + 회사명 조회 헬퍼 (TDD)

**Files:**
- Test: `tests/test_notification_formatting.py` (create)
- Modify: `shared/db/session_repo.py` (add `_format_ticker_notification` near `_generate_notifications`)

- [ ] **Step 1.1: Write failing tests for `_format_ticker_notification`**

Create `tests/test_notification_formatting.py`:

```python
"""구독 알림 title/detail 포맷터 단위 테스트."""
from shared.db.session_repo import _format_ticker_notification


def test_company_name_with_single_theme():
    title, detail = _format_ticker_notification(
        sub_key="112290",
        asset_name="에코프로비엠",
        themes=["2차전지 소재 회복"],
    )
    assert title == "구독 종목 '에코프로비엠 (112290)'이(가) 분석에 등장했습니다"
    assert detail == "테마: 2차전지 소재 회복"


def test_company_name_with_multiple_themes():
    title, detail = _format_ticker_notification(
        sub_key="112290",
        asset_name="에코프로비엠",
        themes=["2차전지 소재 회복", "소재주 반등"],
    )
    assert title == "구독 종목 '에코프로비엠 (112290)'이(가) 분석에 등장했습니다 (2개 테마)"
    assert detail == "2차전지 소재 회복 · 소재주 반등"


def test_no_company_name_with_single_theme():
    title, detail = _format_ticker_notification(
        sub_key="AAPL",
        asset_name=None,
        themes=["AI 인프라"],
    )
    assert title == "구독 종목 'AAPL'이(가) 분석에 등장했습니다"
    assert detail == "테마: AI 인프라"


def test_no_company_name_no_themes_backfill_fallback():
    # backfill 케이스 — 세션 CASCADE 삭제 후
    title, detail = _format_ticker_notification(
        sub_key="112290",
        asset_name=None,
        themes=[],
    )
    assert title == "구독 종목 '112290'이(가) 분석에 등장했습니다"
    assert detail is None


def test_company_name_no_themes_backfill_fallback():
    title, detail = _format_ticker_notification(
        sub_key="112290",
        asset_name="에코프로비엠",
        themes=[],
    )
    assert title == "구독 종목 '에코프로비엠 (112290)'이(가) 분석에 등장했습니다"
    assert detail is None


def test_empty_string_asset_name_treated_as_none():
    title, _ = _format_ticker_notification(
        sub_key="112290",
        asset_name="   ",
        themes=["테마"],
    )
    assert title == "구독 종목 '112290'이(가) 분석에 등장했습니다"
```

- [ ] **Step 1.2: Run tests — verify ImportError**

```
pytest tests/test_notification_formatting.py -v
```
Expected: `ImportError: cannot import name '_format_ticker_notification' from 'shared.db.session_repo'`

- [ ] **Step 1.3: Implement `_format_ticker_notification` in `shared/db/session_repo.py`**

Add immediately above `_generate_notifications` (around line 266):

```python
def _format_ticker_notification(
    sub_key: str,
    asset_name: str | None,
    themes: list[str],
) -> tuple[str, str | None]:
    """ticker 구독 알림의 title/detail 생성 — 신규/backfill 공통 포맷터.

    회사명이 있으면 '에코프로비엠 (112290)' 형태, 없으면 티커만.
    테마 1개면 'detail: 테마: X', 다수면 가운뎃점 구분 + 타이틀에 '(N개 테마)'.
    빈 themes (backfill 폴백) 면 detail=None.
    """
    name_clean = (asset_name or "").strip()
    if name_clean:
        label = f"{name_clean} ({sub_key})"
    else:
        label = sub_key

    n = len(themes)
    if n == 0:
        title = f"구독 종목 '{label}'이(가) 분석에 등장했습니다"
        detail = None
    elif n == 1:
        title = f"구독 종목 '{label}'이(가) 분석에 등장했습니다"
        detail = f"테마: {themes[0]}"
    else:
        title = f"구독 종목 '{label}'이(가) 분석에 등장했습니다 ({n}개 테마)"
        detail = " · ".join(themes)

    return title, detail
```

- [ ] **Step 1.4: Run tests — verify all pass**

```
pytest tests/test_notification_formatting.py -v
```
Expected: 6 passed.

- [ ] **Step 1.5: Commit**

```bash
git add tests/test_notification_formatting.py shared/db/session_repo.py
git commit -m "$(cat <<'EOF'
feat(notifications): _format_ticker_notification 포맷터 추가

신규/backfill 공통 — 회사명 + 등장 테마 표기 포맷.
다음 task 에서 _generate_notifications + v47 마이그레이션이 import 사용.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `_fetch_company_names` 헬퍼 + `_generate_notifications` 보강

**Files:**
- Modify: `shared/db/session_repo.py` (add `_fetch_company_names` + revise `_generate_notifications`)

- [ ] **Step 2.1: Add `_fetch_company_names` helper**

Add directly above `_format_ticker_notification`:

```python
def _fetch_company_names(cur, tickers: set[str]) -> dict[str, str]:
    """ticker(대문자) → asset_name lookup. stock_universe 단일 쿼리.

    NULL/공백 asset_name 은 결과에서 제외 → 폴백(티커만 표시)이 자연스럽게 동작.
    """
    if not tickers:
        return {}
    # 테이블 부재 환경 (v25 미적용) 가드
    cur.execute(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'stock_universe')"
    )
    if not cur.fetchone()[0]:
        return {}
    cur.execute(
        "SELECT upper(ticker), asset_name FROM stock_universe "
        "WHERE upper(ticker) = ANY(%s) "
        "AND asset_name IS NOT NULL AND btrim(asset_name) <> ''",
        (list(tickers),),
    )
    return {row[0]: row[1] for row in cur.fetchall()}
```

- [ ] **Step 2.2: Replace `_generate_notifications` body with enriched version**

Locate current implementation at `shared/db/session_repo.py:266-324` and replace with:

```python
def _generate_notifications(cur, session_id: int, themes: list) -> None:
    """구독 매칭 알림 생성 — 분석 저장 시 호출"""
    # user_subscriptions 테이블이 없으면 스킵 (v12 미적용 환경)
    cur.execute(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'user_subscriptions')"
    )
    if not cur.fetchone()[0]:
        return

    # 이번 분석에 등장한 ticker, theme_key 수집 + ticker→themes 매핑
    tickers: set[str] = set()
    theme_keys: dict[str, str] = {}  # key -> theme_name
    ticker_themes: dict[str, list[str]] = {}  # upper(ticker) -> [theme_name, ...]
    for theme in themes:
        theme_name = theme.get("theme_name", "")
        tk = _resolve_theme_key(theme)
        if tk:
            theme_keys[tk] = theme_name
        # 폴백: 한국어 정규화 키로도 매칭 (기존 구독 호환)
        tk_legacy = _normalize_theme_key(theme_name)
        if tk_legacy and tk_legacy != tk:
            theme_keys[tk_legacy] = theme_name
        for p in theme.get("proposals", []):
            t = (p.get("ticker") or "").upper().strip()
            if not t:
                continue
            tickers.add(t)
            if theme_name:
                bucket = ticker_themes.setdefault(t, [])
                if theme_name not in bucket:
                    bucket.append(theme_name)

    if not tickers and not theme_keys:
        return

    # 회사명 일괄 조회 (N+1 회피)
    company_names = _fetch_company_names(cur, tickers)

    # 매칭 구독 조회 — 일반 커서이므로 컬럼 인덱스로 접근
    cur.execute(
        "SELECT id, user_id, sub_type, sub_key, label FROM user_subscriptions"
    )
    subs = cur.fetchall()

    noti_count = 0
    for sub in subs:
        # (id, user_id, sub_type, sub_key, label)
        sub_id, user_id, sub_type, sub_key, label = sub
        title = None
        detail = None
        link = None
        if sub_type == "ticker" and sub_key.upper() in tickers:
            ticker_upper = sub_key.upper()
            asset_name = company_names.get(ticker_upper)
            theme_list = ticker_themes.get(ticker_upper, [])
            title, detail = _format_ticker_notification(sub_key, asset_name, theme_list)
            link = f"/pages/stocks/{sub_key}"
        elif sub_type == "theme" and sub_key in theme_keys:
            display_label = label or theme_keys[sub_key]
            title = f"구독 테마 '{display_label}'이(가) 분석에 등장했습니다"
            link = f"/pages/themes/history/{sub_key}"

        if title:
            cur.execute(
                "INSERT INTO user_notifications (user_id, sub_id, session_id, title, detail, link) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (user_id, sub_id, session_id, title, detail, link),
            )
            noti_count += 1

    if noti_count:
        print(f"[DB] 구독 알림 {noti_count}건 생성")
```

Key changes vs original:
- Build `ticker_themes` while iterating themes (preserves theme order, dedup).
- Call `_fetch_company_names` once before the subscription loop.
- ticker branch delegates to `_format_ticker_notification` (no string interpolation here).
- INSERT now includes `detail` column.
- theme branch unchanged (per Q4 = A 결정).

- [ ] **Step 2.3: Run existing tests to ensure no regression**

```
pytest tests/test_notification_formatting.py -v
pytest -k "session_repo or save_analysis or notification" -v
```
Expected: all pass (포맷터 6개 + 기존 통과).

- [ ] **Step 2.4: Commit**

```bash
git add shared/db/session_repo.py
git commit -m "$(cat <<'EOF'
feat(notifications): 종목 구독 알림에 회사명·테마 보강

_generate_notifications 가 stock_universe.asset_name 일괄 조회 + 등장 테마
매핑 후 _format_ticker_notification 으로 title/detail 생성.
기존 22건은 다음 task 의 v47 마이그레이션이 backfill.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: v47 backfill 마이그레이션

**Files:**
- Modify: `shared/db/migrations/versions.py` (append `_migrate_to_v47`)
- Modify: `shared/db/migrations/__init__.py` (register `47: _v._migrate_to_v47`)
- Modify: `shared/db/schema.py` (`SCHEMA_VERSION = 47` + 주석 갱신)

- [ ] **Step 3.1: Append `_migrate_to_v47` to `shared/db/migrations/versions.py`**

Append at end of file (after `_migrate_to_v46`):

```python
def _migrate_to_v47(cur) -> None:
    """v47: ticker 구독 알림 회사명·테마 backfill.

    기존 user_notifications 의 ticker 알림은 title 에 종목코드만 노출돼
    사용자가 어느 종목인지 즉시 식별 못하는 UX 이슈가 있었음.
    stock_universe.asset_name + session 의 등장 theme 을 join 해 title/detail
    을 새 포맷으로 일괄 갱신. is_read / link / created_at 은 보존.

    공식 포맷터 (`shared.db.session_repo._format_ticker_notification`) 를
    그대로 import 해 단일 진실 소스 유지.
    """
    from shared.db.session_repo import _format_ticker_notification

    # ticker 알림 + 컨텍스트 한 번에 조회
    cur.execute("""
        SELECT n.id,
               s.sub_key,
               u.asset_name,
               COALESCE(
                   (SELECT array_agg(DISTINCT t.theme_name ORDER BY t.theme_name)
                    FROM investment_themes t
                    JOIN investment_proposals p ON p.theme_id = t.id
                    WHERE t.session_id = n.session_id
                      AND upper(p.ticker) = upper(s.sub_key)
                      AND t.theme_name IS NOT NULL
                      AND btrim(t.theme_name) <> ''),
                   ARRAY[]::TEXT[]
               ) AS themes
          FROM user_notifications n
          JOIN user_subscriptions s
            ON s.id = n.sub_id AND s.sub_type = 'ticker'
          LEFT JOIN stock_universe u
            ON upper(u.ticker) = upper(s.sub_key)
           AND u.asset_name IS NOT NULL
           AND btrim(u.asset_name) <> ''
    """)
    rows = cur.fetchall()

    updates = []
    for noti_id, sub_key, asset_name, themes in rows:
        title, detail = _format_ticker_notification(
            sub_key=sub_key,
            asset_name=asset_name,
            themes=list(themes or []),
        )
        updates.append((title, detail, noti_id))

    if updates:
        cur.executemany(
            "UPDATE user_notifications SET title = %s, detail = %s WHERE id = %s",
            updates,
        )

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (47)
        ON CONFLICT (version) DO NOTHING;
    """)
    print(f"[DB] v47 마이그레이션 완료 — ticker 알림 backfill {len(updates)}건")
```

- [ ] **Step 3.2: Register migration in `shared/db/migrations/__init__.py`**

Open file, find `_MIGRATIONS` dict, add line after `46: _v._migrate_to_v46,`:

```python
    46: _v._migrate_to_v46,
    47: _v._migrate_to_v47,
}
```

- [ ] **Step 3.3: Bump `SCHEMA_VERSION` in `shared/db/schema.py`**

Find line 12:
```python
SCHEMA_VERSION = 46  # v46: 매도/익절 시그널 — investment_proposals.target_hit_notified_at / stop_loss_notified_at
```

Replace with:
```python
SCHEMA_VERSION = 47  # v47: ticker 구독 알림 회사명·테마 backfill — user_notifications.title/detail 재생성
```

- [ ] **Step 3.4: Verify migration importability + dict consistency**

```
python -c "from shared.db.migrations.versions import _migrate_to_v47; print('OK')"
python -c "from shared.db.migrations import _MIGRATIONS; assert 47 in _MIGRATIONS; print(f'registered: {sorted(_MIGRATIONS)[-3:]}')"
python -c "from shared.db.schema import SCHEMA_VERSION; assert SCHEMA_VERSION == 47; print(f'SCHEMA_VERSION={SCHEMA_VERSION}')"
```
Expected:
```
OK
registered: [45, 46, 47]
SCHEMA_VERSION=47
```

- [ ] **Step 3.5: Run full test suite**

```
pytest -x
```
Expected: all tests pass (마이그레이션 테스트가 별도로 있으면 v47 도 자동 포함; 신규 회귀 0).

- [ ] **Step 3.6: Commit**

```bash
git add shared/db/migrations/versions.py shared/db/migrations/__init__.py shared/db/schema.py
git commit -m "$(cat <<'EOF'
feat(db): v47 — ticker 구독 알림 회사명·테마 backfill

기존 user_notifications row 들을 stock_universe + session themes 로 join 해
새 포맷으로 title/detail 일괄 UPDATE. is_read / link / created_at 보존.
공식 포맷터 _format_ticker_notification 을 import 하여 단일 진실 소스 유지.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: CLAUDE.md 갱신

**Files:**
- Modify: `CLAUDE.md` (DB Schema 절 + Key Conventions 절)

- [ ] **Step 4.1: Add v47 line under "## DB Schema"**

Locate the block of `- ... (vNN)` bullets (currently ends around v45/v46 entries). Append after the v44 외국인 수급 항목 (or wherever the latest version bullet sits):

```markdown
- `user_notifications` 회사명·테마 보강(v47) — ticker 구독 알림 title 에 `stock_universe.asset_name` 으로 회사명 보강(`'에코프로비엠 (112290)'`), 등장 테마명을 detail 라인에 가운뎃점 구분으로 노출 (다수 테마 시 타이틀에 `(N개 테마)`). 마이그레이션은 `_format_ticker_notification` 단일 포맷터를 import 해 신규/backfill 동일 포맷 보장. UI 변경 없음 (`notifications.html` 의 detail 분기 재사용).
```

(Ordering: append after the last `(vNN)` entry — typically near `stock_universe_foreign_flow(v44)` in current CLAUDE.md.)

- [ ] **Step 4.2: Add 1-line convention under "## Key Conventions"**

Find the existing notification-related bullet (`_base_ctx()` 가 ... `unread_notifications` 주입). Add a new bullet right after it:

```markdown
- ticker 구독 알림은 `_format_ticker_notification(sub_key, asset_name, themes)` 단일 포맷터로 생성 — `stock_universe.asset_name` 폴백 시 티커만 표시, 테마 다수 시 타이틀에 `(N개 테마)` + detail 에 `·` 구분. 신규(`_generate_notifications`) / backfill(v47 마이그레이션) 양쪽이 동일 포맷터 import.
```

- [ ] **Step 4.3: Sanity check render**

```
grep -n "v47" CLAUDE.md
grep -n "_format_ticker_notification" CLAUDE.md
```
Expected: 양쪽 모두 1 개 이상 매치.

- [ ] **Step 4.4: Commit**

```bash
git add CLAUDE.md
git commit -m "$(cat <<'EOF'
docs(CLAUDE): v47 알림 회사명·테마 보강 항목 추가

DB Schema 절에 v47 한 줄, Key Conventions 절에 _format_ticker_notification
단일 포맷터 사용 규칙 1줄 추가.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: 수동 검증 + prompt 기록 commit

**Files:**
- Modify: `_docs/_prompts/20260505_prompt.md` (스테이징만 — 작업 로그 끝에 묶음 commit)

- [ ] **Step 5.1: Restart API + DB 마이그레이션 트리거**

```bash
# Windows 개발 환경 — venv 활성화 후
python -c "from shared.db.schema import init_db; from shared.config import DatabaseConfig; init_db(DatabaseConfig())"
```
Expected log: `[DB] v47 마이그레이션 완료 — ticker 알림 backfill N건` (N = ticker 알림 row 수, 사용자 환경 22 근처).

- [ ] **Step 5.2: Browse `/pages/notifications`**

브라우저에서 `http://localhost:8000/pages/notifications` 접속 → 다음 항목 확인:
- 종목 코드 알림이 `'회사명 (티커)'` 형태로 갱신됐는지 (예: `'에코프로비엠 (112290)'`)
- 다수 테마 알림은 타이틀에 `(N개 테마)` + detail 에 `·` 구분 표시
- 회사명을 못 찾는 종목 (US 비-S&P/NDX 등) 은 티커만 표시되는지
- `is_read` 상태 (좌측 accent border) 가 보존됐는지
- 빈 detail 알림 (세션 CASCADE 삭제된 경우) 은 detail 줄 미표시인지

UI 가 이미 detail 분기 보유 → HTML 변경 없음 확인.

- [ ] **Step 5.3: 신규 분석 1회 실행 (옵션 — 데이터 있으면)**

```bash
python -m analyzer.main
```
저장 후 `/pages/notifications` 재확인 — 신규 알림도 동일 포맷으로 생성됐는지.

- [ ] **Step 5.4: prompt 기록 + 작업 묶음 commit**

```bash
git add _docs/_prompts/20260505_prompt.md
git commit -m "$(cat <<'EOF'
docs(_prompts): 20260505 — 알림 회사명·테마 보강 작업 프롬프트 기록

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

(CLAUDE.md 규칙: prompt 기록은 작업 관련 마지막 commit 에 묶거나 별도 commit 1건으로 처리. 본 plan 은 후자로 분리 — 작업 자체가 4건 commit 으로 의미 단위 명확.)

---

## Self-Review

- **Spec coverage**:
  - § 1 포맷 명세 → Task 1 (포맷터 + 6개 단위 테스트가 표 모든 케이스 커버)
  - § 2 데이터 수집 경로 → Task 2 (`_fetch_company_names` + `_generate_notifications` 보강)
  - § 3 v47 마이그레이션 → Task 3
  - § 4 폴백 (US 종목, 빈 themes) → Task 1 의 두 폴백 테스트 + `_fetch_company_names` 의 NULL/공백 필터
  - § 5 검증 → Task 1 단위 테스트 + Task 5 수동 검증
  - 비변경 사항 (notifications.html, 테마 알림) → 명시적으로 손대지 않음 ✓
  - CLAUDE.md 갱신 → Task 4 ✓

- **Placeholder scan**: 모든 step 에 실제 code/command/expected output 포함. TBD/TODO 없음. ✓

- **Type consistency**:
  - `_format_ticker_notification(sub_key: str, asset_name: str | None, themes: list[str])` — Task 1 정의, Task 2/3 동일 시그니처로 호출 ✓
  - `_fetch_company_names(cur, tickers: set[str]) -> dict[str, str]` — 키는 upper(ticker), Task 2 에서 `sub_key.upper()` 로 lookup ✓
  - SQL `array_agg(...) -> array` → Python `list(themes or [])` 변환 ✓

- **Commit 단위**: 5 commits — (1) 포맷터+테스트, (2) 신규 알림 보강, (3) v47 마이그레이션, (4) docs, (5) prompt 기록. 각 commit 이 독립적으로 revert 가능, 4번까지는 revert 해도 시스템 동작 유지.
