"""채팅 답변 인용 카드 추출 (Tier 1 #6).

assistant 답변 텍스트에서 stock_universe / investment_themes 매칭을 추출하여
chat bubble 하단에 inline 카드 (종목 / 테마) 로 노출하기 위한 휘발성 후처리.

핵심 정책:
  - **DB 화이트리스트 검증 필수** — hallucination ticker 차단
  - 추출 실패 / 결과 0 → 빈 리스트 (채팅 자체는 영향 없음)
  - 4개 채팅 라우트 (theme / general / education / streaming) 공통 진입점

저장 컬럼 추가 없이 페이지 렌더 시 1쿼리로 즉시 추출 — 마이그레이션 불필요.
"""
from __future__ import annotations

import re
import sys
from typing import Any

from psycopg2.extras import RealDictCursor

# general_chat_engine 의 패턴 재사용
_KRX_TICKER_RE = re.compile(r"\b\d{6}\b")
_US_TICKER_RE = re.compile(r"\b[A-Z]{2,5}\b")  # 1자 ticker 는 false positive 비율 높음
_MIN_NAME_LEN = 3
_MAX_TICKERS = 5
_MAX_THEMES = 3


def extract_citations(text: str, conn) -> dict:
    """답변 텍스트에서 종목·테마 매칭 추출.

    Args:
        text: assistant 메시지 본문
        conn: 활성 DB 커넥션 (psycopg2)

    Returns:
        {"tickers": [...], "themes": [...]}
        실패·결과없음 → {"tickers": [], "themes": []}
    """
    if not text or not isinstance(text, str) or not text.strip():
        return {"tickers": [], "themes": []}
    if not conn:
        return {"tickers": [], "themes": []}

    tickers = _extract_tickers(text, conn)
    themes = _extract_themes(text, conn)
    return {"tickers": tickers, "themes": themes}


def _extract_tickers(text: str, conn) -> list[dict]:
    """stock_universe 화이트리스트 기반 종목 추출."""
    krx_candidates = set(_KRX_TICKER_RE.findall(text))
    us_candidates = {m.upper() for m in _US_TICKER_RE.findall(text)}
    direct_tickers = krx_candidates | us_candidates

    found: dict[tuple[str, str], dict[str, Any]] = {}
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if direct_tickers:
                cur.execute(
                    """
                    SELECT ticker, market, asset_name, sector_norm,
                           last_price_ccy AS currency, market_cap_krw
                    FROM stock_universe
                    WHERE UPPER(ticker) = ANY(%s) AND listed = TRUE
                    """,
                    (list(direct_tickers),),
                )
                for row in cur.fetchall():
                    key = (row["ticker"].upper(), row["market"])
                    found[key] = dict(row)

            # 종목명 substring (한국어 회사명 포함)
            cur.execute(
                """
                SELECT ticker, market, asset_name, sector_norm,
                       last_price_ccy AS currency, market_cap_krw
                FROM stock_universe
                WHERE listed = TRUE
                  AND LENGTH(asset_name) >= %s
                  AND %s LIKE '%%' || asset_name || '%%'
                ORDER BY LENGTH(asset_name) DESC,
                         market_cap_krw DESC NULLS LAST
                LIMIT 30
                """,
                (_MIN_NAME_LEN, text),
            )
            for row in cur.fetchall():
                key = (row["ticker"].upper(), row["market"])
                if key not in found:
                    found[key] = dict(row)
    except Exception as e:
        print(
            f"[chat_citations._extract_tickers] DB 조회 실패: {e}",
            file=sys.stderr,
        )
        try:
            conn.rollback()
        except Exception:
            pass
        return []

    if not found:
        return []
    sorted_list = sorted(
        found.values(),
        key=lambda x: x.get("market_cap_krw") or 0,
        reverse=True,
    )
    return sorted_list[:_MAX_TICKERS]


def _extract_themes(text: str, conn) -> list[dict]:
    """investment_themes 화이트리스트 기반 테마 추출.

    최근 30일 분석 세션의 theme_name 부분 일치만 매칭 — 너무 옛날 테마는 이미 의미 적음.
    같은 theme_name 이 여러 세션 등장 시 가장 최근 1건만.
    """
    found: dict[str, dict] = {}
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (t.theme_name)
                    t.id AS theme_id, t.theme_name, t.theme_key,
                    t.session_id, t.confidence_score,
                    s.analysis_date
                FROM investment_themes t
                JOIN analysis_sessions s ON s.id = t.session_id
                WHERE LENGTH(t.theme_name) >= %s
                  AND s.analysis_date >= CURRENT_DATE - INTERVAL '30 days'
                  AND %s LIKE '%%' || t.theme_name || '%%'
                ORDER BY t.theme_name, s.analysis_date DESC
                LIMIT 10
                """,
                (_MIN_NAME_LEN, text),
            )
            for row in cur.fetchall():
                key = row["theme_name"]
                found[key] = dict(row)
    except Exception as e:
        print(
            f"[chat_citations._extract_themes] DB 조회 실패: {e}",
            file=sys.stderr,
        )
        try:
            conn.rollback()
        except Exception:
            pass
        return []

    if not found:
        return []
    sorted_list = sorted(
        found.values(),
        key=lambda x: x.get("analysis_date") or "",
        reverse=True,
    )
    return sorted_list[:_MAX_THEMES]


def attach_citations_to_messages(messages: list[dict], conn) -> list[dict]:
    """메시지 리스트의 assistant 메시지에 citations 필드 주입.

    채팅 페이지 SSR 직전 호출. 사용자 메시지는 건너뜀.
    실패 시 citations 필드 없이 그대로 — 채팅 자체는 안 죽음.
    """
    if not messages or not conn:
        return messages
    try:
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content") or ""
            if not content.strip():
                continue
            cit = extract_citations(content, conn)
            if cit["tickers"] or cit["themes"]:
                msg["citations"] = cit
    except Exception as e:
        print(f"[chat_citations.attach_citations] 실패: {e}", file=sys.stderr)
    return messages
