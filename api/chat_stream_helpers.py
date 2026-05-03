# api/chat_stream_helpers.py
"""Claude Agent SDK partial 메시지 → 토큰 delta 추출 공용 로직.

3개 엔진(general/theme/education)이 동일한 SDK 호출 패턴을 공유하므로
delta 계산 로직을 공용화한다.
"""
from __future__ import annotations
import sys
import warnings
from typing import Awaitable, Callable, Optional

from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock

OnToken = Callable[[str], Awaitable[None]]
OnError = Callable[[str, str], Awaitable[None]]


def _extract_text(message) -> Optional[str]:
    """SDK 메시지에서 누적/신규 텍스트 추출.

    AssistantMessage / PartialAssistantMessage(SDK 신규 타입, duck-typed) 의
    content 의 모든 TextBlock 을 concat 한 문자열을 반환.
    텍스트 없는 메시지(ToolUseBlock·ResultMessage 등) 는 None.

    호출자는 직전 누적과 prefix 비교하여 delta 를 계산한다.
    """
    if not hasattr(message, "content"):
        return None
    parts: list[str] = []
    # SDK 업그레이드 시 재검토 필요: ToolUseBlock/ToolResultBlock 등이 미래에
    # `.text` 속성을 갖게 되면 hasattr guard 가 잘못 매칭할 수 있음.
    # 현재 SDK 버전 (claude-agent-sdk) 에선 안전하나, partial 메시지 타입이
    # 추가될 때 _extract_text 의 매칭 조건을 재확인할 것.
    for block in getattr(message, "content", []) or []:
        if isinstance(block, TextBlock) or hasattr(block, "text"):
            text = getattr(block, "text", None)
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts) if parts else None


async def stream_claude_chat(
    prompt: str,
    system: str,
    *,
    on_token: OnToken,
    on_error: OnError,
    max_turns: int = 1,
) -> str:
    """Claude SDK 스트리밍 호출 + 토큰 delta 콜백. 최종 누적 텍스트 반환.

    on_token 호출은 직전 누적과의 delta 만 보낸다 (중복 송출 X).
    on_error 는 raise 전에 호출 — broker 가 구독자에게 error 이벤트 전파.
    """
    accumulated = ""
    cli_stderr: list[str] = []

    def _on_stderr(line: str) -> None:
        cli_stderr.append(line)

    try:
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                system_prompt=system,
                max_turns=max_turns,
                include_partial_messages=True,
                stderr=_on_stderr,
            ),
        ):
            new_text = _extract_text(message)
            if new_text is None:
                continue
            if new_text.startswith(accumulated):
                delta = new_text[len(accumulated):]
                accumulated = new_text
            else:
                # 새 chunk (별개 TextBlock 또는 분리된 메시지)
                # 운영 단계에서 SDK 가 prefix-style 이 아닌 chunk 를 보내는지
                # 검증하기 위한 경로 — 발생 시 진단 로그 + accumulated concat
                warnings.warn(
                    f"[chat_stream] non-prefix chunk detected: "
                    f"prev_len={len(accumulated)}, new_len={len(new_text)}, "
                    f"new_text[:50]={new_text[:50]!r}",
                    RuntimeWarning,
                    stacklevel=2,
                )
                delta = new_text
                accumulated += new_text
            if delta:
                await on_token(delta)
    except Exception as e:
        dump = "\n".join(cli_stderr[-200:]) if cli_stderr else "(stderr empty)"
        msg = f"{type(e).__name__}: {e}"
        print(
            f"[chat_stream] Claude SDK 호출 실패: {msg}\n"
            f"--- CLI stderr (마지막 200줄) ---\n{dump}\n--- end ---",
            file=sys.stderr, flush=True,
        )
        await on_error(msg, "sdk_failure")
        raise
    return accumulated
