"""Claude Code SDK 기반 멀티스테이지 분석 파이프라인

Stage 1: 뉴스 → 이슈 분석 + 테마 발굴 (시나리오/매크로 포함)
Stage 2: 핵심 종목 심층분석 (펀더멘털·퀀트·센티먼트)
"""
import json
import asyncio
import anyio
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock

from analyzer.prompts import (
    STAGE1_SYSTEM, STAGE1_PROMPT,
    STAGE2_SYSTEM, STAGE2_PROMPT,
)
from analyzer.stock_data import fetch_multiple_stocks, format_stock_data_text
from shared.config import AnalyzerConfig


def _parse_json_response(full_response: str) -> dict:
    """Claude 응답에서 JSON 추출 및 파싱"""
    json_str = full_response.strip()
    if "```json" in json_str:
        json_str = json_str.split("```json")[1].split("```")[0].strip()
    elif "```" in json_str:
        json_str = json_str.split("```")[1].split("```")[0].strip()

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"[분석] JSON 파싱 실패: {e}")
        print(f"[분석] 원본 응답:\n{full_response[:500]}")
        return {"error": str(e)}


async def _query_claude(prompt: str, system_prompt: str, max_turns: int) -> str:
    """Claude SDK 쿼리 공통 함수"""
    full_response = ""
    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            system_prompt=system_prompt,
            max_turns=max_turns,
        ),
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    full_response += block.text
    return full_response


# ── Stage 1: 이슈 분석 + 테마 발굴 ──────────────────

async def stage1_discover_themes(news_text: str, date: str, max_turns: int = 6) -> dict:
    """Stage 1: 뉴스 기반 이슈 분석 + 테마 발굴 + 시나리오/매크로 분석 + 투자 제안"""
    prompt = STAGE1_PROMPT.format(news_text=news_text, date=date)
    response = await _query_claude(prompt, STAGE1_SYSTEM, max_turns)
    return _parse_json_response(response)


# ── Stage 2: 핵심 종목 심층분석 ──────────────────────

async def stage2_analyze_stock(
    ticker: str, asset_name: str, market: str,
    theme_context: str, date: str, max_turns: int = 6,
    stock_data_text: str = "",
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
    response = await _query_claude(prompt, STAGE2_SYSTEM, max_turns)
    return _parse_json_response(response)


# ── 통합 파이프라인 ──────────────────────────────────

async def run_pipeline(
    news_text: str, date: str, cfg: AnalyzerConfig,
) -> dict:
    """멀티스테이지 분석 파이프라인 실행

    Stage 1: 뉴스 → 이슈/테마/시나리오/매크로/제안
    Stage 2: 상위 테마의 핵심 종목 심층분석 (선택적)
    """
    # ── Stage 1 ──
    print("[Stage 1] 이슈 분석 + 테마 발굴 중...")
    result = await stage1_discover_themes(news_text, date, cfg.max_turns)

    if result.get("error"):
        return result

    themes = result.get("themes", [])
    issues = result.get("issues", [])
    print(f"[Stage 1] 완료 — 이슈 {len(issues)}건, 테마 {len(themes)}건")

    # ── Stage 2: 핵심 종목 심층분석 ──
    if not cfg.enable_stock_analysis:
        print("[Stage 2] 종목 심층분석 비활성화 — 건너뜀")
        return result

    # 상위 테마에서 buy/sell 제안 중 stock 타입 종목 추출 (테마당 top_stocks_per_theme개)
    stock_targets = []
    for theme in themes[:cfg.top_themes]:
        theme_stocks = 0
        for proposal in theme.get("proposals", []):
            if (proposal.get("asset_type") == "stock"
                    and proposal.get("action") in ("buy", "sell")
                    and proposal.get("ticker")
                    and theme_stocks < cfg.top_stocks_per_theme):
                stock_targets.append((proposal, theme.get("theme_name", "")))
                theme_stocks += 1

    if not stock_targets:
        print("[Stage 2] 심층분석 대상 종목 없음 — 건너뜀")
        return result

    # ── 주가 데이터 일괄 조회 ──
    stock_data_map: dict[str, dict] = {}
    if cfg.enable_stock_data:
        print(f"[주가 데이터] {len(stock_targets)}종목 실시간 데이터 조회 시작...")
        stock_list = [
            {"ticker": p["ticker"], "market": p.get("market", "")}
            for p, _ in stock_targets
        ]
        stock_data_map = fetch_multiple_stocks(stock_list)
        print(f"[주가 데이터] {len(stock_data_map)}/{len(stock_targets)}종목 조회 완료")
    else:
        print("[주가 데이터] 비활성화 — Claude 추정치 사용")

    print(f"[Stage 2] 종목 심층분석 시작 — {len(stock_targets)}종목 (병렬 실행)")

    # 병렬 분석 태스크 생성
    async def _analyze_one(proposal: dict, theme_name: str) -> None:
        ticker = proposal["ticker"]
        asset_name = proposal.get("asset_name", ticker)
        market = proposal.get("market", "")
        print(f"  → {asset_name} ({ticker}) 분석 중...")

        try:
            sd = stock_data_map.get(ticker.upper())
            sd_text = format_stock_data_text(sd) if sd else ""

            stock_result = await stage2_analyze_stock(
                ticker=ticker, asset_name=asset_name,
                market=market, theme_context=theme_name,
                date=date, max_turns=cfg.max_turns,
                stock_data_text=sd_text,
            )

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
                print(f"  ✓ {asset_name} 심층분석 완료")
            else:
                print(f"  ✗ {asset_name} 심층분석 실패: {stock_result['error']}")
        except Exception as e:
            print(f"  ✗ {asset_name} 심층분석 오류: {e}")

    await asyncio.gather(*[
        _analyze_one(proposal, theme_name)
        for proposal, theme_name in stock_targets
    ])

    print(f"[Stage 2] 종목 심층분석 완료")
    return result


# ── 동기 래퍼 (하위호환) ─────────────────────────────

def run_analysis(news_text: str, date: str, max_turns: int = 6) -> dict:
    """동기 래퍼 — 기존 인터페이스 호환 (Stage 1만 실행)"""
    return anyio.run(stage1_discover_themes, news_text, date, max_turns)


def run_full_analysis(news_text: str, date: str, cfg: AnalyzerConfig) -> dict:
    """동기 래퍼 — 멀티스테이지 전체 파이프라인"""
    return anyio.run(run_pipeline, news_text, date, cfg)
