# Foreign Flow Screener Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** KOSPI/KOSDAQ 종목별 외국인 보유율 + 일별 순매수액 PIT 시계열을 신규 테이블에 90일 백필 후 매일 sync, 스크리너 사이드패널에 추세 필터 5종 + 정렬 3종을 추가한다. 기관/개인 데이터도 같은 호출로 함께 수집해 데이터 레이어에 보존(미래 재백필 회피).

**Architecture:** `analyzer/fundamentals_sync.py` 와 동형 패턴의 신규 모듈 `analyzer/foreign_flow_sync.py` + 신규 테이블 `stock_universe_foreign_flow` (v44). `analyzer/universe_sync.py` 의 `--mode foreign` 으로 합류, systemd timer 06:40 KST 일배치. 스크리너는 신규 CTE 2개를 LEFT JOIN, 정렬 윈도우는 필터 라디오 윈도우와 자동 연동. UI 미노출 컬럼(`inst_net_buy_value`, `retail_net_buy_value`) 도 함께 저장해 미래 확장 비용 0.

**Tech Stack:** Python 3.10+, pykrx, psycopg2 + PostgreSQL, FastAPI, Jinja2 + inline JS, systemd, pytest.

**Spec:** [docs/superpowers/specs/2026-04-30-foreign-flow-screener-design.md](../specs/2026-04-30-foreign-flow-screener-design.md)

---

## Task 0: 사전 확인

**Files:** none

- [ ] **Step 1: 현재 SCHEMA_VERSION 이 43 인지 확인**

```bash
grep -n "SCHEMA_VERSION" shared/db/schema.py
```

Expected: `12:SCHEMA_VERSION = 43  # v43: ...`

- [ ] **Step 2: pykrx 설치 확인**

```bash
python -c "from pykrx import stock; print(stock.__name__)"
```

Expected: `pykrx.stock` (no error)

- [ ] **Step 3: 현재 모든 테스트 통과 확인 (baseline)**

```bash
pytest -q
```

Expected: 모든 테스트 PASS. 실패 있으면 STOP — 본 작업과 무관한 회귀를 만들지 않기 위해 먼저 정리.

---

## Task 1: DB 마이그레이션 v44 추가

**Files:**
- Modify: `shared/db/schema.py:12` — SCHEMA_VERSION 43 → 44
- Modify: `shared/db/migrations/versions.py` — `_migrate_to_v44()` 함수 추가
- Modify: `shared/db/migrations/__init__.py` — registry 등록

- [ ] **Step 1: 마이그레이션 등록 패턴 확인**

```bash
grep -n "_migrate_to_v43\|44\|43:" shared/db/migrations/__init__.py
```

기존 등록 위치를 파악 (registry dict 또는 if-chain).

- [ ] **Step 2: `_migrate_to_v44` 추가**

`shared/db/migrations/versions.py` 끝에 추가:

```python
def _migrate_to_v44(cur) -> None:
    """v44: stock_universe_foreign_flow — KRX 종목 투자자별 수급 PIT 시계열.

    pykrx 2종 API 일배치 수집:
      - get_exhaustion_rates_of_foreign_investment → foreign_ownership_pct
      - get_market_trading_value_by_date          → foreign/inst/retail net_buy_value

    v1 UI 는 외국인 컬럼만 노출, 기관/개인은 데이터 레이어에만 저장 (재백필 회피).

    Spec: docs/superpowers/specs/2026-04-30-foreign-flow-screener-design.md §3.2
    """
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stock_universe_foreign_flow (
            ticker                TEXT NOT NULL,
            market                TEXT NOT NULL,
            snapshot_date         DATE NOT NULL,
            foreign_ownership_pct NUMERIC(7,4),
            foreign_net_buy_value BIGINT,
            inst_net_buy_value    BIGINT,
            retail_net_buy_value  BIGINT,
            data_source           TEXT NOT NULL DEFAULT 'pykrx',
            fetched_at            TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (ticker, market, snapshot_date)
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_foreign_flow_latest
            ON stock_universe_foreign_flow(ticker, market, snapshot_date DESC);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_foreign_flow_date
            ON stock_universe_foreign_flow(snapshot_date);
    """)
    cur.execute("""
        INSERT INTO schema_version (version) VALUES (44)
        ON CONFLICT (version) DO NOTHING;
    """)
    print("[DB] v44 마이그레이션 완료 — stock_universe_foreign_flow")
```

- [ ] **Step 3: registry/체인에 등록**

`shared/db/migrations/__init__.py` 의 기존 `_migrate_to_v43` 등록 부분과 동일 형식으로 v44 추가. 정확한 위치는 Step 1 결과로 확정. 예시 (registry dict 패턴):

```python
from shared.db.migrations.versions import (
    ...,
    _migrate_to_v43,
    _migrate_to_v44,
)

MIGRATIONS = {
    ...,
    43: _migrate_to_v43,
    44: _migrate_to_v44,
}
```

- [ ] **Step 4: SCHEMA_VERSION 갱신**

`shared/db/schema.py:12`:
```python
SCHEMA_VERSION = 44  # v44: stock_universe_foreign_flow (KRX 외국인/기관/개인 수급 PIT)
```

- [ ] **Step 5: 마이그레이션 단위 테스트**

`tests/test_migrations.py` 가 있다면 v43 → v44 마이그레이션 적용 후 테이블 존재 확인 케이스를 추가. 없으면 새로 만들지 말고 다음 단계 (sync 테스트)에서 통합 검증.

수동 검증:
```bash
python -c "from shared.db import init_db; from shared.config import DatabaseConfig; init_db(DatabaseConfig())"
psql -d investment_advisor -c "\d stock_universe_foreign_flow"
```

Expected: 8개 컬럼 (ticker/market/snapshot_date/foreign_ownership_pct/foreign_net_buy_value/inst_net_buy_value/retail_net_buy_value/data_source/fetched_at).

- [ ] **Step 6: Commit**

```bash
git add shared/db/schema.py shared/db/migrations/versions.py shared/db/migrations/__init__.py
git commit -m "feat(db): v44 마이그레이션 — stock_universe_foreign_flow PIT 테이블"
```

---

## Task 2: ForeignFlowConfig + .env.example

**Files:**
- Modify: `shared/config.py` — `ForeignFlowConfig` dataclass 추가, `AppConfig.foreign_flow` 노출
- Modify: `.env.example` — 신규 환경변수 7종 추가
- Test: `tests/test_foreign_flow_config.py` (신규)

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_foreign_flow_config.py`:

```python
"""ForeignFlowConfig 환경변수 파싱 테스트."""
import os
from unittest.mock import patch

from shared.config import AppConfig, ForeignFlowConfig


def test_default_values():
    """미설정 시 default 값 적용."""
    with patch.dict(os.environ, {}, clear=False):
        for k in [
            "FOREIGN_FLOW_SYNC_ENABLED", "FOREIGN_FLOW_RETENTION_DAYS",
            "FOREIGN_FLOW_DELISTED_RETENTION_DAYS", "FOREIGN_FLOW_MAX_CONSECUTIVE_FAILURES",
            "FOREIGN_FLOW_STALENESS_DAYS",
            "FOREIGN_FLOW_MISSING_THRESHOLD_KOSPI", "FOREIGN_FLOW_MISSING_THRESHOLD_KOSDAQ",
        ]:
            os.environ.pop(k, None)
        cfg = ForeignFlowConfig()
    assert cfg.sync_enabled is True
    assert cfg.retention_days == 400
    assert cfg.delisted_retention_days == 200
    assert cfg.max_consecutive_failures == 50
    assert cfg.staleness_days == 2
    assert cfg.missing_threshold_kospi == 5.0
    assert cfg.missing_threshold_kosdaq == 10.0


def test_env_override():
    """환경변수 설정 시 override."""
    overrides = {
        "FOREIGN_FLOW_SYNC_ENABLED": "false",
        "FOREIGN_FLOW_RETENTION_DAYS": "180",
        "FOREIGN_FLOW_MISSING_THRESHOLD_KOSPI": "3.5",
    }
    with patch.dict(os.environ, overrides):
        cfg = ForeignFlowConfig()
    assert cfg.sync_enabled is False
    assert cfg.retention_days == 180
    assert cfg.missing_threshold_kospi == 3.5


def test_missing_pct_threshold_dispatch():
    """시장별 임계 조회 — 미정의 시장은 fallback."""
    cfg = ForeignFlowConfig()
    assert cfg.missing_pct_threshold("KOSPI") == cfg.missing_threshold_kospi
    assert cfg.missing_pct_threshold("KOSDAQ") == cfg.missing_threshold_kosdaq
    assert cfg.missing_pct_threshold("NASDAQ") == 100.0  # KRX 외 = 자연 제외 의미


def test_app_config_exposes_foreign_flow():
    cfg = AppConfig()
    assert isinstance(cfg.foreign_flow, ForeignFlowConfig)
```

- [ ] **Step 2: 테스트 실행 — FAIL 확인**

```bash
pytest tests/test_foreign_flow_config.py -v
```

Expected: ImportError or AttributeError on `ForeignFlowConfig` / `AppConfig.foreign_flow`.

- [ ] **Step 3: `ForeignFlowConfig` 추가**

`shared/config.py` 의 `FundamentalsConfig` 정의 직후 (line 298 아래) 추가:

```python
@dataclass
class ForeignFlowConfig:
    """외국인/기관/개인 수급 PIT 시계열 수집 설정 (KRX 한정).

    `stock_universe_foreign_flow` 테이블의 수집·보존 정책.
    Spec: docs/superpowers/specs/2026-04-30-foreign-flow-screener-design.md §3.5
    """
    sync_enabled: bool = field(
        default_factory=lambda: _env_bool("FOREIGN_FLOW_SYNC_ENABLED", True)
    )
    retention_days: int = field(
        default_factory=lambda: int(os.getenv("FOREIGN_FLOW_RETENTION_DAYS", "400"))
    )
    delisted_retention_days: int = field(
        default_factory=lambda: int(os.getenv("FOREIGN_FLOW_DELISTED_RETENTION_DAYS", "200"))
    )
    max_consecutive_failures: int = field(
        default_factory=lambda: int(os.getenv("FOREIGN_FLOW_MAX_CONSECUTIVE_FAILURES", "50"))
    )
    staleness_days: int = field(
        default_factory=lambda: int(os.getenv("FOREIGN_FLOW_STALENESS_DAYS", "2"))
    )
    missing_threshold_kospi: float = field(
        default_factory=lambda: float(os.getenv("FOREIGN_FLOW_MISSING_THRESHOLD_KOSPI", "5.0"))
    )
    missing_threshold_kosdaq: float = field(
        default_factory=lambda: float(os.getenv("FOREIGN_FLOW_MISSING_THRESHOLD_KOSDAQ", "10.0"))
    )

    def missing_pct_threshold(self, market: str) -> float:
        """시장별 결측률 임계 조회. KRX 외 시장은 100.0 fallback (사실상 무제한 = 검증 제외)."""
        table = {
            "KOSPI": self.missing_threshold_kospi,
            "KOSDAQ": self.missing_threshold_kosdaq,
        }
        return table.get(market.upper(), 100.0)
