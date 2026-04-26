# Screener Fundamentals M1+M2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Screener에 거장 전략 프리셋을 출시하기 위한 사전 인프라 구축 — `stock_universe_fundamentals` PIT 시계열 테이블 + pykrx/yfinance 일별 수집 파이프라인 + systemd unit 통합 (관리자 메뉴 화이트리스트 포함).

**Architecture:** 신규 마이그레이션 v39 (펀더 시계열 테이블) + v40 (screener_presets 확장 — 시드 프리셋 대비). `analyzer/fundamentals_sync.py` 신규 모듈에서 시장별 분기(KRX→pykrx, US→yfinance.info) → UPSERT. `universe_sync.py` CLI에 `--mode fundamentals` 추가. systemd unit 2개 신설 + `admin_systemd.MANAGED_UNITS` 화이트리스트 등록.

**Tech Stack:** PostgreSQL (psycopg2), pykrx 1.2.7+, yfinance, FastAPI, pytest (psycopg2/feedparser/claude_agent_sdk mock 처리됨), systemd.

**Spec:** `docs/superpowers/specs/2026-04-26-screener-investor-strategies-design.md`

**Scope:** M1 (DB 인프라) + M2 (수집 파이프라인 + systemd). M3~M6은 후속 plan.

---

## File Structure

**Create:**
- `analyzer/fundamentals_sync.py` — pykrx/yfinance 분기 수집 + UPSERT (M2 핵심)
- `tools/fundamentals_health_check.py` — 결측률 진단 도구
- `deploy/systemd/investment-advisor-fundamentals.service` — oneshot systemd unit
- `deploy/systemd/investment-advisor-fundamentals.timer` — KST 06:35 timer
- `tests/test_fundamentals_config.py` — FundamentalsConfig dataclass 검증
- `tests/test_migration_v39_v40.py` — 마이그레이션 idempotent
- `tests/test_fundamentals_sync.py` — pykrx/yfinance mock 단위 테스트
- `tests/test_fundamentals_health_check.py` — 결측률 계산
- `tests/test_admin_systemd_managed_units.py` — 신규 unit 화이트리스트 등록 검증

**Modify:**
- `shared/config.py` — `FundamentalsConfig` dataclass 추가, `AppConfig`에 필드 추가
- `shared/db/migrations/versions.py` — `_migrate_to_v39()`, `_migrate_to_v40()`, `init_db()` 디스패치
- `shared/db/schema.py` — `SCHEMA_VERSION = 40`
- `analyzer/universe_sync.py` — `--mode fundamentals` argparse 분기 + 디스패치
- `api/routes/admin_systemd.py` — `MANAGED_UNITS`에 fundamentals unit 추가
- `deploy/systemd/README.md` — sudoers 화이트리스트 예시 갱신

---

## Task 1: FundamentalsConfig 추가

**Files:**
- Modify: `shared/config.py` (line ~205 OhlcvConfig 다음에 추가, AppConfig에도 필드 추가)
- Test: `tests/test_fundamentals_config.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_fundamentals_config.py
"""FundamentalsConfig dataclass 환경변수 파싱 검증."""
import os
import pytest
from shared.config import FundamentalsConfig, AppConfig


def test_defaults():
    cfg = FundamentalsConfig()
    assert cfg.retention_days == 800
    assert cfg.delisted_retention_days == 400
    assert cfg.sync_enabled is True
    assert cfg.pykrx_batch_size == 200
    assert cfg.yfinance_batch_size == 50
    assert cfg.validation_tolerance_pct == 5.0


def test_env_override(monkeypatch):
    monkeypatch.setenv("FUNDAMENTALS_RETENTION_DAYS", "365")
    monkeypatch.setenv("FUNDAMENTALS_SYNC_ENABLED", "false")
    monkeypatch.setenv("FUNDAMENTALS_PYKRX_BATCH_SIZE", "50")
    cfg = FundamentalsConfig()
    assert cfg.retention_days == 365
    assert cfg.sync_enabled is False
    assert cfg.pykrx_batch_size == 50


def test_appconfig_includes_fundamentals():
    app = AppConfig()
    assert isinstance(app.fundamentals, FundamentalsConfig)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_fundamentals_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'FundamentalsConfig' from 'shared.config'`

- [ ] **Step 3: 구현 — FundamentalsConfig + AppConfig 필드 추가**

`shared/config.py`의 `OhlcvConfig` 다음 (line ~204 근처)에 삽입:

```python
@dataclass
class FundamentalsConfig:
    """펀더멘털 PIT 시계열 수집 설정 (B-Lite — pykrx KR + yfinance.info US).

    `stock_universe_fundamentals` 테이블의 수집·보존 정책.
    Spec: docs/superpowers/specs/2026-04-26-screener-investor-strategies-design.md
    """
    retention_days: int = field(
        default_factory=lambda: int(os.getenv("FUNDAMENTALS_RETENTION_DAYS", "800"))
    )
    delisted_retention_days: int = field(
        default_factory=lambda: int(os.getenv("FUNDAMENTALS_DELISTED_RETENTION_DAYS", "400"))
    )
    sync_enabled: bool = field(
        default_factory=lambda: _env_bool("FUNDAMENTALS_SYNC_ENABLED", True)
    )
    pykrx_batch_size: int = field(
        default_factory=lambda: int(os.getenv("FUNDAMENTALS_PYKRX_BATCH_SIZE", "200"))
    )
    yfinance_batch_size: int = field(
        default_factory=lambda: int(os.getenv("FUNDAMENTALS_YFINANCE_BATCH_SIZE", "50"))
    )
    validation_tolerance_pct: float = field(
        default_factory=lambda: float(os.getenv("FUNDAMENTALS_VALIDATION_TOLERANCE_PCT", "5.0"))
    )
```

`AppConfig`에 필드 추가 (line ~270 근처):

```python
@dataclass
class AppConfig:
    db: DatabaseConfig = field(default_factory=DatabaseConfig)
    news: NewsConfig = field(default_factory=NewsConfig)
    analyzer: AnalyzerConfig = field(default_factory=AnalyzerConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    fundamentals: FundamentalsConfig = field(default_factory=FundamentalsConfig)  # NEW
```

(기존 추가된 다른 필드 — universe/screener/ohlcv 등 — 도 그대로 두고 fundamentals만 추가.)

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_fundamentals_config.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add shared/config.py tests/test_fundamentals_config.py
git commit -m "feat(config): FundamentalsConfig 추가 — pykrx/yfinance PIT 수집 설정"
```

---

## Task 2: v39 마이그레이션 — `stock_universe_fundamentals`

**Files:**
- Modify: `shared/db/schema.py` (`SCHEMA_VERSION = 39`)
- Modify: `shared/db/migrations/versions.py` (line ~1490 `_migrate_to_v33` 다음에 `_migrate_to_v39` 추가)
- Modify: `shared/db/migrations/__init__.py` 또는 `run_migrations` 디스패치 함수 — 어느 파일인지 grep으로 확인 후 수정
- Test: `tests/test_migration_v39_v40.py`

- [ ] **Step 1: 디스패치 함수 위치 확인**

Run: `grep -rn "run_migrations\|def run_migrations" shared/db/migrations/`
Expected: `versions.py` 내 `run_migrations(cur, current, target)` 정의 발견. 해당 함수 내 `if current < N: _migrate_to_vN(cur)` 분기 패턴 확인.

- [ ] **Step 2: 실패 테스트 작성**

```python
# tests/test_migration_v39_v40.py
"""v39 — stock_universe_fundamentals 테이블 생성 검증.

기존 conftest.py 가 psycopg2 mock 처리하므로,
실제 DB 호출 대신 _migrate_to_v39 가 SQL을 어떤 순서로 실행했는지만 검증한다.
"""
from unittest.mock import MagicMock
from shared.db.migrations.versions import _migrate_to_v39


