"""Claude Code SDK 기반 멀티스테이지 분석 파이프라인

Stage 1: 뉴스 → 이슈 분석 + 테마 발굴 (시나리오/매크로 포함)
Stage 2: 핵심 종목 심층분석 (펀더멘털·퀀트·센티먼트)
"""
import json
import asyncio
import time
import anyio
import traceback
from claude_agent_sdk import (
    query, ClaudeAgentOptions,
    AssistantMessage, TextBlock, ResultMessage, SystemMessage,
)
from shared.logger import get_logger

from analyzer.prompts import (
    STAGE1_SYSTEM, STAGE1_PROMPT,
    STAGE1A_SYSTEM, STAGE1A_PROMPT,
    STAGE1B_SYSTEM, STAGE1B_PROMPT,
    STAGE2_SYSTEM, STAGE2_PROMPT,
)
from analyzer.stock_data import fetch_multiple_stocks, fetch_stock_data, format_stock_data_text, fetch_momentum_batch, validate_krx_tickers
from shared.config import AnalyzerConfig, DatabaseConfig
from shared.db import get_recent_recommendations, get_existing_theme_keys


def _try_fix_truncated_json(json_str: str) -> str | None:
    """잘린 JSON 복구 시도 — 미종료 문자열/배열/객체를 닫아줌"""
    s = json_str.rstrip()

    # 미종료 문자열 닫기: 마지막 열린 따옴표 찾기
    in_string = False
    escape = False
    last_quote_pos = -1
    for i, ch in enumerate(s):
        if escape:
            escape = False
            continue
        if ch == '\\':
            escape = True
            continue
        if ch == '"':
            if not in_string:
                in_string = True
                last_quote_pos = i
            else:
                in_string = False

    if in_string:
        # 문자열 내부에서 잘림 → 따옴표로 닫기
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


def _parse_json_response(full_response: str) -> dict:
    """Claude 응답에서 JSON 추출 및 파싱 (잘린 JSON 복구 지원)"""
    log = get_logger("분석")

    if not full_response or not full_response.strip():
        log.error("빈 응답 수신 — JSON 파싱 불가")
        return {"error": "빈 응답"}

    json_str = full_response.strip()
    if "```json" in json_str:
        json_str = json_str.split("```json")[1].split("```")[0].strip()
    elif "```" in json_str:
        json_str = json_str.split("```")[1].split("```")[0].strip()

    try:
        result = json.loads(json_str)
        result["_truncated"] = False
        return result
    except json.JSONDecodeError as e:
        log.warning(f"JSON 파싱 실패: {e}")
        log.info("잘린 JSON 복구 시도 중...")

        fixed = _try_fix_truncated_json(json_str)
        if fixed:
            try:
                result = json.loads(fixed)
                themes_count = len(result.get("themes", []))
                issues_count = len(result.get("issues", []))
                proposals_count = len(result.get("proposals", []))
                log.info(f"JSON 복구 성공 (이슈 {issues_count}건, 테마 {themes_count}건, 제안 {proposals_count}건)")
                result["_truncated"] = True
                return result
            except json.JSONDecodeError:
                pass

        log.error(f"JSON 복구 실패 — 원본 응답 앞부분:\n{full_response[:500]}")
        return {"error": str(e)}