```

`AppConfig` 클래스 (line ~399) 에 필드 추가:

```python
foreign_flow: ForeignFlowConfig = field(default_factory=ForeignFlowConfig)
```

- [ ] **Step 4: 테스트 PASS 확인**

```bash
pytest tests/test_foreign_flow_config.py -v
```

Expected: 4 PASS.

- [ ] **Step 5: `.env.example` 갱신**

`.env.example` 끝에 추가:

```
# ── 외국인/기관/개인 수급 PIT (v44, KRX 한정) ─────────────────
FOREIGN_FLOW_SYNC_ENABLED=true
FOREIGN_FLOW_RETENTION_DAYS=400
FOREIGN_FLOW_DELISTED_RETENTION_DAYS=200
FOREIGN_FLOW_MAX_CONSECUTIVE_FAILURES=50
FOREIGN_FLOW_STALENESS_DAYS=2
FOREIGN_FLOW_MISSING_THRESHOLD_KOSPI=5.0
FOREIGN_FLOW_MISSING_THRESHOLD_KOSDAQ=10.0
```

- [ ] **Step 6: Commit**

```bash
git add shared/config.py .env.example tests/test_foreign_flow_config.py
git commit -m "feat(config): ForeignFlowConfig — sync 토글/retention/임계 환경변수"
```

---

## Task 3: foreign_flow_sync.py — fetch 함수

**Files:**
- Create: `analyzer/foreign_flow_sync.py`
- Test: `tests/test_foreign_flow_sync.py` (신규)

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_foreign_flow_sync.py`:

```python
"""foreign_flow_sync — pykrx 호출 + 컬럼 매핑 + 가드 테스트."""
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd

from analyzer import foreign_flow_sync


def _ownership_df():
    """get_exhaustion_rates_of_foreign_investment 가 반환할 모양."""
    df = pd.DataFrame(
        {"한도수량": [10, 20], "보유수량": [5, 12], "지분율": [50.0, 60.0]},
        index=pd.to_datetime(["2026-04-28", "2026-04-29"]),
    )
    return df


def _trading_value_df():
    """get_market_trading_value_by_date 가 반환할 모양."""
    df = pd.DataFrame(
        {
            "외국인합계": [100_000_000, -50_000_000],
            "기관합계":   [200_000_000,  30_000_000],
            "개인":       [-300_000_000, 20_000_000],
        },
        index=pd.to_datetime(["2026-04-28", "2026-04-29"]),
    )
    return df


def test_fetch_kr_investor_flow_happy_path():
    """두 API 모두 성공 → 영업일별 row 생성, 모든 컬럼 채워짐."""
    with patch.object(foreign_flow_sync, "_check_pykrx", return_value=True), \
         patch.object(foreign_flow_sync, "pykrx_stock") as mock_pykrx:
        mock_pykrx.get_exhaustion_rates_of_foreign_investment.return_value = _ownership_df()
        mock_pykrx.get_market_trading_value_by_date.return_value = _trading_value_df()

        rows = foreign_flow_sync.fetch_kr_investor_flow(
            "005930", date(2026, 4, 28), date(2026, 4, 29)
        )

    assert len(rows) == 2
    assert rows[0]["snapshot_date"] == date(2026, 4, 28)
    assert rows[0]["foreign_ownership_pct"] == 50.0
    assert rows[0]["foreign_net_buy_value"] == 100_000_000
    assert rows[0]["inst_net_buy_value"]    == 200_000_000
    assert rows[0]["retail_net_buy_value"]  == -300_000_000
    assert rows[0]["data_source"] == "pykrx"


def test_fetch_kr_investor_flow_pykrx_disabled():
    """pykrx 비활성 시 빈 리스트."""
    with patch.object(foreign_flow_sync, "_check_pykrx", return_value=False):
        rows = foreign_flow_sync.fetch_kr_investor_flow(
            "005930", date(2026, 4, 28), date(2026, 4, 29)
        )
    assert rows == []


def test_fetch_kr_investor_flow_partial_success():
    """ownership 만 성공, trading_value 빈 응답 → row 는 생성되지만 net_buy 컬럼 NULL."""
    with patch.object(foreign_flow_sync, "_check_pykrx", return_value=True), \
         patch.object(foreign_flow_sync, "pykrx_stock") as mock_pykrx:
        mock_pykrx.get_exhaustion_rates_of_foreign_investment.return_value = _ownership_df()
        mock_pykrx.get_market_trading_value_by_date.return_value = pd.DataFrame()

        rows = foreign_flow_sync.fetch_kr_investor_flow(
            "005930", date(2026, 4, 28), date(2026, 4, 29)
        )

    assert len(rows) == 2
    assert rows[0]["foreign_ownership_pct"] == 50.0
    assert rows[0]["foreign_net_buy_value"] is None
    assert rows[0]["inst_net_buy_value"]    is None
    assert rows[0]["retail_net_buy_value"]  is None


def test_fetch_kr_investor_flow_both_empty():
    """두 API 모두 빈 응답 → 빈 리스트."""
    with patch.object(foreign_flow_sync, "_check_pykrx", return_value=True), \
         patch.object(foreign_flow_sync, "pykrx_stock") as mock_pykrx:
        mock_pykrx.get_exhaustion_rates_of_foreign_investment.return_value = pd.DataFrame()
        mock_pykrx.get_market_trading_value_by_date.return_value = pd.DataFrame()

        rows = foreign_flow_sync.fetch_kr_investor_flow(
            "005930", date(2026, 4, 28), date(2026, 4, 29)
        )
    assert rows == []


def test_fetch_kr_investor_flow_non_korean_ticker():
    """비-숫자 티커 (US 종목 등) → 빈 리스트, pykrx 호출 안 함."""
    with patch.object(foreign_flow_sync, "_check_pykrx", return_value=True), \
         patch.object(foreign_flow_sync, "pykrx_stock") as mock_pykrx:
        rows = foreign_flow_sync.fetch_kr_investor_flow(
            "AAPL", date(2026, 4, 28), date(2026, 4, 29)
        )
    assert rows == []
    mock_pykrx.get_exhaustion_rates_of_foreign_investment.assert_not_called()
```

- [ ] **Step 2: 테스트 실행 — FAIL 확인**

```bash
pytest tests/test_foreign_flow_sync.py -v
```

Expected: ImportError on `analyzer.foreign_flow_sync`.

- [ ] **Step 3: `analyzer/foreign_flow_sync.py` 생성**

