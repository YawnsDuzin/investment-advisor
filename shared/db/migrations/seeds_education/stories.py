"""stories 카테고리 — 투자 이야기 (신규 카테고리, v24)."""
import json

TOPICS: list[dict] = []

# v24 마이그레이션에서 신규 추가되는 토픽의 slug 집합
V24_SLUGS: set[str] = set()
