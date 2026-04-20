"""페이지 라우트 회귀 테스트용 baseline 캡처/diff 도구.

사용법:
  # 캡처 (서버는 별도 터미널에서 AUTH_ENABLED=false uvicorn ...로 기동)
  python scripts/route_baseline.py capture --label before
  python scripts/route_baseline.py capture --label after

  # 비교
  python scripts/route_baseline.py diff before after
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import httpx

BASE_URL = "http://localhost:8000"
OUT_DIR = Path("_baselines")

# pages.py에서 추출한 모든 GET 페이지 URL.
# 동적 path는 실데이터 의존 없이 검증 가능한 값을 사용 (없으면 404 OK).
ROUTES = [
    "/",
    "/pages/sessions",
    "/pages/sessions/date/2026-04-20",       # 없으면 404 — 그것도 baseline에 포함
    "/pages/sessions/1",
    "/pages/stocks/AAPL",
    "/pages/themes",
    "/pages/themes/history/test_key",
    "/pages/proposals",
    "/pages/proposals/history/AAPL",
    "/proposals/1/stock-analysis",
    "/pages/watchlist",
    "/pages/notifications",
    "/pages/profile",
    "/pages/chat",
    "/pages/chat/new/1",
    "/pages/chat/1",
    "/pages/education",
    "/pages/education/topic/intro",
    "/pages/education/chat",
    "/pages/education/chat/new/1",
    "/pages/education/chat/1",
    "/pages/track-record",
    "/pages/landing",
    "/pages/pricing",
    "/pages/inquiry",
    "/pages/inquiry/new",
    "/pages/inquiry/1",
]


def capture(label: str) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    results = []
    with httpx.Client(base_url=BASE_URL, timeout=30.0, follow_redirects=False) as client:
        for url in ROUTES:
            try:
                r = client.get(url)
                body = r.content
                results.append({
                    "url": url,
                    "status": r.status_code,
                    "length": len(body),
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "location": r.headers.get("location"),  # 리다이렉트 추적
                })
            except Exception as e:
                results.append({"url": url, "error": str(e)})

    out = OUT_DIR / f"route_baseline_{label}.json"
    out.write_text(
        json.dumps({"label": label, "captured_at": datetime.now().isoformat(), "routes": results}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[OK] {len(results)}개 라우트 캡처 → {out}")


def diff(label_a: str, label_b: str) -> int:
    a = json.loads((OUT_DIR / f"route_baseline_{label_a}.json").read_text(encoding="utf-8"))
    b = json.loads((OUT_DIR / f"route_baseline_{label_b}.json").read_text(encoding="utf-8"))
    by_url_a = {r["url"]: r for r in a["routes"]}
    by_url_b = {r["url"]: r for r in b["routes"]}

    diffs = []
    for url in sorted(set(by_url_a) | set(by_url_b)):
        ra, rb = by_url_a.get(url), by_url_b.get(url)
        if ra != rb:
            diffs.append((url, ra, rb))

    if not diffs:
        print(f"[OK] diff 없음 ({label_a} vs {label_b})")
        return 0

    print(f"[FAIL] {len(diffs)}개 라우트에서 변화 감지:")
    for url, ra, rb in diffs:
        print(f"\n  {url}")
        print(f"    BEFORE: {ra}")
        print(f"    AFTER : {rb}")
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    cap = sub.add_parser("capture")
    cap.add_argument("--label", required=True)
    df = sub.add_parser("diff")
    df.add_argument("a")
    df.add_argument("b")
    args = parser.parse_args()

    if args.cmd == "capture":
        capture(args.label)
    elif args.cmd == "diff":
        sys.exit(diff(args.a, args.b))