```python
"""KRX 투자자별 수급 PIT 시계열 수집 (외국인 + 기관 + 개인).

매일 KST 06:40 systemd timer 가 호출. 1일 sync 또는 N일 백필 모두 지원.
v1 UI 는 외국인 컬럼만 노출, 기관/개인은 데이터 레이어에만 보존 (재백필 회피).

Spec: docs/superpowers/specs/2026-04-30-foreign-flow-screener-design.md
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from psycopg2.extras import execute_values

from shared.config import DatabaseConfig, ForeignFlowConfig
from shared.db import get_connection
from shared.logger import get_logger
from analyzer.stock_data import _check_pykrx, _safe_pykrx_call

try:
    from pykrx import stock as pykrx_stock
except ImportError:
    pykrx_stock = None


_log = get_logger("foreign_flow_sync")
KST = timezone(timedelta(hours=9))

_KR_MARKETS = ("KOSPI", "KOSDAQ")


def _today_kst() -> date:
    return datetime.now(KST).date()


def _pick_column(df, *candidates: str):
    """DataFrame 에서 후보 이름 중 첫 매칭 컬럼을 반환. 없으면 None."""
    for c in candidates:
        if c in df.columns:
            return c
    # 부분 매칭 (예: "외국인합계" vs "외국인" only)
    for c in df.columns:
        s = str(c)
        for cand in candidates:
            if cand in s:
                return c
    return None


def _to_int_or_none(v):
    try:
        if v is None:
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def _to_float_or_none(v):
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_kr_investor_flow(
    ticker: str, start_date: date, end_date: date
) -> list[dict]:
    """단일 KRX 종목 N일 일괄 수집.

    pykrx 두 API 호출:
      - get_exhaustion_rates_of_foreign_investment(start, end, ticker) → ownership_pct
      - get_market_trading_value_by_date(start, end, ticker) → foreign/inst/retail net_buy

    영업일별 row 생성. 한 API 만 성공해도 다른 컬럼은 NULL 로 row 보존.
    한 API 도 성공 못 하면 빈 리스트.

    Args:
        ticker: KRX 6자리 종목코드 (비-숫자면 빈 리스트 반환).
        start_date, end_date: 수집 범위 (inclusive).

    Returns:
        [{ticker, market, snapshot_date, foreign_ownership_pct,
          foreign_net_buy_value, inst_net_buy_value, retail_net_buy_value,
          data_source}, ...]
        market 은 호출자가 채움 (이 함수에선 미설정 — 하위 호환).
    """
    if not _check_pykrx():
        return []
    raw = ticker.strip().upper()
    if not raw.isdigit():
        return []

    start_str = start_date.strftime("%Y%m%d")
    end_str = end_date.strftime("%Y%m%d")

    # 1) 외국인 보유율 시계열
    own_df = _safe_pykrx_call(
        pykrx_stock.get_exhaustion_rates_of_foreign_investment,
        start_str, end_str, raw,
    )

    # 2) 투자자별 거래대금 (외국인/기관/개인 같이 옴)
    tv_df = _safe_pykrx_call(
        pykrx_stock.get_market_trading_value_by_date,
        start_str, end_str, raw,
    )

    own_empty = own_df is None or own_df.empty
    tv_empty = tv_df is None or tv_df.empty
    if own_empty and tv_empty:
        return []

    # 컬럼 매핑
    own_col = None if own_empty else _pick_column(own_df, "지분율", "보유비중")
    f_col = None if tv_empty else _pick_column(tv_df, "외국인합계", "외국인")
    i_col = None if tv_empty else _pick_column(tv_df, "기관합계", "기관")
    r_col = None if tv_empty else _pick_column(tv_df, "개인")

    # snapshot_date set 합집합
    own_dates: set[date] = set()
    tv_dates: set[date] = set()
    if not own_empty and own_col:
        own_dates = {pd_to_date(idx) for idx in own_df.index}
    if not tv_empty and f_col:
        tv_dates = {pd_to_date(idx) for idx in tv_df.index}
    all_dates = sorted(own_dates | tv_dates)

    rows: list[dict] = []
    for d in all_dates:
        if d is None:
            continue
        own_val = None
        if not own_empty and own_col and d in own_dates:
            try:
                own_val = _to_float_or_none(own_df.loc[own_df.index.normalize() == _to_pd_ts(d), own_col].iloc[0])
            except Exception:
                own_val = None
        f_val = i_val = r_val = None
        if not tv_empty and d in tv_dates:
            try:
                row = tv_df.loc[tv_df.index.normalize() == _to_pd_ts(d)].iloc[0]
                f_val = _to_int_or_none(row[f_col]) if f_col else None
                i_val = _to_int_or_none(row[i_col]) if i_col else None
                r_val = _to_int_or_none(row[r_col]) if r_col else None
            except Exception:
                pass

        # 모든 컬럼 None 이면 row 건너뜀 (의미 없음)
        if own_val is None and f_val is None and i_val is None and r_val is None:
            continue

        rows.append({
            "ticker": raw,
            "snapshot_date": d,
            "foreign_ownership_pct": own_val,
            "foreign_net_buy_value": f_val,
            "inst_net_buy_value": i_val,
            "retail_net_buy_value": r_val,
            "data_source": "pykrx",
        })
    return rows


def pd_to_date(idx) -> Optional[date]:
    """pandas Timestamp / datetime / str → date. 실패 시 None."""
    try:
        if hasattr(idx, "date"):
            return idx.date()
        return datetime.strptime(str(idx)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _to_pd_ts(d: date):
    """date → pandas Timestamp (DataFrame 인덱스 매칭용)."""
    import pandas as pd
    return pd.Timestamp(d)
```

- [ ] **Step 4: 테스트 PASS 확인**

```bash
pytest tests/test_foreign_flow_sync.py -v
```

Expected: 5 PASS.

만약 pandas 인덱스 비교 관련 문제 발생 시: `_to_pd_ts` / `pd_to_date` 의 timezone naive/aware 정합성 점검. 테스트의 `pd.to_datetime(...)` 는 naive 이므로 fetch 함수도 naive 비교만 하도록.

- [ ] **Step 5: Commit**

```bash
git add analyzer/foreign_flow_sync.py tests/test_foreign_flow_sync.py
git commit -m "feat(analyzer): foreign_flow_sync.fetch_kr_investor_flow — pykrx 2종 API 통합"
```

---

## Task 4: foreign_flow_sync.py — UPSERT + 시장 sync

**Files:**
- Modify: `analyzer/foreign_flow_sync.py` — `upsert_investor_flow`, `sync_market_investor_flow` 추가
- Modify: `tests/test_foreign_flow_sync.py` — 테스트 추가

- [ ] **Step 1: 실패 테스트 추가**

`tests/test_foreign_flow_sync.py` 에 추가:

```python
def test_upsert_investor_flow_executes_values():
    """upsert_investor_flow 가 execute_values 로 일괄 INSERT ... ON CONFLICT 실행."""
    from analyzer.foreign_flow_sync import upsert_investor_flow

    cur = MagicMock()
    rows = [
        {
            "ticker": "005930", "market": "KOSPI", "snapshot_date": date(2026, 4, 28),
            "foreign_ownership_pct": 51.5, "foreign_net_buy_value": 100,
            "inst_net_buy_value": 200, "retail_net_buy_value": -300,
            "data_source": "pykrx",
        },
        {
            "ticker": "035720", "market": "KOSPI", "snapshot_date": date(2026, 4, 28),
            "foreign_ownership_pct": 30.0, "foreign_net_buy_value": -50,
            "inst_net_buy_value": 0, "retail_net_buy_value": 50,
            "data_source": "pykrx",
        },
    ]
    with patch("analyzer.foreign_flow_sync.execute_values") as mock_exec:
        upsert_investor_flow(cur, rows)
    assert mock_exec.called
    args = mock_exec.call_args
    sql = args[0][1]
    assert "INSERT INTO stock_universe_foreign_flow" in sql
    assert "ON CONFLICT (ticker, market, snapshot_date) DO UPDATE" in sql
    assert "inst_net_buy_value" in sql and "retail_net_buy_value" in sql


def test_upsert_investor_flow_empty_noop():
    from analyzer.foreign_flow_sync import upsert_investor_flow
    cur = MagicMock()
    with patch("analyzer.foreign_flow_sync.execute_values") as mock_exec:
        upsert_investor_flow(cur, [])
    mock_exec.assert_not_called()


def test_sync_market_investor_flow_skips_failed_tickers():
    """fetch 가 빈 리스트 반환하는 종목은 skip, 성공한 종목만 row 누적."""
    from analyzer.foreign_flow_sync import sync_market_investor_flow

    def _fake_fetch(ticker, start, end):
        if ticker == "005930":
            return [{
                "ticker": "005930", "snapshot_date": date(2026, 4, 28),
                "foreign_ownership_pct": 51.5, "foreign_net_buy_value": 100,
                "inst_net_buy_value": 200, "retail_net_buy_value": -300,
                "data_source": "pykrx",
            }]
        return []

    cur = MagicMock()
    with patch("analyzer.foreign_flow_sync.fetch_kr_investor_flow", side_effect=_fake_fetch), \
         patch("analyzer.foreign_flow_sync.execute_values") as mock_exec:
        n = sync_market_investor_flow(
            cur, "KOSPI", ["005930", "FAILED1"], date(2026, 4, 28), date(2026, 4, 28),
            max_workers=2,
        )
    assert n == 1
    # market 이 row 에 채워졌는지 확인
    values = mock_exec.call_args[0][2]
    assert values[0][1] == "KOSPI"
```

- [ ] **Step 2: 테스트 실행 — FAIL 확인**

```bash
pytest tests/test_foreign_flow_sync.py::test_upsert_investor_flow_executes_values -v
```

Expected: ImportError on `upsert_investor_flow`.

- [ ] **Step 3: `analyzer/foreign_flow_sync.py` 에 추가**

```python
_UPSERT_SQL = """
INSERT INTO stock_universe_foreign_flow (
    ticker, market, snapshot_date,
    foreign_ownership_pct, foreign_net_buy_value,
    inst_net_buy_value, retail_net_buy_value,
    data_source
) VALUES %s
ON CONFLICT (ticker, market, snapshot_date) DO UPDATE SET
    foreign_ownership_pct = EXCLUDED.foreign_ownership_pct,
    foreign_net_buy_value = EXCLUDED.foreign_net_buy_value,
    inst_net_buy_value    = EXCLUDED.inst_net_buy_value,
    retail_net_buy_value  = EXCLUDED.retail_net_buy_value,
    data_source           = EXCLUDED.data_source,
    fetched_at            = NOW()
"""


def upsert_investor_flow(cur, rows: list[dict]) -> None:
    """일괄 UPSERT. 빈 리스트는 no-op."""
    if not rows:
        return
    values = [
        (
            r["ticker"], r["market"], r["snapshot_date"],
            r.get("foreign_ownership_pct"),
            r.get("foreign_net_buy_value"),
            r.get("inst_net_buy_value"),
            r.get("retail_net_buy_value"),
            r.get("data_source") or "pykrx",
        )
        for r in rows
    ]
    execute_values(cur, _UPSERT_SQL, values, page_size=500)


def sync_market_investor_flow(
    cur,
    market: str,
    tickers: list[str],
    start_date: date,
    end_date: date,
    *,
    max_workers: int = 4,
    max_consecutive_failures: int = 0,
) -> int:
    """단일 시장 일괄 sync. 병렬 fetch → 한꺼번에 UPSERT.

    Returns: UPSERT 된 row 수 (= 성공 종목별 영업일 수 합).
    """
    started = time.time()
    market_up = market.upper()
    if market_up not in _KR_MARKETS:
        _log.warning(f"[{market}] KRX 외 시장 — skip")
        return 0

    all_rows: list[dict] = []
    consecutive_failures = 0
    aborted = False

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {
            pool.submit(fetch_kr_investor_flow, t, start_date, end_date): t
            for t in tickers
        }
        for fut in as_completed(future_map):
            t = future_map[fut]
            try:
                rows = fut.result() or []
            except Exception as e:
                _log.debug(f"[{t}] fetch 예외: {e}")
                rows = []
            if not rows:
                consecutive_failures += 1
                if max_consecutive_failures > 0 and consecutive_failures >= max_consecutive_failures:
                    _log.warning(
                        f"[{market_up}] 연속 {max_consecutive_failures}건 실패 — 조기 종료"
                    )
                    aborted = True
                    break
                continue
            consecutive_failures = 0
            for r in rows:
                r["market"] = market_up
                all_rows.append(r)

    upsert_investor_flow(cur, all_rows)
    duration = time.time() - started
    abort_marker = " (early-abort)" if aborted else ""
    success_tickers = len({r["ticker"] for r in all_rows})
    _log.info(
        f"[{market_up}] {start_date}~{end_date} 수급 sync — "
        f"{len(all_rows)} row / {success_tickers}/{len(tickers)} 종목{abort_marker} / {duration:.1f}s"
    )
    return len(all_rows)
```