def test_v39_creates_fundamentals_table():
    cur = MagicMock()
    _migrate_to_v39(cur)

    # cur.execute 호출 누적
    sqls = [call.args[0] for call in cur.execute.call_args_list]
    joined = " ".join(sqls).upper()
    assert "CREATE TABLE IF NOT EXISTS STOCK_UNIVERSE_FUNDAMENTALS" in joined
    assert "PRIMARY KEY (TICKER, MARKET, SNAPSHOT_DATE)" in joined.replace("\n", " ")
    assert "IDX_FUND_LATEST" in joined
    assert "IDX_FUND_DATE" in joined
    assert "INSERT INTO SCHEMA_VERSION (VERSION) VALUES (39)" in joined


def test_v39_idempotent_via_if_not_exists():
    """IF NOT EXISTS 가드로 두 번 호출되어도 문제 없음."""
    cur = MagicMock()
    _migrate_to_v39(cur)
    _migrate_to_v39(cur)
    # SQL 중 모든 CREATE 문이 IF NOT EXISTS 포함
    sqls = [call.args[0] for call in cur.execute.call_args_list]
    for sql in sqls:
        if "CREATE TABLE" in sql.upper():
            assert "IF NOT EXISTS" in sql.upper(), f"비-멱등 SQL: {sql[:100]}"
        if "CREATE INDEX" in sql.upper():
            assert "IF NOT EXISTS" in sql.upper(), f"비-멱등 SQL: {sql[:100]}"
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `pytest tests/test_migration_v39_v40.py::test_v39_creates_fundamentals_table -v`
Expected: FAIL — `ImportError: cannot import name '_migrate_to_v39'`

- [ ] **Step 4: 구현 — `_migrate_to_v39` 추가**

`shared/db/migrations/versions.py` 끝에 추가 (기존 _migrate_to_v33 다음, 모든 마이그레이션 함수 다음):

```python
def _migrate_to_v39(cur) -> None:
    """v39: stock_universe_fundamentals — 종목별 PIT 펀더멘털 시계열.

    pykrx (KR PER/PBR/EPS/BPS/DPS/배당률) + yfinance.info (US trailingPE/priceToBook/...)
    를 일별로 누적. `stock_universe_ohlcv` 와 동일한 PIT 정책 — FK 미설정으로 상폐 종목 이력 보존.

    Spec: docs/superpowers/specs/2026-04-26-screener-investor-strategies-design.md §3.2
    """
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stock_universe_fundamentals (
            ticker          TEXT NOT NULL,
            market          TEXT NOT NULL,
            snapshot_date   DATE NOT NULL,
            per             NUMERIC(12,4),
            pbr             NUMERIC(12,4),
            eps             NUMERIC(18,4),
            bps             NUMERIC(18,4),
            dps             NUMERIC(18,4),
            dividend_yield  NUMERIC(8,4),
            data_source     TEXT NOT NULL,
            fetched_at      TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (ticker, market, snapshot_date)
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_fund_latest
            ON stock_universe_fundamentals(ticker, market, snapshot_date DESC);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_fund_date
            ON stock_universe_fundamentals(snapshot_date);
    """)
    cur.execute("""
        INSERT INTO schema_version (version) VALUES (39)
        ON CONFLICT (version) DO NOTHING;
    """)
    print("[DB] v39 마이그레이션 완료 — stock_universe_fundamentals (B-Lite 펀더 PIT)")
```

`run_migrations` 디스패치에 추가 (기존 패턴 따라):

```python
    if current < 39:
        _migrate_to_v39(cur)
```

`shared/db/schema.py`의 `SCHEMA_VERSION` 갱신:

```python
SCHEMA_VERSION = 39  # v39: stock_universe_fundamentals (B-Lite 펀더 PIT)
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `pytest tests/test_migration_v39_v40.py::test_v39_creates_fundamentals_table tests/test_migration_v39_v40.py::test_v39_idempotent_via_if_not_exists -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add shared/db/migrations/versions.py shared/db/schema.py tests/test_migration_v39_v40.py
git commit -m "feat(db): v39 마이그레이션 — stock_universe_fundamentals 펀더 PIT 시계열"
```

---

## Task 3: v40 마이그레이션 — `screener_presets` 확장

**Files:**
- Modify: `shared/db/migrations/versions.py` (Task 2의 `_migrate_to_v39` 다음에 `_migrate_to_v40` 추가)
- Modify: `shared/db/schema.py` (`SCHEMA_VERSION = 40`)
- Test: `tests/test_migration_v39_v40.py` (Task 2와 동일 파일에 테스트 추가)

- [ ] **Step 1: 실패 테스트 작성 (Task 2 파일에 추가)**

```python
# tests/test_migration_v39_v40.py 끝에 추가
from shared.db.migrations.versions import _migrate_to_v40


def test_v40_alters_screener_presets():
    cur = MagicMock()
    _migrate_to_v40(cur)
    sqls = " ".join(call.args[0] for call in cur.execute.call_args_list).upper()

    assert "ALTER TABLE SCREENER_PRESETS" in sqls
    assert "DROP NOT NULL" in sqls          # user_id NOT NULL 해제
    assert "IS_SEED" in sqls
    assert "STRATEGY_KEY" in sqls
    assert "PERSONA" in sqls
    assert "PERSONA_SUMMARY" in sqls
    assert "MARKETS_SUPPORTED" in sqls
    assert "RISK_WARNING" in sqls
    assert "UQ_SCREENER_PRESETS_STRATEGY_KEY" in sqls
    assert "INSERT INTO SCHEMA_VERSION (VERSION) VALUES (40)" in sqls


