"""basics 카테고리 — 기초 개념 교육 토픽."""
import json

TOPICS: list[dict] = []

# v24 마이그레이션에서 신규 추가되는 토픽의 slug 집합
V24_SLUGS: set[str] = set()
