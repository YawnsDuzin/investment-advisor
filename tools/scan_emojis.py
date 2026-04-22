"""이모지 사용 현황 스캔 — Phase 2 범위 파악용 (일회성 스크립트)"""
import re
import pathlib
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

EMOJI = re.compile(
    r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF]"
)


def scan(root: pathlib.Path) -> None:
    total = 0
    for p in sorted(root.rglob("*.html")):
        content = p.read_text(encoding="utf-8")
        matches = EMOJI.findall(content)
        if not matches:
            continue
        counter: dict[str, int] = {}
        for m in matches:
            counter[m] = counter.get(m, 0) + 1
        summary = " ".join(
            f"{k}x{v}" for k, v in sorted(counter.items(), key=lambda x: -x[1])
        )
        total += len(matches)
        rel = p.as_posix()
        print(f"{rel:65s} {summary}")
    print(f"TOTAL: {total} emoji occurrences")


if __name__ == "__main__":
    scan(pathlib.Path("api/templates"))