def test_v40_alter_uses_if_not_exists():
    """ADD COLUMN IF NOT EXISTS 로 멱등."""
    cur = MagicMock()
    _migrate_to_v40(cur)
    sqls = [call.args[0] for call in cur.execute.call_args_list]
    for sql in sqls:
        if "ADD COLUMN" in sql.upper():
            assert "IF NOT EXISTS" in sql.upper(), f"비-멱등: {sql[:100]}"
        if "CREATE UNIQUE INDEX" in sql.upper():
            assert "IF NOT EXISTS" in sql.upper(), f"비-멱등: {sql[:100]}"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_migration_v39_v40.py::test_v40_alters_screener_presets -v`
Expected: FAIL — `ImportError: cannot import name '_migrate_to_v40'`

- [ ] **Step 3: 구현 — `_migrate_to_v40` 추가**

`shared/db/migrations/versions.py`에 `_migrate_to_v39` 다음으로 추가:

```python
def _migrate_to_v40(cur) -> None:
    """v40: screener_presets 확장 — 거장 시드 프리셋 대비.

    - user_id NULLABLE (시드 = NULL)
    - is_seed / strategy_key / persona / persona_summary / markets_supported / risk_warning 추가
    - strategy_key 부분 UNIQUE (is_seed=TRUE 한정) — UPSERT 멱등 보장

    UNIQUE(user_id, name) 기존 제약은 유지 — PostgreSQL에서 NULL은 UNIQUE 무관하므로
    여러 시드 row가 user_id NULL 이어도 충돌 없음.

    Spec: docs/superpowers/specs/2026-04-26-screener-investor-strategies-design.md §4.2
    """
    cur.execute("""
        ALTER TABLE screener_presets
            ALTER COLUMN user_id DROP NOT NULL;
    """)
    cur.execute("""
        ALTER TABLE screener_presets
            ADD COLUMN IF NOT EXISTS is_seed BOOLEAN DEFAULT FALSE;
    """)
    cur.execute("""
        ALTER TABLE screener_presets
            ADD COLUMN IF NOT EXISTS strategy_key TEXT;
    """)
    cur.execute("""
        ALTER TABLE screener_presets
            ADD COLUMN IF NOT EXISTS persona TEXT;
    """)
    cur.execute("""
        ALTER TABLE screener_presets
            ADD COLUMN IF NOT EXISTS persona_summary TEXT;
    """)
    cur.execute("""
        ALTER TABLE screener_presets
            ADD COLUMN IF NOT EXISTS markets_supported TEXT[];
    """)
    cur.execute("""
        ALTER TABLE screener_presets
            ADD COLUMN IF NOT EXISTS risk_warning TEXT;
    """)
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_screener_presets_strategy_key
            ON screener_presets(strategy_key) WHERE is_seed = TRUE;
    """)
    cur.execute("""
        INSERT INTO schema_version (version) VALUES (40)
        ON CONFLICT (version) DO NOTHING;
    """)
    print("[DB] v40 마이그레이션 완료 — screener_presets 확장 (시드 프리셋 대비)")
```

`run_migrations` 디스패치에 추가:

```python
    if current < 40:
        _migrate_to_v40(cur)
```

`shared/db/schema.py`:

```python
SCHEMA_VERSION = 40  # v40: screener_presets 확장 (시드 프리셋 대비)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_migration_v39_v40.py -v`
Expected: PASS (4 passed — Task 2의 2개 + Task 3의 2개)

- [ ] **Step 5: Commit**

```bash
git add shared/db/migrations/versions.py shared/db/schema.py tests/test_migration_v39_v40.py
git commit -m "feat(db): v40 마이그레이션 — screener_presets 확장 (시드 프리셋 대비)"
```

---

## Task 4: pykrx 단일 종목 fetcher

**Files:**
- Create: `analyzer/fundamentals_sync.py`
- Test: `tests/test_fundamentals_sync.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_fundamentals_sync.py
"""펀더멘털 sync — pykrx/yfinance 분기 fetcher 검증."""
from datetime import date
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest


def test_fetch_kr_returns_normalized_dict(monkeypatch):
    """pykrx 응답 → 표준화된 dict (per/pbr/eps/bps/dps/dividend_yield) 변환."""
    fake_df = pd.DataFrame({
        "BPS": [50000], "PER": [12.5], "PBR": [0.95],
        "EPS": [4000], "DIV": [3.2], "DPS": [1600],
    }, index=["005930"])
    monkeypatch.setattr(
        "pykrx.stock.get_market_fundamental_by_date",
        lambda from_d, to_d, ticker: fake_df,
    )
    from analyzer.fundamentals_sync import fetch_kr_fundamental
    out = fetch_kr_fundamental("005930", date(2026, 4, 25))
    assert out["per"] == 12.5
    assert out["pbr"] == 0.95
    assert out["eps"] == 4000
    assert out["bps"] == 50000
    assert out["dps"] == 1600
    assert out["dividend_yield"] == 3.2
    assert out["data_source"] == "pykrx"


def test_fetch_kr_handles_empty_dataframe(monkeypatch):
    """pykrx 빈 DataFrame (휴장일/조회 실패) → None."""
    monkeypatch.setattr(
        "pykrx.stock.get_market_fundamental_by_date",
        lambda from_d, to_d, ticker: pd.DataFrame(),
    )
    from analyzer.fundamentals_sync import fetch_kr_fundamental
    assert fetch_kr_fundamental("000000", date(2026, 4, 25)) is None


def test_fetch_kr_handles_pykrx_exception(monkeypatch):
    """pykrx 예외 → None (sync는 계속 진행)."""
    def _raise(*a, **kw):
        raise RuntimeError("pykrx network error")
    monkeypatch.setattr("pykrx.stock.get_market_fundamental_by_date", _raise)
    from analyzer.fundamentals_sync import fetch_kr_fundamental
    assert fetch_kr_fundamental("005930", date(2026, 4, 25)) is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_fundamentals_sync.py::test_fetch_kr_returns_normalized_dict -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analyzer.fundamentals_sync'`

- [ ] **Step 3: 구현 — fetch_kr_fundamental**

```python
# analyzer/fundamentals_sync.py
"""펀더멘털 PIT 시계열 수집 (B-Lite — pykrx KR + yfinance.info US).

매일 sync로 `stock_universe_fundamentals` 에 일별 row 누적. 결측 종목은 skip
(NULL row 기록하지 않음 — IS NOT NULL 필터로 latest 조회 단순화).

Spec: docs/superpowers/specs/2026-04-26-screener-investor-strategies-design.md §3
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from shared.logger import get_logger

try:
    from pykrx import stock as pykrx_stock
except ImportError:
    pykrx_stock = None

try:
    import yfinance as yf
except ImportError:
    yf = None


_log = get_logger("fundamentals_sync")


def _to_float(v) -> Optional[float]:
    """NaN/None/이상값 안전 변환."""
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def fetch_kr_fundamental(ticker: str, snapshot_date: date) -> Optional[dict]:
    """KRX 종목 단일 펀더 조회 (pykrx).

    Returns:
        {"per", "pbr", "eps", "bps", "dps", "dividend_yield", "data_source"}
        또는 None (조회 실패 / 빈 DataFrame).
    """
    if pykrx_stock is None:
        _log.warning("pykrx 미설치 — KR 펀더 sync 불가")
        return None

    yyyymmdd = snapshot_date.strftime("%Y%m%d")
    try:
        df = pykrx_stock.get_market_fundamental_by_date(yyyymmdd, yyyymmdd, ticker)
    except Exception as e:
        _log.debug(f"[{ticker}] pykrx 조회 실패: {e}")
        return None

    if df is None or df.empty:
        return None

    row = df.iloc[0]
    return {
        "per":            _to_float(row.get("PER")),
        "pbr":            _to_float(row.get("PBR")),
        "eps":            _to_float(row.get("EPS")),
        "bps":            _to_float(row.get("BPS")),
        "dps":            _to_float(row.get("DPS")),
        "dividend_yield": _to_float(row.get("DIV")),
        "data_source":    "pykrx",
    }
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_fundamentals_sync.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add analyzer/fundamentals_sync.py tests/test_fundamentals_sync.py
git commit -m "feat(fundamentals): pykrx 단일 종목 fetcher (KR 펀더 PIT)"
```

---

## Task 5: yfinance 단일 종목 fetcher

**Files:**
- Modify: `analyzer/fundamentals_sync.py`
- Modify: `tests/test_fundamentals_sync.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
# tests/test_fundamentals_sync.py 끝에 추가

