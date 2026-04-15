#!/usr/bin/env python3
"""Stop hook — 응답 완료 시 구분선 추가"""
import json
import sys
import os
from datetime import datetime

def main():
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
    except Exception:
        return

    session_id = data.get("session_id", "unknown")[:8]
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    date_str = datetime.now().strftime("%Y%m%d")

    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)

    log_path = os.path.join(log_dir, f"{date_str}.md")
    with open(log_path, "a", encoding="utf-8", errors="replace") as f:
        f.write(f"\n---\n<!-- Stop: {timestamp} session:{session_id} -->\n")

if __name__ == "__main__":
    main()