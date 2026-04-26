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
