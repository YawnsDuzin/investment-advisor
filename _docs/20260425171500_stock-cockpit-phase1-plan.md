# Stock Cockpit Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/pages/stocks/{ticker}` 페이지를 Stock Cockpit 으로 in-place 교체 — § Hero + § 1 가격차트(추천마커+볼륨) + § 2-A 벤치마크 상대성과 + § 4 펀더멘털 흡수 + § 6 추천 이력 타임라인.

**Architecture:** 기존 `stock_fundamentals.html` 단일 파일을 `stock_cockpit.html` 신규로 교체 (URL 동일). 백엔드는 신규 API 2개(`/overview`, `/proposals`) + 기존 `/ohlcv` `/fundamentals` `/api/indices/{code}/ohlcv` 재사용. 프론트는 lightweight-charts CDN(가격·벤치마크) + Vanilla JS(타임라인·Hero). 모든 차트는 페이지 `{% block scripts %}` 안에서만 로드.

**Tech Stack:** FastAPI, psycopg2 (RealDictCursor), Jinja2, lightweight-charts v4.2.0 (CDN), Vanilla JS. 테스트는 pytest + tests/conftest.py 의 mock 환경.

**Spec:** [`_docs/20260425170650_stock-cockpit-design.md`](20260425170650_stock-cockpit-design.md)

---

## File Structure

| 액션 | 경로 | 책임 |
|---|---|---|
| Modify | [`api/routes/stocks.py`](../api/routes/stocks.py) | 신규 API 2개 추가 + pages_router 가 새 템플릿 렌더 |
| Create | `api/templates/stock_cockpit.html` | 새 페이지 — Hero/차트/벤치마크/타임라인/펀더멘털 |
| Delete | `api/templates/stock_fundamentals.html` | in-place 교체 — 기존 8카드는 새 템플릿이 흡수 |
| Create | `tests/test_stock_cockpit.py` | 신규 API 2개 + 페이지 200 스모크 테스트 |

