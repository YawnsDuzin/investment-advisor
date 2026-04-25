"""Stock Cockpit API + 페이지 단위 테스트.

psycopg2가 conftest에서 mock되므로 get_connection → cursor → fetch 체인을
가짜 객체로 꾸민다.
"""
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest


def _fake_db_cfg():
    """factor_engine 등 외부 모듈에 넘길 가짜 DatabaseConfig — 어차피 get_connection 이 patch 됨."""
    from shared.config import DatabaseConfig
    return DatabaseConfig()


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

        # fetch 순서: meta(stock_universe), latest 2 rows(ohlcv), prop_stats, factor_snapshot
        # meta_row keys reflect SQL aliases — actual columns: asset_name/sector_norm/last_price_ccy
        meta_row = {
            "name": "Texas Instruments",
            "sector": "Technology",
            "industry": "Semiconductors",
            "currency": "USD",
            "market": "NASDAQ",
        }
        latest_rows = [
            {"trade_date": date(2026, 4, 24), "close": Decimal("277.14"), "volume": 9240450},
            {"trade_date": date(2026, 4, 23), "close": Decimal("282.21"), "volume": 8800000},
        ]
        stats_row = {
            "proposal_count": 4,
            "avg_post_return_3m_pct": Decimal("12.4"),
            "avg_alpha_vs_benchmark_pct": Decimal("5.1"),
            # latest_consensus 는 backend SQL 에서 제거됨 — 핸들러가 setdefault(None) 처리
        }
        factor_row = {
            "factor_snapshot": {
                "r1m_pctile": 0.7, "r3m_pctile": 0.8, "r6m_pctile": 0.85, "r12m_pctile": 0.78,
                "low_vol_pctile": 0.55, "volume_pctile": 0.88,
            },
        }
        krx_row = {
            "foreign_ownership_pct": 18.5,
            "foreign_net_buy_signal": "positive",
            "squeeze_risk": "low",
            "index_membership": ["KOSPI200", "KRX300"],
        }

        conn = _fake_conn([meta_row, latest_rows, stats_row, factor_row, krx_row])

        with patch("api.routes.stocks.get_connection", return_value=conn):
            result = get_stock_overview(ticker="TXN", market="NASDAQ")

        assert result["ticker"] == "TXN"
        assert result["name"] == "Texas Instruments"
        assert result["latest"]["close"] == 277.14
        # 변동률 = (277.14 - 282.21) / 282.21 * 100 ≈ -1.80
        assert round(result["latest"]["change_pct"], 2) == -1.80
        assert result["stats"]["proposal_count"] == 4
        assert result["stats"]["avg_post_return_3m_pct"] == 12.4
        assert result["stats"]["alpha_vs_benchmark_pct"] == 5.1
        # AI 점수 산식 검증
        # factor_score = (0.7+0.8+0.85+0.78)/4 = 0.7825
        # hist_score = clamp(12.4/30, 0, 1) ≈ 0.4133
        # consensus_score = 항상 중립 (0.5) — DB 컬럼 없음, CKPT-P2-8
        # score = 100*(0.5*0.7825 + 0.3*0.4133 + 0.2*0.5) = 62
        assert result["stats"]["ai_score"] == 62
        assert result["score_breakdown"]["weights"] == {"factor": 0.5, "hist": 0.3, "consensus": 0.2}
        # Phase 2 — factor_snapshot raw exposure (§ 2-B 가 사용)
        assert result["factor_snapshot"] == factor_row["factor_snapshot"]
        # Phase 2 — krx_extended (§ 5 가 사용)
        assert result["krx_extended"]["foreign_ownership_pct"] == 18.5
        assert result["krx_extended"]["foreign_net_buy_signal"] == "positive"
        assert result["krx_extended"]["squeeze_risk"] == "low"
        assert result["krx_extended"]["index_membership"] == ["KOSPI200", "KRX300"]

    def test_overview_zero_proposals_uses_neutral_score(self):
        from api.routes.stocks import get_stock_overview

        # meta_row keys reflect SQL aliases — actual columns: asset_name/sector_norm/last_price_ccy
        meta_row = {"name": "Foo", "sector": None, "industry": None,
                    "currency": "USD", "market": "NASDAQ"}
        latest_rows = []
        stats_row = {
            "proposal_count": 0, "avg_post_return_3m_pct": None,
            "avg_alpha_vs_benchmark_pct": None,
        }
        factor_row = {}
        krx_row = None

        conn = _fake_conn([meta_row, latest_rows, stats_row, factor_row, krx_row])

        with patch("api.routes.stocks.get_connection", return_value=conn):
            result = get_stock_overview(ticker="FOO", market="NASDAQ")

        # 모든 컴포넌트 중립 0.5 → ai_score = 50
        assert result["stats"]["ai_score"] == 50
        assert result["latest"] is None
        assert result["stats"]["proposal_count"] == 0
        assert result["stats"]["avg_post_return_3m_pct"] is None
        assert result["factor_snapshot"] is None
        assert result["krx_extended"] is None


