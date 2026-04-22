"""AI 쿼리 아카이브 재분석 CLI.

`ai_query_archive` 테이블에 저장된 원본 응답을 현재 파서 로직으로 다시 파싱하여
- 복구 가능 여부 확인
- 유효 item(테마/이슈/제안) 개수 리포트
- 전체 JSON 또는 특정 필드 미리보기

사용 예:
    python -m analyzer.replay --id 27
    python -m analyzer.replay --id 27 --stage stage1a
    python -m analyzer.replay --id 27 --dump-json out.json
    python -m analyzer.replay --list-failed     # 실패 아카이브 최근 20건 목록
"""
import argparse
import json
import sys
from pathlib import Path

from shared.config import DatabaseConfig
from shared.logger import get_ai_query_raw, init_logger
from analyzer.analyzer import _parse_json_response


def _list_failed(db_cfg: DatabaseConfig, limit: int = 20) -> None:
    """parse_status != 'success' 인 최근 아카이브 목록."""
    from psycopg2.extras import RealDictCursor
    from shared.db import get_connection

    conn = get_connection(db_cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT id, run_id, stage, target_key, model,
                          response_chars, elapsed_sec, parse_status,
                          parse_error, created_at
                   FROM ai_query_archive
                   WHERE parse_status != 'success'
                   ORDER BY created_at DESC
                   LIMIT %s""",
                (limit,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        print("실패 아카이브 없음.")
        return

    print(f"최근 실패 아카이브 {len(rows)}건:\n")
    print(f"{'ID':>5} | {'STAGE':<10} | {'STATUS':<22} | {'CHARS':>6} | {'SEC':>5} | TARGET")
    print("-" * 90)
    for r in rows:
        target = (r.get("target_key") or "")[:30]
        print(
            f"{r['id']:>5} | {r['stage']:<10} | {r['parse_status']:<22} | "
            f"{r['response_chars']:>6} | {float(r['elapsed_sec'] or 0):>5.1f} | {target}"
        )


def _replay(db_cfg: DatabaseConfig, archive_id: int, dump_path: str | None) -> int:
    row = get_ai_query_raw(db_cfg, archive_id)
    if not row:
        print(f"archive id={archive_id} 없음.", file=sys.stderr)
        return 2

    print(f"== AI Query Archive #{archive_id} ==")
    print(f"stage         : {row.get('stage')}")
    print(f"target_key    : {row.get('target_key')}")
    print(f"model         : {row.get('model')}")
    print(f"response_chars: {row.get('response_chars')}")
    print(f"elapsed_sec   : {row.get('elapsed_sec')}")
    print(f"parse_status  : {row.get('parse_status')}  (저장 시점)")
    print(f"parse_error   : {row.get('parse_error')}")
    print(f"created_at    : {row.get('created_at')}")
    print()

    raw = row.get("response_raw") or ""
    if not raw.strip():
        print("response_raw 비어있음 — 재분석 불가.", file=sys.stderr)
        return 3

    print("== 현재 파서로 재분석 ==")
    result = _parse_json_response(raw)

    print(f"parse_status  : {result.get('_parse_status')}  (재분석 시점)")
    print(f"parse_error   : {result.get('_parse_error')}")
    print(f"truncated     : {result.get('_truncated')}")
    print(f"dropped_partial: {result.get('_dropped_partial', 0)}")
    for key in ("issues", "themes", "proposals"):
        if key in result:
            print(f"{key:14s}: {len(result[key])}건")

    if result.get("error"):
        print(f"\n❌ 재분석 실패: {result['error']}", file=sys.stderr)
        return 1

    if dump_path:
        dump_result = {k: v for k, v in result.items() if not k.startswith("_")}
        Path(dump_path).write_text(
            json.dumps(dump_result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n→ 파싱 결과를 {dump_path}에 저장")

    # 상위 테마/이슈 미리보기
    themes = result.get("themes", [])
    if themes:
        print(f"\n== 복구된 테마 ({len(themes)}건) ==")
        for i, t in enumerate(themes[:10]):
            print(f"  [{i}] {t.get('theme_key', '?'):<40} {t.get('theme_name', '')[:40]}")

    issues = result.get("issues", [])
    if issues:
        print(f"\n== 복구된 이슈 ({len(issues)}건, 상위 5건) ==")
        for i, iss in enumerate(issues[:5]):
            print(
                f"  [{i}] ({iss.get('category', '?')}, 중요도 {iss.get('importance', '?')}) "
                f"{iss.get('title', '')[:60]}"
            )

    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="ai_query_archive 재분석 CLI")
    p.add_argument("--id", type=int, help="아카이브 id (단일 재분석)")
    p.add_argument("--dump-json", help="파싱 결과 JSON을 저장할 경로")
    p.add_argument("--list-failed", action="store_true", help="실패 아카이브 목록")
    p.add_argument("--limit", type=int, default=20, help="--list-failed 조회 건수")
    args = p.parse_args()

    db_cfg = DatabaseConfig()  # dataclass 기본 필드가 .env를 읽음
    init_logger(db_cfg)  # archive 접근에 필요

    if args.list_failed:
        _list_failed(db_cfg, args.limit)
        return 0

    if not args.id:
        p.error("--id 또는 --list-failed 필요")

    return _replay(db_cfg, args.id, args.dump_json)


if __name__ == "__main__":
    sys.exit(main())
