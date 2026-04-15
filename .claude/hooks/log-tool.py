#!/usr/bin/env python3
"""PostToolUse hook — Write/Edit/Bash 도구 사용을 로그 파일에 저장"""
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

    tool_name = data.get("tool_name", "unknown")
    tool_input = data.get("tool_input", {})
    session_id = data.get("session_id", "unknown")[:8]
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    date_str = datetime.now().strftime("%Y%m%d")

    # 도구별 요약 생성
    if tool_name == "Write":
        summary = f"Write → `{tool_input.get('file_path', '?')}`"
    elif tool_name == "Edit":
        summary = f"Edit → `{tool_input.get('file_path', '?')}`"
    elif tool_name == "Bash":
        cmd = tool_input.get("command", "?")
        if len(cmd) > 200:
            cmd = cmd[:200] + "..."
        summary = f"Bash → `{cmd}`"
    else:
        summary = f"{tool_name}"

    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)

    log_path = os.path.join(log_dir, f"{date_str}.md")
    with open(log_path, "a", encoding="utf-8", errors="replace") as f:
        f.write(f"- `{timestamp}` [{session_id}] {summary}\n")

if __name__ == "__main__":
    main()