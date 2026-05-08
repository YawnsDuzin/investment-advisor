"""채팅 starter 질문 통합 API.

GET /api/chat-starters?scope={general|theme|education}&theme_id=&topic_id=
→ {"questions": [...], "cached": bool, "scope": ...}

- general: in-memory TTL 캐시 (user_id+date) — Free 사용자도 OK
- theme:   investment_themes.starter_questions JSONB DB 캐시
- education: education_topics.starter_questions JSONB DB 캐시

실패 시 정적 fallback 으로 200 반환 — 빈 채팅방 진입은 절대 안 죽음.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg2.extras import RealDictCursor

from api.auth.dependencies import get_current_user
from api.auth.models import UserInDB
from api.chat_engine import build_theme_context
from api.chat_starters import (
    cache_get_general,
    cache_put_general,
    generate_starter_questions,
    get_fallback_questions,
)
from api.deps import get_db_conn
from api.education_engine import build_topic_context
from api.general_chat_engine import build_user_context


router = APIRouter(prefix="/api/chat-starters", tags=["chat-starters"])


_VALID_SCOPES = ("general", "theme", "education")


async def _generate_async(scope: str, context_text: str) -> list[str]:
    """동기 SDK 호출을 thread pool 에서 실행 (uvicorn 이벤트 루프 블로킹 방지)."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, generate_starter_questions, scope, context_text
    )


@router.get("")
async def get_starters(
    scope: str = Query(..., pattern="^(general|theme|education)$"),
    theme_id: Optional[int] = Query(default=None),
    topic_id: Optional[int] = Query(default=None),
    conn=Depends(get_db_conn),
    user: Optional[UserInDB] = Depends(get_current_user),
):
    if scope not in _VALID_SCOPES:
        raise HTTPException(status_code=400, detail="invalid scope")

    if scope == "general":
        return await _handle_general(conn, user)
    if scope == "theme":
        if theme_id is None:
            raise HTTPException(status_code=400, detail="theme_id required for scope=theme")
        return await _handle_theme(conn, theme_id)
    if scope == "education":
        if topic_id is None:
            raise HTTPException(status_code=400, detail="topic_id required for scope=education")
        return await _handle_education(conn, topic_id)
    raise HTTPException(status_code=400, detail="unsupported scope")


# ── general ───────────────────────────────────────


async def _handle_general(conn, user: Optional[UserInDB]):
    user_id = user.id if user else None

    cached = cache_get_general(user_id)
    if cached:
        return {"scope": "general", "questions": cached, "cached": True}

    # 컨텍스트 build — user_id None 이면 빈 컨텍스트 → fallback
    try:
        context_text = build_user_context(conn, user_id)
    except Exception:
        context_text = ""

    if not context_text.strip():
        questions = get_fallback_questions("general")
        # 빈 컨텍스트도 캐시 (불필요한 SDK 호출 방지)
        cache_put_general(user_id, questions)
        return {"scope": "general", "questions": questions, "cached": False}

    questions = await _generate_async("general", context_text)
    cache_put_general(user_id, questions)
    return {"scope": "general", "questions": questions, "cached": False}


# ── theme ─────────────────────────────────────────


async def _handle_theme(conn, theme_id: int):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT id, theme_name, description, confidence_score, time_horizon, "
            "theme_type, starter_questions FROM investment_themes WHERE id = %s",
            (theme_id,),
        )
        theme = cur.fetchone()
    if not theme:
        raise HTTPException(status_code=404, detail="theme not found")

    cached = _extract_cached_questions(theme.get("starter_questions"))
    if cached:
        return {"scope": "theme", "questions": cached, "cached": True}

    # 컨텍스트 빌드 — scenarios/proposals/macro
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT scenario_type, probability, description FROM theme_scenarios "
            "WHERE theme_id = %s ORDER BY probability DESC",
            (theme_id,),
        )
        scenarios = [dict(r) for r in cur.fetchall()]
        cur.execute(
            "SELECT asset_name, ticker, action, conviction, target_allocation, rationale "
            "FROM investment_proposals WHERE theme_id = %s ORDER BY target_allocation DESC NULLS LAST LIMIT 8",
            (theme_id,),
        )
        proposals = [dict(r) for r in cur.fetchall()]
        cur.execute(
            "SELECT variable_name, base_case, worse_case, better_case "
            "FROM macro_impacts WHERE theme_id = %s",
            (theme_id,),
        )
        macros = [dict(r) for r in cur.fetchall()]

    context_text = build_theme_context(dict(theme), scenarios, proposals, macros)
    questions = await _generate_async("theme", context_text)

    # DB 캐시 영속화 — 실패해도 응답은 정상
    payload = {
        "questions": questions,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investment_themes SET starter_questions = %s WHERE id = %s",
                (json.dumps(payload, ensure_ascii=False), theme_id),
            )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass

    return {"scope": "theme", "questions": questions, "cached": False}


# ── education ─────────────────────────────────────


async def _handle_education(conn, topic_id: int):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM education_topics WHERE id = %s", (topic_id,))
        topic = cur.fetchone()
    if not topic:
        raise HTTPException(status_code=404, detail="topic not found")

    cached = _extract_cached_questions(topic.get("starter_questions"))
    if cached:
        return {"scope": "education", "questions": cached, "cached": True}

    context_text = build_topic_context(dict(topic))
    questions = await _generate_async("education", context_text)

    payload = {
        "questions": questions,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE education_topics SET starter_questions = %s WHERE id = %s",
                (json.dumps(payload, ensure_ascii=False), topic_id),
            )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass

    return {"scope": "education", "questions": questions, "cached": False}


# ── 공용 ──────────────────────────────────────────


def _extract_cached_questions(raw) -> Optional[list[str]]:
    """JSONB 컬럼에서 questions 배열 추출. 형식 어긋나면 None."""
    if not raw:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return None
    if not isinstance(raw, dict):
        return None
    qs = raw.get("questions")
    if not isinstance(qs, list):
        return None
    out = [q for q in qs if isinstance(q, str) and 4 <= len(q.strip()) <= 100]
    return out[:3] if len(out) >= 3 else None
