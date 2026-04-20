"""style.css의 선택자→속성 매핑을 JSON으로 캡처.

사용: python -m tools.c1_css_split.capture_css > tools/c1_css_split/baseline.json
비교: python -m tools.c1_css_split.capture_css | diff - tools/c1_css_split/baseline.json

주석·공백 무시. 규칙 순서는 리스트 position으로 보존.
@media, @keyframes 등 at-rule은 블록 전체를 단일 item으로 처리.
"""
import hashlib
import json
import re
import sys
from pathlib import Path


def parse_css(text: str) -> list[dict]:
    """단순 파서 — {selector, decls_hash, decl_count, position} 리스트 반환."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    items: list[dict] = []
    pos = 0
    position = 0
    while pos < len(text):
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text):
            break
        depth = 0
        start = pos
        in_block = False
        while pos < len(text):
            c = text[pos]
            if c == "{":
                depth += 1
                in_block = True
            elif c == "}":
                depth -= 1
                if depth == 0:
                    pos += 1
                    break
            pos += 1
        if not in_block:
            break
        block = text[start:pos].strip()
        brace = block.find("{")
        selector = re.sub(r"\s+", " ", block[:brace].strip())
        decls = block[brace + 1 : -1]
        decl_map: dict[str, str] = {}
        for d in re.split(r";(?![^(]*\))", decls):
            d = d.strip()
            if not d or ":" not in d:
                continue
            name, _, value = d.partition(":")
            decl_map[name.strip().lower()] = re.sub(r"\s+", " ", value.strip())
        decls_hash = hashlib.sha256(
            json.dumps(sorted(decl_map.items()), ensure_ascii=False).encode()
        ).hexdigest()[:16]
        items.append(
            {
                "position": position,
                "selector": selector,
                "decls_hash": decls_hash,
                "decl_count": len(decl_map),
            }
        )
        position += 1
    return items


def main() -> int:
    path = Path("api/static/css/style.css")
    items = parse_css(path.read_text(encoding="utf-8"))
    json.dump(
        {"total_rules": len(items), "rules": items},
        sys.stdout,
        indent=2,
        ensure_ascii=False,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