- [ ] **Step 4: 테스트 PASS 확인**

```bash
pytest tests/test_foreign_flow_sync.py -v
```

Expected: 8 PASS (5 from Task 3 + 3 new).

- [ ] **Step 5: Commit**

```bash
git add analyzer/foreign_flow_sync.py tests/test_foreign_flow_sync.py
git commit -m "feat(analyzer): foreign_flow_sync UPSERT + 시장 단위 병렬 sync"
```

---

## Task 5: foreign_flow_sync.py — entrypoint `run_foreign_flow_sync`

**Files:**
- Modify: `analyzer/foreign_flow_sync.py` — `run_foreign_flow_sync` 추가
- Modify: `tests/test_foreign_flow_sync.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
def test_run_foreign_flow_sync_skips_when_disabled():
    from analyzer.foreign_flow_sync import run_foreign_flow_sync
    db_cfg = MagicMock()
    cfg = MagicMock(sync_enabled=False, max_consecutive_failures=0)
    result = run_foreign_flow_sync(db_cfg, cfg=cfg)
    assert result["total"] == 0


def test_run_foreign_flow_sync_calls_market_sync():
    from analyzer.foreign_flow_sync import run_foreign_flow_sync

    db_cfg = MagicMock()
    cfg = MagicMock(sync_enabled=True, max_consecutive_failures=50)

    fake_conn = MagicMock()
    fake_cur = MagicMock()
    fake_cur.fetchall.return_value = [
        ("005930", "KOSPI"), ("035720", "KOSPI"), ("247540", "KOSDAQ"),
    ]
    fake_conn.cursor.return_value.__enter__.return_value = fake_cur

    with patch("analyzer.foreign_flow_sync.get_connection", return_value=fake_conn), \
         patch("analyzer.foreign_flow_sync.sync_market_investor_flow", return_value=3) as mock_sync:
        result = run_foreign_flow_sync(
            db_cfg, cfg=cfg, snapshot_date=date(2026, 4, 28), backfill_days=0
        )

    # KOSPI + KOSDAQ 각각 1번 호출
    assert mock_sync.call_count == 2
    assert result["total"] == 6  # 2 markets × 3 rows


def test_run_foreign_flow_sync_backfill_days_expands_range():
    from analyzer.foreign_flow_sync import run_foreign_flow_sync

    cfg = MagicMock(sync_enabled=True, max_consecutive_failures=0)
    fake_conn = MagicMock()
    fake_cur = MagicMock()
    fake_cur.fetchall.return_value = [("005930", "KOSPI")]
    fake_conn.cursor.return_value.__enter__.return_value = fake_cur

    captured: dict = {}

    def _capture(cur, market, tickers, start, end, **kw):
        captured["start"] = start
        captured["end"] = end
        return 1

    with patch("analyzer.foreign_flow_sync.get_connection", return_value=fake_conn), \
         patch("analyzer.foreign_flow_sync.sync_market_investor_flow", side_effect=_capture):
        run_foreign_flow_sync(
            MagicMock(), cfg=cfg, snapshot_date=date(2026, 4, 28), backfill_days=90
        )
    assert captured["start"] == date(2026, 4, 28) - timedelta(days=90)
    assert captured["end"] == date(2026, 4, 28)
```

상단 import 에 추가:
```python
from datetime import timedelta
```

- [ ] **Step 2: 테스트 실행 — FAIL 확인**

```bash
pytest tests/test_foreign_flow_sync.py -k run_foreign -v
```

Expected: ImportError on `run_foreign_flow_sync`.

- [ ] **Step 3: `analyzer/foreign_flow_sync.py` 에 추가**

```python
def run_foreign_flow_sync(
    db_cfg: DatabaseConfig,
    *,
    cfg: Optional[ForeignFlowConfig] = None,
    snapshot_date: Optional[date] = None,
    markets: tuple[str, ...] = _KR_MARKETS,
    backfill_days: int = 0,
) -> dict:
    """엔트리포인트. `stock_universe` 활성 KRX 종목 일괄 sync.

    Args:
        snapshot_date: 종료일 기준. None 이면 오늘 (KST).
        backfill_days: 0=종료일 1일만, N>0=종료일 기준 과거 N일 일괄.
        markets: KOSPI/KOSDAQ 만 지원 (다른 시장은 자연 skip).

    Returns:
        {"start_date", "end_date", "by_market": {KOSPI: int, ...}, "total": int}
    """
    cfg = cfg or ForeignFlowConfig()
    if not cfg.sync_enabled:
        _log.info("FOREIGN_FLOW_SYNC_ENABLED=false — skip")
        return {"start_date": None, "end_date": None, "by_market": {}, "total": 0}

    end_d = snapshot_date or _today_kst()
    start_d = end_d - timedelta(days=backfill_days) if backfill_days > 0 else end_d

    conn = get_connection(db_cfg)
    by_market: dict[str, int] = {}
    total = 0
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ticker, market FROM stock_universe
                WHERE listed = TRUE AND has_preferred = FALSE
                  AND market = ANY(%s)
            """, (list(markets),))
            rows = cur.fetchall()
        grouped: dict[str, list[str]] = {}
        for ticker, market in rows:
            grouped.setdefault(market.upper(), []).append(ticker)

        for market, tickers in grouped.items():
            with conn.cursor() as cur:
                n = sync_market_investor_flow(
                    cur, market, tickers, start_d, end_d,
                    max_consecutive_failures=cfg.max_consecutive_failures,
                )
                conn.commit()
            by_market[market] = n
            total += n
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    _log.info(
        f"foreign_flow sync 완료 — 총 {total} row "
        f"(범위 {start_d}~{end_d}, by_market={by_market})"
    )
    return {
        "start_date": start_d,
        "end_date": end_d,
        "by_market": by_market,
        "total": total,
    }
```

- [ ] **Step 4: 테스트 PASS 확인**

```bash
pytest tests/test_foreign_flow_sync.py -v
```

Expected: 11 PASS.

- [ ] **Step 5: Commit**

```bash
git add analyzer/foreign_flow_sync.py tests/test_foreign_flow_sync.py
git commit -m "feat(analyzer): run_foreign_flow_sync — 1일/N일 백필 엔트리포인트"
```

---

## Task 6: universe_sync.py — `--mode foreign` 통합 + cleanup

**Files:**
- Modify: `analyzer/universe_sync.py` — argparse + main 분기 + cleanup 통합

- [ ] **Step 1: 현행 fundamentals 모드 분기 위치 확인**

```bash
grep -n "fundamentals\|args.mode ==" analyzer/universe_sync.py | head -20
```

`fundamentals` 분기 위치를 파악 (보통 line 1876 근처).

- [ ] **Step 2: argparse choices 에 `"foreign"` 추가**

`_parse_args` (line 1676 부근) 수정:

```python
p.add_argument("--mode",
               choices=("meta", "price", "auto", "ohlcv", "backfill", "cleanup",
                        "indices", "industry_kr", "fundamentals", "foreign"),
               default="auto",
               help=(... "foreign: 외국인/기관/개인 수급 PIT sync (KRX 한정) — "
                         "--days N 으로 백필"))
```

- [ ] **Step 3: `main()` 에 분기 추가**

`fundamentals` 분기 아래 (또는 같은 패턴) 에 추가:

```python
if args.mode == "foreign":
    from analyzer.foreign_flow_sync import run_foreign_flow_sync
    backfill = int(args.days) if args.days else 0
    result = run_foreign_flow_sync(
        cfg.db, cfg=cfg.foreign_flow, backfill_days=backfill,
    )
    _log.info(f"--mode foreign 완료: total={result['total']} by_market={result['by_market']}")
    return 0
```

- [ ] **Step 4: cleanup 통합**

`_run_mode_cleanup` (line 1748) 함수에 foreign_flow retention 추가:

```python
def _run_mode_cleanup(cfg: AppConfig, args: argparse.Namespace) -> dict:
    """--mode cleanup: retention 초과 row 삭제 (OHLCV + foreign_flow)."""
    retention = args.days if args.days is not None else cfg.ohlcv.retention_days
    delisted = cfg.ohlcv.delisted_retention_days
    result = {"ohlcv": cleanup_ohlcv(cfg.db, retention_days=retention,
                                     delisted_retention_days=delisted)}

    # foreign_flow cleanup
    ff_cfg = cfg.foreign_flow
    result["foreign_flow"] = _cleanup_foreign_flow(
        cfg.db, ff_cfg.retention_days, ff_cfg.delisted_retention_days,
    )
    return result


def _cleanup_foreign_flow(db_cfg, retention_days: int, delisted_retention_days: int) -> dict:
    """`stock_universe_foreign_flow` retention 초과 row 삭제."""
    conn = get_connection(db_cfg)
    deleted = 0
    try:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM stock_universe_foreign_flow
                WHERE snapshot_date < CURRENT_DATE - %s::int
            """, (int(retention_days),))
            deleted = cur.rowcount
            # 상폐 종목 축소 retention
            cur.execute("""
                DELETE FROM stock_universe_foreign_flow ff
                USING stock_universe u
                WHERE u.ticker = ff.ticker AND u.market = ff.market
                  AND u.listed = FALSE
                  AND ff.snapshot_date < CURRENT_DATE - %s::int
            """, (int(delisted_retention_days),))
            deleted += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    _log.info(f"foreign_flow cleanup — {deleted} row 삭제")
    return {"deleted": deleted}
```

- [ ] **Step 5: 수동 검증 — dry run with --days 5**

(DB 연결 + pykrx 환경에서)
```bash
python -m analyzer.universe_sync --mode foreign --days 5
```

