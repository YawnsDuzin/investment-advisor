"""신규 페이지 라우트 스모크 테스트

/pages/pricing, /pages/track-record — 비로그인도 200 반환하는지 확인.
psycopg2가 mock된 상태라 _base_ctx() 내부 DB 호출은 try/except로 우회됨.
"""
from contextlib import contextmanager
from unittest.mock import patch, MagicMock

import pytest


def _patch_fake_conn_for_base_ctx():
    """_base_ctx가 호출하는 get_connection → cursor 체인을 간단한 MagicMock으로"""
    cur = MagicMock()
    cur.fetchone.return_value = [0]  # 워치리스트/구독/알림 카운트 = 0

    @contextmanager
    def _cursor(**kwargs):
        yield cur

    conn = MagicMock()
    conn.cursor = _cursor
    return conn


def _make_client():
    """mock된 psycopg2 환경에서도 앱을 import하여 TestClient 생성."""
    from fastapi.testclient import TestClient
    from api.main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def patch_base_ctx_db(monkeypatch):
    """모든 페이지 테스트에서 base_ctx + 라우트 DB 호출을 가짜 커넥션으로 대체.

    init_db() 는 lifespan에서 호출되므로 TestClient.__enter__가 필요하다.
    여기서는 TestClient를 with 블록 없이 사용 → lifespan 실행 안됨 → init_db() 호출 안됨.

    B1 이후 pages.py 가 dashboard.py 등으로 분리됨. api.deps.get_db_conn 은
    shared.db.get_connection 을 직접 호출하므로 그 지점을 패치한다.

    주의: api.deps.get_connection 은 shared.db.get_connection 의 import alias.
    deps.py 가 import 방식 바꾸면 본 patch target (`api.deps.get_connection`) 도
    갱신 필요.
    """
    fake = _patch_fake_conn_for_base_ctx()
    # api.deps.get_db_conn 이 import 한 get_connection (shared.db) 을 가짜로
    monkeypatch.setattr("api.deps.get_connection", lambda cfg: fake)
    monkeypatch.setattr("shared.db.init_db", lambda cfg: None)


class TestPricingPage:
    def test_pricing_page_returns_200_and_html(self):
        client = _make_client()
        # TestClient를 with 없이 사용해 lifespan(init_db) 회피
        resp = client.get("/pages/pricing")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        body = resp.text
        # Pricing 핵심 문자열 확인
        assert "Pricing" in body or "플랜" in body
        assert "Free" in body
        assert "Pro" in body
        assert "Premium" in body

    def test_pricing_page_shows_three_tier_cards(self):
        client = _make_client()
        resp = client.get("/pages/pricing")
        html = resp.text
        # 각 티어의 한도 수치 중 하나라도 노출되는지
        assert "5" in html  # 워치리스트 free
        assert "30" in html  # 워치리스트 pro 등
        assert "무제한" in html  # premium


class TestTrackRecordPage:
    def test_track_record_page_returns_200(self):
        client = _make_client()
        resp = client.get("/pages/track-record")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        body = resp.text
        assert "Track Record" in body
        # fetch API 엔드포인트 경로가 JS에 포함되는지
        assert "/api/track-record/summary" in body


class TestPartialsIncluded:
    """base.html 수정으로 모든 페이지에 partial이 포함되는지"""

    def test_disclaimer_banner_rendered_on_pricing(self):
        client = _make_client()
        resp = client.get("/pages/pricing")
        assert "global-disclaimer" in resp.text
        assert "투자 권유가 아닙니다" in resp.text

    def test_bottom_tabbar_rendered(self):
        client = _make_client()
        resp = client.get("/pages/pricing")
        assert "bottom-tabbar" in resp.text
        assert "Today" in resp.text

    def test_upgrade_modal_rendered(self):
        client = _make_client()
        resp = client.get("/pages/pricing")
        assert "upgrade-modal" in resp.text


