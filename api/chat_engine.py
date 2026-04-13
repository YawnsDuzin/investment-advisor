"""테마 채팅 엔진 — Claude Code SDK 기반 대화형 질의"""
import anyio
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock

CHAT_SYSTEM_PROMPT = """당신은 20년 경력의 글로벌 매크로 투자 전략가(CFA, CAIA)입니다.
사용자가 특정 투자 테마에 대해 질문합니다.

아래는 해당 테마의 분석 컨텍스트입니다:
{theme_context}

답변 원칙:
- 구체적인 데이터와 근거를 포함하여 답변
- 불확실한 정보는 명시적으로 "추정" 또는 "미확인" 표기
- 투자 리스크를 항상 언급
- 한국어로 답변
- Markdown 포맷 사용 가능"""


def build_theme_context(theme: dict, scenarios: list, proposals: list,
                        macro_impacts: list) -> str:
    """DB에서 조회한 테마 데이터를 텍스트 컨텍스트로 변환"""
    lines = [
        f"## 테마: {theme['theme_name']}",
        f"- 설명: {theme['description']}",
        f"- 신뢰도: {theme['confidence_score']}",
        f"- 시계: {theme['time_horizon']}",
    ]
    if theme.get("theme_type"):
        lines.append(f"- 유형: {theme['theme_type']}")

    if scenarios:
        lines.append("\n### 시나리오 분석")
        for sc in scenarios:
            lines.append(
                f"- {sc['scenario_type']} ({sc['probability']}%): {sc['description']}"
            )

    if proposals:
        lines.append("\n### 투자 제안 종목")
        for p in proposals:
            line = f"- {p['asset_name']} ({p['ticker']}) — {p['action']}/{p['conviction']}"
            if p.get("target_allocation"):
                line += f", 비중 {p['target_allocation']}%"
            lines.append(line)
            if p.get("rationale"):
                lines.append(f"  근거: {p['rationale']}")

    if macro_impacts:
        lines.append("\n### 매크로 영향 변수")
        for m in macro_impacts:
            lines.append(
                f"- {m['variable_name']}: Base {m['base_case']}, "
                f"Worse {m['worse_case']}, Better {m['better_case']}"
            )

    return "\n".join(lines)


async def _query_claude_chat(
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


def query_theme_chat_sync(
    theme_context: str,
    conversation_history: list[dict],
    user_message: str,
    max_turns: int = 2,
) -> str:
    """테마 채팅용 Claude SDK 동기 호출

    별도 이벤트 루프에서 실행하여 uvicorn 루프와 충돌 방지.
    FastAPI 엔드포인트에서 run_in_executor로 호출.
    """
    # 대화 이력을 프롬프트에 포함 (최근 20개 제한)
    recent_history = conversation_history[-20:]
    history_text = ""
    if recent_history:
        history_text = "\n\n## 이전 대화\n"
        for msg in recent_history:
            prefix = "사용자" if msg["role"] == "user" else "어시스턴트"
            history_text += f"\n**{prefix}:** {msg['content']}\n"

    prompt = f"{history_text}\n사용자: {user_message}"
    system = CHAT_SYSTEM_PROMPT.format(theme_context=theme_context)

    return anyio.run(_query_claude_chat, prompt, system, max_turns)
