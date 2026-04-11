"""Claude Code SDK (Agent SDK) 간단한 프롬프트 호출 테스트.
사용 방법:
SDK 설치: pip install claude-agent-sdk

실행: python test_claude_sdk.py
"""

import asyncio
from claude_agent_sdk import ClaudeAgentOptions, query

async def main():
    prompt = "파이썬 언어에 대해서 설명해줘."

    print("=== Claude Code SDK 프롬프트 호출 테스트 ===\n")
    print(f"프롬프트: {prompt}\n")
    print("응답:")

    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(allowed_tools=[]),
    ):
        if hasattr(message, "result"):
            print(message.result)

if __name__ == "__main__":
    asyncio.run(main())