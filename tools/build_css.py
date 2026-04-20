"""src/*.css를 정렬된 순서로 합쳐 style.css 생성.

사용: python -m tools.build_css
"""
from pathlib import Path

SRC = Path("api/static/css/src")
OUT = Path("api/static/css/style.css")
HEADER = "/* AUTO-GENERATED — edit files in src/ and run `python -m tools.build_css` */\n\n"


def main() -> int:
    # 00_legacy.css는 이전 전체 CSS의 잔여분. 분할 완료 전까지 임시로 마지막에 처리.
    # 번호 prefix(01_~19_)가 로드 순서(cascade)를 결정하며, 00_legacy.css는 점진적으로 비워진다.
    all_files = sorted(SRC.glob("*.css"))
    legacy = [f for f in all_files if f.name == "00_legacy.css"]
    numbered = [f for f in all_files if f.name != "00_legacy.css"]
    files = numbered + legacy
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
