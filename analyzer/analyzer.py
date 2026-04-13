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
from analyzer.stock_data import fetch_multiple_stocks, format_stock_data_text, fetch_momentum_batch
from shared.config import AnalyzerConfig, DatabaseConfig
from shared.db import get_recent_recommendations


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

def _format_recent_recommendations(recent_recs: list[dict]) -> str:
    """최근 추천 이력을 프롬프트용 텍스트로 포맷팅"""
    if not recent_recs:
        return ""

    lines = [
        "\n---\n",
        "## 최근 추천 이력 (중복 방지 — 최근 7일)",
        "",
        "아래 종목은 최근 이미 추천된 종목입니다.",
        "**이 종목들은 신규 추천에서 제외**하고, 동일 밸류체인 내 아직 발굴되지 않은 2~3차 수혜주를 대신 찾으세요.",
        "단, 기존 포지션의 목표가 조정이나 청산 판단이 필요하면 별도 언급할 수 있습니다.",
        "",
    ]
    for rec in recent_recs:
        lines.append(
            f"  - {rec['ticker']} ({rec['asset_name']}) — "
            f"테마: {rec['theme_name']}, {rec['action']}/{rec['conviction']}, "
            f"{rec['count']}회 추천 ({rec['first_date']}~{rec['last_date']})"
        )
    lines.append("")
    return "\n".join(lines)


async def stage1_discover_themes(
    news_text: str, date: str, max_turns: int = 6,
    recent_recs: list[dict] | None = None,
) -> dict:
    """Stage 1: 뉴스 기반 이슈 분석 + 테마 발굴 + 시나리오/매크로 분석 + 투자 제안"""
    recent_section = _format_recent_recommendations(recent_recs or [])
    prompt = STAGE1_PROMPT.format(
        news_text=news_text, date=date,
        recent_recommendations_section=recent_section,
    )
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
    db_cfg: DatabaseConfig | None = None,
) -> dict:
    """멀티스테이지 분석 파이프라인 실행

    Stage 1: 뉴스 → 이슈/테마/시나리오/매크로/제안
    Stage 2: 상위 테마의 핵심 종목 심층분석 (선택적)
    """
    # ── 최근 추천 이력 조회 (중복 방지용) ──
    recent_recs = []
    if db_cfg:
        try:
            recent_recs = get_recent_recommendations(db_cfg, days=7)
            if recent_recs:
                print(f"[피드백] 최근 7일 추천 이력 {len(recent_recs)}건 로드 — 중복 방지 적용")
        except Exception as e:
            print(f"[피드백] 추천 이력 조회 실패 (무시): {e}")

    # ── Stage 1 ──
    print("[Stage 1] 이슈 분석 + 테마 발굴 중...")
    result = await stage1_discover_themes(news_text, date, cfg.max_turns, recent_recs)

    if result.get("error"):
        return result

    themes = result.get("themes", [])
    issues = result.get("issues", [])
    print(f"[Stage 1] 완료 — 이슈 {len(issues)}건, 테마 {len(themes)}건")

    # ── 모멘텀 체크: Stage 1 추천 종목의 1개월 수익률 조회 ──
    if cfg.enable_stock_data:
        all_proposals = []
        for theme in themes:
            for p in theme.get("proposals", []):
                if p.get("ticker") and p.get("asset_type") == "stock":
                    all_proposals.append(p)

        if all_proposals:
            print(f"[모멘텀 체크] {len(all_proposals)}종목 1개월 수익률 조회...")
            momentum_map = fetch_momentum_batch([
                {"ticker": p["ticker"], "market": p.get("market", "")}
                for p in all_proposals
            ])

            run_count = 0
            for p in all_proposals:
                ticker = p["ticker"].strip().upper()
                mdata = momentum_map.get(ticker)
                if mdata:
                    p["price_momentum_check"] = mdata["momentum_tag"]
                    if mdata.get("current_price"):
                        p["current_price"] = mdata["current_price"]
                    if mdata["momentum_tag"] == "already_run":
                        run_count += 1

            if run_count:
                print(f"[모멘텀 체크] {run_count}종목 급등 감지 (1개월 +20% 이상)")
            print(f"[모멘텀 체크] 완료 — {len(momentum_map)}/{len(all_proposals)}종목 조회 성공")

    # ── Stage 2: 핵심 종목 심층분석 ──
    if not cfg.enable_stock_analysis:
        print("[Stage 2] 종목 심층분석 비활성화 — 건너뜀")
        return result

    # 상위 테마에서 buy/sell 제안 중 stock 타입 종목 추출 (테마당 top_stocks_per_theme개)
    # 급등 종목(already_run)보다 미반영 종목(early_signal/undervalued)을 우선 선정
    stock_targets = []
    for theme in themes[:cfg.top_themes]:
        candidates = [
            p for p in theme.get("proposals", [])
            if (p.get("asset_type") == "stock"
                and p.get("action") in ("buy", "sell")
                and p.get("ticker"))
        ]
        # 정렬: already_run 종목은 뒤로, early_signal/undervalued 우선
        priority = {"undervalued": 0, "early_signal": 0, "unknown": 1, "fair_priced": 1, "already_run": 2}
        candidates.sort(key=lambda p: (
            priority.get(p.get("price_momentum_check", "unknown"), 1),
            -1 if p.get("discovery_type") in ("early_signal", "contrarian", "deep_value") else 0,
        ))
        for proposal in candidates[:cfg.top_stocks_per_theme]:
            stock_targets.append((proposal, theme.get("theme_name", "")))

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