async def _query_claude(
    prompt: str, system_prompt: str, max_turns: int,
    model: str | None = None,
    max_retries: int = 2,
    timeout_sec: int = 600,
) -> str:
    """Claude SDK 쿼리 공통 함수 (재시도 + 타임아웃 지원)

    Args:
        model: 사용할 모델 (None이면 기본 모델 사용)
        max_retries: 실패 시 최대 재시도 횟수 (기본 2회)
        timeout_sec: 단일 쿼리 타임아웃 (기본 600초=10분)
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
            return full_response

        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            last_error = TimeoutError(f"SDK 쿼리 타임아웃 ({timeout_sec}초 초과, {elapsed:.0f}초 경과)")
            log.error(f"타임아웃 발생 ({elapsed:.0f}초): {timeout_sec}초 초과")
            if full_response:
                log.warning(f"타임아웃 전 부분 응답 {len(full_response):,}자 수신됨 — 부분 응답 반환")
                return full_response
            if attempt < max_retries:
                wait = 10 * attempt
                log.info(f"{wait}초 후 재시도...")
                await asyncio.sleep(wait)
            else:
                log.error("최대 재시도 횟수 초과 — 실패 확정")

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

    raise last_error


# ── Stage 1: 이슈 분석 + 테마 발굴 ──────────────────

def _format_recent_recommendations(recent_recs: list[dict]) -> str:
    """최근 추천 이력을 프롬프트용 요약 텍스트로 포맷팅 (토큰 절약)"""
    if not recent_recs:
        return ""

    # 티커별로 그룹핑하여 요약
    ticker_map: dict[str, dict] = {}
    for rec in recent_recs:
        tk = rec['ticker']
        if tk not in ticker_map:
            ticker_map[tk] = {
                'name': rec['asset_name'],
                'themes': set(),
                'count': 0,
            }
        ticker_map[tk]['themes'].add(rec['theme_name'])
        ticker_map[tk]['count'] += rec['count']

    lines = [
        "\n---\n",
        "## 최근 추천 이력 (중복 방지 — 최근 7일)",
        "",
        f"아래 {len(ticker_map)}개 종목은 최근 이미 추천되었습니다.",
        "**이 종목들은 신규 추천에서 제외**하고, 동일 밸류체인 내 아직 발굴되지 않은 2~3차 수혜주를 대신 찾으세요.",
        "단, 기존 포지션의 목표가 조정이나 청산 판단이 필요하면 별도 언급할 수 있습니다.",
        "",
        "제외 종목 목록:",
    ]
    for tk, info in ticker_map.items():
        themes_str = "/".join(sorted(info['themes']))
        lines.append(f"  - {tk} ({info['name']}) [{themes_str}] {info['count']}회")
    lines.append("")
    return "\n".join(lines)


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

async def stage1a_discover_themes(
    news_text: str, date: str, max_turns: int = 6,
    existing_keys: list[dict] | None = None,
    model: str | None = None,
) -> dict:
    """Stage 1-A: 뉴스 기반 이슈 분석 + 테마 발굴 (투자 제안 제외)"""
    keys_section = _format_existing_theme_keys(existing_keys or [])
    prompt = STAGE1A_PROMPT.format(
        news_text=news_text, date=date,
        existing_theme_keys_section=keys_section,
    )
    response = await _query_claude(prompt, STAGE1A_SYSTEM, max_turns, model=model)
    return _parse_json_response(response)


async def stage1b_generate_proposals(
    theme: dict, date: str, max_turns: int = 6,
    recent_recs: list[dict] | None = None,
    model: str | None = None,
) -> list[dict]:
    """Stage 1-B: 개별 테마에 대한 투자 제안 생성 (10~15건)"""
    recent_section = _format_recent_recommendations(recent_recs or [])
    prompt = STAGE1B_PROMPT.format(
        date=date,
        theme_name=theme.get("theme_name", ""),
        theme_description=theme.get("description", ""),
        theme_type=theme.get("theme_type", ""),
        time_horizon=theme.get("time_horizon", ""),
        confidence_score=theme.get("confidence_score", ""),
        recent_recommendations_section=recent_section,
    )
    response = await _query_claude(prompt, STAGE1B_SYSTEM, max_turns, model=model)
    result = _parse_json_response(response)
    return result.get("proposals", [])


# ── Stage 2: 핵심 종목 심층분석 ──────────────────────

async def stage2_analyze_stock(
    ticker: str, asset_name: str, market: str,
    theme_context: str, date: str, max_turns: int = 6,
    stock_data_text: str = "",
    model: str | None = None,
) -> dict:
    """Stage 2: 개별 종목 심층분석 (펀더멘털·산업·모멘텀·퀀트·리스크)"""
    # 주가 데이터가 있으면 프롬프트에 삽입
    stock_data_section = ""
    if stock_data_text:
        stock_data_section = f"\n\n## 실시간 시장 데이터 (조회 시점: {date})\n\n{stock_data_text}\n"

    prompt = STAGE2_PROMPT.format(
        ticker=ticker, asset_name=asset_name,
        market=market, theme_context=theme_context, date=date,
        stock_data_section=stock_data_section,
    )
    response = await _query_claude(prompt, STAGE2_SYSTEM, max_turns, model=model)
    return _parse_json_response(response)


# ── 통합 파이프라인 ──────────────────────────────────

async def run_pipeline(
    news_text: str, date: str, cfg: AnalyzerConfig,
    db_cfg: DatabaseConfig | None = None,
) -> dict:
    """멀티스테이지 분석 파이프라인 실행

    Stage 1-A: 뉴스 → 이슈/테마/시나리오/매크로 (제안 제외)
    Stage 1-B: 테마별 투자 제안 생성 (병렬, 동시 2개 제한)
    Stage 2: 상위 테마의 핵심 종목 심층분석 (병렬, 동시 2개 제한)
    """
    log = get_logger("파이프라인")
    # SDK 동시 호출 수 제한 — 과도한 병렬 실행 시 CLI 프로세스 충돌 방지
    _sdk_semaphore = asyncio.Semaphore(2)
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

    # ── Stage 1-A: 이슈 분석 + 테마 발굴 (잘림 시 1회 재시도) ──
    log.info(f"[Stage 1-A] 이슈 분석 + 테마 발굴 중... (모델: {cfg.model_analysis})")
    result = await stage1a_discover_themes(
        news_text, date, cfg.max_turns,
        existing_keys=existing_keys,
        model=cfg.model_analysis,
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
        )
        if not retry_result.get("error") and len(retry_result.get("themes", [])) > len(result.get("themes", [])):
            result = retry_result
            log.info(f"[Stage 1-A] 재시도 성공 — 테마 {len(result.get('themes', []))}건 복구")
        else:
            log.warning("[Stage 1-A] 재시도 결과 개선 없음 — 기존 결과 사용")

    themes = result.get("themes", [])
    issues = result.get("issues", [])
    log.info(f"[Stage 1-A] 완료 — 이슈 {len(issues)}건, 테마 {len(themes)}건")

    # ── Stage 1-B: 테마별 투자 제안 생성 (병렬) ──
    log.info(f"[Stage 1-B] 테마별 투자 제안 생성 시작 — {len(themes)}개 테마 (병렬 실행)")

    async def _generate_proposals_for_theme(i: int, theme: dict) -> None:
        async with _sdk_semaphore:
            theme_name = theme.get("theme_name", f"테마{i+1}")
            log.info(f"[Stage 1-B] ({i+1}/{len(themes)}) '{theme_name}' 제안 생성 중...")
            try:
                proposals = await stage1b_generate_proposals(
                    theme, date, cfg.max_turns, recent_recs,
                    model=cfg.model_analysis,
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

    # ── KRX 티커 검증/교정 (Stage 1-B 이후, 모멘텀 체크 전) ──
    all_stock_proposals = [
        p for theme in themes
        for p in theme.get("proposals", [])
        if p.get("ticker") and p.get("asset_type") == "stock"
    ]
    if all_stock_proposals:
        try:
            vresult = validate_krx_tickers(all_stock_proposals)
            if vresult["corrected"]:
                log.info(f"[티커 검증] KRX 티커 {vresult['corrected']}건 교정:")
                for d in vresult["details"]:
                    if "미등록" not in d:
                        log.info(f"  → {d}")
            if vresult["invalid"]:
                log.warning(f"[티커 검증] KRX 미등록 {vresult['invalid']}건 (확인 필요)")
            if not vresult["corrected"] and not vresult["invalid"]:
                log.info("[티커 검증] KRX 종목 전체 정상")
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
            momentum_map = fetch_momentum_batch([
                {"ticker": p["ticker"], "market": p.get("market", "")}
                for p in all_proposals
            ])

            run_count = 0
            fallback_count = 0
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
                else:
                    # 모멘텀 체크 실패 → fetch_stock_data로 현재가만 재시도
                    try:
                        sd = fetch_stock_data(ticker, p.get("market", ""))
                        if sd and sd.get("price"):
                            p["current_price"] = sd["price"]
                            p["price_source"] = sd.get("price_source", "yfinance_realtime")
                            fallback_count += 1
                        else:
                            p["current_price"] = None
                            p["price_source"] = None
                    except Exception as e:
                        log.warning(f"[모멘텀 체크] {ticker} 개별 재조회 실패: {e}")
                        p["current_price"] = None
                        p["price_source"] = None

            if run_count:
                log.info(f"[모멘텀 체크] {run_count}종목 급등 감지 (1개월 +20% 이상)")
            if fallback_count:
                log.info(f"[모멘텀 체크] {fallback_count}종목 개별 재조회로 가격 확보")
            log.info(f"[모멘텀 체크] 완료 — {len(momentum_map)}/{len(all_proposals)}종목 조회 성공")

    # ── AI 추정 가격 제거: yfinance 미조회 종목의 current_price를 null로 ──
    if not cfg.enable_stock_data:
        for theme in themes:
            for p in theme.get("proposals", []):
                if p.get("ticker") and p.get("asset_type") == "stock":
                    p["current_price"] = None
                    p["price_source"] = None

    # ── Stage 2: 핵심 종목 심층분석 ──
    if not cfg.enable_stock_analysis:
        log.info("[Stage 2] 종목 심층분석 비활성화 — 건너뜀")
        return result

    # 상위 테마에서 buy/sell 제안 중 stock 타입 종목 추출 (테마당 top_stocks_per_theme개)
    stock_targets = []
    for theme in themes[:cfg.top_themes]:
        candidates = [
            p for p in theme.get("proposals", [])
            if (p.get("asset_type") == "stock"
                and p.get("action") in ("buy", "sell")
                and p.get("ticker"))
        ]
        priority = {"undervalued": 0, "early_signal": 0, "unknown": 1, "fair_priced": 1, "already_run": 2}
        candidates.sort(key=lambda p: (
            priority.get(p.get("price_momentum_check", "unknown"), 1),
            -1 if p.get("discovery_type") in ("early_signal", "contrarian", "deep_value") else 0,
        ))
        for proposal in candidates[:cfg.top_stocks_per_theme]:
            stock_targets.append((proposal, theme.get("theme_name", "")))

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

    log.info(f"[Stage 2] 종목 심층분석 시작 — {len(stock_targets)}종목 (병렬 실행)")

    _STAGE2_REQUIRED_FIELDS = ("factor_scores", "sentiment_score", "recommendation")

    async def _analyze_one(proposal: dict, theme_name: str) -> None:
        async with _sdk_semaphore:
            ticker = proposal["ticker"]
            asset_name = proposal.get("asset_name", ticker)
            market = proposal.get("market", "")
            log.info(f"  → {asset_name} ({ticker}) 분석 중...")

            try:
                sd = stock_data_map.get(ticker.upper())
                sd_text = format_stock_data_text(sd) if sd else ""

                max_attempts = 2
                stock_result = None
                for attempt in range(1, max_attempts + 1):
                    stock_result = await stage2_analyze_stock(
                        ticker=ticker, asset_name=asset_name,
                        market=market, theme_context=theme_name,
                        date=date, max_turns=cfg.max_turns,
                        stock_data_text=sd_text,
                        model=cfg.model_analysis,
                    )

                    if stock_result.get("error"):
                        break

                    if stock_result.get("_truncated"):
                        missing = [f for f in _STAGE2_REQUIRED_FIELDS if not stock_result.get(f)]
                        if missing and attempt < max_attempts:
                            log.warning(f"  {asset_name} 심층분석 잘림 (누락: {', '.join(missing)}) — 재시도 {attempt+1}/{max_attempts}")
                            await asyncio.sleep(5)
                            continue
                        elif missing:
                            log.warning(f"  {asset_name} 심층분석 잘림 — 핵심 필드 누락: {', '.join(missing)} (재시도 소진)")
                    break

                if not stock_result.get("error"):
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
                else:
                    log.error(f"  {asset_name} 심층분석 실패: {stock_result['error']}")
            except Exception as e:
                log.error(f"  {asset_name} 심층분석 오류: {e}", extra={"detail": traceback.format_exc()})

    await asyncio.gather(*[
        _analyze_one(proposal, theme_name)
        for proposal, theme_name in stock_targets
    ])

    log.info("[Stage 2] 종목 심층분석 완료")
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
            response = await _query_claude(
                prompt, system_prompt, max_turns=1, model=model,
            )
            parsed = _parse_json_response(response)
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
) -> dict:
    """동기 래퍼 — 멀티스테이지 전체 파이프라인"""
    return anyio.run(run_pipeline, news_text, date, cfg, db_cfg)