Expected: stderr 에 "foreign_flow sync 완료 — 총 N row" 로그. KOSPI ~950, KOSDAQ ~1,550 종목 × 5일 ≒ 12,500 row 내외. 1~3분 소요.

만약 pykrx 인증 문제 발생 시 `KRX_ID/KRX_PW` 환경변수 확인.

수동 검증이므로 결과를 stdout 에 캡처해 확인. CI 자동 검증 불가 (실 데이터 의존).

- [ ] **Step 6: Commit**

```bash
git add analyzer/universe_sync.py
git commit -m "feat(analyzer): --mode foreign + cleanup 통합 — universe_sync 합류"
```

---

## Task 7: 스크리너 백엔드 — CTE + WHERE + 정렬 + SELECT

**Files:**
- Modify: `api/routes/screener.py:run_screener`
- Test: `tests/test_screener_foreign_flow.py` (신규)

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_screener_foreign_flow.py`:

```python
"""스크리너 외국인 수급 필터 — SQL 생성 검증.

run_screener 의 SQL 빌드 로직만 검증 (실제 DB 호출은 mock).
"""
from unittest.mock import MagicMock, patch

import pytest


def _run(spec, user_id=None):
    """run_screener 실행 후 cur.execute 가 받은 (sql, params) 캡처."""
    from api.routes import screener
    captured = {}
    fake_cur = MagicMock()

    def _capture_execute(sql, params):
        captured["sql"] = sql
        captured["params"] = list(params)

    fake_cur.execute.side_effect = _capture_execute
    fake_cur.fetchall.return_value = []
    fake_conn = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cur

    user = None
    if user_id:
        user = MagicMock()
        user.id = user_id
        user.effective_tier.return_value = "premium"

    screener.run_screener(spec=spec, user=user, conn=fake_conn)
    return captured


def test_no_foreign_keys_skips_join():
    """spec 에 foreign 키 없으면 foreign_flow CTE/JOIN 안 들어감."""
    cap = _run({"sectors": ["semiconductors"]})
    assert "foreign_flow_metrics" not in cap["sql"]
    assert "ff.own_latest" not in cap["sql"]


def test_min_foreign_ownership_pct_clause():
    cap = _run({"min_foreign_ownership_pct": 30.0})
    assert "foreign_flow_metrics" in cap["sql"]
    assert "ff.own_latest >= %s" in cap["sql"]
    assert 30.0 in cap["params"]


def test_delta_filter_with_window_5():
    cap = _run({
        "min_foreign_ownership_delta_pp": 1.5,
        "delta_window_days": 5,
    })
    assert "ff.own_d5" in cap["sql"]
    assert "ff.own_latest - ff.own_d5" in cap["sql"]
    assert 1.5 in cap["params"]


def test_delta_filter_default_window_20():
    cap = _run({"min_foreign_ownership_delta_pp": 1.0})
    assert "ff.own_d20" in cap["sql"]


def test_delta_filter_window_60():
    cap = _run({
        "min_foreign_ownership_delta_pp": 0.5,
        "delta_window_days": 60,
    })
    assert "ff.own_d60" in cap["sql"]


def test_delta_filter_invalid_window_falls_back_to_20():
    cap = _run({
        "min_foreign_ownership_delta_pp": 0.5,
        "delta_window_days": 99,  # invalid
    })
    assert "ff.own_d20" in cap["sql"]
    assert "ff.own_d99" not in cap["sql"]


def test_net_buy_filter_with_window_60():
    cap = _run({
        "min_foreign_net_buy_krw": 1_000_000_000,
        "net_buy_window_days": 60,
    })
    assert "ff.net_buy_60d" in cap["sql"]
    assert 1_000_000_000 in cap["params"]


def test_negative_delta_input_allowed():
    cap = _run({"min_foreign_ownership_delta_pp": -2.0})
    assert -2.0 in cap["params"]


def test_sort_foreign_delta_uses_filter_window():
    """sort=foreign_delta_desc + delta_window_days=5 → ORDER BY 가 own_d5 참조."""
    cap = _run({
        "sort": "foreign_delta_desc",
        "delta_window_days": 5,
    })
    assert "ORDER BY (ff.own_latest - ff.own_d5) DESC" in cap["sql"]


def test_sort_foreign_net_buy_uses_filter_window():
    cap = _run({
        "sort": "foreign_net_buy_desc",
        "net_buy_window_days": 60,
    })
    assert "ORDER BY ff.net_buy_60d DESC" in cap["sql"]


def test_sort_foreign_ownership_simple():
    cap = _run({"sort": "foreign_ownership_desc"})
    assert "ORDER BY ff.own_latest DESC" in cap["sql"]


def test_response_includes_window_metadata():
    """응답 row 에 윈도우 표기를 위해 SELECT 에 *_window_days 필드 포함."""
    cap = _run({
        "min_foreign_ownership_delta_pp": 1.0,
        "delta_window_days": 5,
    })
    assert "foreign_ownership_delta_window_days" in cap["sql"]
```

- [ ] **Step 2: 테스트 실행 — FAIL 확인**

```bash
pytest tests/test_screener_foreign_flow.py -v
```

Expected: 다수 FAIL — SQL 에 foreign_flow 관련 절 없음.

- [ ] **Step 3: `api/routes/screener.py` 수정**

`run_screener` 함수 안에서 `where: list[str]`, `params: list` 초기화 직후에 다음 블록 추가:

```python
    # ── 외국인 수급 필터 (v44 stock_universe_foreign_flow) ──
    join_foreign = False

    # 윈도우 화이트리스트 (SQL injection 방어)
    delta_win = spec.get("delta_window_days") or 20
    netbuy_win = spec.get("net_buy_window_days") or 20
    try:
        delta_win = int(delta_win)
        netbuy_win = int(netbuy_win)
    except (TypeError, ValueError):
        delta_win = netbuy_win = 20
    if delta_win not in (5, 20, 60):
        delta_win = 20
    if netbuy_win not in (5, 20, 60):
        netbuy_win = 20

    if spec.get("min_foreign_ownership_pct") is not None:
        join_foreign = True
        where.append("ff.own_latest IS NOT NULL AND ff.own_latest >= %s")
        params.append(float(spec["min_foreign_ownership_pct"]))

    if spec.get("min_foreign_ownership_delta_pp") is not None:
        join_foreign = True
        where.append(
            f"ff.own_latest IS NOT NULL AND ff.own_d{delta_win} IS NOT NULL "
            f"AND (ff.own_latest - ff.own_d{delta_win}) >= %s"
        )
        params.append(float(spec["min_foreign_ownership_delta_pp"]))

    if spec.get("min_foreign_net_buy_krw") is not None:
        join_foreign = True
        where.append(f"ff.net_buy_{netbuy_win}d IS NOT NULL AND ff.net_buy_{netbuy_win}d >= %s")
        params.append(float(spec["min_foreign_net_buy_krw"]))

    sort_key = spec.get("sort") or ""
    if sort_key in ("foreign_ownership_desc", "foreign_delta_desc", "foreign_net_buy_desc"):
        join_foreign = True
```

`sort_map` 정의 직후에 동적 매핑 덮어쓰기 추가:

```python
    sort_map["foreign_ownership_desc"] = "ff.own_latest DESC NULLS LAST" \
        if join_foreign else "u.market_cap_krw DESC NULLS LAST"
    sort_map["foreign_delta_desc"] = (
        f"(ff.own_latest - ff.own_d{delta_win}) DESC NULLS LAST"
    ) if join_foreign else "u.market_cap_krw DESC NULLS LAST"
    sort_map["foreign_net_buy_desc"] = (
        f"ff.net_buy_{netbuy_win}d DESC NULLS LAST"
    ) if join_foreign else "u.market_cap_krw DESC NULLS LAST"
```

`common_ctes` (line 301~) 에 `foreign_flow_*` CTE 두 개 합류 — `latest_fund` 와 같은 형태로:

```python
    # common_ctes 의 latest_fund 정의 직후 (또는 끝에) 추가
    foreign_flow_cte = """,
        foreign_flow_ranked AS (
            SELECT ticker, UPPER(market) AS market, snapshot_date,
                   foreign_ownership_pct::float AS ownership_pct,
                   foreign_net_buy_value AS net_buy,
                   ROW_NUMBER() OVER (PARTITION BY ticker, UPPER(market)
                                      ORDER BY snapshot_date DESC) AS rn
            FROM stock_universe_foreign_flow
            WHERE snapshot_date >= CURRENT_DATE - 90
        ),
        foreign_flow_metrics AS (
            SELECT ticker, market,
                   MAX(CASE WHEN rn=1   THEN ownership_pct END) AS own_latest,
                   MAX(CASE WHEN rn=6   THEN ownership_pct END) AS own_d5,
                   MAX(CASE WHEN rn=21  THEN ownership_pct END) AS own_d20,
                   MAX(CASE WHEN rn=61  THEN ownership_pct END) AS own_d60,
                   SUM(net_buy) FILTER (WHERE rn<=5)  AS net_buy_5d,
                   SUM(net_buy) FILTER (WHERE rn<=20) AS net_buy_20d,
                   SUM(net_buy) FILTER (WHERE rn<=60) AS net_buy_60d
            FROM foreign_flow_ranked
            GROUP BY ticker, market
        )
    """ if join_foreign else ""
    common_ctes_full = common_ctes + foreign_flow_cte
```

기존 `common_ctes` 사용처 모두 `common_ctes_full` 로 교체.

`common_select_tail` 에 외국인 메트릭 추가 (`join_foreign=True` 분기):

```python
    foreign_select_tail = (
        f", ff.own_latest AS foreign_ownership_pct,"
        f" (ff.own_latest - ff.own_d{delta_win}) AS foreign_ownership_delta_pp,"
        f" ff.net_buy_{netbuy_win}d AS foreign_net_buy_krw,"
        f" {delta_win}::int AS foreign_ownership_delta_window_days,"
        f" {netbuy_win}::int AS foreign_net_buy_window_days"
    ) if join_foreign else ""