# ── 뉴스 제목 한글 번역 ─────────────────────────────

async def _translate_news_titles(articles: list[dict]) -> list[dict]:
    """뉴스 기사 제목을 한글로 일괄 번역 (Claude SDK 단일 호출)"""
    if not articles:
        return articles

    # 이미 한글인 제목은 번역 불필요 — 한글 포함 여부로 판별
    import re
    def _has_korean(text: str) -> bool:
        return bool(re.search(r'[\uac00-\ud7af]', text))

    to_translate = []
    for i, a in enumerate(articles):
        title = a.get("title", "")
        if _has_korean(title):
            a["title_ko"] = title  # 이미 한글
        else:
            to_translate.append((i, title))

    if not to_translate:
        print("[번역] 모든 뉴스가 한글 — 번역 건너뜀")
        return articles

    # 번역 대상 제목 목록 구성
    title_lines = "\n".join(f"{idx}: {title}" for idx, title in to_translate)

    prompt = f"""아래 뉴스 제목들을 한국어로 번역해주세요.
각 줄은 "번호: 제목" 형식입니다. 같은 형식으로 번역 결과를 JSON으로 반환하세요.

```
{title_lines}
```

반드시 아래 JSON 형식으로만 응답:
{{"translations": {{"번호": "한글 번역", ...}}}}"""

    system_prompt = "뉴스 제목 번역 전문가입니다. 간결하고 자연스러운 한국어로 번역합니다. JSON으로만 응답합니다."

    print(f"[번역] {len(to_translate)}건 뉴스 제목 한글 번역 중...")
    try:
        response = await _query_claude(prompt, system_prompt, max_turns=1)
        parsed = _parse_json_response(response)
        translations = parsed.get("translations", {})

        translated_count = 0
        for idx_str, ko_title in translations.items():
            idx = int(idx_str)
            if 0 <= idx < len(articles):
                articles[idx]["title_ko"] = ko_title
                translated_count += 1

        print(f"[번역] {translated_count}/{len(to_translate)}건 번역 완료")
    except Exception as e:
        print(f"[번역] 번역 실패 (원문 유지): {e}")

    return articles


def translate_news(articles: list[dict]) -> list[dict]:
    """뉴스 제목 한글 번역 — 동기 래퍼"""
    return anyio.run(_translate_news_titles, articles)


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
