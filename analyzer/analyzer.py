"""Claude Code SDK 기반 멀티스테이지 분석 파이프라인

Stage 1: 뉴스 → 이슈 분석 + 테마 발굴 (시나리오/매크로 포함)
Stage 2: 핵심 종목 심층분석 (펀더멘털·퀀트·센티먼트)
"""
import json
import re
import asyncio
import time
import anyio
import traceback
from claude_agent_sdk import (
    query, ClaudeAgentOptions,
    AssistantMessage, TextBlock, ResultMessage, SystemMessage,
)
from shared.logger import get_logger, archive_ai_query

from analyzer.prompts import (
    STAGE1_SYSTEM, STAGE1_PROMPT,
    STAGE1A_SYSTEM, STAGE1A_PROMPT,
    STAGE1A1_SYSTEM, STAGE1A1_PROMPT,
    STAGE1A2_SYSTEM, STAGE1A2_PROMPT,
    STAGE1B_SYSTEM, STAGE1B_PROMPT,
    STAGE1B1_SYSTEM, STAGE1B1_PROMPT,
    STAGE1B3_SYSTEM, STAGE1B3_PROMPT,
    STAGE2_SYSTEM, STAGE2_PROMPT,
)
from analyzer.stock_data import fetch_multiple_stocks, fetch_stock_data, format_stock_data_text, fetch_momentum_batch, validate_krx_tickers, validate_us_tickers
from analyzer.screener import screen as screen_universe, candidates_to_prompt_table
from shared.config import AnalyzerConfig, DatabaseConfig, ScreenerConfig
from shared.db import get_recent_recommendations, get_existing_theme_keys


def _detect_proposal_price_anomalies(proposal: dict) -> list[str]:
    """투자 제안의 current_price/market 기준 가격 이상 감지.

    모멘텀 체크 경로에서는 fetch_stock_data의 상세 정보(52주 고저/시총)가 없으므로,
    통화별 penny stock 임계값만 적용한다.
    """
    flags: list[str] = []
    price = proposal.get("current_price")
    if not price or price <= 0:
        return flags

    market = (proposal.get("market") or "").strip().upper()
    # 시장으로 통화 추론
    market_currency = {
        "KRX": "KRW", "KOSPI": "KRW", "KSE": "KRW", "KOSDAQ": "KRW", "KQ": "KRW",
        "NYSE": "USD", "NASDAQ": "USD", "NYSEARCA": "USD", "AMEX": "USD",
        "TSE": "JPY", "JPX": "JPY", "TYO": "JPY",
        "HKEX": "HKD", "HKG": "HKD", "HKSE": "HKD",
        "TWSE": "TWD", "TPE": "TWD",
        "SSE": "CNY", "SZSE": "CNY", "SHA": "CNY", "SHE": "CNY",
        "LSE": "GBP", "LON": "GBP",
        "FSE": "EUR", "FRA": "EUR", "XETRA": "EUR",
    }
    currency = proposal.get("currency") or market_currency.get(market)

    penny_thresholds = {
        "USD": 1.0, "EUR": 1.0, "GBP": 0.5, "CAD": 1.0, "AUD": 1.0,
        "KRW": 100.0, "JPY": 10.0, "HKD": 1.0, "TWD": 10.0, "CNY": 1.0,
    }
    threshold = penny_thresholds.get(currency)
    if threshold is not None and float(price) < threshold:
        flags.append(f"penny_stock(<{threshold}{currency})")

    return flags


def _has_unterminated_string(text: str) -> bool:
    """텍스트가 JSON 문자열 값 내부에서 끝났는지 판정.

    2026-04-22 Stage 1-A 재발: 모델이 `"description": "일` 까지 쓴 뒤 문자열 값 안에
    ```json 마크다운 펜스를 삽입하며 탈출 → regex가 그 안쪽 백틱을 펜스 종결로
    오인하면 `blocks[0]`이 문자열 중간에서 끊긴 상태로 추출됨.
    이 패턴을 sanitize 단에서 감지하여 multi-block 병합을 차단한다.
    """
    in_string = False
    escape = False
    for ch in text:
        if escape:
            escape = False
            continue
        if ch == '\\':
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
    return in_string


def _trim_to_last_complete_array_item(text: str) -> str:
    """배열 내 마지막으로 완전히 닫힌 `}` (또는 `]`) 뒤까지만 보존.

    깊이 기반 스캔으로 "배열 레벨(=top 배열 내부)에서 객체가 닫힌 직후" 위치를 추적.
    미완 객체(부분 theme/issue)를 떨어뜨리기 위한 안전 절단점으로 사용.
    반환값은 새 `,` 또는 `]`가 붙기 전의 위치 — 호출자가 닫기 보정을 이어서 수행.
    """
    in_string = False
    escape = False
    depth = 0
    last_item_close = -1  # 배열 안의 `}`/`]`가 닫힌 직후 위치
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in ('{', '['):
            depth += 1
        elif ch in ('}', ']'):
            depth -= 1
            # 루트 `{`(depth=1) 내부의 배열(`issues`/`themes` depth=2) 안에서 닫힌 객체를 추적
            # depth>=2는 top 객체 바깥, depth==1 복귀는 배열이 닫힌 것 — 그 전 `}`가 마지막 완전 item
            if depth >= 2:
                last_item_close = i + 1
    if last_item_close > 0:
        return text[:last_item_close]
    return text


def _try_fix_truncated_json(json_str: str) -> str | None:
    """잘린 JSON 복구 시도 — 미종료 문자열/배열/객체를 닫아줌.

    전략:
    1. 문자열 중간에서 끊긴 경우 → 마지막 "완전히 닫힌 배열 item" 위치로 절단하여
       부분 객체(theme/issue)를 드롭 → 이후 브래킷 닫기
    2. 구조(중괄호/대괄호)만 열려 있는 경우 → 그대로 닫기
    """
    s = json_str.rstrip()

    # 1단계: 문자열 내부에서 잘렸는지 확인
    if _has_unterminated_string(s):
        # 마지막 완전히 닫힌 배열 item 직후로 절단하여 부분 객체 제거
        trimmed = _trim_to_last_complete_array_item(s)
        if trimmed != s and trimmed.strip():
            s = trimmed.rstrip().rstrip(',')
        else:
            # 절단점 못 찾으면 따옴표만 닫고 진행
            s = s + '"'

    # 마지막 불완전 key-value 쌍 제거 (예: "key": "val 에서 잘린 경우)
    # 이미 따옴표를 닫았으므로, 남은 구조만 닫으면 됨

    # 열린 브래킷 수 계산하여 닫기
    stack = []
    in_str = False
    esc = False
    for ch in s:
        if esc:
            esc = False
            continue
        if ch == '\\' and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in ('{', '['):
            stack.append(ch)
        elif ch == '}' and stack and stack[-1] == '{':
            stack.pop()
        elif ch == ']' and stack and stack[-1] == '[':
            stack.pop()

    # 마지막 trailing comma 제거
    s = s.rstrip()
    if s and s[-1] == ',':
        s = s[:-1]

    # 열린 브래킷 역순으로 닫기
    for bracket in reversed(stack):
        if bracket == '{':
            # 마지막 trailing comma 제거 후 닫기
            s = s.rstrip().rstrip(',')
            s += '}'
        elif bracket == '[':
            s = s.rstrip().rstrip(',')
            s += ']'

    return s


def _escape_control_chars_in_strings(json_str: str) -> str:
    """JSON 문자열 값 내부의 raw 제어문자(\\n, \\r, \\t)를 이스케이프.

    상태머신으로 문자열 내부(`"..."`)만 처리하여 구조 문자는 건드리지 않는다.
    2026-04-22 Stage 1-A 실패 대응: 모델이 값 안에 raw 개행을 삽입하는 경우 복구.
    """
    out = []
    in_string = False
    escape = False
    for ch in json_str:
        if escape:
            out.append(ch)
            escape = False
            continue
        if ch == '\\':
            out.append(ch)
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            out.append(ch)
            continue
        if in_string and ch == '\n':
            out.append('\\n')
        elif in_string and ch == '\r':
            out.append('\\r')
        elif in_string and ch == '\t':
            out.append('\\t')
        else:
            out.append(ch)
    return ''.join(out)


def _sanitize_json_response(text: str) -> str:
    """파싱 전 전처리 — 모델이 망가뜨린 JSON을 복원 가능한 형태로 정리.

    처리 순서:
    1. 여러 개의 ```json 코드블록이 있을 때:
       - 첫 블록이 **미종료 문자열** 상태로 끝나면 → 문자열 값 내부에 펜스가 잘못 삽입된 케이스
         (2026-04-22 재발 패턴) → 첫 블록만 사용하여 절단 복구 위임 (병합하면 구조가 꼬임)
       - 둘 다 정상 종결이면 Part 1/2 식 쪼개짐으로 간주하고 연결
    2. 단일 코드블록이면 블록 내용만 추출
    3. JSON 바깥의 마크다운 헤더(`**[...]**`) 제거
    4. 값 내부의 자기주석 패턴(`*(issue N ...)*`) 제거
    5. 문자열 값 내부의 raw 제어문자를 \\n 등으로 이스케이프
    """
    if not text:
        return text

    # 1~2. 코드블록 처리
    blocks = re.findall(r"```json\s*(.*?)```", text, re.DOTALL)
    if len(blocks) >= 2:
        # 2026-04-22 재발 대응: 첫 블록이 미종료 문자열이면 multi-block이 아니라
        # "값 내부에 펜스 삽입"으로 regex가 오인한 경우. 병합하면 오히려 구조 파괴됨.
        if _has_unterminated_string(blocks[0]):
            log = get_logger("분석")
            log.warning(
                f"멀티블록 감지됐으나 첫 블록이 미종료 문자열 → 첫 블록만 사용 "
                f"(blocks={len(blocks)}, block0={len(blocks[0])}자)"
            )
            text = blocks[0]
        else:
            text = "".join(blocks)  # Part 1 + Part 2 정상 연결
    elif len(blocks) == 1:
        text = blocks[0]
    else:
        # ```json 없는 경우 일반 ``` 블록 시도
        plain_blocks = re.findall(r"```\s*(.*?)```", text, re.DOTALL)
        if plain_blocks:
            text = "".join(plain_blocks) if len(plain_blocks) >= 2 else plain_blocks[0]

    # 3. 마크다운 헤더 제거 (예: **[테마 Part 1 — 1~3번]**)
    text = re.sub(r"\*\*\[[^\]]*\]\*\*", "", text)

    # 4. 자기주석 패턴 제거
    text = re.sub(r"\*\(issue\s+\d+[^)]*\)\*", "", text)
    text = re.sub(r"\*?\((?:이하\s*계속|생략|continued|to\s*be\s*continued)\)\*?", "", text, flags=re.IGNORECASE)

    # 5. 문자열 값 내부 제어문자 이스케이프
    text = _escape_control_chars_in_strings(text)

    return text.strip()


