"""education_topics 시드 데이터 검증 — 카운트·JSON·slug 유니크·V35 멱등성."""
import json

from shared.db.migrations.seeds_education import (
    ALL_TOPICS,
    NEW_TOPICS_V24,
)


def test_total_topic_count():
    """v35 후 총 40 토픽."""
    assert len(ALL_TOPICS) == 40, f"expected 40, got {len(ALL_TOPICS)}"


def test_category_distribution():
    """카테고리별 분포 검증."""
    from collections import Counter
    counts = Counter(t["category"] for t in ALL_TOPICS)
    assert counts == {
        "basics": 10,
        "analysis": 6,
        "risk": 5,
        "macro": 4,
        "practical": 4,
        "stories": 8,
        "tools": 3,
    }, f"unexpected distribution: {counts}"


def test_all_slugs_unique():
    """모든 slug 유니크."""
    slugs = [t["slug"] for t in ALL_TOPICS]
    assert len(slugs) == len(set(slugs)), "duplicate slugs"


def test_required_keys_present():
    """모든 토픽에 필수 키 존재."""
    required = {"category", "slug", "title", "summary", "content",
                "examples", "difficulty", "sort_order"}
    for t in ALL_TOPICS:
        assert required <= set(t.keys()), f"missing keys in {t.get('slug')}"


def test_examples_valid_json():
    """examples 컬럼이 valid JSON 직렬화 가능."""
    for t in ALL_TOPICS:
        parsed = json.loads(t["examples"])
        assert isinstance(parsed, list), f"{t['slug']}: examples not list"
        for ex in parsed:
            assert "title" in ex and "description" in ex, \
                f"{t['slug']}: example missing title/description"


def test_content_min_length():
    """V35 신규 토픽 content 최소 분량 (800자 이상). 기존 v21/v24 토픽은 별도 영역."""
    from shared.db.migrations.seeds_education import NEW_TOPICS_V35
    for t in NEW_TOPICS_V35:
        assert len(t["content"]) >= 800, \
            f"{t['slug']} content too short ({len(t['content'])} chars)"


def test_difficulty_valid():
    """difficulty는 beginner/intermediate/advanced 중 하나."""
    valid = {"beginner", "intermediate", "advanced"}
    for t in ALL_TOPICS:
        assert t["difficulty"] in valid, \
            f"{t['slug']}: invalid difficulty {t['difficulty']}"


def test_v35_new_topics_count():
    """V35 신규 14 토픽 노출."""
    from shared.db.migrations.seeds_education import NEW_TOPICS_V35
    assert len(NEW_TOPICS_V35) == 14, f"expected 14, got {len(NEW_TOPICS_V35)}"


def test_v35_topics_disjoint_from_v24():
    """V35 신규 slug는 V24와 겹치지 않음."""
    from shared.db.migrations.seeds_education import NEW_TOPICS_V35
    v24_slugs = {t["slug"] for t in NEW_TOPICS_V24}
    v35_slugs = {t["slug"] for t in NEW_TOPICS_V35}
    assert v24_slugs.isdisjoint(v35_slugs), \
        f"overlap: {v24_slugs & v35_slugs}"


def test_tools_category_exists():
    """tools 카테고리에 정확히 3 토픽."""
    tools = [t for t in ALL_TOPICS if t["category"] == "tools"]
    assert len(tools) == 3, f"expected 3 tools topics, got {len(tools)}"
    assert {t["slug"] for t in tools} == {
        "factor-six-axes",
        "market-regime-reading",
        "pre-market-briefing-guide",
    }


def test_edu_categories_label_includes_tools():
    """라우터 라벨 매핑에 tools 추가됨."""
    from api.routes.education import _EDU_CATEGORIES
    assert "tools" in _EDU_CATEGORIES
    assert _EDU_CATEGORIES["tools"] == "도구·시스템 가이드"


def test_v36_visual_topics_have_image_refs():
    """V36 시각화 적용된 14개 슬러그의 content 에 SVG 이미지 참조가 1개 이상 존재."""
    visual_slugs = {
        "per-pbr-roe", "business-cycle", "chart-key-five",
        "momentum-investing", "diversification", "risk-adjusted-return",
        "correlation-trap", "interest-rates", "yield-curve-inversion",
        "what-if-2015", "korea-market-timeline", "tesla-eight-years",
        "factor-six-axes", "market-regime-reading",
    }
    matched = [t for t in ALL_TOPICS if t["slug"] in visual_slugs]
    assert len(matched) == 14, f"expected 14 visual topics, found {len(matched)}"
    for t in matched:
        assert "/static/edu/charts/" in t["content"], \
            f"{t['slug']} missing SVG image reference"


def test_svg_files_exist():
    """모든 시각화 토픽의 차트 파일이 디스크에 존재."""
    import os
    base = "api/static/edu/charts"
    expected = [
        "per-pbr-roe-1.svg", "business-cycle-1.svg",
        "chart-key-five-1.svg", "chart-key-five-2.svg",
        "momentum-investing-1.svg", "diversification-1.svg",
        "risk-adjusted-return-1.svg", "risk-adjusted-return-2.svg",
        "correlation-trap-1.svg", "interest-rates-1.svg",
        "yield-curve-1.svg", "yield-curve-2.svg",
        "what-if-2015-1.svg", "korea-market-timeline-1.svg",
        "tesla-eight-years-1.svg", "factor-six-axes-1.svg",
        "factor-six-axes-2.svg", "market-regime-1.svg",
    ]
    missing = [f for f in expected if not os.path.exists(os.path.join(base, f))]
    assert not missing, f"missing SVG files: {missing}"