```

`common_join_tail` 에 LEFT JOIN 추가:

```python
    foreign_join_tail = """
        LEFT JOIN foreign_flow_metrics ff
          ON UPPER(u.ticker) = UPPER(ff.ticker) AND UPPER(u.market) = ff.market
    """ if join_foreign else ""
```

기존 sql 조립 부분 (`SELECT ... {common_select_tail} ... {common_join_tail}`) 에 신규 변수 합류:

```python
    # OHLCV 분기 / non-OHLCV 분기 모두에 적용
    sql = f"""
    {cte_or_with}
    SELECT u.ticker, u.market, u.asset_name, ...
           {common_select_tail}{foreign_select_tail}
    FROM stock_universe u
    {ohlcv_join_or_empty}
    {common_join_tail}{foreign_join_tail}
    WHERE {where_sql}
    ORDER BY {order_by}
    LIMIT %s
    """
```

(정확한 조립 위치는 기존 코드의 if/else 분기를 따라 양쪽 모두 적용.)

- [ ] **Step 4: 테스트 PASS 확인**

```bash
pytest tests/test_screener_foreign_flow.py -v
```

Expected: 12 PASS.

기존 스크리너 테스트도 통과 확인:
```bash
pytest tests/test_screener*.py -v
```

Expected: 모두 PASS (회귀 없음).

- [ ] **Step 5: Commit**

```bash
git add api/routes/screener.py tests/test_screener_foreign_flow.py
git commit -m "feat(스크리너): 외국인 수급 필터 5종 + 정렬 자동연동 (백엔드)"
```

---

## Task 8: 스크리너 UI — 사이드패널 그룹 + SpecBuilder + chips + 결과 컬럼

**Files:**
- Modify: `api/templates/screener.html`

이 task 는 인라인 JS + HTML 변경으로 자동 테스트 어려움. 수동 UI 검증 필수.

- [ ] **Step 1: 사이드패널 그룹 추가**

`api/templates/screener.html` 의 `data-group="fund"` `</details>` 다음 (line 524 부근) 에 추가:

```html
<details class="filter-group" data-group="foreign">
  <summary>외국인 수급 <span class="active-dot"></span></summary>

  <label>현재 보유율 ≥
    <input id="f-foreign-own-min" type="number" step="0.1"
           placeholder="예: 30" inputmode="decimal"> %
  </label>

  <fieldset class="window-radio">
    <legend>변화 윈도우</legend>
    <label><input type="radio" name="foreign-delta-window" value="5"> 5일</label>
    <label><input type="radio" name="foreign-delta-window" value="20" checked> 20일</label>
    <label><input type="radio" name="foreign-delta-window" value="60"> 60일</label>
  </fieldset>
  <label>보유율 변화 ≥
    <input id="f-foreign-delta-min" type="number" step="0.1"
           placeholder="예: 1.5 (음수=감소)" inputmode="decimal"> %p
  </label>

  <fieldset class="window-radio">
    <legend>순매수 윈도우</legend>
    <label><input type="radio" name="foreign-netbuy-window" value="5"> 5일</label>
    <label><input type="radio" name="foreign-netbuy-window" value="20" checked> 20일</label>
    <label><input type="radio" name="foreign-netbuy-window" value="60"> 60일</label>
  </fieldset>
  <label>누적 순매수 ≥
    <input id="f-foreign-netbuy-min" type="number" step="10"
           placeholder="예: 500 (음수=순매도)" inputmode="decimal"> 억원
  </label>
</details>
```

- [ ] **Step 2: SpecBuilder 양방향 매핑**

`screener.html` 의 inline `<script>` 안 (line 575~) `SpecBuilder` 객체에 추가.

`fromDOM` 함수 내부에 추가:
```js
    const ownMin     = parseFloat(document.getElementById('f-foreign-own-min')?.value);
    const deltaMin   = parseFloat(document.getElementById('f-foreign-delta-min')?.value);
    const netbuyMinB = parseFloat(document.getElementById('f-foreign-netbuy-min')?.value);
    const deltaWin   = document.querySelector('input[name="foreign-delta-window"]:checked')?.value;
    const netbuyWin  = document.querySelector('input[name="foreign-netbuy-window"]:checked')?.value;

    if (Number.isFinite(ownMin))   spec.min_foreign_ownership_pct = ownMin;
    if (Number.isFinite(deltaMin)) spec.min_foreign_ownership_delta_pp = deltaMin;
    if (Number.isFinite(netbuyMinB)) spec.min_foreign_net_buy_krw = Math.round(netbuyMinB * 1e8);
    if (deltaWin)  spec.delta_window_days = parseInt(deltaWin, 10);
    if (netbuyWin) spec.net_buy_window_days = parseInt(netbuyWin, 10);
```

`toDOM` 함수 내부에 추가:
```js
    if (spec.min_foreign_ownership_pct !== undefined)
      document.getElementById('f-foreign-own-min').value = spec.min_foreign_ownership_pct;
    if (spec.min_foreign_ownership_delta_pp !== undefined)
      document.getElementById('f-foreign-delta-min').value = spec.min_foreign_ownership_delta_pp;
    if (spec.min_foreign_net_buy_krw !== undefined)
      document.getElementById('f-foreign-netbuy-min').value = spec.min_foreign_net_buy_krw / 1e8;
    if (spec.delta_window_days) {
      const r = document.querySelector(`input[name="foreign-delta-window"][value="${spec.delta_window_days}"]`);
      if (r) r.checked = true;
    }
    if (spec.net_buy_window_days) {
      const r = document.querySelector(`input[name="foreign-netbuy-window"][value="${spec.net_buy_window_days}"]`);
      if (r) r.checked = true;
    }
```

- [ ] **Step 3: CHIP_DEFS 추가**

`CHIP_DEFS` 배열에 추가:

```js
    {
      key: 'min_foreign_ownership_pct',
      label: (v) => `외국인 보유 ≥ ${v}%`,
      reset: (spec) => { delete spec.min_foreign_ownership_pct; }
    },
    {
      key: 'min_foreign_ownership_delta_pp',
      label: (v, spec) => {
        const w = spec.delta_window_days || 20;
        const sign = v >= 0 ? '+' : '';
        return `외국인 ${w}일 변화 ≥ ${sign}${v}%p`;
      },
      reset: (spec) => {
        delete spec.min_foreign_ownership_delta_pp;
        delete spec.delta_window_days;
      }
    },
    {
      key: 'min_foreign_net_buy_krw',
      label: (v, spec) => {
        const w = spec.net_buy_window_days || 20;
        const sign = v >= 0 ? '+' : '';
        return `외국인 ${w}일 순매수 ≥ ${sign}${(v / 1e8).toFixed(0)}억`;
      },
      reset: (spec) => {
        delete spec.min_foreign_net_buy_krw;
        delete spec.net_buy_window_days;
      }
    },
```

(기존 chip reset 시그니처가 다르면 그 형식에 맞춰 조정. CHIP_DEFS 의 기존 entry 1개를 참고해 정확한 형식 따를 것.)

- [ ] **Step 4: 결과 표 컬럼 + 정렬 옵션 추가**

표 헤더에 (기존 컬럼 토글 패턴 따라):
```html
<th data-col="foreign_own" class="col-optional hidden">보유율 (%)</th>
<th data-col="foreign_delta" class="col-optional hidden">보유 변화 (%p)</th>
<th data-col="foreign_netbuy" class="col-optional hidden">순매수 (억)</th>
```

renderRow JS 함수 (또는 이름 동등) 에 cell 렌더링:
```js
const ownPct = row.foreign_ownership_pct;
const deltaPp = row.foreign_ownership_delta_pp;
const deltaWin = row.foreign_ownership_delta_window_days || 20;
const netbuyKrw = row.foreign_net_buy_krw;
const netbuyWin = row.foreign_net_buy_window_days || 20;

cells.push(`<td data-col="foreign_own">${ownPct?.toFixed?.(2) ?? '-'}</td>`);
cells.push(`<td data-col="foreign_delta" class="${deltaPp > 0 ? 'pos' : deltaPp < 0 ? 'neg' : ''}">
  ${deltaPp != null ? (deltaPp >= 0 ? '+' : '') + deltaPp.toFixed(2) : '-'}<small> ${deltaWin}d</small>
</td>`);
cells.push(`<td data-col="foreign_netbuy" class="${netbuyKrw > 0 ? 'pos' : netbuyKrw < 0 ? 'neg' : ''}">
  ${netbuyKrw != null ? (netbuyKrw >= 0 ? '+' : '') + (netbuyKrw / 1e8).toFixed(0) : '-'}<small> ${netbuyWin}d억</small>
</td>`);
```

`<select id="f-sort">` 에 옵션 추가:
```html
<option value="foreign_ownership_desc">외국인 보유율 ↓</option>
<option value="foreign_delta_desc">외국인 보유 변화 ↓</option>
<option value="foreign_net_buy_desc">외국인 순매수 ↓</option>
```

- [ ] **Step 5: 수동 UI 검증**

서버 기동:
```bash
python -m api.main
```

브라우저에서 `http://localhost:8000/pages/screener` 접속:

검증 항목:
1. 좌측 사이드패널에 "외국인 수급" 그룹 표시, 펼침/접힘 동작.
2. "보유율 30 이상" 입력 → 자동 실행 (또는 실행 버튼) → 결과 row 가 30% 이상만 나오는지.
3. "보유율 변화 1.5 이상 + 라디오 5일" → 결과 응답에 `foreign_ownership_delta_pp` 필드 + `foreign_ownership_delta_window_days = 5` 포함 확인 (Network 탭).
4. 정렬 "외국인 변화 ↓" + 라디오 60일 → ORDER BY 가 60일 기준 적용 (응답 순서 + Network 탭 SQL 로그).
5. Chips 표시: "외국인 5일 변화 ≥ +1.5%p" 형식. × 클릭 시 reset.
6. 음수 입력 (`-2`) — 정상 처리.
7. 결과 표 컬럼 토글 패널에서 "보유율/보유 변화/순매수" on/off.
8. 회귀 검증: 펀더 필터 / OHLCV 필터 / 검색 등 기존 그룹 동작 정상.