_THEME_REQUIRED_FIELDS = ("theme_key", "theme_name", "description", "time_horizon")
_ISSUE_REQUIRED_FIELDS = ("category", "title", "summary", "impact_short")


def _drop_partial_items(result: dict) -> int:
    """복구된 JSON에서 필수 필드가 빠진 theme/issue 엔트리 제거.

    잘린 JSON 복구 후 마지막 엔트리가 반쪽짜리로 남는 경우(예: description만 있고
    scenarios/macro_impacts 누락)에 다운스트림 검증기가 실패하므로 여기서 정리.

    Returns:
        제거된 항목 수
    """
    dropped = 0
    themes = result.get("themes")
    if isinstance(themes, list):
        kept = [
            t for t in themes
            if isinstance(t, dict)
            and all(t.get(f) for f in _THEME_REQUIRED_FIELDS)
        ]
        dropped += len(themes) - len(kept)
        result["themes"] = kept
    issues = result.get("issues")
    if isinstance(issues, list):
        kept = [
            i for i in issues
            if isinstance(i, dict)
            and all(i.get(f) for f in _ISSUE_REQUIRED_FIELDS)
        ]
        dropped += len(issues) - len(kept)
        result["issues"] = kept
    return dropped


def _parse_json_response(full_response: str) -> dict:
    """Claude 응답에서 JSON 추출 및 파싱 (전처리 + 잘린 JSON 복구 지원).

    반환 딕셔너리에는 진단용 메타 필드가 첨부된다:
    - `_parse_status`: 'success' | 'sanitized_recovered' | 'truncated_recovered' | 'failed' | 'empty'
    - `_parse_error`: JSONDecodeError 메시지 (실패 시)
    - `_truncated`: 복구 여부 boolean
    - `_dropped_partial`: 복구 후 필수 필드 누락으로 제거된 item 수
    """
    log = get_logger("분석")

    if not full_response or not full_response.strip():
        log.error("빈 응답 수신 — JSON 파싱 불가")
        return {
            "error": "빈 응답",
            "_parse_status": "empty",
            "_parse_error": "empty_response",
            "_truncated": False,
        }

    # 기존 경량 추출 경로 (정상 케이스 유지)
    json_str = full_response.strip()
    if "```json" in json_str:
        json_str = json_str.split("```json")[1].split("```")[0].strip()
    elif "```" in json_str:
        json_str = json_str.split("```")[1].split("```")[0].strip()

    try:
        result = json.loads(json_str)
        result["_truncated"] = False
        result["_parse_status"] = "success"
        return result
    except json.JSONDecodeError as e:
        first_error = e
        log.warning(f"JSON 파싱 실패: {e}")

        # 전처리 경로: 쪼개진 코드블록·메타 주석·raw 제어문자 복구
        log.info("JSON 전처리(sanitize) 시도 중...")
        sanitized = _sanitize_json_response(full_response)
        if sanitized and sanitized != json_str:
            try:
                result = json.loads(sanitized)
                dropped = _drop_partial_items(result)
                if dropped:
                    log.warning(f"복구 후 부분 항목 {dropped}건 제거")
                themes_count = len(result.get("themes", []))
                issues_count = len(result.get("issues", []))
                proposals_count = len(result.get("proposals", []))
                log.info(
                    f"JSON 전처리 복구 성공 (이슈 {issues_count}건, 테마 {themes_count}건, 제안 {proposals_count}건)"
                )
                result["_truncated"] = False
                result["_parse_status"] = "sanitized_recovered"
                result["_parse_error"] = str(first_error)
                result["_dropped_partial"] = dropped
                return result
            except json.JSONDecodeError as se:
                log.info(f"전처리 후에도 파싱 실패 — 잘린 JSON 복구로 진행: {se}")
                json_str = sanitized  # 다음 단계에 전처리 결과 전달

        # 기존 잘린 JSON 복구 경로
        log.info("잘린 JSON 복구 시도 중...")
        fixed = _try_fix_truncated_json(json_str)
        if fixed:
            try:
                result = json.loads(fixed)
                dropped = _drop_partial_items(result)
                if dropped:
                    log.warning(f"복구 후 부분 항목 {dropped}건 제거")
                themes_count = len(result.get("themes", []))
                issues_count = len(result.get("issues", []))
                proposals_count = len(result.get("proposals", []))
                log.info(f"JSON 복구 성공 (이슈 {issues_count}건, 테마 {themes_count}건, 제안 {proposals_count}건)")
                result["_truncated"] = True
                result["_parse_status"] = "truncated_recovered"
                result["_parse_error"] = str(first_error)
                result["_dropped_partial"] = dropped
                return result
            except json.JSONDecodeError:
                pass

        log.error(f"JSON 복구 실패 — 원본 응답 앞부분:\n{full_response[:500]}")
        return {
            "error": str(first_error),
            "_parse_status": "failed",
            "_parse_error": str(first_error),
            "_truncated": False,
        }


def _archive_result(
    *, stage: str, target_key: str | None, model: str | None,
    system_prompt: str, user_prompt: str, response: str,
    parsed: dict, elapsed_sec: float = 0.0,
) -> None:
    """파싱 완료 후 ai_query_archive에 저장 — 성공/실패 모두 보존.

    실패·빈 복구 건은 추후 raw response로 수동 재파싱 가능하게 된다.
    """
    parse_status = parsed.get("_parse_status", "success")
    parse_error = parsed.get("_parse_error")

    # 복구된 필드 요약 (진단용)
    recovered: dict = {}
    for k in ("themes", "issues", "proposals"):
        if k in parsed and isinstance(parsed[k], list):
            recovered[k] = len(parsed[k])
    for k in ("factor_scores", "sentiment_score", "recommendation",
              "target_price_low", "target_price_high"):
        if k in parsed and parsed[k] is not None and parsed[k] != "":
            recovered[k] = True

    try:
        archive_ai_query(
            stage=stage,
            target_key=target_key,
            model=model,
            prompt_system=system_prompt,
            prompt_user=user_prompt,
            response_raw=response or "",
            elapsed_sec=elapsed_sec,
            parse_status=parse_status,
            parse_error=parse_error,
            recovered_fields=recovered or None,
        )
    except Exception:
        pass


async def _query_claude(
    prompt: str, system_prompt: str, max_turns: int,
    model: str | None = None,
    max_retries: int = 2,
    timeout_sec: int = 600,
    archive_stage: str | None = None,
    archive_target_key: str | None = None,
) -> str:
    """Claude SDK 쿼리 공통 함수 (재시도 + 타임아웃 + B-1 아카이빙 지원)

    Args:
        model: 사용할 모델 (None이면 기본 모델 사용)
        max_retries: 실패 시 최대 재시도 횟수 (기본 2회)
        timeout_sec: 단일 쿼리 타임아웃 (기본 600초=10분)
        archive_stage: ai_query_archive에 저장할 스테이지 태그 (None이면 아카이브 스킵)
        archive_target_key: 테마명 또는 ticker (디버깅 식별자)
    """
    log = get_logger("SDK")
    last_error = None
    for attempt in range(1, max_retries + 1):
        full_response = ""
        start_time = time.time()
        msg_count = 0
        retry_label = f" (재시도 {attempt}/{max_retries})" if attempt > 1 else ""
        log.info(f"쿼리 시작{retry_label} (max_turns={max_turns}, model={model or 'default'}, timeout={timeout_sec}s)")

        try:
            async def _run_query():
                nonlocal full_response, msg_count
                async for message in query(
                    prompt=prompt,
                    options=ClaudeAgentOptions(
                        system_prompt=system_prompt,
                        max_turns=max_turns,
                        model=model,
                        tools=[],
                        permission_mode="plan",
                        setting_sources=[],
                    ),
                ):
                    elapsed = time.time() - start_time
                    if isinstance(message, AssistantMessage):
                        msg_count += 1
                        chunk_len = sum(len(b.text) for b in message.content if isinstance(b, TextBlock))
                        full_response += "".join(b.text for b in message.content if isinstance(b, TextBlock))
                        log.info(f"응답 수신 #{msg_count} (+{chunk_len:,}자, 누적 {len(full_response):,}자, {elapsed:.0f}초)")
                    elif isinstance(message, ResultMessage):
                        log.info(f"완료 — 턴 {message.num_turns}회, {elapsed:.0f}초 소요")
                    elif isinstance(message, SystemMessage):
                        log.info(f"시스템: {message.subtype}")

            await asyncio.wait_for(_run_query(), timeout=timeout_sec)

            total = time.time() - start_time
            log.info(f"쿼리 종료 (응답 {len(full_response):,}자, 총 {total:.0f}초)")
            # B-1: 쿼리 성공(응답 수신) — 파싱 단계에서 archive가 호출됨
            #      여기서는 attempt/elapsed 메타만 response 에 부착해 상위로 전달
            if archive_stage:
                # raw 응답 자체는 상위 _parse_json_response() 에서 archive됨.
                # 쿼리 단계에서 실패한 경우만 여기서 직접 archive 호출.
                pass
            return full_response

        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            last_error = TimeoutError(f"SDK 쿼리 타임아웃 ({timeout_sec}초 초과, {elapsed:.0f}초 경과)")
            log.error(f"타임아웃 발생 ({elapsed:.0f}초): {timeout_sec}초 초과")
            if full_response:
                log.warning(f"타임아웃 전 부분 응답 {len(full_response):,}자 수신됨 — 부분 응답 반환")
                # B-1: 타임아웃 중 부분 응답도 아카이브 (사후 재파싱 가능)
                if archive_stage:
                    try:
                        archive_ai_query(
                            stage=archive_stage,
                            target_key=archive_target_key,
                            model=model,
                            prompt_system=system_prompt,
                            prompt_user=prompt,
                            response_raw=full_response,
                            elapsed_sec=elapsed,
                            parse_status="timeout_partial",
                            parse_error=f"timeout_after_{timeout_sec}s",
                        )
                    except Exception:
                        pass
                return full_response
            if attempt < max_retries:
                wait = 10 * attempt
                log.info(f"{wait}초 후 재시도...")
                await asyncio.sleep(wait)
            else:
                log.error("최대 재시도 횟수 초과 — 실패 확정")
                # B-1: 재시도 소진 타임아웃도 아카이브
                if archive_stage:
                    try:
                        archive_ai_query(
                            stage=archive_stage,
                            target_key=archive_target_key,
                            model=model,
                            prompt_system=system_prompt,
                            prompt_user=prompt,
                            response_raw="",
                            elapsed_sec=elapsed,
                            parse_status="timeout_exhausted",
                            parse_error=str(last_error),
                        )
                    except Exception:
                        pass

        except Exception as e:
            elapsed = time.time() - start_time
            last_error = e
            log.error(f"오류 발생 ({elapsed:.0f}초): {e}", extra={"detail": traceback.format_exc()})
            if attempt < max_retries:
                wait = 10 * attempt
                log.info(f"{wait}초 후 재시도...")
                await asyncio.sleep(wait)
            else:
                log.error("최대 재시도 횟수 초과 — 실패 확정")
                if archive_stage:
                    try:
                        archive_ai_query(
                            stage=archive_stage,
                            target_key=archive_target_key,
                            model=model,
                            prompt_system=system_prompt,
                            prompt_user=prompt,
                            response_raw=full_response or "",
                            elapsed_sec=elapsed,
                            parse_status="sdk_error",
                            parse_error=f"{type(e).__name__}: {e}",
                        )
                    except Exception:
                        pass

    raise last_error


