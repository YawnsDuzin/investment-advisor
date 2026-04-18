"""투자 교육 AI 튜터 엔진 — Claude Code SDK 기반 교육 대화"""
import anyio
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock

EDU_SYSTEM_PROMPT = """당신은 20년 경력의 투자 교육 전문가(CFA, 前 증권사 리서치센터장)입니다.
사용자가 투자 지식에 대해 질문합니다.

아래는 현재 학습 중인 토픽의 내용입니다:
{topic_context}

답변 원칙:
- **실제 사례와 숫자**를 들어 설명 (추상적 설명 지양)
- 어려운 개념은 **비유와 일상적 예시**로 풀어서 설명
- 개념을 설명한 후 \"이 앱에서는 이렇게 활용할 수 있습니다\"라는 **실전 연결** 포함
- 질문의 수준에 맞춰 난이도 조절 (초보자에게는 쉽게, 경험자에게는 깊게)
- 투자 리스크와 한계를 항상 언급
- 특정 종목 매수/매도 추천은 하지 않음 — 판단 기준과 방법론만 교육
- 한국어로 답변
- Markdown 포맷 사용 가능"""


def build_topic_context(topic: dict) -> str:
    """DB에서 조회한 교육 토픽을 텍스트 컨텍스트로 변환"""
    lines = [
        f"## 토픽: {topic['title']}",
        f"- 카테고리: {topic['category']}",
        f"- 난이도: {topic['difficulty']}",
        f"- 요약: {topic.get('summary', '')}",
    ]

    if topic.get("content"):
        lines.append(f"\n### 토픽 본문\n{topic['content']}")

    if topic.get("examples"):
        examples = topic["examples"]
        if isinstance(examples, str):
            import json
            try:
                examples = json.loads(examples)
            except (json.JSONDecodeError, TypeError):
                examples = []
        if examples:
            lines.append("\n### 과거 사례")
            for ex in examples:
                lines.append(f"- **{ex.get('title', '')}** ({ex.get('period', '')})")
                lines.append(f"  {ex.get('description', '')}")
                if ex.get("lesson"):
                    lines.append(f"  교훈: {ex['lesson']}")

    return "\n".join(lines)


async def _query_edu_chat(
    prompt: str, system: str, max_turns: int,
) -> str:
    """Claude SDK 비동기 쿼리 (내부용)"""
    full_response = ""
    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            system_prompt=system,
            max_turns=max_turns,
        ),
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    full_response += block.text
    return full_response


def query_edu_chat_sync(
    topic_context: str,
    conversation_history: list[dict],
    user_message: str,
    max_turns: int = 1,
) -> str:
    """교육 AI 튜터 Claude SDK 동기 호출

    별도 이벤트 루프에서 실행하여 uvicorn 루프와 충돌 방지.
    """
    # 대화 이력을 프롬프트에 포함 (최근 20개 제한)
    recent_history = conversation_history[-20:]
    history_text = ""
    if recent_history:
        history_text = "\n\n## 이전 대화\n"
        for msg in recent_history:
            prefix = "학습자" if msg["role"] == "user" else "튜터"
            history_text += f"\n**{prefix}:** {msg['content']}\n"

    prompt = f"{history_text}\n학습자: {user_message}"
    system = EDU_SYSTEM_PROMPT.format(topic_context=topic_context)

    return anyio.run(_query_edu_chat, prompt, system, max_turns)