검증 실패 시 STOP — 디버깅 후 재시도.

- [ ] **Step 6: Commit**

```bash
git add api/templates/screener.html
git commit -m "feat(스크리너): 외국인 수급 사이드패널 그룹 + chips + 결과 컬럼 (UI)"
```

---

## Task 9: tools/foreign_flow_health_check.py — 결측률 진단 도구

**Files:**
- Create: `tools/foreign_flow_health_check.py`
- Test: `tests/test_foreign_flow_health_check.py` (신규)

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_foreign_flow_health_check.py`:

```python
"""foreign_flow 결측률 진단 도구 — DB 모킹 단위 테스트."""
from datetime import datetime
from unittest.mock import MagicMock, patch


def test_compute_missing_rate_kospi_clean():
    from tools.foreign_flow_health_check import compute_missing_rate
    fake_conn = MagicMock()
    fake_cur = MagicMock()
    fake_cur.fetchall.return_value = [
        ("KOSPI", 100, 98, datetime(2026, 4, 30, 6, 40)),
        ("KOSDAQ", 200, 180, datetime(2026, 4, 30, 6, 40)),
    ]
    fake_conn.cursor.return_value.__enter__.return_value = fake_cur

    result = compute_missing_rate(fake_conn, staleness_days=2)
    assert len(result) == 2
    assert result[0]["market"] == "KOSPI"
    assert result[0]["missing_pct"] == 2.0
    assert result[1]["missing_pct"] == 10.0


def test_main_returns_nonzero_when_threshold_exceeded():
    from tools.foreign_flow_health_check import main
    from shared.config import DatabaseConfig, ForeignFlowConfig

    fake_conn = MagicMock()
    fake_cur = MagicMock()
    fake_cur.fetchall.return_value = [
        ("KOSPI", 100, 98, datetime(2026, 4, 30)),   # 2.0% — under 5.0
        ("KOSDAQ", 100, 70, datetime(2026, 4, 30)),  # 30.0% — over 10.0
    ]
    fake_conn.cursor.return_value.__enter__.return_value = fake_cur

    with patch("tools.foreign_flow_health_check.get_connection", return_value=fake_conn):
        exit_code = main(DatabaseConfig(), ForeignFlowConfig())
    assert exit_code == 1


def test_main_returns_zero_when_clean():
    from tools.foreign_flow_health_check import main
    from shared.config import DatabaseConfig, ForeignFlowConfig

    fake_conn = MagicMock()
    fake_cur = MagicMock()
    fake_cur.fetchall.return_value = [
        ("KOSPI", 100, 98, datetime(2026, 4, 30)),
        ("KOSDAQ", 100, 95, datetime(2026, 4, 30)),
    ]
    fake_conn.cursor.return_value.__enter__.return_value = fake_cur

    with patch("tools.foreign_flow_health_check.get_connection", return_value=fake_conn):
        exit_code = main(DatabaseConfig(), ForeignFlowConfig())
    assert exit_code == 0
```

- [ ] **Step 2: 테스트 실행 — FAIL 확인**

```bash
pytest tests/test_foreign_flow_health_check.py -v
```

Expected: ImportError on `tools.foreign_flow_health_check`.

- [ ] **Step 3: `tools/foreign_flow_health_check.py` 생성**

```python
"""외국인 수급 결측률 진단 도구.

`stock_universe`(활성+보통주, KRX 한정) vs `stock_universe_foreign_flow`(staleness_days 내) 비교
→ 시장별 결측 비율 + 마지막 sync 시각.

CLI:
    python -m tools.foreign_flow_health_check
    # → 표 출력 + 임계 초과 시 exit code 1
"""
from __future__ import annotations

import sys
from typing import Optional

from shared.config import DatabaseConfig, ForeignFlowConfig
from shared.db import get_connection


def compute_missing_rate(conn, *, staleness_days: int = 2) -> list[dict]:
    """KRX 시장(KOSPI/KOSDAQ)별 외국인 수급 결측률.

    Returns: [{"market", "total", "with_data", "missing_pct", "last_fetched_at"}, ...]
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                u.market,
                COUNT(*) AS total,
                COUNT(ff.ticker) AS with_data,
                MAX(ff.fetched_at) AS last_fetched_at
            FROM stock_universe u
            LEFT JOIN LATERAL (
                SELECT ticker, fetched_at
                FROM stock_universe_foreign_flow ff
                WHERE ff.ticker = u.ticker
                  AND ff.market = u.market
                  AND ff.snapshot_date >= CURRENT_DATE - %s::int
                ORDER BY snapshot_date DESC LIMIT 1
            ) ff ON TRUE
            WHERE u.listed = TRUE AND u.has_preferred = FALSE
              AND u.market IN ('KOSPI', 'KOSDAQ')
            GROUP BY u.market
            ORDER BY u.market
        """, (int(staleness_days),))
        rows = cur.fetchall()
    out = []
    for market, total, with_data, last_at in rows:
        missing = (total - with_data)
        pct = round((missing / total) * 100, 3) if total > 0 else 0.0
        out.append({
            "market": market,
            "total": int(total),
            "with_data": int(with_data),
            "missing_pct": pct,
            "last_fetched_at": last_at,
        })
    return out