# ── Stage 1: 이슈 분석 + 테마 발굴 ──────────────────

_RECENT_RECS_MAX_TICKERS = 80  # Top-N 캡 — 누적 증가로 인한 컨텍스트 팽창 방지
_RECENT_RECS_THEME_SUMMARY_MAX = 12  # 이미 다룬 테마 키워드 요약 최대 개수


def _format_recent_recommendations(recent_recs: list[dict]) -> str:
    """최근 추천 이력을 **컴팩트 인라인 포맷**으로 변환 (토큰 절약).

    설계 원칙:
    - Stage 1-B가 테마 개수만큼 반복 호출되므로 블록이 매번 전송됨 → 짧을수록 유리
    - 목적은 "이미 추천된 티커에서 벗어나라"는 신호 → 티커+이름만 있으면 충분
    - 최근 다룬 테마 키워드는 별도 요약 한 줄로 제공 (밸류체인 확장 힌트)
    - NULL 필드 방어 처리 (2026-04-22 Stage 1-B 전체 실패 재발 방지)
    - Top-N 캡으로 누적 팽창 방지 — count DESC 기준 상위 유지
    """
    if not recent_recs:
        return ""

    # 티커별 그룹핑 — NULL 필드는 건너뛰거나 기본값으로 대체
    ticker_map: dict[str, dict] = {}
    theme_counter: dict[str, int] = {}
    for rec in recent_recs:
        tk = rec.get('ticker')
        if not tk:
            continue
        if tk not in ticker_map:
            ticker_map[tk] = {
                'name': rec.get('asset_name') or '',
                'count': 0,
            }
        ticker_map[tk]['count'] += rec.get('count', 0) or 0
        theme_name = rec.get('theme_name')
        if theme_name:
            theme_counter[theme_name] = theme_counter.get(theme_name, 0) + (rec.get('count', 0) or 0)

    if not ticker_map:
        return ""

    # Top-N 캡 — count DESC 정렬 후 상위만 (쿼리는 이미 count DESC이지만 방어적으로 재정렬)
    ranked = sorted(ticker_map.items(), key=lambda kv: (-kv[1]['count'], kv[0]))
    truncated = len(ranked) > _RECENT_RECS_MAX_TICKERS
    ranked = ranked[:_RECENT_RECS_MAX_TICKERS]

    # 컴팩트 인라인 포맷 — 한 줄 당 여러 티커
    ticker_tokens = []
    for tk, info in ranked:
        name = info['name']
        ticker_tokens.append(f"{tk} {name}".strip() if name else tk)
    tickers_line = ", ".join(ticker_tokens)

    # 최근 다룬 테마 키워드 요약 (상위 N개)
    top_themes = sorted(theme_counter.items(), key=lambda kv: -kv[1])[:_RECENT_RECS_THEME_SUMMARY_MAX]
    themes_line = ", ".join(t for t, _ in top_themes) if top_themes else ""

    header = f"## 최근 7일 추천 이력 (총 {len(ticker_map)}개 티커"
    if truncated:
        header += f", 상위 {_RECENT_RECS_MAX_TICKERS}개 표시"
    header += ")"

    parts = [
        "\n---",
        header,
        "**위 티커는 신규 추천에서 제외**하고 동일 밸류체인의 2~3차 수혜주 또는 다른 테마의 종목을 발굴하세요.",
    ]
    if themes_line:
        parts.append(f"- 이미 다룬 테마 키워드: {themes_line}")
    parts.append(f"- 제외 티커: {tickers_line}")
    return "\n".join(parts) + "\n"


def _format_existing_theme_keys(existing_keys: list[dict]) -> str:
    """기존 theme_key 목록을 프롬프트용 텍스트로 포맷팅"""
    if not existing_keys:
        return ""

    lines = [
        "",
        "아래는 이전 분석에서 사용된 theme_key 목록입니다.",
        "동일하거나 유사한 테마가 다시 등장하면 **반드시 동일한 theme_key를 재사용**하세요:",
        "",
    ]
    for k in existing_keys:
        lines.append(f"  - `{k['theme_key']}` ← {k['theme_name']} (최근: {k['last_seen_date']}, {k['appearances']}회)")
    lines.append("")
    return "\n".join(lines)


async def stage1_discover_themes(
    news_text: str, date: str, max_turns: int = 6,
    recent_recs: list[dict] | None = None,
    existing_keys: list[dict] | None = None,
    model: str | None = None,
) -> dict:
    """Stage 1 (통합): 뉴스 기반 이슈 분석 + 테마 발굴 + 투자 제안 — 단일 호출"""
    recent_section = _format_recent_recommendations(recent_recs or [])
    keys_section = _format_existing_theme_keys(existing_keys or [])
    prompt = STAGE1_PROMPT.format(
        news_text=news_text, date=date,
        recent_recommendations_section=recent_section,
        existing_theme_keys_section=keys_section,
    )
    response = await _query_claude(prompt, STAGE1_SYSTEM, max_turns, model=model)
    return _parse_json_response(response)


# ── Stage 1 분할: 1-A(이슈+테마) + 1-B(테마별 제안) ──

def _format_issues_context(issues: list[dict]) -> str:
    """Stage 1-A2에 넘길 이슈 요약 컨텍스트 생성 — 인덱스·카테고리·중요도·핵심 요약만."""
    if not issues:
        return "(이슈 없음)"
    lines = []
    for idx, issue in enumerate(issues):
        cat = issue.get("category", "")
        imp = issue.get("importance", "")
        title = issue.get("title", "").strip()
        summary = (issue.get("summary") or "").strip()
        impact_mid = (issue.get("impact_mid") or "").strip()
        lines.append(f"- **[{idx}] ({cat}, 중요도 {imp})** {title}")
        if summary:
            lines.append(f"  요약: {summary}")
        if impact_mid:
            lines.append(f"  중기 파급: {impact_mid}")
    return "\n".join(lines)


async def stage1a1_analyze_issues(
    news_text: str, date: str, max_turns: int = 6,
    model: str | None = None,
    timeout_sec: int = 600,
    bond_yield_section: str = "",
    market_regime_section: str = "",
) -> dict:
    """Stage 1-A1: 이슈만 분석 (테마 제외) — 출력 8~10KB로 안정화.

    2026-04-22 Stage 1-A 재발 대응: 단일 쿼리가 23KB까지 늘며 self-interruption 발생.
    이슈·테마를 분리하여 각 쿼리를 10~12KB로 축소.
    """
    prompt = STAGE1A1_PROMPT.format(
        news_text=news_text, date=date,
        bond_yield_section=bond_yield_section,
        market_regime_section=market_regime_section,
    )
    start = time.time()
    response = await _query_claude(
        prompt, STAGE1A1_SYSTEM, max_turns, model=model, timeout_sec=timeout_sec,
        archive_stage="stage1a1", archive_target_key=date,
    )
    parsed = _parse_json_response(response)
    _archive_result(
        stage="stage1a1", target_key=date, model=model,
        system_prompt=STAGE1A1_SYSTEM, user_prompt=prompt,
        response=response, parsed=parsed, elapsed_sec=time.time() - start,
    )
    return parsed


async def stage1a2_build_themes(
    news_text: str, date: str, issues: list[dict],
    max_turns: int = 6,
    existing_keys: list[dict] | None = None,
    model: str | None = None,
    timeout_sec: int = 600,
    bond_yield_section: str = "",
    market_regime_section: str = "",
    sector_rotation_section: str = "",
) -> dict:
    """Stage 1-A2: 이슈 목록을 받아 투자 테마 4~6개 발굴."""
    keys_section = _format_existing_theme_keys(existing_keys or [])
    issues_context = _format_issues_context(issues)
    prompt = STAGE1A2_PROMPT.format(
        news_text=news_text, date=date,
        issues_context=issues_context,
        existing_theme_keys_section=keys_section,
        bond_yield_section=bond_yield_section,
        market_regime_section=market_regime_section,
        sector_rotation_section=sector_rotation_section,
    )
    start = time.time()
    response = await _query_claude(
        prompt, STAGE1A2_SYSTEM, max_turns, model=model, timeout_sec=timeout_sec,
        archive_stage="stage1a2", archive_target_key=date,
    )
    parsed = _parse_json_response(response)
    _archive_result(
        stage="stage1a2", target_key=date, model=model,
        system_prompt=STAGE1A2_SYSTEM, user_prompt=prompt,
        response=response, parsed=parsed, elapsed_sec=time.time() - start,
    )
    return parsed


async def stage1a_discover_themes(
    news_text: str, date: str, max_turns: int = 6,
    existing_keys: list[dict] | None = None,
    model: str | None = None,
    timeout_sec: int = 600,
    bond_yield_section: str = "",
    market_regime_section: str = "",
    sector_rotation_section: str = "",
) -> dict:
    """Stage 1-A: 이슈 분석 + 테마 발굴 오케스트레이터.

    내부적으로 Stage 1-A1(이슈) → Stage 1-A2(테마)를 순차 호출하여 출력 크기를
    제어한다. 반환 스키마는 기존 단일 호출 버전과 동일(`{issues, themes, ...}`).
    """
    log = get_logger("파이프라인")

    # ── Stage 1-A1: 이슈 분석 ──
    log.info(f"[Stage 1-A1] 이슈 분석 시작 (모델: {model or 'default'}, 타임아웃: {timeout_sec}초)")
    issues_result = await stage1a1_analyze_issues(
        news_text, date, max_turns,
        model=model, timeout_sec=timeout_sec,
        bond_yield_section=bond_yield_section,
        market_regime_section=market_regime_section,
    )

    if issues_result.get("error"):
        log.error(f"[Stage 1-A1] 이슈 분석 실패 — {issues_result['error']}")
        return issues_result

    issues = issues_result.get("issues", [])
    log.info(f"[Stage 1-A1] 이슈 {len(issues)}건 분석 완료")

    if not issues:
        log.error("[Stage 1-A1] 이슈 0건 — Stage 1-A2 스킵")
        return {
            "error": "이슈 분석 결과 없음",
            "_parse_status": "empty",
            "_parse_error": "stage1a1_no_issues",
            "_truncated": False,
            **issues_result,
        }

    # ── Stage 1-A2: 테마 발굴 ──
    log.info(f"[Stage 1-A2] 테마 발굴 시작 (이슈 {len(issues)}건 컨텍스트 주입)")
    themes_result = await stage1a2_build_themes(
        news_text, date, issues, max_turns,
        existing_keys=existing_keys,
        model=model, timeout_sec=timeout_sec,
        bond_yield_section=bond_yield_section,
        market_regime_section=market_regime_section,
        sector_rotation_section=sector_rotation_section,
    )

    if themes_result.get("error"):
        log.warning(f"[Stage 1-A2] 테마 발굴 실패 — 이슈만으로 결과 반환: {themes_result['error']}")
        # 이슈는 확보됐으므로 테마 없이라도 진행
        themes_result = {"themes": []}

    themes = themes_result.get("themes", [])
    log.info(f"[Stage 1-A2] 테마 {len(themes)}건 발굴 완료")

    # ── 결과 병합 (기존 호출자 호환) ──
    merged = {
        "analysis_date": issues_result.get("analysis_date", date),
        "market_summary": issues_result.get("market_summary", ""),
        "risk_temperature": issues_result.get("risk_temperature", "medium"),
        "data_sources": issues_result.get("data_sources", ["RSS뉴스"]),
        "issues": issues,
        "themes": themes,
        "_parse_status": "success",
        "_truncated": bool(
            issues_result.get("_truncated") or themes_result.get("_truncated")
        ),
        "_stage1a_split": True,  # 분할 실행 여부 플래그 (진단용)
    }
    # 복구 메타 유지
    for key in ("_parse_error", "_dropped_partial"):
        if issues_result.get(key) or themes_result.get(key):
            merged[key] = issues_result.get(key) or themes_result.get(key)
    return merged


