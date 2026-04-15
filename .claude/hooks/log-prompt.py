#!/usr/bin/env python3
"""UserPromptSubmit hook — 사용자 프롬프트를 로그 파일에 저장"""
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

    prompt = data.get("prompt", "").strip()
    if not prompt:
        return

    session_id = data.get("session_id", "unknown")[:8]
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    date_str = datetime.now().strftime("%Y%m%d")

    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)

    log_path = os.path.join(log_dir, f"{date_str}.md")
    with open(log_path, "a", encoding="utf-8", errors="replace") as f:
        f.write(f"\n## [{timestamp}] session:{session_id}\n")
        f.write(f"**Prompt:**\n```\n{prompt}\n```\n")

if __name__ == "__main__":
    main()