콜로케이션 원칙: stocks 도메인은 한 라우터 파일에 JSON API + 페이지 라우트 동거 (기존 패턴 준수, [`stocks.py:14`](../api/routes/stocks.py#L14)).

---

## Task 1: `GET /api/stocks/{ticker}/overview` — Hero 종합 응답

**Files:**
- Modify: `api/routes/stocks.py` (신규 핸들러 추가)
- Test: `tests/test_stock_cockpit.py` (Create)

**응답 스키마:**

```json
{
  "ticker": "TXN",
  "market": "NASDAQ",
  "name": "Texas Instruments Incorporated",
  "sector": "Technology",
  "industry": "Semiconductors",
  "currency": "USD",
  "latest": {
    "date": "2026-04-24",
    "close": 277.14,
    "change_pct": -1.80,
    "volume": 9240450,
    "source": "ohlcv_db"
  },
  "stats": {
    "ai_score": 78,
    "proposal_count": 4,
    "avg_post_return_3m_pct": 12.4,
    "alpha_vs_benchmark_pct": 5.1,
    "factor_pctile_avg": 0.78
  },
  "score_breakdown": {
    "factor_score": 0.78,
    "hist_score": 0.62,
    "consensus_score": 0.75,
    "weights": {"factor": 0.5, "hist": 0.3, "consensus": 0.2}
  }
}
```

- [ ] **Step 1.1: Write the failing test (정상 케이스)**

`tests/test_stock_cockpit.py` 신규 파일:

```python
"""Stock Cockpit API + 페이지 단위 테스트.

psycopg2가 conftest에서 mock되므로 get_connection → cursor → fetch 체인을
가짜 객체로 꾸민다. fundamentals analyst.recommendation 만 yfinance를
patch 한다.
"""
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest


def _fake_conn(fetch_sequence):
    """fetchone/fetchall 호출 순서대로 값 반환하는 가짜 커넥션."""
    cur = MagicMock()
    idx = {"n": 0}

    def _next():
        v = fetch_sequence[idx["n"]]
        idx["n"] += 1
        return v

    cur.fetchone.side_effect = _next
    cur.fetchall.side_effect = _next

    @contextmanager
    def _cursor(**kwargs):
        yield cur

    conn = MagicMock()
    conn.cursor = _cursor
    return conn


class TestStockOverviewAPI:
    """GET /api/stocks/{ticker}/overview"""

    def test_overview_returns_hero_payload(self):
        from api.routes.stocks import get_stock_overview
        from shared.config import DatabaseConfig

        # fetch 순서: meta(stock_universe), latest 2 rows(ohlcv), prop_stats, factor_snapshot
        meta_row = {
            "name": "Texas Instruments",
            "sector": "Technology",
            "industry": "Semiconductors",
            "currency": "USD",
            "market": "NASDAQ",
        }
        latest_rows = [
            {"trade_date": date(2026, 4, 23), "close": Decimal("282.21"), "volume": 8800000},
            {"trade_date": date(2026, 4, 24), "close": Decimal("277.14"), "volume": 9240450},
        ]
        stats_row = {
            "proposal_count": 4,
            "avg_post_return_3m_pct": Decimal("12.4"),
            "avg_alpha_vs_benchmark_pct": Decimal("5.1"),
            "latest_consensus": "BUY",
        }
        factor_row = {
            "factor_snapshot": {
                "r1m_pctile": 0.7, "r3m_pctile": 0.8, "r6m_pctile": 0.85, "r12m_pctile": 0.78,
            },
        }

        conn = _fake_conn([meta_row, latest_rows, stats_row, factor_row])

        with patch("api.routes.stocks.get_connection", return_value=conn):
            result = get_stock_overview(ticker="TXN", market="NASDAQ")

        assert result["ticker"] == "TXN"
        assert result["name"] == "Texas Instruments"
        assert result["latest"]["close"] == 277.14
        # 변동률 = (277.14 - 282.21) / 282.21 * 100 ≈ -1.80
        assert round(result["latest"]["change_pct"], 2) == -1.80
        assert result["stats"]["proposal_count"] == 4
        assert result["stats"]["avg_post_return_3m_pct"] == 12.4
        # AI 점수 산식 검증
        # factor_score = (0.7+0.8+0.85+0.78)/4 = 0.7825
        # hist_score = clamp(12.4/30, 0, 1) ≈ 0.4133
        # consensus_score = BUY → 0.75
        # score = 100*(0.5*0.7825 + 0.3*0.4133 + 0.2*0.75) = 100*(0.391 + 0.124 + 0.15) = 66.5
        assert 60 <= result["stats"]["ai_score"] <= 75
        assert result["score_breakdown"]["weights"] == {"factor": 0.5, "hist": 0.3, "consensus": 0.2}
```

- [ ] **Step 1.2: Run test to verify it fails**

Run: `pytest tests/test_stock_cockpit.py::TestStockOverviewAPI::test_overview_returns_hero_payload -v`
Expected: FAIL — `ImportError: cannot import name 'get_stock_overview' from 'api.routes.stocks'`

- [ ] **Step 1.3: Implement `get_stock_overview` 핸들러**

`api/routes/stocks.py` 끝(indices_router 위 또는 적절 위치)에 추가:

```python
# ──────────────────────────────────────────────
# Stock Cockpit — Hero overview API
# ──────────────────────────────────────────────
_AI_SCORE_WEIGHTS = {"factor": 0.5, "hist": 0.3, "consensus": 0.2}

_CONSENSUS_MAP = {
    "STRONG_BUY": 1.0, "BUY": 0.75, "HOLD": 0.5,
    "SELL": 0.25, "STRONG_SELL": 0.0,
}


def _clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def _compute_ai_score(factor_snapshot, avg_post_return_3m, consensus):
    """AI 종합 점수 0~100. 컴포넌트 누락 시 0.5 중립값."""
    if factor_snapshot:
        pctiles = [
            factor_snapshot.get(k)
            for k in ("r1m_pctile", "r3m_pctile", "r6m_pctile", "r12m_pctile")
            if factor_snapshot.get(k) is not None
        ]
        factor_score = sum(pctiles) / len(pctiles) if pctiles else 0.5
    else:
        factor_score = 0.5

    if avg_post_return_3m is None:
        hist_score = 0.5
    else:
        hist_score = _clamp(float(avg_post_return_3m) / 30.0)

    consensus_score = _CONSENSUS_MAP.get(
        (consensus or "").upper(), 0.5
    )

    score = (
        _AI_SCORE_WEIGHTS["factor"] * factor_score
        + _AI_SCORE_WEIGHTS["hist"] * hist_score
        + _AI_SCORE_WEIGHTS["consensus"] * consensus_score
    )
    return {
        "ai_score": round(score * 100),
        "factor_score": round(factor_score, 4),
        "hist_score": round(hist_score, 4),
        "consensus_score": round(consensus_score, 4),
    }


@router.get("/{ticker}/overview")
def get_stock_overview(
    ticker: str,
    market: str = Query(default="", description="시장 코드"),
):
    """Cockpit Hero 종합 응답 — 메타 + 최신가 + 추천 통계 + AI 종합 점수."""
    cfg = AppConfig()
    tk = ticker.strip().upper()
    mk = (market or "").strip().upper()

    conn = get_connection(cfg.db)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 1) 종목 메타 — stock_universe 우선
            if mk:
                cur.execute("""
                    SELECT name, sector, industry, currency, market
                    FROM stock_universe
                    WHERE UPPER(ticker) = %s AND UPPER(market) = %s
                    LIMIT 1
                """, (tk, mk))
            else:
                cur.execute("""
                    SELECT name, sector, industry, currency, market
                    FROM stock_universe
                    WHERE UPPER(ticker) = %s
                    ORDER BY (CASE WHEN listing_status='active' THEN 0 ELSE 1 END)
                    LIMIT 1
                """, (tk,))
            meta = cur.fetchone() or {}

            # 2) 최신 2 거래일 종가 — 변동률 계산용
            cur.execute("""
                SELECT trade_date, close, volume
                FROM stock_universe_ohlcv
                WHERE UPPER(ticker) = %s
                  AND (%s = '' OR UPPER(market) = %s)
                ORDER BY trade_date DESC
                LIMIT 2
            """, (tk, mk, mk))
            latest_rows = cur.fetchall()

            # 3) 추천 통계 — 같은 ticker 모든 proposals 집계
            cur.execute("""
                SELECT
                    COUNT(*) AS proposal_count,
                    AVG(post_return_3m_pct) AS avg_post_return_3m_pct,
                    AVG(alpha_vs_benchmark_pct) AS avg_alpha_vs_benchmark_pct,
                    (
                        SELECT analyst_recommendation
                        FROM investment_proposals
                        WHERE UPPER(ticker) = %s
                          AND analyst_recommendation IS NOT NULL
                        ORDER BY created_at DESC LIMIT 1
                    ) AS latest_consensus
                FROM investment_proposals
                WHERE UPPER(ticker) = %s
            """, (tk, tk))
            stats = cur.fetchone() or {}

            # 4) 최신 factor_snapshot — 가장 최근 추천에서
            cur.execute("""
                SELECT factor_snapshot
                FROM investment_proposals
                WHERE UPPER(ticker) = %s
                  AND factor_snapshot IS NOT NULL
                ORDER BY created_at DESC LIMIT 1
            """, (tk,))
            factor_row = cur.fetchone() or {}
    finally:
        conn.close()

    # 최신가 + 변동률
    latest = None
    if latest_rows:
        last = latest_rows[0]
        prev = latest_rows[1] if len(latest_rows) >= 2 else None
        change_pct = None
        if prev and prev.get("close") and float(prev["close"]) > 0:
            change_pct = round(
                (float(last["close"]) - float(prev["close"])) / float(prev["close"]) * 100,
                2,
            )
        latest = {
            "date": last["trade_date"].isoformat(),
            "close": float(last["close"]) if last.get("close") is not None else None,
            "change_pct": change_pct,
            "volume": int(last["volume"]) if last.get("volume") is not None else None,
            "source": "ohlcv_db",
        }

    score = _compute_ai_score(
        factor_row.get("factor_snapshot"),
        stats.get("avg_post_return_3m_pct"),
        stats.get("latest_consensus"),
    )

    return {
        "ticker": tk,
        "market": meta.get("market") or mk or None,
        "name": meta.get("name") or tk,
        "sector": meta.get("sector"),
        "industry": meta.get("industry"),
        "currency": meta.get("currency"),
        "latest": latest,
        "stats": {
            "ai_score": score["ai_score"],
            "proposal_count": int(stats.get("proposal_count") or 0),
            "avg_post_return_3m_pct": (
                round(float(stats["avg_post_return_3m_pct"]), 2)
                if stats.get("avg_post_return_3m_pct") is not None else None
            ),
            "alpha_vs_benchmark_pct": (
                round(float(stats["avg_alpha_vs_benchmark_pct"]), 2)
                if stats.get("avg_alpha_vs_benchmark_pct") is not None else None
            ),
            "factor_pctile_avg": (
                round(score["factor_score"], 4) if factor_row.get("factor_snapshot") else None
            ),
        },
        "score_breakdown": {
            "factor_score": score["factor_score"],
            "hist_score": score["hist_score"],
            "consensus_score": score["consensus_score"],
            "weights": _AI_SCORE_WEIGHTS,
        },
    }
```

상단 import 확인 — `RealDictCursor`, `Depends`, `AppConfig` 이미 있음. `RealDictCursor` 가 stocks.py 에 import 안 돼있으면 추가:

```python
from psycopg2.extras import RealDictCursor
```

- [ ] **Step 1.4: Run test to verify it passes**

Run: `pytest tests/test_stock_cockpit.py::TestStockOverviewAPI::test_overview_returns_hero_payload -v`
Expected: PASS

- [ ] **Step 1.5: Add edge-case test (추천 0건)**

`tests/test_stock_cockpit.py` 동일 클래스에 추가:

```python
    def test_overview_zero_proposals_uses_neutral_score(self):
        from api.routes.stocks import get_stock_overview
        from shared.config import DatabaseConfig

        meta_row = {"name": "Foo", "sector": None, "industry": None,
                    "currency": "USD", "market": "NASDAQ"}
        latest_rows = []
        stats_row = {
            "proposal_count": 0, "avg_post_return_3m_pct": None,
            "avg_alpha_vs_benchmark_pct": None, "latest_consensus": None,
        }
        factor_row = {}

        conn = _fake_conn([meta_row, latest_rows, stats_row, factor_row])

        with patch("api.routes.stocks.get_connection", return_value=conn):
            result = get_stock_overview(ticker="FOO", market="NASDAQ")

        # 모든 컴포넌트 중립 0.5 → ai_score = 50
        assert result["stats"]["ai_score"] == 50
        assert result["latest"] is None
        assert result["stats"]["proposal_count"] == 0
        assert result["stats"]["avg_post_return_3m_pct"] is None
```

- [ ] **Step 1.6: Run all overview tests**

Run: `pytest tests/test_stock_cockpit.py::TestStockOverviewAPI -v`
Expected: 2 PASSED

- [ ] **Step 1.7: Commit**

```bash
git add api/routes/stocks.py tests/test_stock_cockpit.py
git commit -m "feat(cockpit): GET /api/stocks/{ticker}/overview — Hero 종합 응답 + AI 종합 점수"
```

---

## Task 2: `GET /api/stocks/{ticker}/proposals` — 추천 이력 타임라인

**Files:**
- Modify: `api/routes/stocks.py`
- Test: `tests/test_stock_cockpit.py`

**응답 스키마:**

```json
{
  "ticker": "TXN",
  "count": 4,
  "items": [
    {
      "proposal_id": 12345,
      "analysis_date": "2026-04-15",
      "created_at": "2026-04-15T08:30:00",
      "theme_id": 99,
      "theme_name": "AI 반도체 인프라",
      "theme_validity": "active",
      "action": "buy",
      "conviction": "high",
      "discovery_type": "early_signal",
      "rationale": "엣지 AI 침투 가속, ...",
      "entry_price": 245.30,
      "target_price_low": 290.00,
      "target_price_high": 310.00,
      "post_return_1m_pct": 8.4,
      "post_return_3m_pct": 12.4,
      "post_return_6m_pct": null,
      "post_return_1y_pct": null,
      "max_drawdown_pct": -6.2,
      "max_drawdown_date": "2026-04-20",
      "alpha_vs_benchmark_pct": 5.1,
      "validation_mismatches": [
        {"field_name": "current_price", "mismatch_pct": -2.1}
      ]
    }
  ]
}
```

- [ ] **Step 2.1: Write the failing test**

`tests/test_stock_cockpit.py` 에 클래스 추가:

```python
class TestStockProposalsAPI:
    """GET /api/stocks/{ticker}/proposals"""

    def test_proposals_returns_timeline(self):
        from api.routes.stocks import get_stock_proposals
        from shared.config import DatabaseConfig

        prop_rows = [
            {
                "id": 12345, "analysis_date": date(2026, 4, 15),
                "created_at": date(2026, 4, 15), "theme_id": 99,
                "theme_name": "AI 반도체 인프라", "theme_validity": "active",
                "action": "buy", "conviction": "high",
                "discovery_type": "early_signal",
                "rationale": "엣지 AI 침투 가속", "entry_price": Decimal("245.30"),
                "target_price_low": Decimal("290.00"),
                "target_price_high": Decimal("310.00"),
                "post_return_1m_pct": Decimal("8.4"),
                "post_return_3m_pct": Decimal("12.4"),
                "post_return_6m_pct": None, "post_return_1y_pct": None,
                "max_drawdown_pct": Decimal("-6.2"),
                "max_drawdown_date": date(2026, 4, 20),
                "alpha_vs_benchmark_pct": Decimal("5.1"),
            },
        ]
        validation_rows = [
            {"proposal_id": 12345, "field_name": "current_price",
             "mismatch_pct": Decimal("-2.1"), "mismatch": True},
        ]

        conn = _fake_conn([prop_rows, validation_rows])

        with patch("api.routes.stocks.get_connection", return_value=conn):
            result = get_stock_proposals(ticker="TXN")

        assert result["ticker"] == "TXN"
        assert result["count"] == 1
        item = result["items"][0]
        assert item["proposal_id"] == 12345
        assert item["theme_name"] == "AI 반도체 인프라"
        assert item["entry_price"] == 245.30
        assert item["post_return_3m_pct"] == 12.4
        assert item["max_drawdown_pct"] == -6.2
        assert item["max_drawdown_date"] == "2026-04-20"
        assert len(item["validation_mismatches"]) == 1
        assert item["validation_mismatches"][0]["field_name"] == "current_price"

    def test_proposals_empty_for_unknown_ticker(self):
        from api.routes.stocks import get_stock_proposals
        from shared.config import DatabaseConfig

        conn = _fake_conn([[], []])
        with patch("api.routes.stocks.get_connection", return_value=conn):
            result = get_stock_proposals(ticker="UNKNOWN")

        assert result["count"] == 0
        assert result["items"] == []
```

- [ ] **Step 2.2: Run test to verify it fails**

Run: `pytest tests/test_stock_cockpit.py::TestStockProposalsAPI -v`
Expected: FAIL — `ImportError: cannot import name 'get_stock_proposals'`

- [ ] **Step 2.3: Implement `get_stock_proposals` 핸들러**

`api/routes/stocks.py` Task 1 핸들러 아래에 추가:

```python
@router.get("/{ticker}/proposals")
def get_stock_proposals(ticker: str):
    """이 종목의 모든 investment_proposals 시계열 + validation_log 조인."""
    cfg = AppConfig()
    tk = ticker.strip().upper()

    conn = get_connection(cfg.db)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    p.id, s.analysis_date, p.created_at,
                    t.id AS theme_id, t.theme_name, t.theme_validity,
                    p.action, p.conviction, p.discovery_type,
                    p.rationale, p.entry_price,
                    p.target_price_low, p.target_price_high,
                    p.post_return_1m_pct, p.post_return_3m_pct,
                    p.post_return_6m_pct, p.post_return_1y_pct,
                    p.max_drawdown_pct, p.max_drawdown_date,
                    p.alpha_vs_benchmark_pct
                FROM investment_proposals p
                JOIN investment_themes t ON p.theme_id = t.id
                JOIN analysis_sessions s ON t.session_id = s.id
                WHERE UPPER(p.ticker) = %s
                ORDER BY s.analysis_date DESC, p.id DESC
            """, (tk,))
            prop_rows = cur.fetchall()

            if prop_rows:
                proposal_ids = [r["id"] for r in prop_rows]
                cur.execute("""
                    SELECT proposal_id, field_name, mismatch_pct, mismatch
                    FROM proposal_validation_log
                    WHERE proposal_id = ANY(%s) AND mismatch = TRUE
                """, (proposal_ids,))
                validation_rows = cur.fetchall()
            else:
                validation_rows = []
    finally:
        conn.close()

    # 검증 mismatch 를 proposal_id 별로 그룹화
    mismatches_by_pid = {}
    for vr in validation_rows:
        pid = vr["proposal_id"]
        mismatches_by_pid.setdefault(pid, []).append({
            "field_name": vr["field_name"],
            "mismatch_pct": (
                round(float(vr["mismatch_pct"]), 2)
                if vr.get("mismatch_pct") is not None else None
            ),
        })

    def _f(v):
        return float(v) if v is not None else None

    def _d(v):
        return v.isoformat() if v is not None else None

    items = []
    for r in prop_rows:
        items.append({
            "proposal_id": r["id"],
            "analysis_date": _d(r["analysis_date"]),
            "created_at": _d(r["created_at"]),
            "theme_id": r["theme_id"],
            "theme_name": r["theme_name"],
            "theme_validity": r["theme_validity"],
            "action": r["action"],
            "conviction": r["conviction"],
            "discovery_type": r["discovery_type"],
            "rationale": r["rationale"],
            "entry_price": _f(r["entry_price"]),
            "target_price_low": _f(r["target_price_low"]),
            "target_price_high": _f(r["target_price_high"]),
            "post_return_1m_pct": _f(r["post_return_1m_pct"]),
            "post_return_3m_pct": _f(r["post_return_3m_pct"]),
            "post_return_6m_pct": _f(r["post_return_6m_pct"]),
            "post_return_1y_pct": _f(r["post_return_1y_pct"]),
            "max_drawdown_pct": _f(r["max_drawdown_pct"]),
            "max_drawdown_date": _d(r["max_drawdown_date"]),
            "alpha_vs_benchmark_pct": _f(r["alpha_vs_benchmark_pct"]),
            "validation_mismatches": mismatches_by_pid.get(r["id"], []),
        })

    return {"ticker": tk, "count": len(items), "items": items}
```

- [ ] **Step 2.4: Run tests**

Run: `pytest tests/test_stock_cockpit.py::TestStockProposalsAPI -v`
Expected: 2 PASSED

- [ ] **Step 2.5: Commit**

```bash
git add api/routes/stocks.py tests/test_stock_cockpit.py
git commit -m "feat(cockpit): GET /api/stocks/{ticker}/proposals — 추천 이력 타임라인 + validation_log 조인"
```

---

## Task 3: 새 템플릿 `stock_cockpit.html` — 페이지 구조 + Hero + 펀더멘털 흡수

**Files:**
- Create: `api/templates/stock_cockpit.html`
- Modify: `api/routes/stocks.py` ([`stock_fundamentals_page`](../api/routes/stocks.py#L169) 가 새 템플릿 렌더)

이 task 는 페이지 골격만 — 차트 자리는 placeholder, 추천 타임라인 자리도 placeholder. 다음 task 들에서 채운다.

- [ ] **Step 3.1: Write the failing test (페이지 200 + 핵심 문자열)**

`tests/test_stock_cockpit.py` 에 클래스 추가 (test_pages_new.py 패턴 차용):

```python
from contextlib import contextmanager


def _patch_fake_conn_for_base_ctx():
    cur = MagicMock()
    cur.fetchone.return_value = [0]

    @contextmanager
    def _cursor(**kwargs):
        yield cur

    conn = MagicMock()
    conn.cursor = _cursor
    return conn


@pytest.fixture
def patched_base_ctx_conn(monkeypatch):
    fake = _patch_fake_conn_for_base_ctx()
    monkeypatch.setattr("api.routes.pages.get_connection", lambda cfg: fake)
    monkeypatch.setattr("shared.db.init_db", lambda cfg: None)
    return fake


def _make_client():
    from fastapi.testclient import TestClient
    from api.main import app
    return TestClient(app)


class TestStockCockpitPage:
    def test_cockpit_page_returns_200(self, patched_base_ctx_conn):
        client = _make_client()
        resp = client.get("/pages/stocks/TXN?market=NASDAQ")
        assert resp.status_code == 200
        body = resp.text
        # 새 템플릿이 렌더됐다는 시그니처
        assert "stock-cockpit" in body
        # API 엔드포인트 경로 (JS 가 호출하는 것들)
        assert "/api/stocks/TXN/overview" in body or "/api/stocks/" in body
        assert "/api/stocks/" in body and "/proposals" in body
        # 펀더멘털 8카드는 흡수됨 (기존 호환)
        assert "valuation-metrics" in body
```

- [ ] **Step 3.2: Run test to verify it fails**

Run: `pytest tests/test_stock_cockpit.py::TestStockCockpitPage -v`
Expected: FAIL — 기존 `stock_fundamentals.html` 에는 "stock-cockpit" 문자열 없음

- [ ] **Step 3.3: Create `api/templates/stock_cockpit.html`**

Hero + 펀더멘털 카드 + 차트/타임라인 placeholder. JS 는 Hero 채우는 것만 작성(차트/타임라인 JS 는 Task 4~6 에서 추가):

```html
{% extends "base.html" %}
{% block title %}{{ ticker }} Cockpit — AlphaSignal{% endblock %}
{% block page_title %}
<span id="stock-name">{{ ticker }}</span>
{% endblock %}

{% block header_actions %}
<a href="/pages/proposals/history/{{ ticker }}" class="btn"
   style="font-size:13px;padding:5px 14px;background:var(--bg-card);
          border:1px solid var(--border);border-radius:6px;
          color:var(--text-muted);text-decoration:none;">추천 이력</a>
{% endblock %}

{% block content %}
<div id="stock-cockpit" data-ticker="{{ ticker }}" data-market="{{ market }}">

  {# ─────────────── § Hero ─────────────── #}
  <section class="cockpit-hero" id="hero">
    <div class="hero-loading" id="hero-loading">Loading...</div>
    <div class="hero-body" id="hero-body" style="display:none;">
      <div class="hero-price-row">
        <div>
          <span id="h-price" class="hero-price"></span>
          <span id="h-change" class="hero-change"></span>
        </div>
        <div class="hero-meta">
          <span>시총 <strong id="h-mcap">-</strong></span>
          <span>거래량 <strong id="h-volume">-</strong></span>
          <span id="h-sector-wrap">업종 <strong id="h-sector">-</strong></span>
        </div>
      </div>
      <div class="hero-chips">
        <div class="hero-chip" title="" id="chip-ai">
          <div class="chip-label">AI 종합</div>
          <div class="chip-value" id="h-ai-score">-</div>
        </div>
        <div class="hero-chip">
          <div class="chip-label">우리 추천</div>
          <div class="chip-value" id="h-prop-count">-</div>
        </div>
        <div class="hero-chip">
          <div class="chip-label">평균 사후 3m</div>
          <div class="chip-value" id="h-avg-3m">-</div>
        </div>
        <div class="hero-chip">
          <div class="chip-label">벤치 알파</div>
          <div class="chip-value" id="h-alpha">-</div>
        </div>
        <div class="hero-chip">
          <div class="chip-label">팩터 분위</div>
          <div class="chip-value" id="h-factor">-</div>
        </div>
      </div>
    </div>
  </section>

  {# ─────────────── § 1. 가격 차트 ─────────────── #}
  <section class="cockpit-section" id="sec-chart">
    <div class="cockpit-section-head">
      <h3>가격 차트</h3>
      <div class="chart-range-toggle" id="range-toggle">
        <button data-range="60">1M</button>
        <button data-range="180">3M</button>
        <button data-range="360" class="active">6M</button>
        <button data-range="720">1Y</button>
        <button data-range="1080">3Y</button>
      </div>
    </div>
    <div id="price-chart" style="height:380px;background:var(--bg-card);
         border:1px solid var(--border);border-radius:10px;">
      <div class="chart-placeholder">차트 로드 중...</div>
    </div>
  </section>

  {# ─────────────── § 2-A. 벤치마크 비교 ─────────────── #}
  <section class="cockpit-section" id="sec-benchmark">
    <div class="cockpit-section-head">
      <h3>벤치마크 상대성과 (=100 정규화)</h3>
      <div class="benchmark-toggle" id="benchmark-toggle">
        <button data-bench="" class="active">자동</button>
      </div>
    </div>
    <div id="benchmark-chart" style="height:260px;background:var(--bg-card);
         border:1px solid var(--border);border-radius:10px;">
      <div class="chart-placeholder">벤치마크 로드 중...</div>
    </div>
  </section>

  {# ─────────────── § 4. 펀더멘털 8카드 (기존 흡수) ─────────────── #}
  <section class="cockpit-section" id="sec-fundamentals">
    <div class="cockpit-section-head"><h3>펀더멘털</h3></div>
    <div id="fundamentals-error" style="display:none;color:var(--text-muted);
         padding:14px;background:var(--bg-card);border-radius:10px;
         border:1px solid var(--border);">
      외부 데이터 일시 조회 실패 — 다른 섹션은 정상 동작합니다.
    </div>
    <div class="fund-grid" id="fundamentals-grid">
      <div class="fund-card"><h4 class="fund-card-title">밸류에이션</h4>
        <div class="fund-metrics" id="valuation-metrics"></div></div>
      <div class="fund-card"><h4 class="fund-card-title">수익성</h4>
        <div class="fund-metrics" id="profitability-metrics"></div></div>
      <div class="fund-card"><h4 class="fund-card-title">재무건전성</h4>
        <div class="fund-metrics" id="health-metrics"></div></div>
      <div class="fund-card"><h4 class="fund-card-title">성장성</h4>
        <div class="fund-metrics" id="growth-metrics"></div></div>
      <div class="fund-card"><h4 class="fund-card-title">배당</h4>
        <div class="fund-metrics" id="dividend-metrics"></div></div>
      <div class="fund-card"><h4 class="fund-card-title">현금흐름</h4>
        <div class="fund-metrics" id="cashflow-metrics"></div></div>
      <div class="fund-card"><h4 class="fund-card-title">기술 지표</h4>
        <div class="fund-metrics" id="technical-metrics"></div></div>
      <div class="fund-card"><h4 class="fund-card-title">애널리스트 컨센서스</h4>
        <div class="fund-metrics" id="analyst-metrics"></div></div>
    </div>
    <div style="margin-top:8px;font-size:11px;color:var(--text-muted);text-align:center;">
      * 펀더멘털 데이터: yfinance (1시간 캐싱). Hero 가격은 자체 OHLCV 우선.
    </div>
  </section>

  {# ─────────────── § 6. 추천 이력 타임라인 ─────────────── #}
  <section class="cockpit-section" id="sec-timeline">
    <div class="cockpit-section-head"><h3>AI 추천 이력</h3></div>
    <div id="timeline-empty" style="display:none;padding:30px;text-align:center;
         color:var(--text-muted);background:var(--bg-card);border-radius:10px;
         border:1px solid var(--border);">
      이 종목은 아직 추천 이력이 없습니다.
    </div>
    <div id="timeline-list" class="timeline-list"></div>
  </section>

</div>
{% endblock %}

{% block scripts %}
<style>
.cockpit-hero {
  background:var(--bg-card); border:1px solid var(--border); border-radius:10px;
  padding:18px 22px; margin-bottom:18px;
}
.hero-price-row { display:flex; justify-content:space-between; align-items:flex-end;
  flex-wrap:wrap; gap:12px; margin-bottom:14px; }
.hero-price { font-size:30px; font-weight:700; }
.hero-change { font-size:16px; margin-left:8px; }
.hero-meta { display:flex; gap:18px; flex-wrap:wrap; color:var(--text-muted); font-size:13px; }
.hero-meta strong { color:var(--text); }
.hero-chips { display:grid; grid-template-columns:repeat(auto-fit, minmax(120px, 1fr));
  gap:10px; }
.hero-chip { background:var(--bg); border:1px solid var(--border); border-radius:8px;
  padding:10px 12px; text-align:center; cursor:default; }
.hero-chip[title]:hover { border-color:var(--accent); }
.chip-label { font-size:11px; color:var(--text-muted); margin-bottom:4px; }
.chip-value { font-size:18px; font-weight:700; font-variant-numeric:tabular-nums; }

.cockpit-section { margin-bottom:22px; }
.cockpit-section-head { display:flex; justify-content:space-between; align-items:center;
  margin-bottom:10px; }
.cockpit-section-head h3 { font-size:15px; color:var(--accent); margin:0; }
.chart-placeholder { text-align:center; padding:60px 0; color:var(--text-muted); }
.chart-range-toggle, .benchmark-toggle { display:flex; gap:4px; }
.chart-range-toggle button, .benchmark-toggle button {
  background:var(--bg-card); border:1px solid var(--border); color:var(--text-muted);
  padding:4px 10px; font-size:12px; border-radius:5px; cursor:pointer;
}
.chart-range-toggle button.active, .benchmark-toggle button.active {
  border-color:var(--accent); color:var(--accent);
}

.fund-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(280px, 1fr));
  gap:14px; }
.fund-card { background:var(--bg-card); border:1px solid var(--border);
  border-radius:10px; padding:16px 18px; }
.fund-card-title { font-size:13px; font-weight:600; color:var(--accent);
  margin:0 0 10px 0; padding-bottom:6px; border-bottom:1px solid var(--border); }
.fund-metrics { display:flex; flex-direction:column; gap:6px; }
.fund-metric-row { display:flex; justify-content:space-between; font-size:13px; }
.fund-metric-label { color:var(--text-muted); }
.fund-metric-value { font-weight:600; font-variant-numeric:tabular-nums; }
.fund-metric-value.positive { color:var(--green); }
.fund-metric-value.negative { color:var(--red); }

.timeline-list { display:flex; flex-direction:column; gap:10px; }
.timeline-card { background:var(--bg-card); border:1px solid var(--border);
  border-radius:10px; padding:14px 16px; }
.timeline-card-head { display:flex; justify-content:space-between;
  align-items:center; margin-bottom:6px; font-size:13px; }
.timeline-date { color:var(--text-muted); }
.timeline-theme { color:var(--accent); font-weight:600; }
.timeline-rationale { font-size:13px; color:var(--text); line-height:1.5;
  margin:6px 0 8px 0; }
.timeline-metrics { display:flex; gap:14px; flex-wrap:wrap; font-size:12px;
  color:var(--text-muted); }
.timeline-metrics strong { color:var(--text); font-variant-numeric:tabular-nums; }
.tl-pos { color:var(--green); }
.tl-neg { color:var(--red); }

@media (max-width: 768px) {
  .fund-grid { grid-template-columns:1fr; }
  .hero-price-row { flex-direction:column; align-items:flex-start; }
}
</style>

<script>
(function() {
  var ticker = document.getElementById('stock-cockpit').dataset.ticker;
  var market = document.getElementById('stock-cockpit').dataset.market;

  var CURRENCY_SYMBOLS = {KRW:'₩', USD:'$', EUR:'€', JPY:'¥', GBP:'£', CNY:'¥', HKD:'HK$', TWD:'NT$'};
  var INT_CURRENCIES = ['KRW', 'JPY'];

  function fmtPrice(v, cur) {
    if (v == null) return '-';
    var sym = CURRENCY_SYMBOLS[cur] || '';
    if (INT_CURRENCIES.indexOf(cur) >= 0) return sym + v.toLocaleString('ko-KR', {maximumFractionDigits:0});
    return sym + v.toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
  }
  function fmtBigNum(v, cur) {
    if (v == null) return '-';
    var sym = CURRENCY_SYMBOLS[cur] || '';
    if (Math.abs(v) >= 1e12) return sym + (v/1e12).toFixed(1) + '조';
    if (Math.abs(v) >= 1e8) return sym + Math.round(v/1e8) + '억';
    if (Math.abs(v) >= 1e6) return sym + (v/1e6).toFixed(1) + 'M';
    return sym + v.toLocaleString();
  }
  function fmtPct(v, withSign) {
    if (v == null) return '-';
    var s = (withSign && v > 0 ? '+' : '') + v.toFixed(2) + '%';
    return s;
  }

  // ── Hero (overview) ──
  var qs = market ? ('?market=' + encodeURIComponent(market)) : '';
  fetch('/api/stocks/' + encodeURIComponent(ticker) + '/overview' + qs)
    .then(function(r) { return r.ok ? r.json() : Promise.reject(r.status); })
    .then(function(d) {
      document.getElementById('hero-loading').style.display = 'none';
      document.getElementById('hero-body').style.display = 'block';
      document.getElementById('stock-name').textContent = (d.name || ticker) + ' (' + ticker + ')';

      var cur = d.currency || '';
      if (d.latest) {
        document.getElementById('h-price').textContent = fmtPrice(d.latest.close, cur);
        var ch = document.getElementById('h-change');
        if (d.latest.change_pct != null) {
          ch.textContent = fmtPct(d.latest.change_pct, true);
          ch.style.color = d.latest.change_pct >= 0 ? 'var(--green)' : 'var(--red)';
        }
        document.getElementById('h-volume').textContent =
          d.latest.volume ? d.latest.volume.toLocaleString() + '주' : '-';
      }
      document.getElementById('h-sector').textContent =
        (d.sector || '-') + (d.industry ? ' / ' + d.industry : '');

      var s = d.stats || {};
      document.getElementById('h-ai-score').textContent =
        (s.ai_score != null ? s.ai_score : '-') + (s.ai_score != null ? '/100' : '');
      document.getElementById('h-prop-count').textContent =
        (s.proposal_count != null ? s.proposal_count + '회' : '-');
      document.getElementById('h-avg-3m').textContent = fmtPct(s.avg_post_return_3m_pct, true);
      document.getElementById('h-alpha').textContent = fmtPct(s.alpha_vs_benchmark_pct, true);
      document.getElementById('h-factor').textContent =
        s.factor_pctile_avg != null ? Math.round(s.factor_pctile_avg * 100) + '%ile' : '-';

      // AI 종합 점수 tooltip 산식
      var sb = d.score_breakdown || {};
      if (sb.weights) {
        document.getElementById('chip-ai').title =
          'factor ' + sb.factor_score + ' × ' + sb.weights.factor +
          ' + hist ' + sb.hist_score + ' × ' + sb.weights.hist +
          ' + consensus ' + sb.consensus_score + ' × ' + sb.weights.consensus;
      }
    })
    .catch(function() {
      document.getElementById('hero-loading').textContent = 'Hero 데이터 조회 실패';
    });

  // ── 펀더멘털 8카드 (기존 fundamentals API 그대로 사용) ──
  fetch('/api/stocks/' + encodeURIComponent(ticker) + '/fundamentals' + qs)
    .then(function(r) { return r.ok ? r.json() : Promise.reject(r.status); })
    .then(function(d) {
      renderFundamentals(d);
    })
    .catch(function() {
      document.getElementById('fundamentals-error').style.display = 'block';
      document.getElementById('fundamentals-grid').style.display = 'none';
    });

  function pctClass(v) {
    if (v == null) return '';
    return v > 0 ? 'positive' : v < 0 ? 'negative' : '';
  }
  function addMetric(id, label, value, cls) {
    if (value == null) value = '-';
    var c = document.getElementById(id);
    if (!c) return;
    var row = document.createElement('div');
    row.className = 'fund-metric-row';
    row.innerHTML = '<span class="fund-metric-label">' + label + '</span>' +
                    '<span class="fund-metric-value' + (cls ? ' ' + cls : '') + '">' + value + '</span>';
    c.appendChild(row);
  }

  function renderFundamentals(d) {
    var cur = d.currency || '';
    var v = d.valuation || {};
    addMetric('valuation-metrics', 'PER (Trailing)', v.trailing_pe);
    addMetric('valuation-metrics', 'PER (Forward)', v.forward_pe);
    addMetric('valuation-metrics', 'PBR', v.pb_ratio);
    addMetric('valuation-metrics', 'PSR', v.ps_ratio);
    addMetric('valuation-metrics', 'PEG', v.peg_ratio);
    addMetric('valuation-metrics', 'EV/EBITDA', v.ev_ebitda);
    addMetric('valuation-metrics', 'EPS (Trailing)', fmtPrice(v.eps_trailing, cur));
    addMetric('valuation-metrics', 'EPS (Forward)', fmtPrice(v.eps_forward, cur));

    var p = d.profitability || {};
    addMetric('profitability-metrics', 'ROE', fmtPct(p.roe, true), pctClass(p.roe));
    addMetric('profitability-metrics', 'ROA', fmtPct(p.roa, true), pctClass(p.roa));
    addMetric('profitability-metrics', '매출총이익률', fmtPct(p.gross_margin, true), pctClass(p.gross_margin));
    addMetric('profitability-metrics', '영업이익률', fmtPct(p.operating_margin, true), pctClass(p.operating_margin));
    addMetric('profitability-metrics', '순이익률', fmtPct(p.net_margin, true), pctClass(p.net_margin));
    addMetric('profitability-metrics', 'EBITDA', fmtBigNum(p.ebitda, cur));

    var h = d.health || {};
    addMetric('health-metrics', '부채비율 (D/E)', h.debt_to_equity);
    addMetric('health-metrics', '유동비율', h.current_ratio);
    addMetric('health-metrics', '당좌비율', h.quick_ratio);
    addMetric('health-metrics', '총 부채', fmtBigNum(h.total_debt, cur));
    addMetric('health-metrics', '보유 현금', fmtBigNum(h.total_cash, cur));

    var g = d.growth || {};
    addMetric('growth-metrics', '매출 성장률 (YoY)', fmtPct(g.revenue_growth, true), pctClass(g.revenue_growth));
    addMetric('growth-metrics', '이익 성장률 (YoY)', fmtPct(g.earnings_growth, true), pctClass(g.earnings_growth));
    addMetric('growth-metrics', '분기 이익 성장률', fmtPct(g.earnings_quarterly_growth, true), pctClass(g.earnings_quarterly_growth));

    var dv = d.dividend || {};
    addMetric('dividend-metrics', '배당수익률', fmtPct(dv.dividend_yield, true));
    addMetric('dividend-metrics', '배당금', dv.dividend_rate != null ? fmtPrice(dv.dividend_rate, cur) : '-');
    addMetric('dividend-metrics', '배당성향', fmtPct(dv.payout_ratio, true));

    var cf = d.cashflow || {};
    addMetric('cashflow-metrics', '영업현금흐름', fmtBigNum(cf.operating_cashflow, cur), pctClass(cf.operating_cashflow));
    addMetric('cashflow-metrics', '잉여현금흐름 (FCF)', fmtBigNum(cf.free_cashflow, cur), pctClass(cf.free_cashflow));

    var t = d.technical || {};
    addMetric('technical-metrics', 'Beta', t.beta);
    addMetric('technical-metrics', '50일 이동평균', fmtPrice(t.fifty_day_avg, cur));
    addMetric('technical-metrics', '200일 이동평균', fmtPrice(t.two_hundred_day_avg, cur));
    if (t.fifty_day_avg && d.price) {
      var vs50 = ((d.price - t.fifty_day_avg) / t.fifty_day_avg * 100);
      addMetric('technical-metrics', '50일선 대비', fmtPct(vs50, true), vs50 >= 0 ? 'positive' : 'negative');
    }
    if (t.two_hundred_day_avg && d.price) {
      var vs200 = ((d.price - t.two_hundred_day_avg) / t.two_hundred_day_avg * 100);
      addMetric('technical-metrics', '200일선 대비', fmtPct(vs200, true), vs200 >= 0 ? 'positive' : 'negative');
    }

    var a = d.analyst || {};
    addMetric('analyst-metrics', '추천', a.recommendation ? a.recommendation.toUpperCase() : '-');
    addMetric('analyst-metrics', '목표가 (평균)', fmtPrice(a.target_mean, cur));
    addMetric('analyst-metrics', '목표가 (저)', fmtPrice(a.target_low, cur));
    addMetric('analyst-metrics', '목표가 (고)', fmtPrice(a.target_high, cur));
    if (a.target_mean && d.price) {
      var upside = ((a.target_mean - d.price) / d.price * 100);
      addMetric('analyst-metrics', '상승여력', fmtPct(upside, true), upside >= 0 ? 'positive' : 'negative');
    }
    addMetric('analyst-metrics', '분석 기관 수', a.num_analysts != null ? a.num_analysts + '개' : '-');

    // Hero 시총이 비어있으면 펀더멘털 시총으로 폴백
    var mcapEl = document.getElementById('h-mcap');
    if (mcapEl.textContent === '-' && d.market_cap) {
      mcapEl.textContent = fmtBigNum(d.market_cap, cur);
    }
  }

  // 차트/타임라인 모듈은 다음 task 들에서 추가됨
  // (window.__cockpit = {ticker, market, qs} 로 공유)
  window.__cockpit = {ticker: ticker, market: market, qs: qs, fmtPrice: fmtPrice, fmtPct: fmtPct};
})();
</script>
{% endblock %}
```

- [ ] **Step 3.4: Modify route to render new template**

`api/routes/stocks.py` 의 [`stock_fundamentals_page`](../api/routes/stocks.py#L169) — 함수명 그대로, 템플릿 이름만 변경:

```python
@pages_router.get("/{ticker}")
def stock_fundamentals_page(
    ticker: str,
    market: str = Query(default="", description="시장 코드"),
    ctx: dict = Depends(make_page_ctx("proposals")),
):
    """Stock Cockpit — 종합 종목 페이지 (in-place 교체)."""
    return templates.TemplateResponse(request=ctx["request"], name="stock_cockpit.html", context={
        **ctx,
        "ticker": ticker.upper(),
        "market": market.upper(),
    })
```

- [ ] **Step 3.5: Run page test**

Run: `pytest tests/test_stock_cockpit.py::TestStockCockpitPage -v`
Expected: PASS

- [ ] **Step 3.6: Run all stock_cockpit tests**

Run: `pytest tests/test_stock_cockpit.py -v`
Expected: 5 PASSED (overview×2, proposals×2, page×1)

- [ ] **Step 3.7: Commit**

```bash
git add api/templates/stock_cockpit.html api/routes/stocks.py tests/test_stock_cockpit.py
git commit -m "feat(cockpit): stock_cockpit.html 신규 + Hero/펀더멘털 흡수, 라우트 교체"
```

---

## Task 4: § 1 가격 차트 + MA50/MA200 + 추천 마커 + 거래량 (lightweight-charts)

**Files:**
- Modify: `api/templates/stock_cockpit.html` (`{% block scripts %}` 추가 코드)

`/api/stocks/{ticker}/ohlcv` (기존) + `/api/stocks/{ticker}/proposals` (Task 2) 응답을 lightweight-charts 로 그린다.

- [ ] **Step 4.1: Write the failing test (페이지에 lightweight-charts CDN 포함)**

`tests/test_stock_cockpit.py::TestStockCockpitPage` 에 메서드 추가:

```python
    def test_cockpit_page_loads_chart_library(self, patched_base_ctx_conn):
        client = _make_client()
        resp = client.get("/pages/stocks/TXN?market=NASDAQ")
        body = resp.text
        # lightweight-charts CDN 로드 확인
        assert "lightweight-charts" in body
        # 차트 컨테이너
        assert 'id="price-chart"' in body
        # OHLCV API 경로
        assert "/api/stocks/TXN/ohlcv" in body or "/ohlcv?days" in body
```

- [ ] **Step 4.2: Run test to verify it fails**

Run: `pytest tests/test_stock_cockpit.py::TestStockCockpitPage::test_cockpit_page_loads_chart_library -v`
Expected: FAIL — "lightweight-charts" 문자열 없음

- [ ] **Step 4.3: Add CDN + 차트 JS to `stock_cockpit.html`**

`{% block scripts %}` 의 첫 줄(스타일 위) 에 CDN 추가:

```html
<script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
```

그리고 기존 `<script>` IIFE 끝부분(`window.__cockpit = ...` 다음 줄) 에 차트 IIFE 추가:

```javascript
// ── § 1 가격 차트 ──
(function() {
  var c = window.__cockpit;
  if (!c || typeof LightweightCharts === 'undefined') return;

  var container = document.getElementById('price-chart');
  container.innerHTML = '';
  var chart = LightweightCharts.createChart(container, {
    height: 380,
    layout: { background: { color: 'transparent' }, textColor: '#a0a0a0' },
    grid: { vertLines: { color: '#2a2a2a' }, horzLines: { color: '#2a2a2a' } },
    rightPriceScale: { borderColor: '#3a3a3a' },
    timeScale: { borderColor: '#3a3a3a', timeVisible: false },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  });

  var lineSeries = chart.addLineSeries({ color: '#4ea3ff', lineWidth: 2 });
  var ma50Series = chart.addLineSeries({
    color: '#f5a623', lineWidth: 1, title: 'MA50', priceLineVisible: false, lastValueVisible: false,
  });
  var ma200Series = chart.addLineSeries({
    color: '#9b59b6', lineWidth: 1, title: 'MA200', priceLineVisible: false, lastValueVisible: false,
  });
  var volSeries = chart.addHistogramSeries({
    color: '#3a3a3a', priceFormat: { type: 'volume' },
    priceScaleId: '', scaleMargins: { top: 0.85, bottom: 0 },
  });

  var currentRange = 360;
  var ohlcvCache = null;

  function movingAvg(series, n) {
    var out = []; var sum = 0; var q = [];
    for (var i = 0; i < series.length; i++) {
      var c = series[i].close;
      q.push(c); sum += c;
      if (q.length > n) sum -= q.shift();
      if (q.length === n) out.push({ time: series[i].date, value: +(sum / n).toFixed(4) });
    }
    return out;
  }

  function loadOhlcv(days) {
    var url = '/api/stocks/' + encodeURIComponent(c.ticker) + '/ohlcv?days=' + days +
              (c.market ? '&market=' + encodeURIComponent(c.market) : '');
    return fetch(url).then(function(r) { return r.ok ? r.json() : Promise.reject(r.status); });
  }

  function applyData(d) {
    if (!d.series || !d.series.length) {
      container.innerHTML = '<div class="chart-placeholder">OHLCV 데이터 수집 대기 중</div>';
      return;
    }
    var prices = d.series.map(function(p) { return { time: p.date, value: p.close }; });
    var vols = d.series.map(function(p) {
      var prev = null;
      return { time: p.date, value: p.volume || 0,
               color: (p.change_pct != null && p.change_pct < 0) ? '#c0392b66' : '#27ae6066' };
    });
    lineSeries.setData(prices);
    ma50Series.setData(movingAvg(d.series, 50));
    ma200Series.setData(movingAvg(d.series, 200));
    volSeries.setData(vols);

    // 추천 마커 (별도 fetch)
    fetch('/api/stocks/' + encodeURIComponent(c.ticker) + '/proposals')
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(p) {
        if (!p || !p.items || !p.items.length) return;
        var firstDate = d.series[0].date;
        var lastDate = d.series[d.series.length - 1].date;
        var markers = p.items
          .filter(function(it) {
            var dt = (it.created_at || it.analysis_date || '').slice(0, 10);
            return dt >= firstDate && dt <= lastDate;
          })
          .map(function(it) {
            var positive = (it.post_return_3m_pct == null) || it.post_return_3m_pct >= 0;
            return {
              time: (it.created_at || it.analysis_date).slice(0, 10),
              position: 'belowBar',
              color: positive ? '#27ae60' : '#c0392b',
              shape: 'arrowUp',
              text: '추천' + (it.entry_price ? ' @' + it.entry_price : ''),
            };
          });
        if (markers.length) lineSeries.setMarkers(markers);
      })
      .catch(function() { /* 마커는 옵션 */ });

    chart.timeScale().fitContent();
  }

  loadOhlcv(currentRange).then(function(d) { ohlcvCache = d; applyData(d); })
    .catch(function() {
      container.innerHTML = '<div class="chart-placeholder">차트 데이터 조회 실패</div>';
    });

  // 기간 토글
  document.querySelectorAll('#range-toggle button').forEach(function(btn) {
    btn.addEventListener('click', function() {
      document.querySelectorAll('#range-toggle button').forEach(function(b) { b.classList.remove('active'); });
      btn.classList.add('active');
      currentRange = parseInt(btn.dataset.range, 10);
      loadOhlcv(currentRange).then(applyData)
        .catch(function() { console.warn('차트 재로드 실패'); });
    });
  });
})();
```

- [ ] **Step 4.4: Run test**

Run: `pytest tests/test_stock_cockpit.py::TestStockCockpitPage -v`
Expected: 2 PASSED

- [ ] **Step 4.5: Manual smoke (선택, dev 서버 띄울 수 있는 경우)**

Run: `python -m api.main` 후 브라우저에서 `http://localhost:8000/pages/stocks/TXN?market=NASDAQ` 접속.
확인:
- 가격 차트 + MA50(주황) + MA200(보라) + 거래량 바 표시
- 1M/3M/6M/1Y/3Y 토글로 기간 전환
- 추천 이력 있는 종목(예: `005930` KOSPI 삼성전자)에서 ▲ 마커 표시

- [ ] **Step 4.6: Commit**

```bash
git add api/templates/stock_cockpit.html tests/test_stock_cockpit.py
git commit -m "feat(cockpit): § 1 가격 차트 + MA50/200 + 거래량 + 추천 마커 (lightweight-charts)"
```

---

## Task 5: § 2-A 벤치마크 상대성과 라인

**Files:**
- Modify: `api/templates/stock_cockpit.html`

종목 close 시리즈 와 벤치마크 인덱스 close 시리즈 를 둘 다 첫날=100 으로 정규화해서 한 차트에 그린다.

- [ ] **Step 5.1: Write the failing test**

`tests/test_stock_cockpit.py::TestStockCockpitPage` 에 메서드 추가:

```python
    def test_cockpit_page_includes_benchmark_section(self, patched_base_ctx_conn):
        client = _make_client()
        resp = client.get("/pages/stocks/TXN?market=NASDAQ")
        body = resp.text
        assert 'id="benchmark-chart"' in body
        # 벤치마크 API 경로
        assert "/api/indices/" in body
```

- [ ] **Step 5.2: Run test**

Run: `pytest tests/test_stock_cockpit.py::TestStockCockpitPage::test_cockpit_page_includes_benchmark_section -v`
Expected: FAIL

- [ ] **Step 5.3: Append benchmark IIFE to `stock_cockpit.html`**

§ 1 차트 IIFE 다음에 추가:

```javascript
// ── § 2-A 벤치마크 상대성과 ──
(function() {
  var c = window.__cockpit;
  if (!c || typeof LightweightCharts === 'undefined') return;

  // 시장 → 벤치마크 자동 선택
  var BENCH_MAP = {
    'KOSPI':  ['KOSPI', 'KOSDAQ'],
    'KOSDAQ': ['KOSDAQ', 'KOSPI'],
    'NASDAQ': ['NDX100', 'SP500'],
    'NYSE':   ['SP500', 'NDX100'],
  };
  var benches = BENCH_MAP[c.market] || ['SP500'];
  var defaultBench = benches[0];

  // 벤치마크 토글 버튼 동적 생성
  var toggleEl = document.getElementById('benchmark-toggle');
  toggleEl.innerHTML = '';
  benches.forEach(function(code, i) {
    var btn = document.createElement('button');
    btn.dataset.bench = code;
    btn.textContent = code;
    if (i === 0) btn.classList.add('active');
    toggleEl.appendChild(btn);
  });

  var container = document.getElementById('benchmark-chart');
  container.innerHTML = '';
  var chart = LightweightCharts.createChart(container, {
    height: 260,
    layout: { background: { color: 'transparent' }, textColor: '#a0a0a0' },
    grid: { vertLines: { color: '#2a2a2a' }, horzLines: { color: '#2a2a2a' } },
    rightPriceScale: { borderColor: '#3a3a3a' },
    timeScale: { borderColor: '#3a3a3a' },
  });
  var stockLine = chart.addLineSeries({ color: '#4ea3ff', lineWidth: 2, title: c.ticker });
  var benchLine = chart.addLineSeries({ color: '#f1c40f', lineWidth: 2, title: defaultBench });

  function normalize(series) {
    if (!series.length) return [];
    var base = series[0].close;
    if (!base || base === 0) return [];
    return series.map(function(p) { return { time: p.date, value: +(p.close / base * 100).toFixed(2) }; });
  }

  function loadAndRender(benchCode) {
    var stockUrl = '/api/stocks/' + encodeURIComponent(c.ticker) + '/ohlcv?days=360' +
                   (c.market ? '&market=' + encodeURIComponent(c.market) : '');
    var benchUrl = '/api/indices/' + benchCode + '/ohlcv?days=360';
    Promise.all([
      fetch(stockUrl).then(function(r) { return r.ok ? r.json() : Promise.reject(); }),
      fetch(benchUrl).then(function(r) { return r.ok ? r.json() : Promise.reject(); }),
    ]).then(function(results) {
      var stockData = results[0].series || [];
      var benchData = results[1].series || [];
      if (!stockData.length || !benchData.length) {
        container.innerHTML = '<div class="chart-placeholder">데이터 부족</div>';
        return;
      }
      // 두 시리즈의 공통 시작일 정렬
      var commonStart = stockData[0].date > benchData[0].date ? stockData[0].date : benchData[0].date;
      var s = stockData.filter(function(p) { return p.date >= commonStart; });
      var b = benchData.filter(function(p) { return p.date >= commonStart; });
      stockLine.setData(normalize(s));
      benchLine.setData(normalize(b));
      benchLine.applyOptions({ title: benchCode });
      chart.timeScale().fitContent();
    }).catch(function() {
      container.innerHTML = '<div class="chart-placeholder">벤치마크 데이터 조회 실패</div>';
    });
  }

  loadAndRender(defaultBench);

  document.querySelectorAll('#benchmark-toggle button').forEach(function(btn) {
    btn.addEventListener('click', function() {
      document.querySelectorAll('#benchmark-toggle button').forEach(function(b) { b.classList.remove('active'); });
      btn.classList.add('active');
      loadAndRender(btn.dataset.bench);
    });
  });
})();
```

- [ ] **Step 5.4: Run test**

Run: `pytest tests/test_stock_cockpit.py::TestStockCockpitPage -v`
Expected: 3 PASSED

- [ ] **Step 5.5: Commit**

```bash
git add api/templates/stock_cockpit.html tests/test_stock_cockpit.py
git commit -m "feat(cockpit): § 2-A 벤치마크 상대성과 라인 (시장별 자동 선택 + 토글)"
```

---

## Task 6: § 6 추천 이력 타임라인 카드

**Files:**
- Modify: `api/templates/stock_cockpit.html`

`/api/stocks/{ticker}/proposals` (Task 2) 응답을 카드 리스트로 렌더.

- [ ] **Step 6.1: Write the failing test**

`tests/test_stock_cockpit.py::TestStockCockpitPage` 에 추가:

```python
    def test_cockpit_page_includes_timeline_section(self, patched_base_ctx_conn):
        client = _make_client()
        resp = client.get("/pages/stocks/TXN?market=NASDAQ")
        body = resp.text
        assert 'id="timeline-list"' in body
        # 타임라인 JS 시그니처
        assert "renderTimeline" in body or "timeline-card" in body
```

- [ ] **Step 6.2: Run test**

Run: `pytest tests/test_stock_cockpit.py::TestStockCockpitPage::test_cockpit_page_includes_timeline_section -v`
Expected: FAIL

- [ ] **Step 6.3: Append timeline IIFE to `stock_cockpit.html`**

벤치마크 IIFE 다음에 추가:

```javascript
// ── § 6 추천 이력 타임라인 ──
(function() {
  var c = window.__cockpit;
  if (!c) return;

  function badge(text, cls) {
    return '<span class="tl-badge ' + (cls || '') + '">' + text + '</span>';
  }

  function pctSpan(v) {
    if (v == null) return '<strong>-</strong>';
    var cls = v > 0 ? 'tl-pos' : v < 0 ? 'tl-neg' : '';
    return '<strong class="' + cls + '">' + (v > 0 ? '+' : '') + v.toFixed(2) + '%</strong>';
  }

  function renderTimeline(items) {
    var listEl = document.getElementById('timeline-list');
    var emptyEl = document.getElementById('timeline-empty');
    if (!items || !items.length) {
      emptyEl.style.display = 'block';
      return;
    }
    items.forEach(function(it) {
      var card = document.createElement('div');
      card.className = 'timeline-card';
      var dt = (it.created_at || it.analysis_date || '').slice(0, 10);
      var entry = it.entry_price != null ? ' · 진입 ' + c.fmtPrice(it.entry_price, '') : '';
      var validation = '';
      if (it.validation_mismatches && it.validation_mismatches.length) {
        validation = ' <span class="tl-warn" title="AI 제시값과 실측 mismatch">⚠ ' +
                     it.validation_mismatches.length + '</span>';
      }
      card.innerHTML =
        '<div class="timeline-card-head">' +
          '<span class="timeline-date">' + dt + '</span>' +
          '<a class="timeline-theme" href="/pages/themes#theme-' + it.theme_id + '">' +
            (it.theme_name || '-') + '</a>' +
        '</div>' +
        '<div class="timeline-rationale">' +
          (it.rationale || '').slice(0, 240) +
          (it.rationale && it.rationale.length > 240 ? '...' : '') +
        '</div>' +
        '<div class="timeline-metrics">' +
          '<span>' + (it.action || '-').toUpperCase() + ' · ' + (it.conviction || '-') + entry + validation + '</span>' +
          '<span>1m ' + pctSpan(it.post_return_1m_pct) + '</span>' +
          '<span>3m ' + pctSpan(it.post_return_3m_pct) + '</span>' +
          '<span>6m ' + pctSpan(it.post_return_6m_pct) + '</span>' +
          '<span>1y ' + pctSpan(it.post_return_1y_pct) + '</span>' +
          '<span>MDD ' + pctSpan(it.max_drawdown_pct) + '</span>' +
          '<span>α ' + pctSpan(it.alpha_vs_benchmark_pct) + '</span>' +
        '</div>';
      listEl.appendChild(card);
    });
  }

  fetch('/api/stocks/' + encodeURIComponent(c.ticker) + '/proposals')
    .then(function(r) { return r.ok ? r.json() : Promise.reject(); })
    .then(function(d) { renderTimeline(d.items || []); })
    .catch(function() {
      document.getElementById('timeline-empty').textContent = '추천 이력 조회 실패';
      document.getElementById('timeline-empty').style.display = 'block';
    });
})();
```

스타일 블록(`<style>` 안)에 `.tl-warn`, `.tl-badge` 추가:

```css
.tl-warn { color:var(--orange, #f5a623); font-size:11px; margin-left:6px; }
.tl-badge { display:inline-block; padding:1px 6px; border-radius:3px; font-size:11px;
  background:var(--bg); border:1px solid var(--border); color:var(--text-muted); }
```

- [ ] **Step 6.4: Run test**

Run: `pytest tests/test_stock_cockpit.py::TestStockCockpitPage -v`
Expected: 4 PASSED

- [ ] **Step 6.5: Commit**

```bash
git add api/templates/stock_cockpit.html tests/test_stock_cockpit.py
git commit -m "feat(cockpit): § 6 추천 이력 타임라인 카드 + validation mismatch 배지"
```

---

## Task 7: 기존 `stock_fundamentals.html` 삭제 + 통합 검증

**Files:**
- Delete: `api/templates/stock_fundamentals.html`

- [ ] **Step 7.1: 기존 템플릿 참조 없음 확인**

Run: `grep -rn "stock_fundamentals.html" api/ tests/`
Expected: 출력 없음 (Task 3.4 에서 라우트가 새 템플릿 가리키게 변경됨)

- [ ] **Step 7.2: Delete the file**

Run: `git rm api/templates/stock_fundamentals.html`
Expected: 파일 삭제 + git stage

- [ ] **Step 7.3: Run all stock_cockpit tests**

Run: `pytest tests/test_stock_cockpit.py -v`
Expected: 7 PASSED (overview×2, proposals×2, page×4 — 200/chart_lib/benchmark/timeline)

- [ ] **Step 7.4: Run full test suite (회귀 확인)**

Run: `pytest -x`
Expected: ALL PASSED — 다른 도메인에 영향 없음

- [ ] **Step 7.5: Commit**

`git rm` 가 이미 stage 했으므로 add 없이 바로 commit:

```bash
git commit -m "refactor(cockpit): 기존 stock_fundamentals.html 제거 — Cockpit이 in-place 흡수"
```

---

## Task 8: 수동 검증 + 문서 업데이트

- [ ] **Step 8.1: Dev 서버 띄워 수동 smoke**

Run: `python -m api.main` (또는 이미 띄워져 있는 dev 서버 사용)

`http://localhost:8000/pages/stocks/{ticker}?market={market}` 로 다음 케이스 확인:

| 케이스 | ticker / market | 확인 |
|---|---|---|
| 추천 이력 많은 KRX | `005930` / `KOSPI` (또는 DB에서 SELECT) | Hero 5칩 채움, § 1 마커 표시, § 6 카드 N개, § 2-A vs KOSPI/KOSDAQ |
| 추천 이력 많은 US | `TXN` / `NASDAQ` (또는 DB에서 SELECT) | Hero 5칩, § 2-A vs NDX100/SP500, § 6 표시 |
| 추천 이력 0 | 임의 신규 종목 | Hero "추천 0회", § 6 빈 상태 안내, AI 점수 산식 중립값 |
| OHLCV 미수집 | universe_sync 안 된 종목 | § 1/§ 2-A "데이터 수집 대기 중" placeholder, 나머지 정상 |
| yfinance 폴백 | 임의 종목 + offline 환경 | § 4 "외부 데이터 일시 조회 실패", § Hero/§ 1/§ 2-A/§ 6 정상 |

대상 ticker 후보 (DB):
```sql
SELECT ticker, market, COUNT(*) AS n
FROM investment_proposals
GROUP BY ticker, market
HAVING COUNT(*) >= 3
ORDER BY n DESC LIMIT 5;
```

- [ ] **Step 8.2: Update CLAUDE.md (선택 — 페이지 구조 갱신 시)**

`api/templates/stock_cockpit.html` 항목으로 [`CLAUDE.md`](../CLAUDE.md) 의 templates 라인 업데이트:

```diff
- watchlist, notifications, profile, chat, education(...), inquiry(...), admin, admin_audit_logs, login, register, user_admin.
+ watchlist, notifications, profile, stock_cockpit(종목 페이지), chat, education(...), inquiry(...), admin, admin_audit_logs, login, register, user_admin.
```

- [ ] **Step 8.3: Update spec status**

[`_docs/20260425170650_stock-cockpit-design.md`](20260425170650_stock-cockpit-design.md) 헤더 상태 변경:
```diff
- - 상태: Draft → 사용자 리뷰 대기
+ - 상태: Phase 1 구현 완료 (커밋 <hash>) — Phase 2 spec 분리 예정
```

- [ ] **Step 8.4: Final commit**

```bash
git add CLAUDE.md _docs/20260425170650_stock-cockpit-design.md
git commit -m "docs(cockpit): Phase 1 구현 완료 표시 + CLAUDE.md templates 라인 갱신"
```

---

## 완료 기준 (spec § 11 검증)

1. ✅ KRX/US 각 1종 진입 시 5초 이내 모든 섹션 렌더 — Task 8.1
2. ✅ 추천 이력 있는 종목에서 § 6 + § 1 마커 표시 — Task 8.1
3. ✅ yfinance fail 상태에서도 § Hero/§ 1/§ 2-A/§ 6 정상 — Task 8.1 (offline 케이스)
4. ⏸ Lighthouse Performance ≥ 80 — 옵션 (Phase 1 종료 후 별도 측정)
5. ✅ 신규 API 2종 단위 테스트 — Task 1 + Task 2 (정상 + 빈 결과 + 폴백 케이스)

## 비범위 (재확인)

- § 2-B 정량 팩터 레이더 → Phase 2
- § 3 시장 레짐 + 섹터 비교 → Phase 2
- § 5 KRX 확장 → Phase 2
- § 7 등장 테마 카드 → Phase 3
- 모바일 우선 재설계 → Phase 3