# ── 레거시 단일 호출 경로 (하위호환용, 신규 코드는 위 분할 버전 사용 권장) ──
async def stage1a_discover_themes_legacy_single_call(
    news_text: str, date: str, max_turns: int = 6,
    existing_keys: list[dict] | None = None,
    model: str | None = None,
    timeout_sec: int = 600,
    bond_yield_section: str = "",
    market_regime_section: str = "",
) -> dict:
    """Stage 1-A 단일 호출 버전 — 2026-04-22 이전 경로 (디버깅·비교용으로만 보존)."""
    keys_section = _format_existing_theme_keys(existing_keys or [])
    prompt = STAGE1A_PROMPT.format(
        news_text=news_text, date=date,
        existing_theme_keys_section=keys_section,
        bond_yield_section=bond_yield_section,
        market_regime_section=market_regime_section,
    )
    start = time.time()
    response = await _query_claude(
        prompt, STAGE1A_SYSTEM, max_turns, model=model, timeout_sec=timeout_sec,
        archive_stage="stage1a", archive_target_key=date,
    )
    parsed = _parse_json_response(response)
    _archive_result(
        stage="stage1a", target_key=date, model=model,
        system_prompt=STAGE1A_SYSTEM, user_prompt=prompt,
        response=response, parsed=parsed, elapsed_sec=time.time() - start,
    )
    return parsed


async def stage1b_generate_proposals(
    theme: dict, date: str, max_turns: int = 6,
    recent_recs: list[dict] | None = None,
    model: str | None = None,
    timeout_sec: int = 600,
) -> list[dict]:
    """Stage 1-B: 개별 테마에 대한 투자 제안 생성 (10~15건)"""
    recent_section = _format_recent_recommendations(recent_recs or [])
    theme_name = theme.get("theme_name", "")
    prompt = STAGE1B_PROMPT.format(
        date=date,
        theme_name=theme_name,
        theme_description=theme.get("description", ""),
        theme_type=theme.get("theme_type", ""),
        time_horizon=theme.get("time_horizon", ""),
        confidence_score=theme.get("confidence_score", ""),
        recent_recommendations_section=recent_section,
    )
    start = time.time()
    response = await _query_claude(
        prompt, STAGE1B_SYSTEM, max_turns, model=model, timeout_sec=timeout_sec,
        archive_stage="stage1b", archive_target_key=theme_name,
    )
    result = _parse_json_response(response)
    _archive_result(
        stage="stage1b", target_key=theme_name, model=model,
        system_prompt=STAGE1B_SYSTEM, user_prompt=prompt,
        response=response, parsed=result, elapsed_sec=time.time() - start,
    )
    return result.get("proposals", [])


# ── Stage 1-B (Universe-First): 1-B1 스펙 생성 + 1-B2 스크리너 + 1-B3 배치 분석 ──

async def stage1b1_generate_spec(
    theme: dict, date: str, regime: str = "neutral",
    recent_recs: list[dict] | None = None,
    max_turns: int = 1,
    model: str | None = None,
    timeout_sec: int = 300,
) -> dict:
    """Stage 1-B1: 테마 → 투자 스펙(JSON, ticker 없음). LLM hallucination 차단의 시작점."""
    recent_section = _format_recent_recommendations(recent_recs or [])
    theme_name = theme.get("theme_name", "")
    theme_key = theme.get("theme_key") or theme_name
    prompt = STAGE1B1_PROMPT.format(
        date=date,
        theme_name=theme_name,
        theme_key=theme_key,
        theme_description=theme.get("description", ""),
        theme_type=theme.get("theme_type", ""),
        time_horizon=theme.get("time_horizon", ""),
        confidence_score=theme.get("confidence_score", ""),
        regime=regime,
        recent_recommendations_section=recent_section,
    )
    start = time.time()
    response = await _query_claude(
        prompt, STAGE1B1_SYSTEM, max_turns, model=model, timeout_sec=timeout_sec,
        archive_stage="stage1b1", archive_target_key=theme_key,
    )
    spec = _parse_json_response(response)
    _archive_result(
        stage="stage1b1", target_key=theme_key, model=model,
        system_prompt=STAGE1B1_SYSTEM, user_prompt=prompt,
        response=response, parsed=spec, elapsed_sec=time.time() - start,
    )
    # theme_key 누락 시 보강 (스크리너 로깅용)
    if not spec.get("theme_key"):
        spec["theme_key"] = theme_key
    return spec


async def stage1b3_analyze_candidates(
    theme: dict, spec: dict, candidates: list[dict], date: str,
    max_turns: int = 1,
    model: str | None = None,
    timeout_sec: int = 600,
) -> list[dict]:
    """Stage 1-B3: 후보 N개에 대해 한 번의 호출로 rationale/risk/conviction 배치 생성.

    화이트리스트 검증: 출력의 ticker가 입력 candidates에 없으면 자동 제외 (hallucination 차단).
    """
    if not candidates:
        return []

    theme_name = theme.get("theme_name", "")
    theme_key = spec.get("theme_key") or theme_name
    candidates_table = candidates_to_prompt_table(candidates, max_rows=30)
    prompt = STAGE1B3_PROMPT.format(
        date=date,
        theme_name=theme_name,
        theme_key=theme_key,
        thesis=spec.get("thesis", ""),
        value_chain_tier=", ".join(spec.get("value_chain_tier") or []),
        sector_norm=", ".join(spec.get("sector_norm") or []),
        catalyst_window=spec.get("expected_catalyst_window_months", "?"),
        candidates_count=len(candidates),
        candidates_table=candidates_table,
    )
    start = time.time()
    response = await _query_claude(
        prompt, STAGE1B3_SYSTEM, max_turns, model=model, timeout_sec=timeout_sec,
        archive_stage="stage1b3", archive_target_key=theme_key,
    )
    parsed = _parse_json_response(response)
    _archive_result(
        stage="stage1b3", target_key=theme_key, model=model,
        system_prompt=STAGE1B3_SYSTEM, user_prompt=prompt,
        response=response, parsed=parsed, elapsed_sec=time.time() - start,
    )

    # 화이트리스트 검증 — 입력 candidates의 ticker만 허용
    allowed_tickers = {c["ticker"] for c in candidates}
    proposals = parsed.get("proposals", []) if isinstance(parsed, dict) else []
    accepted: list[dict] = []
    rejected: list[str] = []
    candidate_index = {c["ticker"]: c for c in candidates}

    for p in proposals:
        tk = (p.get("ticker") or "").strip()
        if tk not in allowed_tickers:
            rejected.append(tk or "(empty)")
            continue
        # 후보 표의 실측 데이터로 보강 (AI가 잘못 적었을 수 있는 필드 교정)
        cand = candidate_index[tk]
        p["asset_name"] = cand.get("asset_name") or p.get("asset_name") or tk
        p["market"] = cand.get("market") or p.get("market")
        if not p.get("sector"):
            p["sector"] = cand.get("sector_norm")
        # 스펙/매칭 근거를 audit trail에 기록 (Phase 3에서 DB 저장)
        p["spec_snapshot"] = spec
        p["screener_match_reason"] = cand.get("screener_match_reason")
        accepted.append(p)

    log = get_logger("stage1b3")
    if rejected:
        log.warning(
            f"[stage1b3:{theme_key}] 화이트리스트 위반 ticker {len(rejected)}건 자동 제외: {rejected[:5]}"
        )
    log.info(f"[stage1b3:{theme_key}] {len(accepted)}/{len(proposals)} proposals accepted")
    return accepted


def stage1b2_screen_candidates(
    spec: dict, db_cfg: DatabaseConfig,
    screener_cfg: ScreenerConfig | None = None,
) -> list[dict]:
    """Stage 1-B2: 결정적 스크리너 호출 — analyzer.screener.screen() wrapper.

    AI 호출 없음. 스펙 → 후보 리스트 (universe에서 추출).
    """
    result = screen_universe(db_cfg, spec, cfg=screener_cfg)
    return result.candidates


# ── B3 폴백: AI 의사결정 실패 시 스크리너 후보를 보수적 watch 제안으로 변환 ──

_FALLBACK_TOP_N = 5  # 폴백 시 노출할 후보 상한
_FALLBACK_TARGET_ALLOCATION = 1.0  # 보수적 비중 (총합 5% 내외)

_MARKET_CURRENCY_MAP = {
    "KOSPI": "KRW", "KOSDAQ": "KRW", "KONEX": "KRW",
    "NYSE": "USD", "NASDAQ": "USD", "AMEX": "USD",
    "TSE": "JPY", "JPX": "JPY",
    "HKEX": "HKD", "TWSE": "TWD",
    "SSE": "CNY", "SZSE": "CNY",
    "LSE": "GBP", "FSE": "EUR", "XETRA": "EUR",
}


