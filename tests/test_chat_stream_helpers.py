# tests/test_chat_stream_helpers.py
"""Claude SDK partial 메시지 → 토큰 delta 추출 헬퍼 테스트.

claude_agent_sdk 는 conftest.py 가 mock 처리. 여기서는 mock SDK 가
yield 하는 메시지 시퀀스에 따라 on_token 호출 횟수와 누적 텍스트가
올바른지 검증한다.
"""
from __future__ import annotations
import asyncio
from unittest.mock import patch
import pytest


def _make_text_block(text: str):
    """conftest 의 TextBlock mock 인스턴스 (text 속성 부여)."""
    from claude_agent_sdk import TextBlock
    block = TextBlock()
    block.text = text
    return block


def _make_assistant_message(text_parts: list[str]):
    """AssistantMessage mock — content=[TextBlock,...]."""
    from claude_agent_sdk import AssistantMessage
    msg = AssistantMessage()
    msg.content = [_make_text_block(t) for t in text_parts]
    return msg


@pytest.mark.asyncio
async def test_stream_extracts_prefix_style_partials():
    """SDK 가 prefix-style partial 을 yield → delta 만 emit."""
    from api.chat_stream_helpers import stream_claude_chat

    # 누적 prefix: "안녕" → "안녕하세" → "안녕하세요"
    messages = [
        _make_assistant_message(["안녕"]),
        _make_assistant_message(["안녕하세"]),
        _make_assistant_message(["안녕하세요"]),
    ]

    async def fake_query(prompt, options):
        for m in messages:
            yield m

    tokens: list[str] = []
    async def on_token(t: str):
        tokens.append(t)
    async def on_error(msg: str, code: str):
        pytest.fail(f"on_error called: {msg} ({code})")

    with patch("api.chat_stream_helpers.query", fake_query):
        result = await stream_claude_chat(
            prompt="안녕", system="sys",
            on_token=on_token, on_error=on_error,
        )
    assert tokens == ["안녕", "하세", "요"]
    assert result == "안녕하세요"


@pytest.mark.asyncio
async def test_stream_handles_non_prefix_chunks():
    """누적이 prefix 가 아닌 새 chunk 인 경우 그대로 추가."""
    from api.chat_stream_helpers import stream_claude_chat

    messages = [
        _make_assistant_message(["첫번째 답"]),
        _make_assistant_message(["완전히 다른 것"]),  # prefix 아님
    ]

    async def fake_query(prompt, options):
        for m in messages:
            yield m

    tokens: list[str] = []
    async def on_token(t: str):
        tokens.append(t)
    async def on_error(msg: str, code: str):
        pytest.fail("on_error called")

    with patch("api.chat_stream_helpers.query", fake_query):
        result = await stream_claude_chat(
            prompt="x", system="s",
            on_token=on_token, on_error=on_error,
        )
    assert tokens == ["첫번째 답", "완전히 다른 것"]
    assert result == "첫번째 답완전히 다른 것"


@pytest.mark.asyncio
async def test_stream_skips_messages_without_text():
    """text 가 없는 메시지(ToolUseBlock 등)는 스킵."""
    from api.chat_stream_helpers import stream_claude_chat

    msg_with_text = _make_assistant_message(["응답"])
    msg_without = _make_assistant_message([])  # 빈 content

    async def fake_query(prompt, options):
        yield msg_without
        yield msg_with_text

    tokens: list[str] = []
    async def on_token(t: str):
        tokens.append(t)
    async def on_error(msg: str, code: str):
        pytest.fail("on_error called")

    with patch("api.chat_stream_helpers.query", fake_query):
        result = await stream_claude_chat(
            prompt="x", system="s",
            on_token=on_token, on_error=on_error,
        )
    assert tokens == ["응답"]
    assert result == "응답"


@pytest.mark.asyncio
async def test_stream_calls_on_error_then_raises():
    """SDK 예외 → on_error 호출 + raise."""
    from api.chat_stream_helpers import stream_claude_chat

    async def fake_query(prompt, options):
        yield _make_assistant_message(["부분 "])
        raise RuntimeError("SDK 폭발")

    tokens: list[str] = []
    errors: list[tuple] = []

    async def on_token(t: str):
        tokens.append(t)

    async def on_error(msg: str, code: str):
        errors.append((msg, code))

    with patch("api.chat_stream_helpers.query", fake_query):
        with pytest.raises(RuntimeError, match="SDK 폭발"):
            await stream_claude_chat(
                prompt="x", system="s",
                on_token=on_token, on_error=on_error,
            )
    assert tokens == ["부분 "]
    assert len(errors) == 1
    assert "SDK 폭발" in errors[0][0]
    assert errors[0][1] == "sdk_failure"
