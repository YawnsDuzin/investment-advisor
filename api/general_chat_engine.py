"""자유 질문 채팅 엔진 — Claude Code SDK 기반.

Theme Chat / AI Tutor 와 달리 테마/토픽 컨텍스트가 없다.
대신 사용자의 워치리스트 + 최근 추천 이력을 동적 주입하여
"내 관심 종목 중 살만한 거 있어?" 같은 개인화 질문을 가능하게 한다.

도메인은 투자/금융 으로 한정 — 페르소나가 비투자 질문은 정중히 거절한다.
"""
from __future__ import annotations

from typing import Optional

import anyio
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock
from psycopg2.extras import RealDictCursor


GENERAL_CHAT_SYSTEM_PROMPT = """당신은 AlphaSignal의 투자 어시스턴트입니다 (CFA, 20년 경력의 글로벌 매크로 전략가).

역할:
- 사용자의 자유 형식 투자 질문에 답변
- 관심 종목·최근 추천 이력 기반 개인화 답변
- 투자/거시경제/종목/시장 분석 도메인 한정

답변 원칙:
- 구체적인 데이터·근거를 포함하되, 학습 데이터 기반 추정값은 명시적으로 "추정" 표기
- 실시간 가격·재무는 보유하지 않음 → 사용자가 종목 상세 페이지(/pages/stocks/<ticker>)에서 확인하도록 안내
- 투자 리스크를 항상 언급
- 한국어로 답변 (Markdown 가능)
- 짧고 단정적으로 — 군더더기 없이 결론부터

도메인 외 질문 처리:
- 투자/금융과 무관한 질문(코딩·일상·번역 등)은 정중히 거절:
  "AlphaSignal 투자 어시스턴트는 투자/시장 관련 질문만 답변할 수 있습니다."
- 다만 거시경제·산업 트렌드·기업 지배구조 등 투자 의사결정에 영향 주는 주변 주제는 허용

{user_context}"""


def build_user_context(
    conn,
    user_id: Optional[int],
    *,
    watchlist_limit: int = 20,
    recent_proposal_days: int = 7,
    recent_proposal_limit: int = 10,
) -> str:
    """사용자 워치리스트 + 최근 추천 이력 → 시스템 프롬프트 주입용 텍스트.

    user_id 가 None 이면 비로그인/익명 — 빈 컨텍스트 반환.
    조회 실패해도 빈 문자열 반환 (LLM 호출은 컨텍스트 없이 진행).
    """
    if user_id is None:
        return ""

    lines: list[str] = []

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 워치리스트 (user_watchlist 스키마: ticker/asset_name/memo — market 컬럼 없음)
            cur.execute(
                """
                SELECT ticker, asset_name, memo
                FROM user_watchlist
                WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (user_id, watchlist_limit),
            )
            watchlist = cur.fetchall()

            # 최근 N일 본 종목 추천 (해당 사용자가 메모를 남긴 것 또는 본인 알림에서 매칭된 것)
            cur.execute(
                """
                SELECT DISTINCT p.ticker, p.asset_name, p.action, p.conviction,
                       t.theme_name
                FROM investment_proposals p
                JOIN investment_themes t ON p.theme_id = t.id
                JOIN analysis_sessions s ON t.session_id = s.id
                WHERE s.analysis_date >= CURRENT_DATE - %s::int
                ORDER BY p.ticker
                LIMIT %s
                """,
                (recent_proposal_days, recent_proposal_limit),
            )
            recent = cur.fetchall()
    except Exception as e:
        print(f"[general_chat_engine.build_user_context] 컨텍스트 조회 실패 (user_id={user_id}): {e}")
        # 망가진 transaction 을 caller 로 흘려보내지 않는다 — 후속 INSERT 가 InFailedSqlTransaction 으로 죽는 것 방지
        try:
            conn.rollback()
        except Exception:
            pass
        return ""

    if watchlist:
        lines.append("## 사용자 관심 종목 (Watchlist)")
        for w in watchlist:
            name = w.get("asset_name") or w["ticker"]
            line = f"- {name} ({w['ticker']})"
            if w.get("memo"):
                line += f" — 메모: {w['memo']}"
            lines.append(line)

    if recent:
        lines.append(f"\n## 최근 {recent_proposal_days}일 시스템 추천 종목 (참고)")
        for r in recent:
            lines.append(
                f"- {r['asset_name']} ({r['ticker']}) — {r['action']}/{r['conviction']} · 테마: {r['theme_name']}"
            )

    if not lines:
        return ""

    return "\n# 사용자 컨텍스트 (자동 주입)\n\n" + "\n".join(lines)


async def _query_claude_chat(prompt: str, system: str, max_turns: int) -> str:
    """Claude SDK 비동기 쿼리 (내부용)"""
    full_response = ""
    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(system_prompt=system, max_turns=max_turns),
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    full_response += block.text
    return full_response


def query_general_chat_sync(
    user_context: str,
    conversation_history: list[dict],
    user_message: str,
    max_turns: int = 1,
) -> str:
    """자유 채팅용 Claude SDK 동기 호출.

    chat_engine.query_theme_chat_sync 와 동일 패턴 — anyio.run 으로
    별도 이벤트 루프에서 실행.
    """
    recent_history = conversation_history[-20:]
    history_text = ""
    if recent_history:
        history_text = "\n\n## 이전 대화\n"
        for msg in recent_history:
            prefix = "사용자" if msg["role"] == "user" else "어시스턴트"
            history_text += f"\n**{prefix}:** {msg['content']}\n"

    prompt = f"{history_text}\n사용자: {user_message}"
    system = GENERAL_CHAT_SYSTEM_PROMPT.format(user_context=user_context or "")

    return anyio.run(_query_claude_chat, prompt, system, max_turns)