class TestComputeAiScore:
    """_compute_ai_score 순수 함수 단위 테스트."""

    def _fn(self, *args, **kwargs):
        from api.routes.stocks import _compute_ai_score
        return _compute_ai_score(*args, **kwargs)

    def test_all_present_full_factor_snapshot(self):
        """factor_snapshot 4개 키 모두 존재, avg 있음, BUY consensus."""
        snapshot = {
            "r1m_pctile": 0.7, "r3m_pctile": 0.8,
            "r6m_pctile": 0.85, "r12m_pctile": 0.78,
        }
        result = self._fn(snapshot, 12.4, "BUY")
        # factor_score = (0.7+0.8+0.85+0.78)/4 = 0.7825
        # hist_score = clamp(12.4/30) ≈ 0.4133
        # consensus_score = 0.75
        # score = 0.5*0.7825 + 0.3*0.4133 + 0.2*0.75 ≈ 0.6655 → 67
        assert result["ai_score"] == 67
        assert result["factor_score"] == round(0.7825, 4)
        assert result["hist_score"] == round(0.4133333333333333, 4)
        assert result["consensus_score"] == 0.75

    def test_partial_pctile_keys(self):
        """pctile 키 일부만 있거나 None — 있는 것만 평균."""
        snapshot = {
            "r1m_pctile": 0.6,
            "r3m_pctile": None,   # None → 제외
            "r6m_pctile": 0.4,
            # r12m_pctile 없음 → 제외
        }
        result = self._fn(snapshot, None, "HOLD")
        # factor_score = (0.6+0.4)/2 = 0.5
        # hist_score = 0.5 (avg None)
        # consensus_score = 0.5 (HOLD)
        # score = 0.5*0.5 + 0.3*0.5 + 0.2*0.5 = 0.5 → 50
        assert result["factor_score"] == 0.5
        assert result["hist_score"] == 0.5
        assert result["consensus_score"] == 0.5
        assert result["ai_score"] == 50

    def test_all_none_inputs_returns_neutral(self):
        """factor_snapshot=None, avg=None, consensus=None → 모두 중립 0.5 → ai_score 50."""
        result = self._fn(None, None, None)
        assert result["ai_score"] == 50
        assert result["factor_score"] == 0.5
        assert result["hist_score"] == 0.5
        assert result["consensus_score"] == 0.5

    def test_empty_factor_snapshot_returns_neutral(self):
        """factor_snapshot={} (빈 dict) → factor_score 중립 0.5."""
        result = self._fn({}, None, None)
        assert result["factor_score"] == 0.5
        assert result["ai_score"] == 50

    def test_returns_four_keys(self):
        """반환 dict에 반드시 4개 키가 존재한다."""
        result = self._fn(None, None, None)
        assert set(result.keys()) == {"ai_score", "factor_score", "hist_score", "consensus_score"}


