"""shared.db 공개 API 스냅샷 — A 트랙 parity 검증용.

사용: python -m tools.a_db_split.capture_api > tools/a_db_split/baseline.json
비교: python -m tools.a_db_split.capture_api | diff - tools/a_db_split/baseline.json

계약된 13개 공개 심볼만 캡처한다. 재생성 시 "generated_at_sha" 필드로
baseline이 어떤 커밋에서 찍혔는지 추적한다.
"""
import inspect
import json
import subprocess
import sys

import shared.db as m

PUBLIC_API = {
    "SCHEMA_VERSION",
    "get_connection",
    "init_db",
    "save_analysis",
    "save_news_articles",
    "get_untranslated_news",
    "update_news_title_ko",
    "update_news_translation",
    "get_latest_news_titles",
    "get_recent_recommendations",
    "get_existing_theme_keys",
    "save_top_picks",
    "update_top_picks_ai_rerank",
}


def _current_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


symbols: dict[str, str] = {}
for name in sorted(dir(m)):
    if name not in PUBLIC_API:
        continue
    obj = getattr(m, name)
    if callable(obj):
        try:
            symbols[name] = f"callable{inspect.signature(obj)}"
        except (TypeError, ValueError):
            symbols[name] = "callable(?)"
    else:
        symbols[name] = f"<{type(obj).__name__}>"

missing = sorted(PUBLIC_API - symbols.keys())
if missing:
    sys.stderr.write(f"WARNING: missing public symbols: {missing}\n")

output = {
    "generated_at_sha": _current_sha(),
    "symbols": symbols,
}
json.dump(output, sys.stdout, indent=2, ensure_ascii=False)
sys.stdout.write("\n")
