"""자유 질문 채팅 엔진 — Claude Code SDK 기반.

Theme Chat / AI Tutor 와 달리 테마/토픽 컨텍스트가 없다.
대신 사용자의 워치리스트 + 최근 추천 이력을 동적 주입하여
"내 관심 종목 중 살만한 거 있어?" 같은 개인화 질문을 가능하게 한다.

도메인은 투자/금융 으로 한정 — 페르소나가 비투자 질문은 정중히 거절한다.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import date
from typing import Any, Optional

import anyio
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock
from psycopg2.extras import RealDictCursor


# ── 메시지 → 티커 추출 (v1: DB only, 외부 API 호출 없음) ─────────────
# 정책:
# - stock_universe 화이트리스트 + asset_name/asset_name_en substring 매칭
# - 직접 티커(005930, AAPL) 정규식 + DB validate
# - 메시지당 최대 5개 (남용 방지)
# - 우선순위: market_cap_krw DESC NULLS LAST
# - 추출/조회 실패 시 빈 결과 — 채팅 자체는 절대 안 죽음
_MIN_NAME_LEN = 3  # 한국어/영어 종목명 최소 길이 (노이즈 차단)
_KRX_TICKER_RE = re.compile(r"\b\d{6}\b")
_US_TICKER_RE = re.compile(r"\b[A-Z]{2,5}\b")  # 1자 ticker 는 false positive 비율 높음 → 제외
_MAX_TICKERS_PER_MESSAGE = 5
_TICKER_INJECTION_ENABLED = os.getenv("GENERAL_CHAT_TICKER_INJECTION", "true").lower() == "true"
_CCY_SYMBOL = {"KRW": "₩", "USD": "$", "EUR": "€", "JPY": "¥", "GBP": "£"}


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


def _extract_tickers_from_message(conn, message: str) -> list[dict]:
    """사용자 메시지에서 stock_universe 화이트리스트 기반 티커 추출.

    1) 직접 티커 (`005930`, `AAPL`) 정규식 추출 후 DB validate
    2) 종목명 substring 매칭 (`asset_name` / `asset_name_en`, 길이 ≥ 3)

    Returns: [{ticker, market, asset_name, currency, market_cap_krw}, ...] 최대 5개
    실패 시 빈 리스트 (caller 는 그냥 컨텍스트 주입을 건너뛴다).
    """
    if not message or not message.strip():
        return []

    krx_candidates = set(_KRX_TICKER_RE.findall(message))
    us_candidates = {m.upper() for m in _US_TICKER_RE.findall(message)}
    direct_tickers = krx_candidates | us_candidates

    found: dict[tuple[str, str], dict[str, Any]] = {}

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if direct_tickers:
                cur.execute(
                    """
                    SELECT ticker, market, asset_name, asset_name_en,
                           last_price_ccy AS currency, market_cap_krw
                    FROM stock_universe
                    WHERE UPPER(ticker) = ANY(%s) AND listed = TRUE
                    """,
                    (list(direct_tickers),),
                )
                for row in cur.fetchall():
                    key = (row["ticker"].upper(), row["market"])
                    found[key] = dict(row)

            cur.execute(
                """
                SELECT ticker, market, asset_name, asset_name_en,
                       last_price_ccy AS currency, market_cap_krw
                FROM stock_universe
                WHERE listed = TRUE
                  AND LENGTH(asset_name) >= %s
                  AND (
                    %s LIKE '%%' || asset_name || '%%'
                    OR (asset_name_en IS NOT NULL
                        AND LENGTH(asset_name_en) >= %s
                        AND %s ILIKE '%%' || asset_name_en || '%%')
                  )
                ORDER BY LENGTH(asset_name) DESC,
                         market_cap_krw DESC NULLS LAST
                LIMIT 30
                """,
                (_MIN_NAME_LEN, message, _MIN_NAME_LEN, message),
            )
            for row in cur.fetchall():
                key = (row["ticker"].upper(), row["market"])
                if key in found:
                    continue
                found[key] = dict(row)
    except Exception as e:
        print(
            f"[general_chat_engine._extract_tickers_from_message] DB 조회 실패: {e}",
            file=sys.stderr,
        )
        try:
            conn.rollback()
        except Exception:
            pass
        return []

    items = sorted(
        found.values(),
        key=lambda r: (
            0 if r.get("market_cap_krw") is not None else 1,
            -(r.get("market_cap_krw") or 0),
        ),
    )
    return items[:_MAX_TICKERS_PER_MESSAGE]


def _format_price(price: float, currency: str) -> str:
    sym = _CCY_SYMBOL.get(currency, currency or "")
    if currency in ("KRW", "JPY"):
        return f"{sym}{price:,.0f}"
    return f"{sym}{price:,.2f}"


def _fetch_ticker_snapshot(conn, tickers: list[dict]) -> str:
    """추출된 티커들의 stock_universe_ohlcv 최신 종가 + investment_proposals 통계 → 텍스트.

    데이터 소스 모두 DB only — 외부 API 호출 없음.
    실패 시 빈 문자열.
    """
    if not tickers:
        return ""

    lines: list[str] = []
    latest_date_overall: Optional[date] = None

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            for meta in tickers:
                tk = meta["ticker"].upper()
                mk = meta["market"]
                name = meta.get("asset_name") or tk
                ccy = (meta.get("currency") or "").strip() or (
                    "KRW" if mk in ("KOSPI", "KOSDAQ") else "USD"
                )

                cur.execute(
                    """
                    SELECT trade_date, close::float AS close,
                           change_pct::float AS change_pct
                    FROM stock_universe_ohlcv
                    WHERE UPPER(ticker) = %s AND UPPER(market) = %s
                    ORDER BY trade_date DESC LIMIT 1
                    """,
                    (tk, mk.upper()),
                )
                price_row = cur.fetchone()

                cur.execute(
                    """
                    SELECT
                        COUNT(*) AS proposal_count,
                        AVG(p.post_return_3m_pct) AS avg_3m,
                        MAX(s.analysis_date) AS last_recommended
                    FROM investment_proposals p
                    JOIN investment_themes t ON p.theme_id = t.id
                    JOIN analysis_sessions s ON t.session_id = s.id
                    WHERE UPPER(p.ticker) = %s
                    """,
                    (tk,),
                )
                stats = cur.fetchone() or {}

                if price_row and price_row.get("close") is not None:
                    price = price_row["close"]
                    chg = price_row.get("change_pct")
                    td = price_row["trade_date"]
                    if latest_date_overall is None or td > latest_date_overall:
                        latest_date_overall = td
                    chg_str = f"{chg:+.2f}%" if chg is not None else "—"
                    line = (
                        f"- {name} ({tk}, {mk}): "
                        f"종가 {_format_price(price, ccy)} ({chg_str}, {td.isoformat()})"
                    )
                else:
                    line = f"- {name} ({tk}, {mk}): 최근 종가 데이터 없음"

                pcount = int(stats.get("proposal_count") or 0)
                if pcount > 0:
                    extras: list[str] = [f"누적 추천 {pcount}회"]
                    last_rec = stats.get("last_recommended")
                    if last_rec:
                        extras.append(f"최근 {last_rec.isoformat()}")
                    avg_3m = stats.get("avg_3m")
                    if avg_3m is not None:
                        extras.append(f"평균 3M post-return {float(avg_3m):+.1f}%")
                    line += " · " + " / ".join(extras)

                lines.append(line)
    except Exception as e:
        print(
            f"[general_chat_engine._fetch_ticker_snapshot] DB 조회 실패: {e}",
            file=sys.stderr,
        )
        try:
            conn.rollback()
        except Exception:
            pass
        return ""

    if not lines:
        return ""

    header = "## 메시지에 언급된 종목 스냅샷"
    if latest_date_overall:
        header += (
            f" (출처: stock_universe_ohlcv, 기준일 {latest_date_overall.isoformat()} 종가)"
        )
    return "\n" + header + "\n" + "\n".join(lines) + "\n"


def build_user_context(
    conn,
    user_id: Optional[int],
    *,
    user_message: Optional[str] = None,
    watchlist_limit: int = 20,
    recent_proposal_days: int = 7,
    recent_proposal_limit: int = 10,
) -> str:
    """사용자 워치리스트 + 최근 추천 이력 + (옵션) 메시지 언급 종목 스냅샷 → 시스템 프롬프트 주입용 텍스트.

    user_id 가 None 이면 비로그인/익명 — 워치리스트/추천 컨텍스트는 비움.
    user_message 가 주어지면 stock_universe 화이트리스트 기반 종목 추출 + D-1 종가 스냅샷 추가.
    조회 실패해도 빈 문자열 반환 (LLM 호출은 컨텍스트 없이 진행).
    """
    ticker_snapshot_text = ""
    if _TICKER_INJECTION_ENABLED and user_message:
        try:
            extracted = _extract_tickers_from_message(conn, user_message)
            ticker_snapshot_text = _fetch_ticker_snapshot(conn, extracted)
        except Exception as e:
            print(
                f"[general_chat_engine.build_user_context] 티커 주입 실패: {e}",
                file=sys.stderr,
            )
            ticker_snapshot_text = ""

    if user_id is None:
        if ticker_snapshot_text:
            return "\n# 사용자 컨텍스트 (자동 주입)\n" + ticker_snapshot_text
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
    """Claude SDK 비동기 쿼리 (내부용).

    CLI subprocess stderr 를 캡처하여 실패 시 systemd journal 에 덤프.
    SDK 기본은 stderr=None(inherit) 인데 운영기에서 침묵 종료(`exit 1`) 케이스가
    잡히지 않아 진단 불가 → callback 으로 직접 보관.
    """
    full_response = ""
    cli_stderr: list[str] = []

    def _on_stderr(line: str) -> None:
        cli_stderr.append(line)

    try:
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                system_prompt=system,
                max_turns=max_turns,
                stderr=_on_stderr,
                tools=[],
                permission_mode="plan",
                setting_sources=[],
            ),
        ):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        full_response += block.text
    except BaseException as e:
        dump = "\n".join(cli_stderr[-200:]) if cli_stderr else "(stderr empty)"
        print(
            f"[general_chat_engine] Claude SDK 호출 실패: {type(e).__name__}: {e}\n"
            f"--- CLI stderr (마지막 200줄) ---\n{dump}\n"
            f"--- end ---",
            file=sys.stderr,
            flush=True,
        )
        raise
    return full_response


def _format_history(conversation_history: list[dict], window: int = 20) -> str:
    """이전 대화 이력 → 프롬프트 삽입용 텍스트.

    기존 query_general_chat_sync 의 inline 로직 추출 — sync/stream 양쪽이 공유.
    """
    recent = conversation_history[-window:]
    if not recent:
        return ""
    parts = ["\n\n## 이전 대화\n"]
    for msg in recent:
        prefix = "사용자" if msg["role"] == "user" else "어시스턴트"
        parts.append(f"\n**{prefix}:** {msg['content']}\n")
    return "".join(parts)


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
    history_text = _format_history(conversation_history)

    prompt = f"{history_text}\n사용자: {user_message}"
    system = GENERAL_CHAT_SYSTEM_PROMPT.format(user_context=user_context or "")

    return anyio.run(_query_claude_chat, prompt, system, max_turns)


from api.chat_stream_helpers import stream_claude_chat, OnToken, OnError


async def query_general_chat_stream(
    user_context: str,
    conversation_history: list[dict],
    user_message: str,
    *,
    on_token: OnToken,
    on_error: OnError,
    max_turns: int = 1,
) -> str:
    """자유 채팅 streaming 변형. 토큰 단위로 on_token 호출.

    sync 함수(query_general_chat_sync) 는 폴백/테스트용으로 유지.
    """
    history_text = _format_history(conversation_history)
    prompt = f"{history_text}\n사용자: {user_message}"
    system = GENERAL_CHAT_SYSTEM_PROMPT.format(user_context=user_context or "")
    return await stream_claude_chat(
        prompt=prompt, system=system,
        on_token=on_token, on_error=on_error, max_turns=max_turns,
    )