def test_fetch_us_returns_normalized_dict(monkeypatch):
    fake_info = {
        "trailingPE": 25.4,
        "priceToBook": 8.1,
        "trailingEps": 6.13,
        "bookValue": 19.2,
        "dividendRate": 0.96,
        "dividendYield": 0.0058,   # yfinance returns ratio (0.58%)
    }
    fake_ticker = MagicMock()
    fake_ticker.info = fake_info
    monkeypatch.setattr("yfinance.Ticker", lambda t: fake_ticker)
    from analyzer.fundamentals_sync import fetch_us_fundamental
    out = fetch_us_fundamental("AAPL")
    assert out["per"] == 25.4
    assert out["pbr"] == 8.1
    assert out["eps"] == 6.13
    assert out["bps"] == 19.2
    assert out["dps"] == 0.96
    # dividend_yield는 % 단위로 정규화 (0.0058 → 0.58)
    assert abs(out["dividend_yield"] - 0.58) < 0.001
    assert out["data_source"] == "yfinance_info"


def test_fetch_us_handles_missing_keys(monkeypatch):
    """yfinance.info에 키가 일부 누락 → 해당 필드만 None."""
    fake_ticker = MagicMock()
    fake_ticker.info = {"trailingPE": 10.0}  # 나머지 키 없음
    monkeypatch.setattr("yfinance.Ticker", lambda t: fake_ticker)
    from analyzer.fundamentals_sync import fetch_us_fundamental
    out = fetch_us_fundamental("XXX")
    assert out["per"] == 10.0
    assert out["pbr"] is None
    assert out["eps"] is None
    assert out["dividend_yield"] is None
    assert out["data_source"] == "yfinance_info"


def test_fetch_us_handles_yfinance_exception(monkeypatch):
    def _raise(t):
        raise RuntimeError("yfinance throttled")
    monkeypatch.setattr("yfinance.Ticker", _raise)
    from analyzer.fundamentals_sync import fetch_us_fundamental
    assert fetch_us_fundamental("AAPL") is None


def test_fetch_us_handles_empty_info(monkeypatch):
    """info가 빈 dict → None (수집할 가치 없음)."""
    fake_ticker = MagicMock()
    fake_ticker.info = {}
    monkeypatch.setattr("yfinance.Ticker", lambda t: fake_ticker)
    from analyzer.fundamentals_sync import fetch_us_fundamental
    assert fetch_us_fundamental("AAPL") is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_fundamentals_sync.py::test_fetch_us_returns_normalized_dict -v`
Expected: FAIL — `ImportError: cannot import name 'fetch_us_fundamental'`

- [ ] **Step 3: 구현 — fetch_us_fundamental**

`analyzer/fundamentals_sync.py` 끝에 추가:

```python
def fetch_us_fundamental(ticker: str) -> Optional[dict]:
    """US 종목 단일 펀더 조회 (yfinance.info — '현재 스냅샷').

    매일 호출 시 그날 값을 누적하여 일별 PIT 구성. yfinance dividendYield는 ratio
    (0.0058 = 0.58%)로 반환되므로 표시 단위(%)로 정규화하여 저장.

    Returns:
        {"per", "pbr", "eps", "bps", "dps", "dividend_yield", "data_source"}
        또는 None (예외/빈 info).
    """
    if yf is None:
        _log.warning("yfinance 미설치 — US 펀더 sync 불가")
        return None
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception as e:
        _log.debug(f"[{ticker}] yfinance 조회 실패: {e}")
        return None
    if not info:
        return None

    div_yield_ratio = _to_float(info.get("dividendYield"))
    div_yield_pct = (div_yield_ratio * 100) if div_yield_ratio is not None else None

    out = {
        "per":            _to_float(info.get("trailingPE")),
        "pbr":            _to_float(info.get("priceToBook")),
        "eps":            _to_float(info.get("trailingEps")),
        "bps":            _to_float(info.get("bookValue")),
        "dps":            _to_float(info.get("dividendRate")),
        "dividend_yield": div_yield_pct,
        "data_source":    "yfinance_info",
    }
    # 모든 메트릭이 None이면 수집할 가치 없음 (사실상 빈 응답)
    if all(out[k] is None for k in ("per", "pbr", "eps", "bps", "dps", "dividend_yield")):
        return None
    return out
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_fundamentals_sync.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add analyzer/fundamentals_sync.py tests/test_fundamentals_sync.py
git commit -m "feat(fundamentals): yfinance.info 단일 종목 fetcher (US 펀더 PIT)"
```

---

## Task 6: UPSERT helper

**Files:**
- Modify: `analyzer/fundamentals_sync.py`
- Modify: `tests/test_fundamentals_sync.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
# tests/test_fundamentals_sync.py 끝에 추가
from datetime import date as _date


def test_upsert_executes_correct_sql():
    cur = MagicMock()
    rows = [
        {"ticker": "005930", "market": "KOSPI", "snapshot_date": _date(2026, 4, 25),
         "per": 12.5, "pbr": 0.95, "eps": 4000, "bps": 50000,
         "dps": 1600, "dividend_yield": 3.2, "data_source": "pykrx"},
    ]
    from analyzer.fundamentals_sync import upsert_fundamentals
    upsert_fundamentals(cur, rows)
    # execute_values 가 호출됐는지 확인 (psycopg2 패턴)
    assert cur.executemany.called or _executed_values_called(cur)


def _executed_values_called(cur):
    """execute_values 는 cur 자체에 묶이지 않으므로 별도 검증 — 여기선 cur.execute가 호출됐는지로 대체.

    실제 구현은 psycopg2.extras.execute_values 사용. 빈 rows 처리 + INSERT...ON CONFLICT 검증은
    test_upsert_skips_empty_rows + test_upsert_uses_on_conflict 로 분리.
    """
    return cur.execute.called


def test_upsert_skips_empty_rows():
    """빈 리스트 → execute 호출 없음."""
    cur = MagicMock()
    from analyzer.fundamentals_sync import upsert_fundamentals
    upsert_fundamentals(cur, [])
    assert not cur.execute.called
    assert not cur.executemany.called


def test_upsert_uses_on_conflict(monkeypatch):
    """SQL에 ON CONFLICT (ticker, market, snapshot_date) DO UPDATE 포함 검증."""
    captured = {}
    def fake_execute_values(cur, sql, rows, **kw):
        captured["sql"] = sql
        captured["rows"] = list(rows)
    monkeypatch.setattr(
        "analyzer.fundamentals_sync.execute_values",
        fake_execute_values,
    )
    cur = MagicMock()
    rows = [{
        "ticker": "AAPL", "market": "NASDAQ",
        "snapshot_date": _date(2026, 4, 25),
        "per": 25.4, "pbr": 8.1, "eps": 6.13,
        "bps": 19.2, "dps": 0.96, "dividend_yield": 0.58,
        "data_source": "yfinance_info",
    }]
    from analyzer.fundamentals_sync import upsert_fundamentals
    upsert_fundamentals(cur, rows)
    sql_upper = captured["sql"].upper()
    assert "INSERT INTO STOCK_UNIVERSE_FUNDAMENTALS" in sql_upper
    assert "ON CONFLICT (TICKER, MARKET, SNAPSHOT_DATE) DO UPDATE" in sql_upper
    assert len(captured["rows"]) == 1
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_fundamentals_sync.py::test_upsert_skips_empty_rows -v`
Expected: FAIL — `ImportError: cannot import name 'upsert_fundamentals'`

- [ ] **Step 3: 구현 — upsert_fundamentals**

`analyzer/fundamentals_sync.py` 상단 import 추가:

```python
from psycopg2.extras import execute_values
```

함수 추가 (파일 끝):

```python
_UPSERT_SQL = """
INSERT INTO stock_universe_fundamentals (
    ticker, market, snapshot_date,
    per, pbr, eps, bps, dps, dividend_yield,
    data_source
) VALUES %s
ON CONFLICT (ticker, market, snapshot_date) DO UPDATE SET
    per            = EXCLUDED.per,
    pbr            = EXCLUDED.pbr,
    eps            = EXCLUDED.eps,
    bps            = EXCLUDED.bps,
    dps            = EXCLUDED.dps,
    dividend_yield = EXCLUDED.dividend_yield,
    data_source    = EXCLUDED.data_source,
    fetched_at     = NOW()
"""


