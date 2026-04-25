"""교육 토픽 시드 데이터 — 카테고리별 모듈 집계.

각 카테고리 모듈에서 TOPICS와 V24_SLUGS / V35_SLUGS를 가져와
ALL_TOPICS (전체 시드용)와 NEW_TOPICS_VN (마이그레이션용)을 노출한다.
"""
from . import basics, analysis, risk, macro, practical, stories, tools

_MODULES = [basics, analysis, risk, macro, practical, stories, tools]

ALL_TOPICS: list[dict] = []
for _m in _MODULES:
    ALL_TOPICS.extend(_m.TOPICS)

_V24_SLUGS: set[str] = set()
for _m in _MODULES:
    _V24_SLUGS.update(getattr(_m, "V24_SLUGS", set()))

_V35_SLUGS: set[str] = set()
for _m in _MODULES:
    _V35_SLUGS.update(getattr(_m, "V35_SLUGS", set()))

NEW_TOPICS_V24: list[dict] = [t for t in ALL_TOPICS if t["slug"] in _V24_SLUGS]
NEW_TOPICS_V35: list[dict] = [t for t in ALL_TOPICS if t["slug"] in _V35_SLUGS]
