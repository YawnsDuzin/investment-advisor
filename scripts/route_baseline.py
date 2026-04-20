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
    "/pages/sessions/date/2026-04-20",
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

# 다음 URL들은 GET 호출이 DB 쓰기/상태 누적을 유발하므로
# body·length·sha256은 비교하지 않고 status만 비교한다.
# (코드 변경이 아닌 DB state 차이를 noise로 처리)
STATUS_ONLY = {
    "/pages/education/chat",         # 채팅 세션 목록 — 세션 누적 시 변동
    "/pages/education/chat/new/1",   # 신규 세션 생성 (302 redirect target ID 증가)
    "/pages/chat/new/1",             # 동일 패턴 가능성 — 안전을 위해 포함
}


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
    errors = [r for r in results if "error" in r]
    status_note = f" ({len(errors)}개 오류)" if errors else ""
    print(f"[OK] {len(results)}개 라우트 캡처{status_note} → {out}")


def _key_for(record: dict, status_only: bool) -> tuple:
    """비교용 키 — STATUS_ONLY 라우트는 body 무시, 그 외는 전체 비교."""
    if "error" in record:
        return ("error", record["error"])
    if status_only:
        return ("status", record["status"])
    return (
        "full",
        record["status"],
        record["length"],
        record["sha256"],
        record["location"],
    )


def diff(label_a: str, label_b: str) -> int:
    path_a = OUT_DIR / f"route_baseline_{label_a}.json"
    path_b = OUT_DIR / f"route_baseline_{label_b}.json"
    for p, lbl in [(path_a, label_a), (path_b, label_b)]:
        if not p.exists():
            print(f"[ERROR] baseline 파일 없음: {p}  (먼저 capture --label {lbl} 실행)")
            return 2
    a = json.loads(path_a.read_text(encoding="utf-8"))
    b = json.loads(path_b.read_text(encoding="utf-8"))
    by_url_a = {r["url"]: r for r in a["routes"]}
    by_url_b = {r["url"]: r for r in b["routes"]}

    diffs = []
    status_only_skipped = []
    for url in sorted(set(by_url_a) | set(by_url_b)):
        ra, rb = by_url_a.get(url), by_url_b.get(url)
        is_status_only = url in STATUS_ONLY
        if ra and rb and is_status_only and ra.get("status") == rb.get("status"):
            status_only_skipped.append(url)
            continue
        ka = _key_for(ra, is_status_only) if ra else None
        kb = _key_for(rb, is_status_only) if rb else None
        if ka != kb:
            diffs.append((url, ra, rb))

    if not diffs:
        suffix = f" (STATUS_ONLY로 body 무시: {len(status_only_skipped)}개)" if status_only_skipped else ""
        print(f"[OK] diff 없음 ({label_a} vs {label_b}){suffix}")
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