class TestDashboardMarketQuotes:
    """Dashboard 시세 바 통합 — 라우트가 import-time 에러 없이 동작하는지.

    실제 wiring 검증 (helper 호출 + context 주입 + 템플릿 렌더) 은 Task 5 의
    더 상세한 fixture (auth bypass + cursor sequence mock) 에서 다룬다.
    여기서는 import smoke 한정 — dashboard.py 가 _fetch_market_quotes 를
    참조해도 module load 가 깨지지 않는지만 확인.
    """

    def test_dashboard_route_loads_without_import_error(self):
        client = _make_client()
        resp = client.get("/")
        # AUTH_ENABLED=True 환경에서는 302 (랜딩 redirect), 비활성에서는 200.
        # 어느 쪽이든 import/wiring 자체가 깨지면 500 발생 → 그것만 잡으면 충분.
        assert resp.status_code in (200, 302)

    def test_dashboard_html_contains_market_quotes_marker_when_session_present(self, monkeypatch):
        """세션이 있을 때 _market_quotes_bar partial 의 '오늘의 시장' 문자열이 포함되는지.

        본 테스트는 auth bypass + cursor sequence mock + helper monkeypatch 까지
        모두 동원해서 실제 wiring 검증을 한다.
        """
        from datetime import date
        from unittest.mock import MagicMock
        from contextlib import contextmanager

        from api.main import app
        from api.deps import get_db_conn
        from api.auth.dependencies import _get_auth_cfg
        from shared.config import AuthConfig

        # ── (0) AUTH bypass — dashboard 의 early return 우회 (auth_enabled=False) ──
        # AuthConfig 인스턴스를 그대로 반환하되 enabled 만 False 로 강제.
        _bypass_cfg = AuthConfig()
        _bypass_cfg.enabled = False
        app.dependency_overrides[_get_auth_cfg] = lambda: _bypass_cfg

        # ── (1) DB cursor mock — dashboard() 의 모든 SQL 호출에 빈 결과 또는 minimal session row 주입 ──
        cur = MagicMock()
        session_row = {
            "id": 1, "analysis_date": date(2026, 4, 22),
            "market_regime": None, "risk_temperature": "medium",
        }
        # fetchone 시퀀스:
        #   ① analysis_sessions LIMIT 1 → session_row
        #   ② issue_count → {"cnt": 0}
        #   ③ bond_yields fetchone (try/except 안) → None
        #   ④ 전일 세션 없음 → None
        # (이슈/테마/제안/추적/뉴스/워치리스트/Top Picks/스파크라인 fetchall 은 빈 리스트로 처리)
        cur.fetchone.side_effect = [
            session_row,
            {"cnt": 0},
            None,
            None,
        ]
        cur.fetchall.return_value = []

        @contextmanager
        def _cursor(**kwargs):
            yield cur
        conn = MagicMock()
        conn.cursor = _cursor

        # ── (2) get_db_conn 의존성 override ──
        app.dependency_overrides[get_db_conn] = lambda: conn

        # ── (3) market_quotes helper mock — 카드 렌더용 dict ──
        fake_quotes = {
            "indices": [{
                "code": "KOSPI", "label": "KOSPI",
                "trade_date": date(2026, 4, 22),
                "close": 2615.32, "change_abs": 10.94, "change_pct": 0.42,
                "spark_points": [2580.0 + i for i in range(21)],
                "trend": "up",
            }],
            "meta": {"kr_trade_date": date(2026, 4, 22), "us_trade_date": None},
        }
        monkeypatch.setattr(
            "api.routes.dashboard._fetch_market_quotes",
            lambda cur_arg: fake_quotes,
        )

        try:
            client = _make_client()
            # follow_redirects=False — AUTH_ENABLED=True 환경에서 dashboard 가 /pages/landing
            # 으로 302 시 실제 status code 그대로 노출 (TestClient 기본 follow=True 면 200 으로 보임).
            resp = client.get("/", follow_redirects=False)
            # AUTH_ENABLED 환경에 따라 200 또는 302
            assert resp.status_code in (200, 302), f"unexpected: {resp.status_code}"
            if resp.status_code == 200:
                body = resp.text
                # 시세 바 마커 문자열 + 카드 데이터
                assert "오늘의 시장" in body
                assert "KOSPI" in body
                # 종가 포맷팅 (천 단위 콤마 + 소수 둘째 자리)
                assert "2,615.32" in body
                # sparkline SVG 렌더 확인
                assert "<polyline" in body
        finally:
            app.dependency_overrides.pop(get_db_conn, None)
            app.dependency_overrides.pop(_get_auth_cfg, None)
