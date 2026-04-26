"""briefing.html 렌더링 테스트.

base.html 의존을 회피하기 위해 DictLoader로 stub base를 주입한 jinja2 환경에서
briefing.html 만 부분 렌더 → 출력 HTML에 substring assertion.
"""
from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

from api.template_filters import register
from tests._fixtures.briefing_sample import make_briefing


# base.html 의 최소 stub — content/head block 만 노출
_BASE_STUB = (
    "{% block head %}{% endblock %}\n"
    "<!--CONTENT-->\n"
    "{% block content %}{% endblock %}\n"
    "<!--/CONTENT-->\n"
)


@pytest.fixture
def env():
    e = Environment(
        loader=ChoiceLoader([
            DictLoader({"base.html": _BASE_STUB}),
            FileSystemLoader("api/templates"),
        ]),
        autoescape=True,
    )
    register(e)
    return e


def render(env, **ctx) -> str:
    """briefing.html 을 stub base 위에 렌더해 HTML 문자열 반환."""
    return env.get_template("briefing.html").render(
        request=MagicMock(),
        active_nav="briefing",
        current_user=None,
        auth_enabled=False,
        unread_notifications=0,
        **ctx,
    )


def find_sector_card(html: str, label_substr: str):
    """주어진 라벨을 summary에 포함하는 sector-card details 한 건을 반환.

    반환: re.Match (group('attrs'), group('body')) 또는 None.
    """
    for m in re.finditer(
        r'<details(?P<attrs>[^>]*sector-card[^>]*)>(?P<body>.*?)</details>',
        html, re.DOTALL,
    ):
        # summary 영역만 추출해 라벨 검사 (body 전체가 아니라 summary 한정)
        sm = re.search(r'<summary[^>]*>(?P<inner>.*?)</summary>',
                       m.group('body'), re.DOTALL)
        if sm and label_substr in sm.group('inner'):
            return m
    return None


def test_renders_without_briefing_shows_empty_state(env):
    html = render(env, briefing=None, requested_date="2026-04-26")
    assert "브리핑이 없습니다" in html or "준비되지 않았습니다" in html


def test_hero_combines_headline_and_morning_brief(env):
    html = render(env, briefing=make_briefing())
    # headline 과 morning_brief 둘 다 같은 hero 컨테이너 안에 있어야 함
    m = re.search(r'<section[^>]*class="[^"]*brief-hero[^"]*"[^>]*>(.*?)</section>',
                  html, re.DOTALL)
    assert m, "brief-hero section not found"
    body = m.group(1)
    assert "INTC +23.60%" in body
    assert "오늘 챙겨야 할 핵심은 반도체" in body


def test_no_separate_morning_section(env):
    """기존 ③ 모닝 코멘트 섹션 자체가 사라져야 함."""
    html = render(env, briefing=make_briefing())
    assert "모닝 코멘트" not in html
    # 기존 .brief-morning 컨테이너도 더 이상 사용하지 않음
    assert 'class="brief-morning"' not in html


def test_sector_card_uses_details_with_open_when_kr_picks_present(env):
    html = render(env, briefing=make_briefing())
    semi = find_sector_card(html, "반도체")
    assert semi, "sector-card details not found for semiconductors"
    assert "open" in semi.group("attrs"), "semiconductors card should be open by default"


def test_sector_card_collapsed_when_no_kr_match(env):
    html = render(env, briefing=make_briefing())
    cable = find_sector_card(html, "통신·케이블")
    assert cable, "sector-card details not found for communication_cable"
    assert "open" not in cable.group("attrs"), \
        "communication_cable card should be collapsed (no KR picks)"


def test_sector_summary_shows_avg_change_and_strength(env):
    html = render(env, briefing=make_briefing())
    m = find_sector_card(html, "반도체")
    assert m
    summary = re.search(r'<summary[^>]*>(?P<inner>.*?)</summary>',
                        m.group("body"), re.DOTALL).group("inner")
    assert "+4.66%" in summary, "avg_change_pct missing/wrong format"
    assert "갭 상승 강력" in summary, "strength label missing"


def test_sector_summary_shows_kr_no_match_when_kr_absent(env):
    html = render(env, briefing=make_briefing())
    m = find_sector_card(html, "통신·케이블")
    assert m
    summary = re.search(r'<summary[^>]*>(?P<inner>.*?)</summary>',
                        m.group("body"), re.DOTALL).group("inner")
    assert "KR 매칭 없음" in summary