class TestStockProposalsAPI:
    """GET /api/stocks/{ticker}/proposals"""

    def test_proposals_returns_timeline(self):
        from api.routes.stocks import get_stock_proposals

        prop_rows = [
            {
                "id": 12345, "analysis_date": date(2026, 4, 15),
                "created_at": datetime(2026, 4, 15, 8, 30), "theme_id": 99,
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
        assert item["created_at"] == "2026-04-15T08:30:00"
        assert len(item["validation_mismatches"]) == 1
        assert item["validation_mismatches"][0]["field_name"] == "current_price"

    def test_proposals_empty_for_unknown_ticker(self):
        from api.routes.stocks import get_stock_proposals

        conn = _fake_conn([[]])
        with patch("api.routes.stocks.get_connection", return_value=conn):
            result = get_stock_proposals(ticker="UNKNOWN")

        assert result["count"] == 0
        assert result["items"] == []


def _patch_fake_conn_for_base_ctx():
    cur = MagicMock()
    cur.fetchone.return_value = {"market_regime": None}  # market_regime 쿼리용 dict

    @contextmanager
    def _cursor(**kwargs):
        yield cur

    conn = MagicMock()
    conn.cursor = _cursor
    return conn


@pytest.fixture
def patched_base_ctx_conn(monkeypatch):
    fake = _patch_fake_conn_for_base_ctx()
    # api.deps.get_db_conn 이 shared.db.get_connection 을 호출 — 여기를 패치
    monkeypatch.setattr("shared.db.connection.get_connection", lambda cfg: fake, raising=False)
    monkeypatch.setattr("api.deps.get_connection", lambda cfg: fake, raising=False)
    monkeypatch.setattr("shared.db.init_db", lambda cfg: None, raising=False)
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
        # 펀더멘털 8카드는 흡수됨 (기존 호환) — DOM 구조 확인
        assert "valuation-metrics" in body
        # 외부 JS 파일 참조 확인 (Phase 2 Task 1 이후)
        assert "stock_cockpit.js" in body

    def test_cockpit_page_loads_chart_library(self, patched_base_ctx_conn):
        client = _make_client()
        resp = client.get("/pages/stocks/TXN?market=NASDAQ")
        body = resp.text
        # lightweight-charts CDN 로드 확인
        assert "lightweight-charts" in body
        # 차트 컨테이너 — DOM 구조 확인
        assert 'id="price-chart"' in body

    def test_cockpit_page_includes_benchmark_section(self, patched_base_ctx_conn):
        client = _make_client()
        resp = client.get("/pages/stocks/TXN?market=NASDAQ")
        body = resp.text
        # 벤치마크 차트 컨테이너 — DOM 구조 확인
        assert 'id="benchmark-chart"' in body
        # 토글 버튼 컨테이너 — DOM 구조 확인
        assert 'id="benchmark-toggle"' in body

    def test_cockpit_page_includes_timeline_section(self, patched_base_ctx_conn):
        client = _make_client()
        resp = client.get("/pages/stocks/TXN?market=NASDAQ")
        body = resp.text
        assert 'id="timeline-list"' in body
        # tl-warn 스타일 존재 확인 (인라인 CSS — 외부 파일 이동 후에도 CSS 는 HTML 에 남아 있음)
        assert "tl-warn" in body

    def test_cockpit_page_escapes_user_content(self, patched_base_ctx_conn):
        client = _make_client()
        resp = client.get("/pages/stocks/TXN?market=NASDAQ")
        body = resp.text
        # esc 헬퍼 함수 및 XSS 방어 시그니처는 외부 JS 파일로 이동됨 (Phase 2 Task 1)
        # 외부 JS 파일이 서빙되는지 확인 — test_external_js_file_served 에서 상세 검증
        assert "stock_cockpit.js" in body

    def test_cockpit_page_uses_external_js(self, patched_base_ctx_conn):
        client = _make_client()
        resp = client.get("/pages/stocks/TXN?market=NASDAQ")
        body = resp.text
        # external JS 파일 참조
        assert '/static/js/stock_cockpit.js' in body
        # 인라인 IIFE 시그니처가 페이지 HTML 에서 제거됨
        # (Hero 의 fetch '/overview' 호출이 인라인 코드에 없어야 함 — 외부 파일로 이동)
        assert "fetch('/api/stocks/' + encodeURIComponent(ticker) + '/overview'" not in body

    def test_external_js_file_served(self):
        client = _make_client()
        resp = client.get("/static/js/stock_cockpit.js")
        assert resp.status_code == 200
        body = resp.text
        # Phase 1 의 4 개 IIFE 시그니처 모두 외부 파일에 존재
        assert "window.__cockpit" in body
        assert "function _compute" not in body  # 백엔드 함수가 아닌지 확인
        assert "// ── § 1 가격 차트 ──" in body
        # § 2-A 마커는 prefix 매칭 (헤더 뒤 부가 설명 변경 가능)
        assert "// ── § 2-A 벤치마크 상대성과" in body
        assert "// ── § 6 추천 이력 타임라인 ──" in body

        # Assertions migrated from Phase 1 tests that previously checked HTML body
        # (now relocated since JS lives in external file)
        assert "/overview" in body          # § Hero / § 2-B / § 5 fetch
        assert "/proposals" in body          # § 1 markers / § 6 timeline fetch
        assert "/ohlcv" in body              # § 1 chart + § 2-A stock data
        assert "/api/indices/" in body       # § 2-A benchmark data
        assert "function escHtml" in body    # XSS guard helper (Phase 1 Task 6)
        assert "&amp;" in body and "&lt;" in body and "&gt;" in body  # escape literals
        assert "renderTimeline" in body or "timeline-card" in body  # § 6 marker

    def test_chart_uses_overlay_pattern(self):
        client = _make_client()
        resp = client.get("/static/js/stock_cockpit.js")
        body = resp.text
        # overlay 패턴 시그니처 — innerHTML 에러 출력 제거
        assert "container.innerHTML = '<div class=\"chart-placeholder\">차트 데이터 조회 실패</div>'" not in body
        assert "container.innerHTML = '<div class=\"chart-placeholder\">벤치마크 데이터 조회 실패</div>'" not in body
        # 새 overlay 시그니처 — class="chart-overlay"
        assert "chart-overlay" in body

    def test_benchmark_iife_renders_all_four_indices(self):
        """§ 2-A 가 4개 시장 인덱스 (KOSPI/KOSDAQ/SP500/NDX100) 동시 표시."""
        client = _make_client()
        resp = client.get("/static/js/stock_cockpit.js")
        body = resp.text
        # 4개 벤치마크 코드 모두 IIFE 안에 등장
        assert "'KOSPI'" in body
        assert "'KOSDAQ'" in body
        assert "'SP500'" in body
        assert "'NDX100'" in body
        # BENCH_INDICES 시그니처 — 4 인덱스 정의 배열
        assert "BENCH_INDICES" in body
        # /api/indices/ fetch URL 패턴
        assert "/api/indices/" in body

    def test_cockpit_page_includes_regime_banner(self, patched_base_ctx_conn):
        client = _make_client()
        resp = client.get("/pages/stocks/TXN?market=NASDAQ")
        body = resp.text
        # § 3 시장 레짐 자리 마크업 (regime context 가 None 이라도 _regime_banner 가 자체 가드)
        assert 'id="sec-regime"' in body
        # 섹터 분위 표 자리
        assert 'id="sector-stats-table"' in body

    def test_external_js_has_sector_stats_iife(self):
        client = _make_client()
        resp = client.get("/static/js/stock_cockpit.js")
        body = resp.text
        assert "// ── § 3 섹터 팩터 분위 ──" in body
        assert "/sector-stats" in body
        assert "sector-stats-table" in body

    def test_cockpit_page_loads_chartjs(self, patched_base_ctx_conn):
        client = _make_client()
        resp = client.get("/pages/stocks/TXN?market=NASDAQ")
        body = resp.text
        assert "chart.js" in body or "chart.umd.min.js" in body
        assert 'id="factor-radar"' in body

    def test_external_js_has_factor_radar_iife(self):
        client = _make_client()
        resp = client.get("/static/js/stock_cockpit.js")
        body = resp.text
        assert "// ── § 2-B 정량 팩터 레이더 ──" in body
        assert "factor-radar" in body
        assert "type: 'radar'" in body or 'type: "radar"' in body

    def test_cockpit_page_includes_krx_section(self, patched_base_ctx_conn):
        client = _make_client()
        resp = client.get("/pages/stocks/TXN?market=NASDAQ")
        body = resp.text
        # § 5 자리 마크업 (외국주 페이지여도 마크업은 존재 — JS가 hide)
        assert 'id="sec-krx"' in body
        assert 'id="krx-foreign-donut"' in body

    def test_external_js_has_krx_iife(self):
        client = _make_client()
        resp = client.get("/static/js/stock_cockpit.js")
        body = resp.text
        assert "// ── § 5 KRX 확장 ──" in body
        assert "krx-foreign-donut" in body
        # 외국주 hide 로직
        assert "KOSPI" in body and "KOSDAQ" in body


class TestComputeSectorPctiles:
    """analyzer.factor_engine.compute_sector_pctiles — 섹터 단위 cross-section pctile."""

    def test_returns_six_axis_pctiles_for_normal_sector(self):
        from analyzer.factor_engine import compute_sector_pctiles

        # SQL 결과 행 — 시장 그룹 결정에 사용 (1개 row)
        market_group_row = {"sector": "Technology"}
        # 섹터 cross-section 결과 — TXN 한 행, sector_size=12
        sector_row = {
            "ticker": "TXN", "market": "NASDAQ",
            "r1m": 8.4, "r3m": 12.4, "r6m": 25.1, "r12m": 48.0,
            "vol60": 18.5, "volume_ratio": 1.42,
            "r1m_pctile": 0.78, "r3m_pctile": 0.85, "r6m_pctile": 0.70, "r12m_pctile": 0.92,
            "low_vol_pctile": 0.55, "volume_pctile": 0.88,
            "sector_size": 12,
        }

        conn = _fake_conn([market_group_row, [sector_row]])

        with patch("analyzer.factor_engine.get_connection", return_value=conn):
            result = compute_sector_pctiles(_fake_db_cfg(), "TXN", "NASDAQ")

        assert result["ticker"] == "TXN"
        assert result["sector"] == "Technology"
        assert result["sector_size"] == 12
        assert result["ranks"]["r3m"]["sector_pctile"] == 0.85
        assert result["ranks"]["r3m"]["sector_top_pct"] == 15  # round((1-0.85)*100)
        assert result["ranks"]["r3m"]["value_pct"] == 12.4
        assert result["ranks"]["volume"]["value_ratio"] == 1.42
        assert result["ranks"]["low_vol"]["sector_pctile"] == 0.55

    def test_small_sector_returns_null_pctiles(self):
        from analyzer.factor_engine import compute_sector_pctiles

        market_group_row = {"sector": "ObscureSector"}
        # sector_size=3 (< 5 임계) — DB 가 pctile 값을 줘도 함수가 suppressed 해야
        sector_row = {
            "ticker": "TXN", "market": "NASDAQ",
            "r1m": 5.0, "r3m": 10.0, "r6m": 20.0, "r12m": 30.0,
            "vol60": 15.0, "volume_ratio": 1.0,
            # 실제 DB 가 PERCENT_RANK 결과를 줘도 sector_size<5 이면 함수가 NULL 처리해야
            "r1m_pctile": 0.5, "r3m_pctile": 0.78, "r6m_pctile": 0.6, "r12m_pctile": 0.4,
            "low_vol_pctile": 0.5, "volume_pctile": 0.7,
            "sector_size": 3,
        }
        conn = _fake_conn([market_group_row, [sector_row]])
        with patch("analyzer.factor_engine.get_connection", return_value=conn):
            result = compute_sector_pctiles(_fake_db_cfg(), "TXN", "NASDAQ")

        assert result["sector_size"] == 3
        assert result["ranks"]["r3m"]["sector_pctile"] is None
        assert result["ranks"]["r3m"]["sector_top_pct"] is None
        # value 는 여전히 채워짐 (raw factor 는 sector size 무관)
        assert result["ranks"]["r3m"]["value_pct"] == 10.0

    def test_unknown_sector_returns_none(self):
        from analyzer.factor_engine import compute_sector_pctiles

        # 첫 쿼리 (sector + market_group 결정) 가 빈 결과
        conn = _fake_conn([None])
        with patch("analyzer.factor_engine.get_connection", return_value=conn):
            result = compute_sector_pctiles(_fake_db_cfg(), "UNKNOWN", "NASDAQ")
        assert result is None


class TestStockSectorStatsAPI:
    """GET /api/stocks/{ticker}/sector-stats"""

    def test_sector_stats_returns_payload(self):
        from api.routes.stocks import get_stock_sector_stats

        sample = {
            "ticker": "TXN", "sector": "Technology", "sector_size": 12,
            "ranks": {
                "r1m": {"value_pct": 8.4, "sector_pctile": 0.78, "sector_top_pct": 22},
                "r3m": {"value_pct": 12.4, "sector_pctile": 0.85, "sector_top_pct": 15},
                "r6m": {"value_pct": 25.1, "sector_pctile": 0.70, "sector_top_pct": 30},
                "r12m": {"value_pct": 48.0, "sector_pctile": 0.92, "sector_top_pct": 8},
                "low_vol": {"value_pct": 18.5, "sector_pctile": 0.55, "sector_top_pct": 45},
                "volume": {"value_ratio": 1.42, "sector_pctile": 0.88, "sector_top_pct": 12},
            },
            "computed_at": "2026-04-25T19:00:00+09:00",
        }
        with patch("api.routes.stocks.compute_sector_pctiles", return_value=sample):
            result = get_stock_sector_stats(ticker="TXN", market="NASDAQ")
        assert result == sample

    def test_sector_stats_404_for_unknown(self):
        from fastapi import HTTPException
        from api.routes.stocks import get_stock_sector_stats

        with patch("api.routes.stocks.compute_sector_pctiles", return_value=None):
            try:
                get_stock_sector_stats(ticker="UNKNOWN", market="NASDAQ")
                assert False, "expected HTTPException"
            except HTTPException as e:
                assert e.status_code == 404