def upsert_fundamentals(cur, rows: list[dict]) -> None:
    """일괄 UPSERT. 빈 리스트는 no-op.

    각 row는 fetch_*_fundamental 결과 + ticker/market/snapshot_date 합본 dict.
    """
    if not rows:
        return
    values = [
        (
            r["ticker"], r["market"], r["snapshot_date"],
            r.get("per"), r.get("pbr"), r.get("eps"),
            r.get("bps"), r.get("dps"), r.get("dividend_yield"),
            r["data_source"],
        )
        for r in rows
    ]
    execute_values(cur, _UPSERT_SQL, values, page_size=500)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_fundamentals_sync.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add analyzer/fundamentals_sync.py tests/test_fundamentals_sync.py
git commit -m "feat(fundamentals): UPSERT helper — execute_values + ON CONFLICT"
```

---

## Task 7: 시장별 배치 sync 함수

**Files:**
- Modify: `analyzer/fundamentals_sync.py`
- Modify: `tests/test_fundamentals_sync.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
# tests/test_fundamentals_sync.py 끝에 추가

def test_sync_kr_market_iterates_tickers(monkeypatch):
    """KR 종목 리스트 → 각 종목 fetch → upsert 호출."""
    captured_rows = []

    def fake_fetch(ticker, snap_date):
        return {
            "per": 10.0, "pbr": 1.0, "eps": 100, "bps": 1000,
            "dps": 50, "dividend_yield": 2.0, "data_source": "pykrx",
        }

    def fake_upsert(cur, rows):
        captured_rows.extend(rows)

    monkeypatch.setattr("analyzer.fundamentals_sync.fetch_kr_fundamental", fake_fetch)
    monkeypatch.setattr("analyzer.fundamentals_sync.upsert_fundamentals", fake_upsert)

    cur = MagicMock()
    from analyzer.fundamentals_sync import sync_market_fundamentals
    n = sync_market_fundamentals(
        cur, market="KOSPI",
        tickers=["005930", "000660", "035420"],
        snapshot_date=_date(2026, 4, 25),
    )
    assert n == 3
    assert len(captured_rows) == 3
    assert all(r["snapshot_date"] == _date(2026, 4, 25) for r in captured_rows)
    assert all(r["market"] == "KOSPI" for r in captured_rows)
    assert {r["ticker"] for r in captured_rows} == {"005930", "000660", "035420"}


def test_sync_skips_missing_tickers(monkeypatch):
    """fetch가 None 반환한 종목은 upsert에 포함되지 않음."""
    captured_rows = []
    monkeypatch.setattr(
        "analyzer.fundamentals_sync.fetch_kr_fundamental",
        lambda t, d: None if t == "BAD" else {
            "per": 10, "pbr": 1, "eps": 100, "bps": 1000,
            "dps": 50, "dividend_yield": 2.0, "data_source": "pykrx",
        },
    )
    monkeypatch.setattr(
        "analyzer.fundamentals_sync.upsert_fundamentals",
        lambda cur, rows: captured_rows.extend(rows),
    )
    cur = MagicMock()
    from analyzer.fundamentals_sync import sync_market_fundamentals
    n = sync_market_fundamentals(cur, "KOSPI", ["005930", "BAD"], _date(2026, 4, 25))
    assert n == 1
    assert {r["ticker"] for r in captured_rows} == {"005930"}


def test_sync_us_market_uses_yfinance(monkeypatch):
    """market이 NASDAQ/NYSE면 fetch_us_fundamental 사용."""
    captured = []
    monkeypatch.setattr(
        "analyzer.fundamentals_sync.fetch_us_fundamental",
        lambda t: {
            "per": 25, "pbr": 8, "eps": 6, "bps": 19,
            "dps": 1, "dividend_yield": 0.58, "data_source": "yfinance_info",
        },
    )
    monkeypatch.setattr(
        "analyzer.fundamentals_sync.upsert_fundamentals",
        lambda cur, rows: captured.extend(rows),
    )
    # KR fetcher가 호출되면 안 됨
    monkeypatch.setattr(
        "analyzer.fundamentals_sync.fetch_kr_fundamental",
        lambda *a, **kw: pytest.fail("KR fetcher should NOT be called for US market"),
    )
    from analyzer.fundamentals_sync import sync_market_fundamentals
    n = sync_market_fundamentals(MagicMock(), "NASDAQ", ["AAPL", "MSFT"], _date(2026, 4, 25))
    assert n == 2
    assert all(r["data_source"] == "yfinance_info" for r in captured)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_fundamentals_sync.py::test_sync_kr_market_iterates_tickers -v`
Expected: FAIL — `ImportError: cannot import name 'sync_market_fundamentals'`

- [ ] **Step 3: 구현 — sync_market_fundamentals**

`analyzer/fundamentals_sync.py` 끝에 추가:

```python
_KR_MARKETS = {"KOSPI", "KOSDAQ", "KONEX"}
_US_MARKETS = {"NASDAQ", "NYSE", "AMEX"}


def sync_market_fundamentals(
    cur,
    market: str,
    tickers: list[str],
    snapshot_date: date,
) -> int:
    """단일 시장 일괄 sync. market에 따라 fetcher 자동 분기.

    Returns:
        UPSERT된 row 수 (결측 제외).
    """
    market_up = market.upper()
    if market_up in _KR_MARKETS:
        fetcher = lambda t: fetch_kr_fundamental(t, snapshot_date)
    elif market_up in _US_MARKETS:
        fetcher = fetch_us_fundamental
    else:
        _log.warning(f"[{market}] 지원하지 않는 시장 — skip")
        return 0

    rows: list[dict] = []
    for ticker in tickers:
        data = fetcher(ticker)
        if data is None:
            continue
        rows.append({
            **data,
            "ticker": ticker,
            "market": market_up,
            "snapshot_date": snapshot_date,
        })

    upsert_fundamentals(cur, rows)
    _log.info(f"[{market_up}] {snapshot_date} 펀더 sync — {len(rows)}/{len(tickers)} 종목")
    return len(rows)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_fundamentals_sync.py -v`
Expected: PASS (13 passed)

- [ ] **Step 5: Commit**

```bash
git add analyzer/fundamentals_sync.py tests/test_fundamentals_sync.py
git commit -m "feat(fundamentals): 시장별 배치 sync — KR/US 자동 분기 + 결측 skip"
```

---

## Task 8: `universe_sync.py --mode fundamentals` 통합

**Files:**
- Modify: `analyzer/universe_sync.py`
- Modify: `tests/test_fundamentals_sync.py`

- [ ] **Step 1: 기존 universe_sync.py 구조 확인**

Run: `grep -n "argparse\|add_argument.*mode\|args.mode\|def main" analyzer/universe_sync.py | head -30`
Expected: argparse 패턴 + `args.mode` 분기 found.

- [ ] **Step 2: 실패 테스트 추가**

```python
# tests/test_fundamentals_sync.py 끝에 추가