def main(
    db_cfg: Optional[DatabaseConfig] = None,
    cfg: Optional[ForeignFlowConfig] = None,
) -> int:
    db_cfg = db_cfg or DatabaseConfig()
    cfg = cfg or ForeignFlowConfig()

    conn = get_connection(db_cfg)
    try:
        results = compute_missing_rate(conn, staleness_days=cfg.staleness_days)
    finally:
        conn.close()

    print(f"{'시장':<10} {'활성종목':>8} {'수급보유':>8} {'결측%':>8} {'마지막sync':>22}")
    exit_code = 0
    for r in results:
        last = str(r["last_fetched_at"]) if r["last_fetched_at"] else "(없음)"
        threshold = cfg.missing_pct_threshold(r["market"])
        marker = ""
        if r["missing_pct"] > threshold:
            marker = f" ⚠ 임계({threshold:.1f}%) 초과"
            exit_code = 1
        print(f"{r['market']:<10} {r['total']:>8} {r['with_data']:>8} "
              f"{r['missing_pct']:>7.2f}% {last:>22}{marker}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 테스트 PASS 확인**

```bash
pytest tests/test_foreign_flow_health_check.py -v
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/foreign_flow_health_check.py tests/test_foreign_flow_health_check.py
git commit -m "feat(tools): foreign_flow_health_check — 결측률 진단 + 임계 exit code"
```

---

## Task 10: systemd unit + admin_systemd 등록

**Files:**
- Create: `deploy/systemd/foreign-flow-sync.service.in`
- Create: `deploy/systemd/foreign-flow-sync.timer.in`
- Modify: `deploy/systemd/install.sh` — 신규 unit 설치 추가
- Modify: `deploy/systemd/README.md` — sudoers 화이트리스트 예시 갱신
- Modify: `api/routes/admin_systemd.py` — `MANAGED_UNITS` 등록

- [ ] **Step 1: 기존 fundamentals timer 형식 확인**

```bash
ls deploy/systemd/*.in
cat deploy/systemd/fundamentals-sync.service.in 2>/dev/null
cat deploy/systemd/fundamentals-sync.timer.in 2>/dev/null
```

(없으면 ohlcv-cleanup 또는 다른 oneshot timer 를 참조.)

- [ ] **Step 2: service.in 생성**

`deploy/systemd/foreign-flow-sync.service.in`:

```ini
[Unit]
Description=Investment Advisor — Foreign/Inst/Retail Flow Sync (KRX)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User={{ SYSTEM_USER }}
WorkingDirectory={{ INSTALL_DIR }}
EnvironmentFile={{ INSTALL_DIR }}/.env
ExecStart={{ INSTALL_DIR }}/venv/bin/python -m analyzer.universe_sync --mode foreign
StandardOutput=journal
StandardError=journal
TimeoutStartSec=1800
```

- [ ] **Step 3: timer.in 생성**

`deploy/systemd/foreign-flow-sync.timer.in`:

```ini
[Unit]
Description=Investment Advisor — Foreign Flow Sync (Daily KST 06:40)

[Timer]
OnCalendar=*-*-* 21:40:00 UTC
Persistent=true
Unit=investment-advisor-foreign-flow-sync.service

[Install]
WantedBy=timers.target
```

- [ ] **Step 4: install.sh 갱신**

`deploy/systemd/install.sh` 의 unit 설치 루프에 신규 unit 추가. 기존 fundamentals timer 패턴을 따라 추가.

```bash
grep -n "fundamentals-sync\|UNITS=\|for unit in" deploy/systemd/install.sh
```

위치 파악 후 동일 패턴으로:
```bash
UNITS=(
    ...
    "fundamentals-sync"
    "foreign-flow-sync"   # 신규
)
```

- [ ] **Step 5: admin_systemd.py — MANAGED_UNITS 등록**

`api/routes/admin_systemd.py:MANAGED_UNITS` dict 에 추가 (기존 패턴 따라):

```python
MANAGED_UNITS["foreign-flow-sync"] = {
    "service": "investment-advisor-foreign-flow-sync.service",
    "timer":   "investment-advisor-foreign-flow-sync.timer",
    "self_protected": False,
    "description": "외국인/기관/개인 수급 PIT 일배치 sync (KRX)",
}
```

(정확한 dict 형식은 기존 `fundamentals-sync` entry 가 있다면 그대로 따를 것.)

- [ ] **Step 6: README.md sudoers 예시 갱신**

`deploy/systemd/README.md` 의 sudoers 화이트리스트 예시에 신규 unit 의 service/timer 추가:

```
%dzp ALL=(ALL) NOPASSWD: /usr/bin/systemctl start investment-advisor-foreign-flow-sync.service, \
                         /usr/bin/systemctl stop investment-advisor-foreign-flow-sync.service, \
                         /usr/bin/systemctl restart investment-advisor-foreign-flow-sync.service, \
                         /usr/bin/systemctl enable investment-advisor-foreign-flow-sync.timer, \
                         /usr/bin/systemctl disable investment-advisor-foreign-flow-sync.timer
```

(기존 형식 그대로 따라.)

- [ ] **Step 7: 단위 테스트 — admin_systemd.py MANAGED_UNITS**

기존에 `tests/test_admin_systemd.py` 가 있다면 신규 unit 등록 검증 케이스 추가:

```python
def test_foreign_flow_unit_registered():
    from api.routes.admin_systemd import MANAGED_UNITS
    assert "foreign-flow-sync" in MANAGED_UNITS
    entry = MANAGED_UNITS["foreign-flow-sync"]
    assert entry["self_protected"] is False
    assert "foreign-flow-sync.service" in entry["service"]
    assert "foreign-flow-sync.timer" in entry["timer"]
```

```bash
pytest tests/test_admin_systemd.py -v
```

(파일 없으면 skip — Task 11 후 통합으로 검증.)

- [ ] **Step 8: Commit**

```bash
git add deploy/systemd/foreign-flow-sync.service.in \
        deploy/systemd/foreign-flow-sync.timer.in \
        deploy/systemd/install.sh \
        deploy/systemd/README.md \
        api/routes/admin_systemd.py \
        tests/test_admin_systemd.py 2>/dev/null
git commit -m "feat(systemd): foreign-flow-sync unit + 웹 UI 화이트리스트"
```

---

## Task 11: admin "도구" 탭 — health check 버튼 (SSE)

**Files:**
- Modify: `api/routes/admin.py`
- Modify: `api/templates/admin.html` (관련 HTML 부분)

- [ ] **Step 1: 기존 fundamentals 진단 버튼 위치 파악**

```bash
grep -n "fundamentals_health\|tools\.fundamentals" api/routes/admin.py api/templates/admin.html
```

기존 패턴을 그대로 본떠 새 endpoint + 버튼 추가.

- [ ] **Step 2: admin.py 에 endpoint 추가**

기존 fundamentals 진단 endpoint 아래에:

```python
@router.get("/diagnostics/foreign-flow", dependencies=[Depends(require_role("admin"))])
async def stream_foreign_flow_diagnostics():
    """foreign_flow_health_check 결과 SSE 스트림."""
    return StreamingResponse(
        _stream_subprocess(["python", "-m", "tools.foreign_flow_health_check"]),
        media_type="text/event-stream",
    )
```

(기존 `_stream_subprocess` 헬퍼가 있는 가정. 없으면 fundamentals 진단 함수 패턴 그대로 복사.)

- [ ] **Step 3: admin.html "도구" 탭에 버튼**

```html
<button class="btn-tool" data-sse="/admin/diagnostics/foreign-flow">
  외국인 수급 결측률 진단
</button>
<pre id="diag-foreign-flow" class="sse-log"></pre>
```

기존 `attachSseLog` (`static/js/sse_log_viewer.js` 의 공용 컨트롤러) 가 자동으로 잡도록 동일 data 속성 패턴 사용.

- [ ] **Step 4: 수동 검증**

서버 재기동 후 `http://localhost:8000/admin` → "도구" 탭 → "외국인 수급 결측률 진단" 클릭. SSE 로그가 stream 되어 표 출력 확인.

- [ ] **Step 5: Commit**

```bash
git add api/routes/admin.py api/templates/admin.html
git commit -m "feat(admin): 외국인 수급 결측률 진단 SSE 버튼"
```

---

## Task 12: 운영기 배포 + 90일 백필 + CLAUDE.md 동기화

**Files:**
- Modify: `CLAUDE.md`

수동 운영 작업 + 문서 마무리.

- [ ] **Step 1: CLAUDE.md "DB Schema" 섹션 갱신**

CLAUDE.md 의 "DB Schema" 섹션 하단 (v43 row 다음) 에 v44 row 추가:

```markdown
- `stock_universe_foreign_flow`(v44) — KRX 종목별 투자자별 수급 PIT 시계열. PK `(ticker, market, snapshot_date)`, 컬럼 `foreign_ownership_pct/foreign_net_buy_value/inst_net_buy_value/retail_net_buy_value/data_source/fetched_at`. pykrx 2종 API (`get_exhaustion_rates_of_foreign_investment` + `get_market_trading_value_by_date`) 일배치 수집. **KRX (KOSPI/KOSDAQ) 한정**. v1 스크리너 UI 는 외국인 컬럼만 노출, 기관/개인은 데이터 레이어에만 보존 (재백필 회피). `analyzer/foreign_flow_sync.py` + `analyzer/universe_sync.py --mode foreign` 으로 관리. retention 400일 (상폐 200일). systemd `foreign-flow-sync.timer` (KST 06:40). 운영 매뉴얼: `_docs/_exception/` (이슈 발생 시 추가).
- **외국인 보유율 PIT 의미** — `foreign_ownership_pct` 는 KSD T+2 결제 룰로 인해 보통 `snapshot_date - 2 영업일` 의 보유 상태. UI 툴팁 + 스크리너 응답 메타에 명시.
- **스크리너 외국인 수급 필터 (v44)** — `routes/screener.py:run_screener` 가 `min_foreign_ownership_pct` / `min_foreign_ownership_delta_pp` (+ `delta_window_days ∈ {5, 20, 60}`) / `min_foreign_net_buy_krw` (+ `net_buy_window_days ∈ {5, 20, 60}`) spec 키 받아 `stock_universe_foreign_flow` LEFT JOIN. 윈도우는 화이트리스트 가드 (외 값은 fallback 20). 정렬 `foreign_ownership_desc` / `foreign_delta_desc` / `foreign_net_buy_desc` 는 필터 윈도우와 자동 연동.
```

CLAUDE.md "Environment Variables" 표에도 추가 (FOREIGN_FLOW_* 7개).

CLAUDE.md "Project Structure" 의 `analyzer/` 라인에 `foreign_flow_sync` 추가, `tools/` 라인에 `foreign_flow_health_check` 추가.

- [ ] **Step 2: 운영기 git pull + API 재기동 (마이그레이션 자동 적용)**

운영기 (라즈베리파이) SSH 접속 후:
```bash
cd /home/dzp/dzp-main/program/investment-advisor
sudo -u dzp git pull origin dev   # 또는 main 머지 후 main pull
sudo systemctl restart investment-advisor-api
sudo journalctl -u investment-advisor-api -n 50 --no-pager
```

Expected log: `[DB] v44 마이그레이션 완료 — stock_universe_foreign_flow`

- [ ] **Step 3: 90일 백필 1회 실행**

```bash
sudo -u dzp /home/dzp/dzp-main/program/investment-advisor/venv/bin/python \
    -m analyzer.universe_sync --mode foreign --days 90
```

Expected: ~7~10분 후 "foreign_flow sync 완료 — 총 N row" (KOSPI ~85k, KOSDAQ ~140k row 내외).

- [ ] **Step 4: systemd unit 등록**

```bash
cd /home/dzp/dzp-main/program/investment-advisor/deploy/systemd
sudo bash install.sh   # 신규 unit 자동 설치
sudo systemctl daemon-reload
sudo systemctl enable --now investment-advisor-foreign-flow-sync.timer
sudo systemctl list-timers | grep foreign-flow
```

Expected: 다음 일정 시각이 KST 06:40 (UTC 21:40) 으로 표시.

- [ ] **Step 5: sudoers 갱신**

```bash
sudo visudo -f /etc/sudoers.d/investment-advisor-systemd
```

신규 unit 화이트리스트 라인 추가 (Task 10 Step 6 의 형식 그대로). 저장 후:
```bash
sudo -u www-data sudo -ln 2>/dev/null | grep foreign-flow
```

(API 가 실행되는 user 로 NOPASSWD 권한 확인.)

- [ ] **Step 6: health check 1회 실행**

```bash
sudo -u dzp /home/dzp/dzp-main/program/investment-advisor/venv/bin/python \
    -m tools.foreign_flow_health_check
echo "exit=$?"
```

Expected: KOSPI 결측률 < 5%, KOSDAQ < 10%, exit=0.

만약 결측률 임계 초과 시 — pykrx 인증 / KRX_ID·KRX_PW 환경변수 / 일부 종목 데이터 미공시 등 원인 진단. `_docs/_exception/` 에 이슈 리포트 작성.

- [ ] **Step 7: UI 검증 (운영기 기준)**

브라우저에서 운영기 URL `/pages/screener` 접속:
1. 사이드패널 "외국인 수급" 그룹 표시.
2. "보유율 변화 1.5%p 이상 + 윈도우 20일" 입력 → 결과 30~100건 매칭.
3. 정렬 "외국인 변화 ↓" → 결과 desc 정렬 확인.
4. 결과 컬럼 "보유율/변화/순매수" 표시 정상.

`/admin` "도구" 탭 → "외국인 수급 결측률 진단" 버튼 클릭 → SSE 로그 표 정상 출력.

- [ ] **Step 8: CLAUDE.md commit + prompt log 묶음**

```bash
git add CLAUDE.md _docs/_prompts/20260429_prompt.md 2>/dev/null
git commit -m "docs(스크리너): 외국인 수급 v44 — CLAUDE.md 동기화 + prompt 기록"
```

---

## 자가 검증 — 완료 후 체크리스트

- [ ] `pytest -q` — 모든 테스트 PASS
- [ ] `python -m analyzer.universe_sync --mode foreign` — 1일 sync 성공
- [ ] `python -m tools.foreign_flow_health_check; echo $?` — exit=0
- [ ] `psql -d investment_advisor -c "SELECT COUNT(*) FROM stock_universe_foreign_flow"` — 누적 row 수 합리
- [ ] 스크리너 UI: 외국인 수급 그룹 + 정렬 + chips 동작
- [ ] systemd: `systemctl list-timers | grep foreign-flow` — 다음 실행 시각 확인
- [ ] admin "도구" 탭: 결측률 진단 SSE 정상

체크 모두 통과 시 → PR 생성 → main 머지.
