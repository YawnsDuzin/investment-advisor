"""채팅 starter 질문 생성 모듈 (Ask AI / Theme Chat / AI Tutor 공용).

빈 채팅방 진입 시 노출할 "이런 질문은 어때요?" 카드 3개를 동적 생성한다.

전략:
- Theme/Tutor: theme_id/topic_id 단위 → DB 영속 캐시 (investment_themes/education_topics.starter_questions JSONB, v48)
- Ask AI: user_id+date 단위 → in-memory TTL 캐시 (6h)
- 모델: Haiku (MODEL_TRANSLATE) 1샷, JSON-only 출력
- 실패 시 정적 fallback — 채팅 진입은 절대 안 죽음
"""
from __future__ import annotations

import json
import re
import sys
import time
from threading import Lock
from typing import Optional

import anyio
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    TextBlock,
    query,
)


_MODEL: Optional[str] = None


def _get_model() -> str:
    """Haiku 모델 ID lazy 로드 — AnalyzerConfig.model_translate 재활용."""
    global _MODEL
    if _MODEL is None:
        from shared.config import AnalyzerConfig
        _MODEL = AnalyzerConfig().model_translate
    return _MODEL


_STARTER_PROMPT_TEMPLATE = """당신은 투자 어시스턴트의 starter question 생성기입니다.
주어진 컨텍스트에서 사용자가 가장 궁금해할 만한 짧은 질문 3개를 만드세요.

역할 힌트: {role_hint}

컨텍스트:
{context_text}

규칙:
- 정확히 3개의 질문
- 각 질문 12~30자, 자연스러운 한국어
- 종목명·테마명·토픽명을 포함해도 좋음
- 답변이 가능한 구체적 질문 — 너무 추상적("뭐 좋아?")은 금지
- 출력은 반드시 단일 JSON 객체. 코드블록·주석·설명 금지

출력 형식:
{{"questions": ["질문 1", "질문 2", "질문 3"]}}"""


_ROLE_HINTS = {
    "general": "사용자의 워치리스트와 최근 추천을 토대로 한 자유 투자 질문",
    "theme": "이 테마에 대해 더 깊이 파고들 수 있는 질문",
    "education": "이 학습 토픽을 처음 접한 사람이 던질 만한 질문",
}


_FALLBACKS = {
    "general": [
        "오늘 시장에서 주목할 종목은?",
        "최근 글로벌 거시 이슈 핵심만 정리해줘",
        "내 워치리스트에서 가장 흥미로운 종목 분석해줘",
    ],
    "theme": [
        "이 테마의 핵심 리스크 3가지는?",
        "추천 종목 중 가장 매력적인 건 어떤 거야?",
        "시장 컨센서스 대비 어떤 차이가 있어?",
    ],
    "education": [
        "이 개념을 처음 듣는 사람도 이해할 수 있게 설명해줘",
        "실제 사례 하나만 자세히 알려줘",
        "이 개념을 투자에 어떻게 활용할 수 있어?",
    ],
}


def get_fallback_questions(scope: str) -> list[str]:
    return list(_FALLBACKS.get(scope, _FALLBACKS["general"]))


_CODEBLOCK_RE = re.compile(r"```(?:json)?\s*|\s*```", re.MULTILINE)


def _parse_questions(raw: str) -> list[str]:
    """Haiku 응답 → 질문 3개 list. 실패 시 빈 리스트."""
    if not raw:
        return []
    text = _CODEBLOCK_RE.sub("", raw).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return []
    payload = text[start : end + 1]
    try:
        data = json.loads(payload)
    except Exception:
        return []
    qs = data.get("questions") if isinstance(data, dict) else None
    if not isinstance(qs, list):
        return []
    out: list[str] = []
    for q in qs:
        if not isinstance(q, str):
            continue
        s = q.strip()
        if 4 <= len(s) <= 100:
            out.append(s)
    return out[:3]


