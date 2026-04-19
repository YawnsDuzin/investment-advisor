"""대시보드 Top Picks 추천 엔진

Stage 3a: 룰 기반 스코어링 + 다양성 제약 → 상위 N 선정
Stage 3b (선택): Claude SDK로 재정렬 + 근거 생성

저장 결과는 daily_top_picks 테이블에 영속화된다.
"""
from __future__ import annotations

from shared.config import RecommendationConfig


def _safe_float(v, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except (ValueError, TypeError):
        return default


def _is_buy_or_watch(proposal: dict) -> bool:
    return proposal.get("action") in ("buy", "watch")


def build_candidate_pool(
    session_id: int,
    themes: list[dict],
    theme_id_map: dict,
    stage2_proposal_ids: set[int],
) -> list[dict]:
    """테마·제안 트리를 평탄화하여 스코어링 대상 후보 목록 생성

    Args:
        themes: Stage 1-B 결과 테마 리스트 (proposals 포함)
        theme_id_map: {theme_name: {"id": int, "confidence": float, "streak_days": int}}
        stage2_proposal_ids: Stage 2 심층분석이 완료된 proposal_id 집합

    Returns:
        각 후보에 테마 메타를 병합한 flat list.
        proposal_id가 없는(아직 DB 저장 전) 항목도 임시 보유 가능.
    """
    candidates = []
    for theme in themes:
        theme_name = theme.get("theme_name", "")
        tmeta = theme_id_map.get(theme_name, {})
        theme_confidence = _safe_float(theme.get("confidence_score"))
        for p in theme.get("proposals", []):
            if not _is_buy_or_watch(p):
                continue
            # asset_type이 stock이 아닌 자산(ETF/commodity 등)은 우선 제외 — 대시보드 초기 범위
            if p.get("asset_type") not in ("stock", "etf"):
                continue

            has_stage2 = (
                p.get("_proposal_id") in stage2_proposal_ids
                if p.get("_proposal_id") else False
            )

            # Stage 2 심층분석 완료 종목에서 upside_pct가 음수면 제외
            # (Stage 1 AI 추정 목표가는 신뢰하지 않으므로 필터링에 사용하지 않음)
            if has_stage2:
                upside = p.get("upside_pct")
                if upside is not None:
                    try:
                        if float(upside) < 0:
                            continue
                    except (ValueError, TypeError):
                        pass

            candidates.append({
                **p,
                "_theme_name": theme_name,
                "_theme_id": tmeta.get("id"),
                "_theme_confidence": theme_confidence,
                "_streak_days": tmeta.get("streak_days", 1),
                "_has_stage2": has_stage2,
            })
    return candidates


def score_proposal(proposal: dict, cfg: RecommendationConfig) -> tuple[float, dict]:
    """단일 제안의 룰 기반 스코어와 기여 내역 반환

    Returns:
        (total_score, breakdown) — breakdown은 각 항목의 기여 점수 dict
    """
    breakdown: dict = {}
    total = 0.0

    # +30 conviction=high
    if proposal.get("conviction") == "high":
        breakdown["conviction_high"] = cfg.w_conviction_high
        total += cfg.w_conviction_high

    # +20 Stage 2 심층분석 완료
    if proposal.get("_has_stage2") or proposal.get("has_stock_analysis"):
        breakdown["stage2_done"] = cfg.w_stage2_done
        total += cfg.w_stage2_done

    # +15 early_signal / deep_value
    if proposal.get("discovery_type") in ("early_signal", "deep_value"):
        breakdown["discovery_early"] = cfg.w_discovery_early
        total += cfg.w_discovery_early

    # +10 action=buy
    if proposal.get("action") == "buy":
        breakdown["action_buy"] = cfg.w_action_buy
        total += cfg.w_action_buy

    # upside 구간별 가점 — Stage 2 심층분석 완료 종목만 신뢰
    # (Stage 1 AI 추정 목표가는 실제 시세와 괴리가 클 수 있어 가점 미적용)
    has_stage2 = proposal.get("_has_stage2") or proposal.get("has_stock_analysis")
    if has_stage2:
        upside = _safe_float(proposal.get("upside_pct"))
        if upside >= cfg.upside_high_threshold:
            breakdown["upside_high"] = cfg.w_upside_high
            total += cfg.w_upside_high
        elif upside >= cfg.upside_mid_threshold:
            breakdown["upside_mid"] = cfg.w_upside_mid
            total += cfg.w_upside_mid

    # 테마 신뢰도 가점 (0.00~1.00 * mult)
    theme_conf = _safe_float(proposal.get("_theme_confidence"))
    if theme_conf > 0:
        score = round(theme_conf * cfg.w_theme_confidence_mult, 2)
        if score > 0:
            breakdown["theme_confidence"] = score
            total += score

    # 연속 등장 테마 보너스
    streak = int(proposal.get("_streak_days") or 1)
    if streak >= cfg.streak_days_threshold:
        breakdown["theme_streak"] = cfg.w_streak_bonus
        total += cfg.w_streak_bonus

    # 감점: 급등(이미 반영)
    return_1m = _safe_float(proposal.get("return_1m_pct"))
    momentum_check = proposal.get("price_momentum_check")
    if momentum_check == "already_run" or return_1m >= cfg.momentum_overheated_pct:
        breakdown["already_priced_penalty"] = -cfg.w_already_priced_penalty
        total -= cfg.w_already_priced_penalty

    # 감점: 실시간 가격 없음 (신뢰도↓)
    if proposal.get("current_price") is None:
        breakdown["no_price_penalty"] = -cfg.w_no_price_penalty
        total -= cfg.w_no_price_penalty

    return round(total, 2), breakdown


def rank_with_diversity(
    scored: list[dict], cfg: RecommendationConfig,
) -> list[dict]:
    """다양성 제약(테마·섹터)을 적용하면서 상위 N 선정

    Args:
        scored: [{..., _score: float, _breakdown: dict}, ...] — 이미 점수화됨
    Returns:
        선정된 픽 리스트 (rank 할당됨)
    """
    # 1차 정렬: 점수 내림차순, 타이브레이커: quant_score > target_allocation
    sorted_cands = sorted(
        scored,
        key=lambda p: (
            -p["_score"],
            -_safe_float(p.get("quant_score")),
            -_safe_float(p.get("target_allocation")),
        ),
    )

    picks = []
    theme_count: dict = {}
    sector_count: dict = {}
    seen_tickers = set()

    for p in sorted_cands:
        if len(picks) >= cfg.max_candidates:
            break

        ticker = (p.get("ticker") or "").upper().strip()
        if not ticker or ticker in seen_tickers:
            continue

        theme_key = p.get("_theme_name", "")
        sector = p.get("sector") or "unknown"

        if theme_count.get(theme_key, 0) >= cfg.max_per_theme:
            continue
        if sector_count.get(sector, 0) >= cfg.max_per_sector:
            continue

        seen_tickers.add(ticker)
        theme_count[theme_key] = theme_count.get(theme_key, 0) + 1
        sector_count[sector] = sector_count.get(sector, 0) + 1
        picks.append(p)

    # rank 할당
    for i, p in enumerate(picks, 1):
        p["_rank"] = i
    return picks


def compute_rule_based_picks(
    session_id: int,
    themes: list[dict],
    cfg: RecommendationConfig,
    theme_id_map: dict | None = None,
    stage2_proposal_ids: set | None = None,
) -> list[dict]:
    """룰 기반 Top Picks 계산 — 순수 로직 (DB I/O 없음)

    Args:
        themes: Stage 1-B 결과 (각 proposal에 _proposal_id 병합되어 있어야 함)
        theme_id_map: {theme_name: {id, confidence, streak_days}}
        stage2_proposal_ids: Stage 2 완료된 proposal_id 집합
    Returns:
        상위 max_candidates 개 픽 리스트 — save_top_picks()에 전달 가능한 형태
    """
    theme_id_map = theme_id_map or {}
    stage2_proposal_ids = stage2_proposal_ids or set()

    candidates = build_candidate_pool(
        session_id, themes, theme_id_map, stage2_proposal_ids,
    )
    if not candidates:
        return []

    # 각 후보에 점수 부여
    for p in candidates:
        score, breakdown = score_proposal(p, cfg)
        p["_score"] = score
        p["_breakdown"] = breakdown

    picks = rank_with_diversity(candidates, cfg)

    # save_top_picks()에 맞는 형태로 변환
    return [
        {
            "rank": p["_rank"],
            "proposal_id": p.get("_proposal_id"),
            "score_rule": p["_score"],
            "score_final": p["_score"],
            "score_breakdown": p["_breakdown"],
            "rationale_text": None,
            "key_risk": None,
            "_ticker": p.get("ticker"),
            "_asset_name": p.get("asset_name"),
            "_sector": p.get("sector"),
            "_theme_name": p.get("_theme_name"),
            "_discovery_type": p.get("discovery_type"),
        }
        for p in picks
        if p.get("_proposal_id")  # DB에 저장 안된 후보는 제외
    ]


# ── Stage 3b: AI 재정렬 ─────────────────────────────

def _build_candidates_summary(picks: list[dict], themes: list[dict]) -> str:
    """AI 프롬프트용 후보 요약 — 토큰 절약을 위해 핵심 필드만"""
    # proposal_id → proposal + theme 매핑
    proposal_map = {}
    for theme in themes:
        for p in theme.get("proposals", []):
            pid = p.get("_proposal_id")
            if pid:
                proposal_map[pid] = (p, theme)

    lines = []
    for pk in picks:
        pid = pk["proposal_id"]
        if pid not in proposal_map:
            continue
        p, theme = proposal_map[pid]
        rationale = (p.get("rationale") or "").replace("\n", " ")[:250]
        lines.append(
            f"- id={pid} | {p.get('asset_name')} ({p.get('ticker')}) "
            f"| 테마: {theme.get('theme_name')} "
            f"| 섹터: {p.get('sector') or '-'} "
            f"| 확신도: {p.get('conviction')} "
            f"| 발굴: {p.get('discovery_type')} "
            f"| 상승여력: {p.get('upside_pct') or '-'}% "
            f"| 룰점수: {pk['score_rule']} "
            f"| 근거: {rationale}"
        )
    return "\n".join(lines)


async def ai_rerank_picks(
    picks: list[dict],
    themes: list[dict],
    market_summary: str,
    risk_temperature: str,
    top_n: int,
    max_turns: int,
    model: str | None = None,
) -> list[dict]:
    """AI(Claude SDK)로 Top Picks 재정렬 + 근거 생성

    실패 시 빈 리스트 반환 → 호출자는 룰 기반 결과 유지.

    Returns:
        [{proposal_id, rank, rationale_text, key_risk, score_final}, ...]
    """
    if not picks:
        return []

    # 지연 임포트 — 순환 참조 방지 및 SDK 미설치 환경 보호
    from analyzer.analyzer import _query_claude, _parse_json_response
    from analyzer.prompts import STAGE3_SYSTEM, STAGE3_PROMPT

    candidates_text = _build_candidates_summary(picks, themes)
    prompt = STAGE3_PROMPT.format(
        candidates_text=candidates_text,
        market_summary=(market_summary or "")[:800],
        risk_temperature=risk_temperature or "medium",
        top_n=top_n,
    )

    try:
        response = await _query_claude(
            prompt=prompt,
            system_prompt=STAGE3_SYSTEM,
            max_turns=max_turns,
            model=model,
            max_retries=1,
        )
    except Exception as e:
        print(f"[Stage 3] AI 재정렬 실패 (룰 결과 유지): {e}")
        return []

    parsed = _parse_json_response(response)
    if parsed.get("error"):
        print(f"[Stage 3] AI 응답 파싱 실패 (룰 결과 유지): {parsed['error']}")
        return []

    results = parsed.get("picks", [])
    if not isinstance(results, list) or not results:
        print("[Stage 3] AI 응답에 picks 배열 없음 (룰 결과 유지)")
        return []

    # 후보 proposal_id 화이트리스트 — AI 환각 방지
    valid_ids = {pk["proposal_id"] for pk in picks}

    validated = []
    rank = 1
    for item in results:
        pid = item.get("proposal_id")
        try:
            pid = int(pid) if pid is not None else None
        except (ValueError, TypeError):
            pid = None
        if pid is None or pid not in valid_ids:
            continue
        # 동일 proposal_id 중복 방지
        if any(v["proposal_id"] == pid for v in validated):
            continue
        validated.append({
            "proposal_id": pid,
            "rank": rank,
            "rationale_text": (item.get("rationale") or "").strip()[:500],
            "key_risk": (item.get("key_risk") or "").strip()[:300],
            "score_final": item.get("score", next(
                (pk["score_rule"] for pk in picks if pk["proposal_id"] == pid), None
            )),
        })
        rank += 1
        if rank > top_n:
            break

    if not validated:
        print("[Stage 3] AI 응답에서 유효한 proposal_id 없음 (룰 결과 유지)")
        return []

    print(f"[Stage 3] AI 재정렬 {len(validated)}건 확정")
    return validated
