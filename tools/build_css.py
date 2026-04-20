"""src/*.css를 정렬된 순서로 합쳐 style.css 생성.

사용: python -m tools.build_css
"""
from pathlib import Path

SRC = Path("api/static/css/src")
OUT = Path("api/static/css/style.css")
HEADER = "/* AUTO-GENERATED — edit files in src/ and run `python -m tools.build_css` */\n\n"


def main() -> int:
    files = sorted(SRC.glob("*.css"))
    if not files:
        print(f"[css] no source files in {SRC}")
        return 1
    parts = [HEADER]
    for f in files:
        parts.append(f"/* ======== {f.name} ======== */\n")
        parts.append(f.read_text(encoding="utf-8").rstrip() + "\n")
        parts.append("\n")
    OUT.write_text("".join(parts), encoding="utf-8")
    total_src = sum(f.stat().st_size for f in files)
    print(f"[css] bundled {len(files)} files → {OUT} (src total: {total_src} bytes, out: {OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
