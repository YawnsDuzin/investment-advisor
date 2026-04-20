"""shared.db 공개 API 스냅샷 — A 트랙 parity 검증용.

사용: python -m tools.a_db_split.capture_api > tools/a_db_split/baseline.json
비교: python -m tools.a_db_split.capture_api | diff - tools/a_db_split/baseline.json
"""
import inspect
import json
import sys

import shared.db as m

snapshot: dict[str, str] = {}
for name in sorted(dir(m)):
    if name.startswith("_"):
        continue
    obj = getattr(m, name)
    if callable(obj):
        try:
            snapshot[name] = f"callable{inspect.signature(obj)}"
        except (TypeError, ValueError):
            snapshot[name] = "callable(?)"
    else:
        snapshot[name] = f"<{type(obj).__name__}>"

json.dump(snapshot, sys.stdout, indent=2, ensure_ascii=False)
sys.stdout.write("\n")