def test_sector_card_body_has_us_movers_and_kr_picks(env):
    html = render(env, briefing=make_briefing())
    m = find_sector_card(html, "반도체")
    assert m
    body = m.group("body")
    # US movers
    assert "INTC" in body and "+23.60%" in body
    assert "ARM" in body and "+14.76%" in body
    # catalyst
    assert "INTC 단일 종목 +23.60% 급등" in body
    # KR picks
    assert "SK하이닉스" in body
    assert "한미반도체" in body
    assert "+2~4%" in body


def test_us_only_sector_shows_no_kr_match_message(env):
    html = render(env, briefing=make_briefing())
    m = find_sector_card(html, "통신·케이블")
    assert m
    body = m.group("body")
    assert "CHTR" in body and "-25.50%" in body
    assert ("한국 매칭 없음" in body) or ("영향 제한적" in body)


def test_mover_pill_is_anchor_with_cockpit_link(env):
    html = render(env, briefing=make_briefing())
    pill = re.search(
        r'<a class="mover-pill"\s+href="([^"]+)"\s+title="([^"]+)"\s*>'
        r'\s*<strong>INTC</strong>',
        html,
    )
    assert pill, "INTC mover pill anchor not found"
    href, title = pill.group(1), pill.group(2)
    assert href == "/pages/stocks/INTC?market=NASDAQ"
    assert "Intel Corporation" in title
    assert "NASDAQ" in title


def test_mover_pill_falls_back_to_ticker_when_no_name_map_hit(env):
    """name_map 에 없는 티커는 풀네임 폴백 + market 쿼리 없이 링크."""
    bd = make_briefing()
    bd["briefing_data"]["us_summary"]["groups"][0]["top_movers"].append(
        {"ticker": "ZZZZ", "change_pct": 1.0}
    )
    html = render(env, briefing=bd)
    pill = re.search(
        r'<a class="mover-pill"\s+href="([^"]+)"\s+title="([^"]+)"\s*>'
        r'\s*<strong>ZZZZ</strong>',
        html,
    )
    assert pill
    assert pill.group(1) == "/pages/stocks/ZZZZ"
    assert pill.group(2).startswith("ZZZZ")


def test_kr_only_sector_renders_after_us_groups(env):
    """US groups 에 없는 sector_norm 의 kr_impact 도 표시되어야 함."""
    bd = make_briefing(with_kr_only_extra=True)
    html = render(env, briefing=bd)
    assert "HD한국조선해양" in html
    assert "조선" in html


def test_kr_only_sector_card_is_open_by_default(env):
    """KR-only 카드는 KR picks 가 정의상 ≥1 이므로 기본 펼침."""
    bd = make_briefing(with_kr_only_extra=True)
    html = render(env, briefing=bd)
    m = find_sector_card(html, "조선")
    assert m, "KR-only sector card not found"
    assert "open" in m.group("attrs")


def test_kr_pick_name_is_anchor_to_cockpit(env):
    """KR 종목명도 US mover pill 처럼 stock cockpit 으로 링크."""
    html = render(env, briefing=make_briefing())
    # SK하이닉스 (000660 · KOSPI) — 이름이 anchor 안에 있어야
    m = re.search(
        r'<a[^>]+class="[^"]*kr-pick-name[^"]*"[^>]+href="([^"]+)"[^>]*>\s*'
        r'SK하이닉스\s*</a>',
        html, re.DOTALL,
    )
    assert m, "SK하이닉스 anchor not found"
    assert m.group(1) == "/pages/stocks/000660?market=KOSPI"


def test_kr_pick_name_anchor_in_kr_only_card(env):
    """KR-only fallback 카드의 종목명도 동일하게 anchor."""
    bd = make_briefing(with_kr_only_extra=True)
    html = render(env, briefing=bd)
    m = re.search(
        r'<a[^>]+class="[^"]*kr-pick-name[^"]*"[^>]+href="([^"]+)"[^>]*>\s*'
        r'HD한국조선해양\s*</a>',
        html, re.DOTALL,
    )
    assert m, "HD한국조선해양 anchor not found"
    assert m.group(1) == "/pages/stocks/009540?market=KOSPI"