async def _query_claude_starter(
    prompt: str, model: str, max_turns: int, timeout_sec: int
) -> str:
    """Haiku starter 호출. 실패는 빈 문자열로 흡수 (caller 에서 fallback)."""
    full = ""
    cli_stderr: list[str] = []

    def _on_stderr(line: str) -> None:
        cli_stderr.append(line)

    try:
        with anyio.fail_after(timeout_sec):
            async for message in query(
                prompt=prompt,
                options=ClaudeAgentOptions(
                    max_turns=max_turns,
                    model=model,
                    stderr=_on_stderr,
                    tools=[],
                    permission_mode="plan",
                    setting_sources=[],
                ),
            ):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            full += block.text
    except BaseException as e:
        dump = "\n".join(cli_stderr[-50:]) if cli_stderr else "(stderr empty)"
        print(
            f"[chat_starters] Claude SDK 호출 실패: {type(e).__name__}: {e}\n"
            f"--- CLI stderr ---\n{dump}\n",
            file=sys.stderr,
            flush=True,
        )
        return ""
    return full


def generate_starter_questions(
    scope: str,
    context_text: str,
    *,
    timeout_sec: int = 60,
) -> list[str]:
    """주어진 scope·context 로 질문 3개 생성. 실패 시 정적 fallback.

    동기 함수 — FastAPI route 에서는 `run_in_executor` 로 호출 권장.
    """
    if not context_text or not context_text.strip():
        return get_fallback_questions(scope)

    role_hint = _ROLE_HINTS.get(scope, _ROLE_HINTS["general"])
    prompt = _STARTER_PROMPT_TEMPLATE.format(
        role_hint=role_hint,
        context_text=context_text[:6000],
    )

    try:
        raw = anyio.run(_query_claude_starter, prompt, _get_model(), 1, timeout_sec)
    except BaseException as e:
        print(f"[chat_starters] anyio.run 실패: {e}", file=sys.stderr, flush=True)
        return get_fallback_questions(scope)

    qs = _parse_questions(raw)
    if len(qs) < 3:
        return get_fallback_questions(scope)
    return qs


# ── in-memory TTL 캐시 (general scope 전용) ───────────
# Theme/Tutor 는 DB 영속 캐시 — 여기는 익명/사용자 단위 휘발성 캐시.
_GENERAL_CACHE: dict[str, tuple[float, list[str]]] = {}
_GENERAL_CACHE_LOCK = Lock()
_GENERAL_TTL_SEC = 6 * 3600
_GENERAL_MAX_ENTRIES = 1000


def _general_key(user_id: Optional[int]) -> str:
    uid = user_id if user_id is not None else 0
    return f"u{uid}:{time.strftime('%Y%m%d')}"


def cache_get_general(user_id: Optional[int]) -> Optional[list[str]]:
    key = _general_key(user_id)
    with _GENERAL_CACHE_LOCK:
        entry = _GENERAL_CACHE.get(key)
        if not entry:
            return None
        ts, qs = entry
        if time.time() - ts > _GENERAL_TTL_SEC:
            _GENERAL_CACHE.pop(key, None)
            return None
        return list(qs)


def cache_put_general(user_id: Optional[int], questions: list[str]) -> None:
    key = _general_key(user_id)
    with _GENERAL_CACHE_LOCK:
        if len(_GENERAL_CACHE) >= _GENERAL_MAX_ENTRIES:
            # 오래된 1/4 evict
            cutoff_idx = len(_GENERAL_CACHE) // 4
            sorted_ts = sorted(v[0] for v in _GENERAL_CACHE.values())
            cutoff = sorted_ts[cutoff_idx] if sorted_ts else 0.0
            for k in list(_GENERAL_CACHE.keys()):
                if _GENERAL_CACHE[k][0] <= cutoff:
                    _GENERAL_CACHE.pop(k, None)
        _GENERAL_CACHE[key] = (time.time(), list(questions))
