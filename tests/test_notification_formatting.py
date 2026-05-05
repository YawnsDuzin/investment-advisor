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