def test_run_fundamentals_sync_queries_universe(monkeypatch):
    """run_fundamentals_sync — stock_universe에서 활성 종목 시장별로 묶어 sync_market_fundamentals 호출."""
    fake_conn = MagicMock()
    fake_cur = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cur
    fake_cur.fetchall.return_value = [
        ("005930", "KOSPI"), ("000660", "KOSPI"),
        ("035420", "KOSDAQ"),
        ("AAPL", "NASDAQ"), ("MSFT", "NASDAQ"),
    ]

    monkeypatch.setattr(
        "analyzer.fundamentals_sync.get_connection",
        lambda cfg: fake_conn,
    )

    captured_calls = []
    def fake_sync(cur, market, tickers, snap):
        captured_calls.append((market, sorted(tickers)))
        return len(tickers)
    monkeypatch.setattr(
        "analyzer.fundamentals_sync.sync_market_fundamentals",
        fake_sync,
    )

    from analyzer.fundamentals_sync import run_fundamentals_sync
    from shared.config import DatabaseConfig, FundamentalsConfig
    total = run_fundamentals_sync(DatabaseConfig(), FundamentalsConfig())
    # 시장별로 한 번씩 호출
    by_market = {m: tk for m, tk in captured_calls}
    assert by_market["KOSPI"] == ["000660", "005930"]
    assert by_market["KOSDAQ"] == ["035420"]
    assert by_market["NASDAQ"] == ["AAPL", "MSFT"]
    assert total == 5


def test_run_fundamentals_sync_respects_disabled_flag(monkeypatch):
    """sync_enabled=False 면 즉시 0 반환 (DB 접속 안 함)."""
    monkeypatch.setattr(
        "analyzer.fundamentals_sync.get_connection",
        lambda cfg: pytest.fail("DB 접속이 호출되면 안 됨"),
    )
    from analyzer.fundamentals_sync import run_fundamentals_sync
    from shared.config import DatabaseConfig, FundamentalsConfig
    cfg = FundamentalsConfig()
    cfg.sync_enabled = False
    assert run_fundamentals_sync(DatabaseConfig(), cfg) == 0
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `pytest tests/test_fundamentals_sync.py::test_run_fundamentals_sync_queries_universe -v`
Expected: FAIL — `ImportError: cannot import name 'run_fundamentals_sync'`

- [ ] **Step 4: 구현 — run_fundamentals_sync + universe_sync CLI**

`analyzer/fundamentals_sync.py`에 추가 (상단 import + 함수):

```python
# 상단 import 부분에 추가
from datetime import datetime, timedelta, timezone

from shared.config import DatabaseConfig, FundamentalsConfig
from shared.db import get_connection

KST = timezone(timedelta(hours=9))


def _today_kst() -> date:
    return datetime.now(KST).date()


def run_fundamentals_sync(
    db_cfg: DatabaseConfig,
    cfg: FundamentalsConfig,
    snapshot_date: Optional[date] = None,
) -> int:
    """`stock_universe` 활성 종목을 시장별로 묶어 일괄 펀더 sync.

    Returns:
        UPSERT 총 row 수.
    """
    if not cfg.sync_enabled:
        _log.info("FUNDAMENTALS_SYNC_ENABLED=false — skip")
        return 0

    snap = snapshot_date or _today_kst()
    conn = get_connection(db_cfg)
    total = 0
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ticker, market FROM stock_universe
                WHERE listed = TRUE AND has_preferred = FALSE
            """)
            rows = cur.fetchall()
        # 시장별 그룹핑
        by_market: dict[str, list[str]] = {}
        for ticker, market in rows:
            by_market.setdefault(market.upper(), []).append(ticker)

        for market, tickers in by_market.items():
            with conn.cursor() as cur:
                n = sync_market_fundamentals(cur, market, tickers, snap)
                conn.commit()
                total += n
    finally:
        conn.close()

    _log.info(f"펀더 sync 완료 — 총 {total} row UPSERT (snapshot={snap})")
    return total
```

`analyzer/universe_sync.py` argparse 분기에 `--mode fundamentals` 추가. 기존 mode 처리 분기(예: `if args.mode == "ohlcv": ...`) 옆에:

```python
    elif args.mode == "fundamentals":
        from analyzer.fundamentals_sync import run_fundamentals_sync
        from shared.config import AppConfig
        app = AppConfig()
        run_fundamentals_sync(app.db, app.fundamentals)
```

`choices=` 리스트 (있다면) 에도 `"fundamentals"` 추가.

- [ ] **Step 5: 테스트 통과 확인**

Run: `pytest tests/test_fundamentals_sync.py -v`
Expected: PASS (15 passed)

- [ ] **Step 6: 수동 smoke test (선택, DB 있는 환경에서만)**

Run: `python -m analyzer.universe_sync --mode fundamentals --help`
Expected: argparse가 fundamentals를 인식 — usage 출력에 mode 옵션으로 표시.

- [ ] **Step 7: Commit**

```bash
git add analyzer/fundamentals_sync.py analyzer/universe_sync.py tests/test_fundamentals_sync.py
git commit -m "feat(fundamentals): universe_sync.py --mode fundamentals CLI 통합"
```

---

## Task 9: `tools/fundamentals_health_check.py`

**Files:**
- Create: `tools/fundamentals_health_check.py`
- Test: `tests/test_fundamentals_health_check.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_fundamentals_health_check.py
"""펀더 결측률 health check — 시장별 결측 비율 + 마지막 sync 시각."""
from datetime import datetime, date, timedelta, timezone
from unittest.mock import MagicMock


def test_compute_missing_rate_per_market():
    """stock_universe 활성 종목 vs 최근 7일 내 펀더 row 보유 종목 비교."""
    fake_conn = MagicMock()
    fake_cur = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cur
    fake_cur.fetchall.return_value = [
        ("KOSPI",  1000, 988, datetime(2026, 4, 25, 6, 35, tzinfo=timezone.utc)),
        ("KOSDAQ", 1500, 1490, datetime(2026, 4, 25, 6, 35, tzinfo=timezone.utc)),
        ("NASDAQ", 600, 598, datetime(2026, 4, 25, 6, 35, tzinfo=timezone.utc)),
        ("NYSE",   400, 0, None),  # 백필 미완 → 결측 100%
    ]
    from tools.fundamentals_health_check import compute_missing_rate
    out = compute_missing_rate(fake_conn)
    by_market = {r["market"]: r for r in out}
    assert abs(by_market["KOSPI"]["missing_pct"] - 1.2) < 0.01
    assert abs(by_market["KOSDAQ"]["missing_pct"] - 0.667) < 0.01
    assert by_market["NYSE"]["missing_pct"] == 100.0
    assert by_market["NYSE"]["last_fetched_at"] is None


def test_compute_missing_rate_zero_total_safe():
    """활성 종목 0개 시장 → division by zero 방지."""
    fake_conn = MagicMock()
    fake_cur = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cur
    fake_cur.fetchall.return_value = [("KONEX", 0, 0, None)]
    from tools.fundamentals_health_check import compute_missing_rate
    out = compute_missing_rate(fake_conn)
    assert out[0]["missing_pct"] == 0.0
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_fundamentals_health_check.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.fundamentals_health_check'`

