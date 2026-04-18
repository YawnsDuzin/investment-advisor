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
    """모든 페이지 테스트에서 _base_ctx의 DB 호출을 가짜 커넥션으로 대체.

    init_db() 는 lifespan에서 호출되므로 TestClient.__enter__가 필요하다.
    여기서는 TestClient를 with 블록 없이 사용 → lifespan 실행 안됨 → init_db() 호출 안됨.
    """
    # pages 내 get_connection + init_db 양쪽 패치 — 안전망
    fake = _patch_fake_conn_for_base_ctx()
    monkeypatch.setattr("api.routes.pages.get_connection", lambda cfg: fake)
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