def _screener_candidates_to_fallback_proposals(
    candidates: list[dict],
    theme: dict,
    spec: dict,
    *,
    top_n: int = _FALLBACK_TOP_N,
    reason: str = "stage1b3_failed",
) -> list[dict]:
    """B3 실패 시 스크리너 후보 상위 N개를 watch 제안으로 자동 변환.

    AI 가 빈 결과를 내거나 예외를 일으킨 테마를 사용자에게 빈 상태로 보여주는 대신,
    화이트리스트 후보를 `conviction=low`, `discovery_type=screener_fallback` 태그로
    보수적 watch 추천으로 노출. 후처리·필터링·UI 배지로 구분 가능하다.

    근거 텍스트는 스크리너 매칭 키워드와 테마 가설을 그대로 인용 — AI 추정 없음.
    """
    if not candidates:
        return []

    theme_name = theme.get("theme_name", "")
    theme_key = spec.get("theme_key") or theme.get("theme_key") or theme_name
    thesis = (spec.get("thesis") or theme.get("description") or "").strip()
    catalyst_window = spec.get("expected_catalyst_window_months", "?")

    proposals: list[dict] = []
    for cand in candidates[:top_n]:
        ticker = (cand.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        market = (cand.get("market") or "").strip().upper()
        sector = cand.get("sector_norm")
        match_reason = cand.get("screener_match_reason") or "sector/cap_only"

        rationale = (
            f"[자동 폴백] AI 의사결정 단계 실패({reason})로 스크리너 화이트리스트 후보 자동 노출. "
            f"테마 '{theme_name}' 가설: {thesis[:160]} "
            f"매칭 근거: {match_reason}. 카탈리스트 창: {catalyst_window}개월. "
            "수동 검증 후 진입 권장."
        )

        proposals.append({
            "asset_type": "stock",
            "asset_name": cand.get("asset_name") or ticker,
            "ticker": ticker,
            "market": market,
            "currency": _MARKET_CURRENCY_MAP.get(market),
            "action": "watch",
            "conviction": "low",
            "discovery_type": "screener_fallback",
            "price_momentum_check": "unknown",
            "current_price": None,
            "target_price_low": None,
            "target_price_high": None,
            "upside_pct": None,
            "target_allocation": _FALLBACK_TARGET_ALLOCATION,
            "rationale": rationale,
            "risk_factors": "AI 심층 검증 미완 — 폴백 후보. 진입 전 수동 펀더/뉴스 점검 필수.",
            "sector": sector,
            "vendor_tier": None,
            "supply_chain_position": None,
            "spec_snapshot": spec,
            "screener_match_reason": match_reason,
            "is_fallback": True,
            "fallback_reason": reason,
        })

    return proposals


async def stage1b_universe_first(
    theme: dict, db_cfg: DatabaseConfig, date: str,
    cfg: AnalyzerConfig, screener_cfg: ScreenerConfig,
    regime: str = "neutral",
    recent_recs: list[dict] | None = None,
    timeout_sec: int = 600,
) -> list[dict]:
    """Universe-First Stage 1-B 통합: 1-B1 → 1-B2 → 1-B3 → 검증된 proposals.

    1-B1/1-B2 실패 시 빈 리스트. 1-B3 가 빈 결과 또는 예외 시 화이트리스트 후보를
    `screener_fallback` 폴백 제안으로 변환하여 비대칭 결과(테마는 있는데 제안 0건)
    방지. 폴백 발동 여부는 ScreenerConfig.b3_fallback_enabled 로 제어.
    """
    log = get_logger("stage1b-universe")
    theme_name = theme.get("theme_name", "")
    theme_key = theme.get("theme_key") or theme_name

    # 1-B1: 스펙 생성
    try:
        spec = await stage1b1_generate_spec(
            theme, date, regime=regime, recent_recs=recent_recs,
            max_turns=cfg.max_turns, model=cfg.model_analysis, timeout_sec=timeout_sec,
        )
    except Exception as e:
        log.error(f"[1-B1:{theme_key}] 스펙 생성 실패: {e}")
        return []

    if not isinstance(spec, dict) or not (spec.get("required_keywords") or spec.get("sector_norm")):
        log.warning(f"[1-B1:{theme_key}] 스펙이 비어 있거나 필수 필드 누락 — 건너뜀")
        return []

    # 1-B2: 결정적 스크리너 (Python only, AI 호출 없음)
    try:
        candidates = stage1b2_screen_candidates(spec, db_cfg, screener_cfg=screener_cfg)
    except Exception as e:
        log.error(f"[1-B2:{theme_key}] 스크리너 실행 실패: {e}")
        return []

    if not candidates:
        log.warning(f"[1-B2:{theme_key}] 스크리너 매칭 0건 — 테마 스킵")
        return []

    # 상위 N개로 제한 (AI 입력 비용 절감)
    candidates = candidates[: screener_cfg.stage1b3_top_n]

    fallback_enabled = getattr(screener_cfg, "b3_fallback_enabled", True)
    fallback_top_n = getattr(screener_cfg, "b3_fallback_top_n", _FALLBACK_TOP_N)

    # 1-B3: 배치 분석 (AI 1회 호출)
    try:
        proposals = await stage1b3_analyze_candidates(
            theme, spec, candidates, date,
            max_turns=cfg.max_turns, model=cfg.model_analysis, timeout_sec=timeout_sec,
        )
    except Exception as e:
        log.error(f"[1-B3:{theme_key}] 배치 분석 실패: {e}")
        if fallback_enabled:
            fallback = _screener_candidates_to_fallback_proposals(
                candidates, theme, spec,
                top_n=fallback_top_n, reason="stage1b3_exception",
            )
            log.warning(
                f"[1-B3:{theme_key}] 폴백 발동 — 스크리너 후보 {len(fallback)}건을 "
                f"screener_fallback watch 로 노출"
            )
            return fallback
        return []

    if not proposals and fallback_enabled:
        fallback = _screener_candidates_to_fallback_proposals(
            candidates, theme, spec,
            top_n=fallback_top_n, reason="stage1b3_empty",
        )
        log.warning(
            f"[1-B3:{theme_key}] AI 응답 0건 — 폴백으로 스크리너 후보 {len(fallback)}건 노출"
        )
        return fallback

    return proposals


# ── Stage 2: 핵심 종목 심층분석 ──────────────────────

async def stage2_analyze_stock(
    ticker: str, asset_name: str, market: str,
    theme_context: str, date: str, max_turns: int = 6,
    stock_data_text: str = "",
    quant_factors_text: str = "",
    fundamentals_text: str = "",
    investor_data_text: str = "",
    short_selling_text: str = "",
    model: str | None = None,
    timeout_sec: int = 600,
) -> dict:
    """Stage 2: 개별 종목 심층분석 (펀더멘털·산업·모멘텀·퀀트·리스크)"""
    stock_data_section = ""
    if stock_data_text:
        stock_data_section = f"\n\n## 실시간 시장 데이터 (조회 시점: {date})\n\n{stock_data_text}\n"

    quant_factors_section = ""
    if quant_factors_text:
        quant_factors_section = (
            "\n\n## 정량 팩터 스냅샷 (DB 산출 실측값 — 그대로 인용하세요)\n\n"
            f"{quant_factors_text}\n"
        )

    fundamentals_section = (
        f"\n\n## 펀더멘털 시계열 (12M PIT — DB 산출, 그대로 인용)\n\n{fundamentals_text}\n"
        if fundamentals_text else ""
    )

    investor_section = f"\n\n{investor_data_text}\n" if investor_data_text else ""
    short_section = f"\n\n{short_selling_text}\n" if short_selling_text else ""

    prompt = STAGE2_PROMPT.format(
        ticker=ticker, asset_name=asset_name,
        market=market, theme_context=theme_context, date=date,
        stock_data_section=stock_data_section,
        quant_factors_section=quant_factors_section,
        fundamentals_section=fundamentals_section,
        investor_data_section=investor_section,
        short_selling_section=short_section,
    )
    start = time.time()
    target_label = f"{ticker}:{asset_name}"
    response = await _query_claude(
        prompt, STAGE2_SYSTEM, max_turns, model=model, timeout_sec=timeout_sec,
        archive_stage="stage2", archive_target_key=target_label,
    )
    parsed = _parse_json_response(response)
    _archive_result(
        stage="stage2", target_key=target_label, model=model,
        system_prompt=STAGE2_SYSTEM, user_prompt=prompt,
        response=response, parsed=parsed, elapsed_sec=time.time() - start,
    )
    return parsed


# ── 통합 파이프라인 ──────────────────────────────────

async def run_pipeline(
    news_text: str, date: str, cfg: AnalyzerConfig,
    db_cfg: DatabaseConfig | None = None,
    checkpoint=None,
) -> dict:
    """멀티스테이지 분석 파이프라인 실행

    Stage 1-A: 뉴스 → 이슈/테마/시나리오/매크로 (제안 제외)
    Stage 1-B: 테마별 투자 제안 생성 (병렬, 동시 2개 제한)
    Stage 2: 상위 테마의 핵심 종목 심층분석 (병렬, 동시 2개 제한)

    Args:
        checkpoint: CheckpointManager 인스턴스 (None이면 체크포인트 미사용)
    """
    log = get_logger("파이프라인")
    # SDK 동시 호출 수 제한 — 과도한 병렬 실행 시 CLI 프로세스 충돌 방지
    # A-6: 환경변수(SDK_CONCURRENCY)로 조정 가능. 기본 2.
    sdk_concurrency = max(1, int(getattr(cfg, "sdk_concurrency", 2)))
    _sdk_semaphore = asyncio.Semaphore(sdk_concurrency)
    log.info(f"[파이프라인] SDK 동시 실행 제한: {sdk_concurrency}건")

    # ── KRX 확장 데이터: 국채 금리 조회 (Stage 1-A 프롬프트용) ──
    bond_yield_text = ""
    try:
        from analyzer.krx_data import fetch_korea_bond_yields, format_bond_yields_text
        bond_data = fetch_korea_bond_yields()
        if bond_data:
            bond_yield_text = "\n" + format_bond_yields_text(bond_data) + "\n"
    except Exception as e:
        log.warning(f"국채 금리 조회 실패 (무시): {e}")

    # ── 시장 레짐 스냅샷 (로드맵 B2) ──
    market_regime_snap: dict = {}
    market_regime_text = ""
    if db_cfg is not None:
        try:
            from analyzer.regime import compute_regime, format_regime_text, infer_positioning_hint
            market_regime_snap = compute_regime(db_cfg)
            if market_regime_snap:
                body = format_regime_text(market_regime_snap)
                hint = infer_positioning_hint(market_regime_snap)
                header = "\n\n## 시장 레짐 스냅샷 (DB 산출 — 국면 지표)"
                if hint:
                    header += f"\n**종합 국면:** {hint}\n"
                market_regime_text = f"{header}\n{body}\n"
        except Exception as e:
            log.warning(f"[regime] 스냅샷 계산 실패 (무시): {e}")

    # ── 섹터 로테이션 스냅샷 (Tier 1 인사이트) ──
    sector_rotation_snap: dict = {}
    sector_rotation_text = ""
    if db_cfg is not None:
        try:
            from analyzer.sector_rotation import (
                compute_sector_rotation, format_sector_rotation_text, infer_rotation_hint,
            )
            sector_rotation_snap = compute_sector_rotation(db_cfg)
            if sector_rotation_snap:
                body = format_sector_rotation_text(sector_rotation_snap)
                hint = infer_rotation_hint(sector_rotation_snap)
                header = "\n\n## 섹터 로테이션 스냅샷 (DB 산출 — sector_norm cross-section)"
                if hint:
                    header += f"\n**회전 흐름:** {hint}\n"
                sector_rotation_text = f"{header}\n{body}\n"
        except Exception as e:
            log.warning(f"[sector_rotation] 스냅샷 계산 실패 (무시): {e}")

    # ── 최근 추천 이력 조회 (중복 방지용) ──
    recent_recs = []
    existing_keys = []
    if db_cfg:
        try:
            recent_recs = get_recent_recommendations(db_cfg, days=7)
            if recent_recs:
                log.info(f"최근 7일 추천 이력 {len(recent_recs)}건 로드 — 중복 방지 적용")
        except Exception as e:
            log.warning(f"추천 이력 조회 실패 (무시): {e}")
        try:
            existing_keys = get_existing_theme_keys(db_cfg)
            if existing_keys:
                log.info(f"기존 theme_key {len(existing_keys)}건 로드 — AI 키 재사용 유도")
        except Exception as e:
            log.warning(f"theme_key 조회 실패 (무시): {e}")

    # ── Stage 1-A: 이슈 분석 + 테마 발굴 (체크포인트 지원) ──
    timeout = cfg.query_timeout
    if checkpoint and checkpoint.has("stage1a"):
        result = checkpoint.load("stage1a")
        log.info(f"[Stage 1-A] 체크포인트에서 복원 — 이슈 {len(result.get('issues', []))}건, 테마 {len(result.get('themes', []))}건")
    else:
        log.info(f"[Stage 1-A] 이슈 분석 + 테마 발굴 중... (모델: {cfg.model_analysis}, 타임아웃: {timeout}초)")
        result = await stage1a_discover_themes(
            news_text, date, cfg.max_turns,
            existing_keys=existing_keys,
            model=cfg.model_analysis,
            timeout_sec=timeout,
            bond_yield_section=bond_yield_text,
            market_regime_section=market_regime_text,
            sector_rotation_section=sector_rotation_text,
        )

        if result.get("error"):
            log.error(f"[Stage 1-A] 실패 — {result['error']}")
            return result

        # 잘린 응답으로 테마가 너무 적으면 1회 재시도
        if result.get("_truncated") and len(result.get("themes", [])) < 3:
            log.warning(f"[Stage 1-A] 잘린 응답으로 테마 {len(result.get('themes', []))}건만 복구됨 — 재시도...")
            await asyncio.sleep(5)
            retry_result = await stage1a_discover_themes(
                news_text, date, cfg.max_turns,
                existing_keys=existing_keys,
                model=cfg.model_analysis,
                timeout_sec=timeout,
                bond_yield_section=bond_yield_text,
                market_regime_section=market_regime_text,
                sector_rotation_section=sector_rotation_text,
            )
            if not retry_result.get("error") and len(retry_result.get("themes", [])) > len(result.get("themes", [])):
                result = retry_result
                log.info(f"[Stage 1-A] 재시도 성공 — 테마 {len(result.get('themes', []))}건 복구")
            else:
                log.warning("[Stage 1-A] 재시도 결과 개선 없음 — 기존 결과 사용")

        if checkpoint:
            checkpoint.save("stage1a", result)

    themes = result.get("themes", [])
    issues = result.get("issues", [])
    log.info(f"[Stage 1-A] 완료 — 이슈 {len(issues)}건, 테마 {len(themes)}건")

    # B2: 레짐 스냅샷을 결과 dict에 첨부 → save_analysis가 analysis_sessions.market_regime에 저장
    if market_regime_snap:
        result["market_regime"] = market_regime_snap

    # 섹터 로테이션 스냅샷 — diagnostics. analysis_sessions.market_regime JSONB 안에 nested 로 보존.
    if sector_rotation_snap:
        if isinstance(result.get("market_regime"), dict):
            result["market_regime"]["sector_rotation"] = sector_rotation_snap
        else:
            result["sector_rotation"] = sector_rotation_snap

    # ── Stage 1-B: 테마별 투자 제안 생성 (체크포인트 지원) ──
    if checkpoint and checkpoint.has("stage1b"):
        stage1b_data = checkpoint.load("stage1b")
        # 체크포인트의 proposals를 themes에 병합
        saved_proposals = stage1b_data.get("theme_proposals", {})
        for theme in themes:
            tname = theme.get("theme_name", "")
            if tname in saved_proposals:
                theme["proposals"] = saved_proposals[tname]
        total_proposals = sum(len(t.get("proposals", [])) for t in themes)
        log.info(f"[Stage 1-B] 체크포인트에서 복원 — 총 {total_proposals}건 제안")
    else:
        # Phase 2: ENABLE_UNIVERSE_FIRST_B 토글에 따라 듀얼 모드 동작
        from shared.config import ScreenerConfig
        screener_cfg = ScreenerConfig()
        universe_first = screener_cfg.enable_universe_first_b
        mode_label = "Universe-First (1-B1+1-B2+1-B3)" if universe_first else "Legacy (LLM 자유 생성)"
        log.info(
            f"[Stage 1-B] 테마별 투자 제안 생성 시작 — {len(themes)}개 테마 (병렬 실행, 모드: {mode_label})"
        )
        # 시장 레짐: Phase 5에서 동적 산정. 현재는 'neutral' 고정.
        regime = "neutral"

        async def _generate_proposals_for_theme(i: int, theme: dict) -> None:
            theme_name = theme.get("theme_name", f"테마{i+1}")
            # A-6: 세마포어 진입 전·후 로그 분리 — 대기/실행 구분 명확화
            log.info(f"[Stage 1-B] ({i+1}/{len(themes)}) '{theme_name}' 대기 (동시 {sdk_concurrency}건 제한)")
            async with _sdk_semaphore:
                log.info(f"[Stage 1-B] ({i+1}/{len(themes)}) '{theme_name}' 제안 생성 시작")
                try:
                    if universe_first:
                        if db_cfg is None:
                            raise RuntimeError("Universe-First 모드는 db_cfg가 필요합니다.")
                        proposals = await stage1b_universe_first(
                            theme, db_cfg, date, cfg, screener_cfg,
                            regime=regime, recent_recs=recent_recs,
                            timeout_sec=timeout,
                        )
                    else:
                        proposals = await stage1b_generate_proposals(
                            theme, date, cfg.max_turns, recent_recs,
                            model=cfg.model_analysis,
                            timeout_sec=timeout,
                        )
                    theme["proposals"] = proposals
                    log.info(f"[Stage 1-B] ({i+1}/{len(themes)}) '{theme_name}' — {len(proposals)}건 제안 완료")
                except Exception as e:
                    log.error(f"[Stage 1-B] ({i+1}/{len(themes)}) '{theme_name}' 제안 생성 실패: {e}",
                              extra={"detail": traceback.format_exc()})
                    theme["proposals"] = []

        await asyncio.gather(*[
            _generate_proposals_for_theme(i, theme)
            for i, theme in enumerate(themes)
        ])

        total_proposals = sum(len(t.get("proposals", [])) for t in themes)
        log.info(f"[Stage 1-B] 완료 — 총 {total_proposals}건 제안 생성")

        if checkpoint:
            theme_proposals = {
                t.get("theme_name", ""): t.get("proposals", [])
                for t in themes
            }
            checkpoint.save("stage1b", {"theme_proposals": theme_proposals})

    # ── KRX 티커 검증/교정 (Stage 1-B 이후, 모멘텀 체크 전) ──
    all_stock_proposals = [
        p for theme in themes
        for p in theme.get("proposals", [])
        if p.get("ticker") and p.get("asset_type") == "stock"
    ]
    if all_stock_proposals:
        try:
            vresult = validate_krx_tickers(all_stock_proposals)
            # US 화이트리스트 검증 (Tier 2) — stock_universe DB 기반
            try:
                us_vresult = validate_us_tickers(all_stock_proposals, db_cfg=db_cfg)
            except Exception as e:
                log.warning(f"[티커 검증] US 검증 실패 (무시): {e}")
                us_vresult = {"corrected": 0, "invalid": 0, "details": []}

            # 합치기 — KRX corrected + US invalid 병합 (incident_report 가 함께 사용)
            vresult = {
                "corrected": vresult.get("corrected", 0) + us_vresult.get("corrected", 0),
                "invalid": vresult.get("invalid", 0) + us_vresult.get("invalid", 0),
                "details": list(vresult.get("details", [])) + list(us_vresult.get("details", [])),
            }
            # B-3: 사건 보고서 집계용으로 결과 보존
            result["_ticker_validation"] = vresult
            if vresult["corrected"]:
                log.info(f"[티커 검증] 티커 {vresult['corrected']}건 교정:")
                for d in vresult["details"]:
                    if "미등록" not in d:
                        log.info(f"  → {d}")
            if vresult["invalid"]:
                # A-3: 미등록 종목은 이름·티커를 명시해 로그에 남김
                log.warning(f"[티커 검증] 미등록 {vresult['invalid']}건 (확인 필요):")
                for d in vresult["details"]:
                    if "미등록" in d:
                        log.warning(f"  → {d}")
            if not vresult["corrected"] and not vresult["invalid"]:
                log.info("[티커 검증] KRX/US 종목 전체 정상")
        except Exception as e:
            log.warning(f"[티커 검증] 검증 실패 (무시): {e}")

    # ── 모멘텀 체크: Stage 1 추천 종목의 기간별 수익률 조회 ──
    if cfg.enable_stock_data:
        all_proposals = []
        for theme in themes:
            for p in theme.get("proposals", []):
                if p.get("ticker") and p.get("asset_type") == "stock":
                    all_proposals.append(p)

        if all_proposals:
            log.info(f"[모멘텀 체크] {len(all_proposals)}종목 기간별 수익률 조회 (1m/3m/6m/1y)...")
            momentum_map = fetch_momentum_batch(
                [
                    {"ticker": p["ticker"], "market": p.get("market", "")}
                    for p in all_proposals
                ],
                db_cfg=db_cfg,
            )

            run_count = 0
            fallback_count = 0
            anomaly_count = 0
            source_stats = {"ohlcv_db": 0, "pykrx": 0, "yfinance_close": 0, "other": 0}
            for p in all_proposals:
                ticker = p["ticker"].strip().upper()
                mdata = momentum_map.get(ticker)
                if mdata:
                    p["price_momentum_check"] = mdata["momentum_tag"]
                    if mdata.get("current_price"):
                        p["current_price"] = mdata["current_price"]
                        p["price_source"] = mdata.get("price_source", "yfinance_close")
                    for key in ("return_1m_pct", "return_3m_pct", "return_6m_pct", "return_1y_pct"):
                        if mdata.get(key) is not None:
                            p[key] = mdata[key]
                    if mdata["momentum_tag"] == "already_run":
                        run_count += 1
                    src = mdata.get("price_source") or "other"
                    source_stats[src if src in source_stats else "other"] += 1
                else:
                    # 모멘텀 체크 실패 → fetch_stock_data로 현재가만 재시도
                    try:
                        sd = fetch_stock_data(ticker, p.get("market", ""))
                        if sd and sd.get("price"):
                            p["current_price"] = sd["price"]
                            p["price_source"] = sd.get("price_source", "yfinance_realtime")
                            if sd.get("price_anomaly"):
                                p["price_anomaly"] = sd["price_anomaly"]
                            fallback_count += 1
                        else:
                            p["current_price"] = None
                            p["price_source"] = None
                    except Exception as e:
                        log.warning(f"[모멘텀 체크] {ticker} 개별 재조회 실패: {e}")
                        p["current_price"] = None
                        p["price_source"] = None

                # A-2: 현재가가 있으면 proposal 자체에도 이상 감지 적용 (모멘텀 경로 포함)
                if p.get("current_price") and not p.get("price_anomaly"):
                    _anomalies = _detect_proposal_price_anomalies(p)
                    if _anomalies:
                        p["price_anomaly"] = _anomalies
                        anomaly_count += 1
                        log.warning(
                            f"[가격 이상] {p.get('asset_name', ticker)} ({ticker}): "
                            f"{', '.join(_anomalies)} — current_price={p['current_price']}",
                            extra={
                                "stage": "momentum",
                                "context": {
                                    "ticker": ticker,
                                    "asset_name": p.get("asset_name"),
                                    "market": p.get("market"),
                                    "current_price": p.get("current_price"),
                                    "anomalies": _anomalies,
                                },
                            },
                        )

            if run_count:
                log.info(f"[모멘텀 체크] {run_count}종목 급등 감지 (1개월 +20% 이상)")
            if fallback_count:
                log.info(f"[모멘텀 체크] {fallback_count}종목 개별 재조회로 가격 확보")
            if anomaly_count:
                log.warning(f"[모멘텀 체크] {anomaly_count}종목 가격 이상 감지 (penny stock 등) — 위 경고 참고")
            log.info(
                f"[모멘텀 체크] 완료 — {len(momentum_map)}/{len(all_proposals)}종목 조회 성공 "
                f"(출처: ohlcv_db={source_stats['ohlcv_db']} "
                f"pykrx={source_stats['pykrx']} "
                f"yfinance={source_stats['yfinance_close']} "
                f"기타={source_stats['other']})"
            )

    # ── AI 추정 가격 제거: yfinance 미조회 종목의 current_price를 null로 ──
    if not cfg.enable_stock_data:
        for theme in themes:
            for p in theme.get("proposals", []):
                if p.get("ticker") and p.get("asset_type") == "stock":
                    p["current_price"] = None
                    p["price_source"] = None

    # 모멘텀 체크포인트 저장 (Stage 1-B + 모멘텀 결과 통합)
    if checkpoint and not checkpoint.has("momentum"):
        checkpoint.save("momentum", result)

    # ── Stage 2: 핵심 종목 심층분석 ──
    if not cfg.enable_stock_analysis:
        log.info("[Stage 2] 종목 심층분석 비활성화 — 건너뜀")
        return result

    # 상위 테마에서 buy/sell 제안 중 stock 타입 종목 추출 (테마당 top_stocks_per_theme개)
    # A-4: 가격 조회 실패(current_price None)/상장폐지 의심 종목은 제외.
    #       모멘텀 체크 단계에서 current_price 를 못 가져온 종목은 Stage 2 분석이 의미 없다.
    stock_targets = []
    excluded_no_price = []  # (asset_name, ticker) — 로그용
    for theme in themes[:cfg.top_themes]:
        raw_candidates = [
            p for p in theme.get("proposals", [])
            if (p.get("asset_type") == "stock"
                and p.get("action") in ("buy", "sell")
                and p.get("ticker"))
        ]
        # 가격 확보 실패 종목 분리
        priced: list = []
        for p in raw_candidates:
            if p.get("current_price") is None:
                excluded_no_price.append(
                    f"{p.get('asset_name', '?')} ({p.get('ticker', '?')}) @ {p.get('market', '?')}"
                )
            else:
                priced.append(p)

        candidates = priced
        priority = {"undervalued": 0, "early_signal": 0, "unknown": 1, "fair_priced": 1, "already_run": 2}
        candidates.sort(key=lambda p: (
            priority.get(p.get("price_momentum_check", "unknown"), 1),
            -1 if p.get("discovery_type") in ("early_signal", "contrarian", "deep_value") else 0,
        ))
        for proposal in candidates[:cfg.top_stocks_per_theme]:
            stock_targets.append((proposal, theme.get("theme_name", "")))

    if excluded_no_price:
        log.warning(
            f"[Stage 2 선정] 가격 조회 실패 {len(excluded_no_price)}종목 제외 "
            f"(상장폐지·심볼오류 의심):"
        )
        for item in excluded_no_price[:10]:
            log.warning(f"  → {item}")
        if len(excluded_no_price) > 10:
            log.warning(f"  ... (외 {len(excluded_no_price) - 10}건)")

    if not stock_targets:
        log.info("[Stage 2] 심층분석 대상 종목 없음 — 건너뜀")
        return result

    # ── 주가 데이터 일괄 조회 ──
    stock_data_map: dict[str, dict] = {}
    if cfg.enable_stock_data:
        log.info(f"[주가 데이터] {len(stock_targets)}종목 실시간 데이터 조회 시작...")
        stock_list = [
            {"ticker": p["ticker"], "market": p.get("market", "")}
            for p, _ in stock_targets
        ]
        try:
            stock_data_map = fetch_multiple_stocks(stock_list)
        except Exception as e:
            log.error(f"[주가 데이터] 일괄 조회 실패: {e}", extra={"detail": traceback.format_exc()})
        # 모멘텀 체크에서 확보한 기간별 수익률을 주가 데이터에 병합
        for proposal, _ in stock_targets:
            tk = proposal["ticker"].strip().upper()
            if tk in stock_data_map:
                for key in ("return_1m_pct", "return_3m_pct", "return_6m_pct", "return_1y_pct"):
                    if proposal.get(key) is not None:
                        stock_data_map[tk][key] = proposal[key]
        log.info(f"[주가 데이터] {len(stock_data_map)}/{len(stock_targets)}종목 조회 완료")
    else:
        log.info("[주가 데이터] 비활성화 — Claude 추정치 사용")

    # ── KRX 확장 데이터: 투자자 수급 + 공매도 조회 (Stage 2 프롬프트용) ──
    investor_map: dict[str, dict] = {}
    short_map: dict[str, dict] = {}
    try:
        from analyzer.krx_data import (
            fetch_investor_trading_batch, fetch_short_selling_batch,
            format_investor_data_text, format_short_selling_text,
        )
        krx_stock_list = [
            {"ticker": p["ticker"], "market": p.get("market", "")}
            for p, _ in stock_targets
        ]
        investor_map = fetch_investor_trading_batch(krx_stock_list)
        short_map = fetch_short_selling_batch(krx_stock_list)
    except Exception as e:
        log.warning(f"KRX 수급/공매도 조회 실패 (무시): {e}")

    # ── 정량 팩터 스냅샷 (로드맵 B1) ──
    factor_map: dict[tuple[str, str], dict] = {}
    if db_cfg is not None:
        try:
            from analyzer.factor_engine import compute_factor_snapshots
            factor_map = compute_factor_snapshots(
                db_cfg,
                [(p["ticker"], p.get("market", "")) for p, _ in stock_targets],
            )
        except Exception as e:
            log.warning(f"[factor] 스냅샷 계산 실패 (무시): {e}")

    # ── 외국인 수급 인사이트 (Tier 1, KRX 한정) ──
    foreign_flow_map: dict[tuple[str, str], dict] = {}
    if db_cfg is not None:
        try:
            from analyzer.foreign_flow_insight import compute_foreign_flow_snapshots
            foreign_flow_map = compute_foreign_flow_snapshots(
                db_cfg,
                [(p["ticker"], p.get("market", "")) for p, _ in stock_targets],
            )
        except Exception as e:
            log.warning(f"[foreign_flow_insight] 스냅샷 계산 실패 (무시): {e}")

    # ── 펀더 시계열 인사이트 (Tier 2, KR + US) ──
    fundamentals_map: dict[tuple[str, str], dict] = {}
    if db_cfg is not None:
        try:
            from analyzer.fundamentals_engine import compute_fundamentals_snapshots
            fundamentals_map = compute_fundamentals_snapshots(
                db_cfg,
                [(p["ticker"], p.get("market", "")) for p, _ in stock_targets],
            )
        except Exception as e:
            log.warning(f"[fundamentals_engine] 스냅샷 계산 실패 (무시): {e}")

    log.info(f"[Stage 2] 종목 심층분석 시작 — {len(stock_targets)}종목 (병렬 실행)")

    _STAGE2_REQUIRED_FIELDS = ("factor_scores", "sentiment_score", "recommendation")

    def _stage2_missing_fields(result: dict) -> list[str]:
        """Stage 2 결과에서 누락된 필수 필드 반환
        factor_scores는 dict이면서 비어있지 않아야 하고,
        sentiment_score/recommendation 은 None/빈문자열이 아니어야 한다.
        """
        missing = []
        fs = result.get("factor_scores")
        if not fs or not isinstance(fs, dict):
            missing.append("factor_scores")
        if result.get("sentiment_score") is None:
            missing.append("sentiment_score")
        rec = result.get("recommendation")
        if rec is None or (isinstance(rec, str) and not rec.strip()):
            missing.append("recommendation")
        return missing

    async def _analyze_one(proposal: dict, theme_name: str) -> None:
        ticker = proposal["ticker"]
        asset_name = proposal.get("asset_name", ticker)
        market = proposal.get("market", "")
        # A-6: 세마포어 대기/진입 구분
        log.info(f"  ⏳ {asset_name} ({ticker}) 대기")
        async with _sdk_semaphore:
            log.info(f"  → {asset_name} ({ticker}) 분석 시작")

            try:
                sd = stock_data_map.get(ticker.upper())
                sd_text = format_stock_data_text(sd) if sd else ""

                # 정량 팩터 스냅샷 (B1)
                tk_upper = ticker.upper()
                mk_upper = (market or "").strip().upper()
                factor_snap = factor_map.get((tk_upper, mk_upper))
                factors_text = ""
                if factor_snap:
                    try:
                        from analyzer.factor_engine import format_factor_snapshot_text
                        factors_text = format_factor_snapshot_text(factor_snap)
                    except Exception:
                        factors_text = ""
                    # proposal에 저장 (session_repo가 JSONB로 삽입)
                    proposal["factor_snapshot"] = factor_snap

                # 외국인 수급 PIT 스냅샷 (Tier 1, KRX 한정) — 정량 팩터 섹션에 합류
                ff_snap = foreign_flow_map.get((tk_upper, mk_upper))
                if ff_snap:
                    try:
                        from analyzer.foreign_flow_insight import format_foreign_flow_text
                        ff_text = format_foreign_flow_text(ff_snap)
                    except Exception:
                        ff_text = ""
                    if ff_text:
                        factors_text = (factors_text + "\n" + ff_text).strip() if factors_text else ff_text
                    proposal["foreign_flow_snapshot"] = ff_snap

                # 펀더 시계열 PIT 스냅샷 (Tier 2, KR + US) — 별도 fundamentals_text 로 전달
                fund_text = ""
                fund_snap = fundamentals_map.get((tk_upper, mk_upper))
                if fund_snap:
                    try:
                        from analyzer.fundamentals_engine import format_fundamentals_text
                        fund_text = format_fundamentals_text(fund_snap)
                    except Exception:
                        fund_text = ""
                    proposal["fundamentals_snapshot"] = fund_snap

                # KRX 확장 데이터 포맷팅
                inv_text = ""
                sht_text = ""
                if tk_upper in investor_map:
                    inv_text = format_investor_data_text(investor_map[tk_upper])
                if tk_upper in short_map:
                    sht_text = format_short_selling_text(short_map[tk_upper])
                    # 공매도 위험도를 proposal에 태깅
                    proposal["squeeze_risk"] = short_map[tk_upper].get("squeeze_risk")
                if tk_upper in investor_map:
                    consec = investor_map[tk_upper].get("foreign_consecutive_days", 0)
                    if consec >= 5:
                        proposal["foreign_net_buy_signal"] = "strong_buy"
                    elif consec >= 3:
                        proposal["foreign_net_buy_signal"] = "buy"
                    elif consec <= -5:
                        proposal["foreign_net_buy_signal"] = "sell"
                    else:
                        proposal["foreign_net_buy_signal"] = "neutral"

                max_attempts = 3  # A-1: 2 → 3 (빈 복구 대비)
                stock_result = None
                last_missing: list[str] = []
                for attempt in range(1, max_attempts + 1):
                    # A-1: 재시도 시 max_turns를 올려 잘림 가능성 완화
                    turns_for_attempt = cfg.max_turns if attempt == 1 else max(cfg.max_turns, 2)
                    stock_result = await stage2_analyze_stock(
                        ticker=ticker, asset_name=asset_name,
                        market=market, theme_context=theme_name,
                        date=date, max_turns=turns_for_attempt,
                        stock_data_text=sd_text,
                        quant_factors_text=factors_text,
                        fundamentals_text=fund_text,
                        investor_data_text=inv_text,
                        short_selling_text=sht_text,
                        model=cfg.model_analysis,
                        timeout_sec=timeout,
                    )

                    if stock_result.get("error"):
                        break

                    # A-1: _truncated 여부와 무관하게 필수 필드 누락이면 재시도
                    missing = _stage2_missing_fields(stock_result)
                    last_missing = missing
                    if missing:
                        reason = "잘림" if stock_result.get("_truncated") else "필드 누락"
                        ctx = {
                            "stage": "stage2",
                            "context": {
                                "ticker": ticker,
                                "asset_name": asset_name,
                                "missing": missing,
                                "attempt": attempt,
                                "truncated": bool(stock_result.get("_truncated")),
                            },
                        }
                        if attempt < max_attempts:
                            log.warning(
                                f"  {asset_name} 심층분석 {reason} "
                                f"(누락: {', '.join(missing)}) — 재시도 {attempt+1}/{max_attempts} "
                                f"(max_turns={max(cfg.max_turns, 2)})",
                                extra=ctx,
                            )
                            await asyncio.sleep(5)
                            continue
                        else:
                            log.warning(
                                f"  {asset_name} 심층분석 실패 — 핵심 필드 누락: "
                                f"{', '.join(missing)} (재시도 {max_attempts}회 소진)",
                                extra=ctx,
                            )
                    break

                if stock_result.get("error"):
                    log.error(f"  {asset_name} 심층분석 실패: {stock_result['error']}")
                    proposal["stage2_status"] = "error"
                    proposal["stage2_error"] = stock_result.get("error")
                elif last_missing:
                    # A-1: 재시도 소진되어도 부분 결과는 기록 — 진단 가능
                    proposal["stage2_status"] = "incomplete"
                    proposal["stage2_missing"] = last_missing
                    if stock_result.get("sentiment_score") is not None:
                        proposal["sentiment_score"] = stock_result["sentiment_score"]
                    if stock_result.get("factor_scores", {}).get("composite") is not None:
                        proposal["quant_score"] = stock_result["factor_scores"]["composite"]
                    if sd and sd.get("price"):
                        proposal["current_price"] = sd["price"]
                        proposal["price_source"] = "yfinance_realtime"
                else:
                    proposal["stage2_status"] = "ok"
                    proposal["stock_analysis"] = stock_result
                    if stock_result.get("sentiment_score") is not None:
                        proposal["sentiment_score"] = stock_result["sentiment_score"]
                    if stock_result.get("factor_scores", {}).get("composite") is not None:
                        proposal["quant_score"] = stock_result["factor_scores"]["composite"]
                    if stock_result.get("target_price_low") is not None:
                        proposal["target_price_low"] = stock_result["target_price_low"]
                    if stock_result.get("target_price_high") is not None:
                        proposal["target_price_high"] = stock_result["target_price_high"]
                    if stock_result.get("entry_condition"):
                        proposal["entry_condition"] = stock_result["entry_condition"]
                    if stock_result.get("exit_condition"):
                        proposal["exit_condition"] = stock_result["exit_condition"]
                    if sd and sd.get("price"):
                        proposal["current_price"] = sd["price"]
                        proposal["price_source"] = "yfinance_realtime"
                    log.info(f"  {asset_name} 심층분석 완료")
            except Exception as e:
                log.error(f"  {asset_name} 심층분석 오류: {e}", extra={"detail": traceback.format_exc()})
                proposal["stage2_status"] = "exception"
                proposal["stage2_error"] = f"{type(e).__name__}: {e}"

    await asyncio.gather(*[
        _analyze_one(proposal, theme_name)
        for proposal, theme_name in stock_targets
    ])

    log.info("[Stage 2] 종목 심층분석 완료")

    if checkpoint:
        checkpoint.save("stage2", result)

    return result


# ── 뉴스 제목 한글 번역 ─────────────────────────────

async def _translate_news_batch(
    articles: list[dict], model: str = "claude-haiku-4-5-20251001",
) -> list[dict]:
    """뉴스 기사 제목+요약을 한글로 배치 번역 (Claude SDK, Haiku 기본)

    30건씩 배치로 묶어 시스템 프롬프트 반복을 최소화합니다.
    """
    if not articles:
        return articles

    import re
    def _has_korean(text: str) -> bool:
        return bool(re.search(r'[\uac00-\ud7af]', text))

    to_translate = []
    for i, a in enumerate(articles):
        title = a.get("title", "")
        summary = a.get("summary", "")
        title_is_ko = _has_korean(title)
        summary_is_ko = _has_korean(summary) or not summary

        if title_is_ko:
            a["title_ko"] = title
        if summary_is_ko:
            a["summary_ko"] = summary

        if not title_is_ko or not summary_is_ko:
            to_translate.append((i, title, summary[:200], title_is_ko, summary_is_ko))

    if not to_translate:
        get_logger("번역").info("모든 뉴스가 한글 — 번역 건너뜀")
        return articles

    # 30건씩 배치 번역 (시스템 프롬프트 1회로 토큰 절감)
    BATCH_SIZE = 30
    total_translated = 0
    system_prompt = "뉴스 제목/요약 번역 전문가입니다. 간결하고 자연스러운 한국어로 번역합니다. JSON으로만 응답합니다."

    for batch_start in range(0, len(to_translate), BATCH_SIZE):
        batch = to_translate[batch_start:batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (len(to_translate) + BATCH_SIZE - 1) // BATCH_SIZE

        # 번역 대상 구성 — 이미 한글인 필드는 제외
        items_text = []
        for idx, title, summary, title_is_ko, summary_is_ko in batch:
            parts = []
            if not title_is_ko:
                parts.append(f"t: {title}")
            if not summary_is_ko and summary:
                parts.append(f"s: {summary}")
            items_text.append(f"{idx}:\n" + "\n".join(parts))

        prompt = f"""아래 뉴스의 제목(t)과 요약(s)을 한국어로 번역해주세요.

```
{"---".join(items_text)}
```

반드시 아래 JSON 형식으로만 응답:
{{"translations": {{{", ".join(f'"{idx}": {{"t": "제목 번역", "s": "요약 번역"}}' for idx, _, _, _, _ in batch)}}}}}

한글인 필드는 원문 그대로 반환하세요."""

        get_logger("번역").info(f"배치 {batch_num}/{total_batches} — {len(batch)}건 번역 중 ({model})...")
        try:
            start = time.time()
            batch_label = f"batch_{batch_num}_of_{total_batches}"
            response = await _query_claude(
                prompt, system_prompt, max_turns=1, model=model,
                archive_stage="translate", archive_target_key=batch_label,
            )
            parsed = _parse_json_response(response)
            _archive_result(
                stage="translate", target_key=batch_label, model=model,
                system_prompt=system_prompt, user_prompt=prompt,
                response=response, parsed=parsed, elapsed_sec=time.time() - start,
            )
            translations = parsed.get("translations", {})

            for idx_str, tr in translations.items():
                idx = int(idx_str)
                if 0 <= idx < len(articles):
                    if isinstance(tr, dict):
                        if tr.get("t"):
                            articles[idx]["title_ko"] = tr["t"]
                        if tr.get("s"):
                            articles[idx]["summary_ko"] = tr["s"]
                    elif isinstance(tr, str):
                        # 제목만 반환된 경우 (하위호환)
                        articles[idx]["title_ko"] = tr
                    total_translated += 1

        except Exception as e:
            get_logger("번역").warning(f"배치 {batch_num} 실패 (원문 유지): {e}")

    get_logger("번역").info(f"총 {total_translated}/{len(to_translate)}건 번역 완료")
    return articles


def translate_news(
    articles: list[dict], model: str = "claude-haiku-4-5-20251001",
) -> list[dict]:
    """뉴스 제목+요약 한글 번역 — 동기 래퍼"""
    return anyio.run(_translate_news_batch, articles, model)


# ── 동기 래퍼 (하위호환) ─────────────────────────────

def run_analysis(news_text: str, date: str, max_turns: int = 6) -> dict:
    """동기 래퍼 — 기존 인터페이스 호환 (Stage 1만 실행)"""
    return anyio.run(stage1_discover_themes, news_text, date, max_turns)


def run_full_analysis(
    news_text: str, date: str, cfg: AnalyzerConfig,
    db_cfg: DatabaseConfig | None = None,
    checkpoint=None,
) -> dict:
    """동기 래퍼 — 멀티스테이지 전체 파이프라인"""
    return anyio.run(run_pipeline, news_text, date, cfg, db_cfg, checkpoint)