- [ ] **Step 3: 구현**

```python
# tools/fundamentals_health_check.py
"""펀더 결측률 진단 도구.

`stock_universe`(활성+보통주) vs `stock_universe_fundamentals`(최근 7일 내) 비교 →
시장별 결측 비율 + 마지막 sync 시각 산출.

CLI:
    python -m tools.fundamentals_health_check
    # → 표 출력 + 임계 초과 시 exit code 1
"""
from __future__ import annotations

import sys
from typing import Optional

from shared.config import DatabaseConfig
from shared.db import get_connection
from shared.logger import get_logger

_log = get_logger("fundamentals_health_check")

# 시장별 결측률 임계 (% 초과 시 빨간 경고)
_MISSING_PCT_THRESHOLD = {"KOSPI": 5.0, "KOSDAQ": 5.0, "NASDAQ": 3.0, "NYSE": 3.0}


def compute_missing_rate(conn) -> list[dict]:
    """시장별 결측 비율.

    Returns:
        [{"market", "total", "with_fund", "missing_pct", "last_fetched_at"}, ...]
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                u.market,
                COUNT(*) AS total,
                COUNT(f.ticker) AS with_fund,
                MAX(f.fetched_at) AS last_fetched_at
            FROM stock_universe u
            LEFT JOIN LATERAL (
                SELECT ticker, fetched_at
                FROM stock_universe_fundamentals f
                WHERE f.ticker = u.ticker
                  AND f.market = u.market
                  AND f.snapshot_date >= CURRENT_DATE - 7
                ORDER BY snapshot_date DESC LIMIT 1
            ) f ON TRUE
            WHERE u.listed = TRUE AND u.has_preferred = FALSE
            GROUP BY u.market
            ORDER BY u.market
        """)
        rows = cur.fetchall()
    out = []
    for market, total, with_fund, last_at in rows:
        missing = (total - with_fund)
        pct = round((missing / total) * 100, 3) if total > 0 else 0.0
        out.append({
            "market": market,
            "total": int(total),
            "with_fund": int(with_fund),
            "missing_pct": pct,
            "last_fetched_at": last_at,
        })
    return out


def main(db_cfg: Optional[DatabaseConfig] = None) -> int:
    cfg = db_cfg or DatabaseConfig()
    conn = get_connection(cfg)
    try:
        results = compute_missing_rate(conn)
    finally:
        conn.close()

    print(f"{'시장':<10} {'활성종목':>8} {'펀더보유':>8} {'결측%':>8} {'마지막sync':>22}")
    exit_code = 0
    for r in results:
        last = str(r["last_fetched_at"]) if r["last_fetched_at"] else "(없음)"
        marker = ""
        threshold = _MISSING_PCT_THRESHOLD.get(r["market"], 10.0)
        if r["missing_pct"] > threshold:
            marker = " ⚠ 임계 초과"
            exit_code = 1
        print(f"{r['market']:<10} {r['total']:>8} {r['with_fund']:>8} "
              f"{r['missing_pct']:>7.2f}% {last:>22}{marker}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_fundamentals_health_check.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add tools/fundamentals_health_check.py tests/test_fundamentals_health_check.py
git commit -m "feat(tools): fundamentals_health_check — 시장별 펀더 결측률 진단"
```

---

## Task 10: systemd unit 파일 2개 생성

**Files:**
- Create: `deploy/systemd/investment-advisor-fundamentals.service`
- Create: `deploy/systemd/investment-advisor-fundamentals.timer`

테스트 없음 (정적 unit 파일). 기존 `universe-sync-price.service` / `.timer` 패턴 참조.

- [ ] **Step 1: 기존 unit 패턴 확인**

Run: `cat deploy/systemd/universe-sync-price.service deploy/systemd/universe-sync-price.timer`
Expected: `{{INSTALL_DIR}}` / `{{SYSTEM_USER}}` 같은 플레이스홀더 + `OnCalendar=` + `WorkingDirectory=` 패턴 확인.

- [ ] **Step 2: service 파일 생성**

```ini
# deploy/systemd/investment-advisor-fundamentals.service
[Unit]
Description=Investment Advisor — 펀더멘털 PIT 일별 sync (B-Lite)
Documentation=file://{{INSTALL_DIR}}/docs/superpowers/specs/2026-04-26-screener-investor-strategies-design.md
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User={{SYSTEM_USER}}
WorkingDirectory={{INSTALL_DIR}}
EnvironmentFile=-{{INSTALL_DIR}}/.env
ExecStart={{INSTALL_DIR}}/venv/bin/python -m analyzer.universe_sync --mode fundamentals
StandardOutput=journal
StandardError=journal
TimeoutStartSec=3600

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: timer 파일 생성**

```ini
# deploy/systemd/investment-advisor-fundamentals.timer
[Unit]
Description=Daily fundamentals sync — KST 06:35 (universe-sync-price 직후)
Documentation=file://{{INSTALL_DIR}}/docs/superpowers/specs/2026-04-26-screener-investor-strategies-design.md

[Timer]
OnCalendar=*-*-* 06:35:00 Asia/Seoul
Persistent=true
Unit=investment-advisor-fundamentals.service

[Install]
WantedBy=timers.target
```

- [ ] **Step 4: 파일 존재 확인**

Run: `ls -la deploy/systemd/investment-advisor-fundamentals.*`
Expected: `.service` + `.timer` 두 파일 존재.

- [ ] **Step 5: Commit**

```bash
git add deploy/systemd/investment-advisor-fundamentals.service deploy/systemd/investment-advisor-fundamentals.timer
git commit -m "feat(systemd): 펀더 sync oneshot service + KST 06:35 timer"
```

---

## Task 11: `admin_systemd.py` MANAGED_UNITS 등록

**Files:**
- Modify: `api/routes/admin_systemd.py` (line 26 `MANAGED_UNITS` 리스트에 추가)
- Test: `tests/test_admin_systemd_managed_units.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_admin_systemd_managed_units.py
"""펀더 sync unit이 MANAGED_UNITS에 등록되었는지 검증.

웹 UI에서 start/stop/journalctl 제어 가능하려면 화이트리스트에 들어야 함.
"""
from api.routes.admin_systemd import MANAGED_UNITS, get_unit


def test_fundamentals_unit_registered():
    unit = get_unit("fundamentals")
    assert unit is not None, "fundamentals unit not in MANAGED_UNITS"
    assert unit["service"] == "investment-advisor-fundamentals.service"
    assert unit["timer"] == "investment-advisor-fundamentals.timer"
    assert unit["self_protected"] is False, (
        "self_protected=True면 웹 UI에서 제어 불가 — 펀더 sync는 운영자가 수동 트리거 가능해야 함"
    )


def test_fundamentals_unit_has_descriptive_metadata():
    unit = get_unit("fundamentals")
    assert unit["label"], "label 비어 있음 — UI 카드 제목 누락"
    assert unit["description"], "description 비어 있음 — UI 부제 누락"
    assert "06:35" in unit["schedule"], "schedule 표기 누락 (KST 06:35)"


def test_no_duplicate_unit_keys():
    """MANAGED_UNITS의 key가 모두 unique."""
    keys = [u["key"] for u in MANAGED_UNITS]
    assert len(keys) == len(set(keys)), f"중복 key 발견: {keys}"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_admin_systemd_managed_units.py::test_fundamentals_unit_registered -v`
Expected: FAIL — `assert None is not None`

- [ ] **Step 3: 구현 — MANAGED_UNITS에 추가**

`api/routes/admin_systemd.py` line 90 (`briefing` unit 다음, 리스트 닫는 `]` 직전)에 추가:

```python
    {
        "key": "fundamentals", "category": "B", "label": "펀더멘털 PIT sync",
        "service": "investment-advisor-fundamentals.service",
        "timer": "investment-advisor-fundamentals.timer",
        "self_protected": False,
        "schedule": "매일 06:35 KST",
        "description": "stock_universe_fundamentals 일별 sync (pykrx KR + yfinance.info US, B-Lite)",
    },
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_admin_systemd_managed_units.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add api/routes/admin_systemd.py tests/test_admin_systemd_managed_units.py
git commit -m "feat(admin): fundamentals systemd unit MANAGED_UNITS 화이트리스트 등록"
```

---

## Task 12: `deploy/systemd/README.md` sudoers 화이트리스트 갱신

**Files:**
- Modify: `deploy/systemd/README.md`

테스트 없음 (운영 매뉴얼 문서). 기존 sudoers 예시 섹션 찾아서 펀더 unit 추가.

- [ ] **Step 1: 기존 README 구조 확인**

Run: `grep -n "sudoers\|systemctl start\|investment-advisor-analyzer" deploy/systemd/README.md | head -20`
Expected: sudoers 화이트리스트 예시 섹션 위치 확인. 기존 unit별로 `dzp ALL=(root) NOPASSWD: /bin/systemctl start <unit>` 형식 row 발견.

- [ ] **Step 2: 화이트리스트에 펀더 unit 추가**

기존 sudoers 예시 (`/etc/sudoers.d/investment-advisor-systemd`) 섹션을 `Read` 도구로 열어 정확한 위치 확인 후, 신규 unit 4줄 추가 (start/stop/restart/enable/disable + timer 동일):

기존 `briefing` 섹션 직후에 동일한 패턴으로 삽입. 예시 (실제 README의 포맷에 맞춰 줄바꿈/들여쓰기 조정):

```
# 펀더멘털 PIT sync (B-Lite)
dzp ALL=(root) NOPASSWD: /bin/systemctl start investment-advisor-fundamentals.service
dzp ALL=(root) NOPASSWD: /bin/systemctl stop investment-advisor-fundamentals.service
dzp ALL=(root) NOPASSWD: /bin/systemctl restart investment-advisor-fundamentals.service
dzp ALL=(root) NOPASSWD: /bin/systemctl enable investment-advisor-fundamentals.timer
dzp ALL=(root) NOPASSWD: /bin/systemctl disable investment-advisor-fundamentals.timer
dzp ALL=(root) NOPASSWD: /bin/systemctl start investment-advisor-fundamentals.timer
dzp ALL=(root) NOPASSWD: /bin/systemctl stop investment-advisor-fundamentals.timer
```

또한 README의 "관리 대상 unit 목록" 표가 있다면 펀더 sync 행 1개 추가 (label + schedule + 설명 — Task 11의 메타와 일치).

설치 절차 섹션에 unit 등록 명령 1줄 추가:

```bash
# 운영기 최초 설치 시
sudo cp deploy/systemd/investment-advisor-fundamentals.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now investment-advisor-fundamentals.timer
```

- [ ] **Step 3: 변경 확인**

Run: `grep "fundamentals" deploy/systemd/README.md`
Expected: 신규 unit 관련 7줄 이상 매칭.

- [ ] **Step 4: Commit**

```bash
git add deploy/systemd/README.md
git commit -m "docs(systemd): 펀더 sync unit sudoers 화이트리스트 + 설치 절차"
```

---

## Task 13: 통합 검증

테스트 X — 수동 smoke test + 전체 테스트 스위트 회귀 확인.

- [ ] **Step 1: 전체 펀더 관련 테스트 실행**

Run: `pytest tests/test_fundamentals_config.py tests/test_migration_v39_v40.py tests/test_fundamentals_sync.py tests/test_fundamentals_health_check.py tests/test_admin_systemd_managed_units.py -v`
Expected: 전체 PASS (대략 23개 테스트).

- [ ] **Step 2: 기존 테스트 회귀 확인**

Run: `pytest -q`
Expected: 기존 테스트 전체 통과 (FundamentalsConfig 추가가 AppConfig 시그니처 변경 외 영향 없음).

- [ ] **Step 3: CLI smoke test (DB 환경 있으면)**

Run: `python -m analyzer.universe_sync --mode fundamentals --help`
Expected: argparse가 fundamentals를 인식하는 usage 출력.

Run: `python -m tools.fundamentals_health_check`
Expected: 시장별 표 출력 (DB에 펀더 row 없으면 결측 100% 표시 — 정상).

- [ ] **Step 4: 운영기 배포 안내 (수동)**

운영기에서 다음 절차 수행:
```bash
cd /home/dzp/dzp-main/program/investment-advisor
git pull
source venv/bin/activate
python -c "from shared.db import init_db; from shared.config import DatabaseConfig; init_db(DatabaseConfig())"   # v39, v40 자동 적용
sudo cp deploy/systemd/investment-advisor-fundamentals.{service,timer} /etc/systemd/system/
sudo cp deploy/systemd/README.md  # 참조용
# /etc/sudoers.d/investment-advisor-systemd 에 README 의 신규 sudoers 행 7개 추가
sudo visudo -c -f /etc/sudoers.d/investment-advisor-systemd   # 문법 검증
sudo systemctl daemon-reload
sudo systemctl enable --now investment-advisor-fundamentals.timer
# 첫 sync 수동 실행 (KST 06:35 기다리지 않고)
sudo systemctl start investment-advisor-fundamentals.service
journalctl -u investment-advisor-fundamentals.service -f -n 100
```

- [ ] **Step 5: 첫 sync 후 결측률 확인**

Run: `python -m tools.fundamentals_health_check`
Expected: KOSPI/KOSDAQ 결측률 < 5%, NASDAQ 결측률 < 3% (5.4 임계). 임계 초과 시 yfinance throttling 의심 — 다음 sync까지 대기하거나 batch_size 조정.

---

## 완료 후 후속 plan

이 plan 완료 시점에서 펀더 데이터가 일별로 쌓이기 시작. 1~2주 누적 후 다음 plan 작성:
- **M3 plan**: `screener.py` CTE에 `fund_latest` / `ma50` / `ma150` / `low_52w_proximity` 추가, `/api/screener/run` 펀더 필터 7종.
- **M4 plan**: `analyzer/investor_strategies.py` 8종 + v41 시드 마이그레이션 + `/api/screener/strategies` + `/presets/{key}/clone`.
- **M5 plan**: Fundamental 탭 활성화 + Investors 탭 신설 + admin 운영 탭 통합.
- **M6 plan**: cross-check 룰 + 결측률 health 카드 + 운영 매뉴얼.
