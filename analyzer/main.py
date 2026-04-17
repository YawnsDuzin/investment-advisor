#!/usr/bin/env python3
"""
투자 테마 분석 시스템 — 메인 엔트리포인트

매일 지정 시간에 실행되어:
1. RSS 피드에서 글로벌 뉴스 수집
2. Claude Code SDK 멀티스테이지 분석 수행
   - Stage 1: 이슈 분석 + 테마 발굴 (시나리오·매크로 포함)
   - Stage 2: 핵심 종목 심층분석 (펀더멘털·퀀트·센티먼트)
3. 결과를 PostgreSQL에 저장
"""
import sys
import anyio
from datetime import date
from concurrent.futures import ThreadPoolExecutor

from shared.config import AppConfig, RecommendationConfig, DatabaseConfig
from shared.db import (
    init_db, save_analysis, save_news_articles, get_latest_news_titles,
    get_connection, save_top_picks, update_top_picks_ai_rerank,
)
from psycopg2.extras import RealDictCursor
from analyzer.news_collector import collect_news_structured
from analyzer.analyzer import run_full_analysis, translate_news
from analyzer.recommender import compute_rule_based_picks, ai_rerank_picks


def main() -> int:
    cfg = AppConfig()

    # DB 초기화 (스키마 마이그레이션 포함)
    try:
        init_db(cfg.db)
    except Exception as e:
        print(f"[에러] DB 연결 실패: {e}")
        return 1

    # 1) 뉴스 수집
    print("=" * 60)
    print(f"[시작] {date.today()} 투자 분석 (멀티스테이지)")
    print("=" * 60)

    news_text, news_articles = collect_news_structured(cfg.news)
    if not news_text:
        print("[경고] 수집된 뉴스가 없습니다. 종료합니다.")
        return 1

    # 1-1) 뉴스 세트 지문 비교 — 신규 뉴스가 임계값 미만이면 스킵
    min_new = cfg.analyzer.min_new_news
    try:
        prev_titles = set(get_latest_news_titles(cfg.db))
        curr_titles = {a["title"] for a in news_articles}
        new_titles = curr_titles - prev_titles
        new_count = len(new_titles)
        print(f"[지문] 수집 {len(curr_titles)}건 중 신규 {new_count}건 "
              f"(이전 세션 {len(prev_titles)}건과 비교)")

        if new_count < min_new and prev_titles:
            print(f"[스킵] 신규 뉴스 {new_count}건 < 임계값 {min_new}건 — 분석 생략")
            return 0
    except Exception as e:
        print(f"[지문] 비교 실패 (무시하고 분석 진행): {e}")

    # 2) 뉴스 번역을 백그라운드에서 먼저 시작 (분석과 병렬 실행)
    executor = ThreadPoolExecutor(max_workers=1)
    print("[번역] 뉴스 한글 번역 백그라운드 시작...")
    translate_future = executor.submit(
        translate_news, news_articles, cfg.analyzer.model_translate,
    )

    # 3) 멀티스테이지 분석
    print("\n[분석] Claude Code SDK 멀티스테이지 파이프라인 시작...")
    result = run_full_analysis(
        news_text=news_text,
        date=str(date.today()),
        cfg=cfg.analyzer,
        db_cfg=cfg.db,
    )

    if result.get("error"):
        print(f"[에러] 분석 실패: {result['error']}")
        # 번역 스레드 정리
        try:
            translate_future.result(timeout=1)
        except Exception:
            pass
        executor.shutdown(wait=False)
        return 1

    issues = result.get("issues", [])
    themes = result.get("themes", [])
    print(f"\n[분석] 전체 완료 — 이슈 {len(issues)}건, 테마 {len(themes)}건")

    # 4) 번역 결과 수집 (이미 완료되었을 가능성 높음)
    try:
        news_articles = translate_future.result(timeout=300)
    except Exception as e:
        print(f"[번역] 백그라운드 번역 실패 (원문 유지): {e}")
    finally:
        executor.shutdown(wait=False)

    # 5) DB 저장
    try:
        session_id = save_analysis(cfg.db, str(date.today()), result)
        # 뉴스 기사 저장
        news_count = save_news_articles(cfg.db, session_id, news_articles)
        print(f"[DB] 뉴스 기사 {news_count}건 저장 완료")
    except Exception as e:
        print(f"[에러] DB 저장 실패: {e}")
        return 1

    # 6) Stage 3: Top Picks 추천 엔진 (룰 기반 + 선택적 AI 재정렬)
    try:
        _run_top_picks_stage(
            cfg.db, cfg.recommendation, cfg.analyzer.model_analysis,
            session_id, str(date.today()), result,
        )
    except Exception as e:
        print(f"[Stage 3] Top Picks 생성 중 오류 (분석 결과에는 영향 없음): {e}")

    # 7) 결과 요약 출력
    _print_summary(result, session_id)
    return 0


def _run_top_picks_stage(
    db_cfg: DatabaseConfig, rec_cfg: RecommendationConfig, model: str,
    session_id: int, analysis_date: str, result: dict,
) -> None:
    """Stage 3: 저장된 분석 결과로부터 Top Picks를 계산하여 영속화

    순서: DB에서 proposal_id/theme_id/streak_days 로드 → 메모리 트리에 병합
          → 룰 기반 스코어링 → daily_top_picks INSERT (source='rule')
          → enable_ai_rerank=True면 AI 재정렬 후 UPDATE (source='ai_rerank')
    """
    themes = result.get("themes", [])
    if not themes:
        print("[Stage 3] 테마 없음 — Top Picks 건너뜀")
        return

    # DB에서 proposal_id, theme_id, streak_days 조회하여 메모리 트리에 병합
    conn = get_connection(db_cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 테마별 id + streak (theme_tracking은 theme_key 기반)
            cur.execute(
                "SELECT id, theme_name, theme_key FROM investment_themes WHERE session_id = %s",
                (session_id,),
            )
            theme_rows = cur.fetchall()

            theme_id_map: dict = {}
            theme_keys = [t["theme_key"] for t in theme_rows if t.get("theme_key")]
            streak_map: dict = {}
            if theme_keys:
                cur.execute(
                    "SELECT theme_key, streak_days FROM theme_tracking "
                    "WHERE theme_key = ANY(%s)",
                    (theme_keys,),
                )
                streak_map = {r["theme_key"]: r["streak_days"] for r in cur.fetchall()}

            # theme_name → {id, streak_days, confidence}
            for tr in theme_rows:
                theme_id_map[tr["theme_name"]] = {
                    "id": tr["id"],
                    "streak_days": streak_map.get(tr["theme_key"], 1),
                }

            # 각 테마의 proposal_id + has_stock_analysis
            all_theme_ids = [tr["id"] for tr in theme_rows]
            proposals_by_theme: dict = {}
            stage2_ids: set = set()
            if all_theme_ids:
                cur.execute(
                    """SELECT p.id, p.theme_id, p.ticker,
                              EXISTS(SELECT 1 FROM stock_analyses sa
                                     WHERE sa.proposal_id = p.id) AS has_stage2
                       FROM investment_proposals p
                       WHERE p.theme_id = ANY(%s)""",
                    (all_theme_ids,),
                )
                for row in cur.fetchall():
                    proposals_by_theme.setdefault(row["theme_id"], {})[
                        (row["ticker"] or "").upper()
                    ] = row["id"]
                    if row["has_stage2"]:
                        stage2_ids.add(row["id"])
    finally:
        conn.close()

    # 메모리 트리의 각 proposal에 _proposal_id 주입
    for theme in themes:
        tmeta = theme_id_map.get(theme.get("theme_name", ""))
        if not tmeta:
            continue
        tid = tmeta["id"]
        theme["_id"] = tid
        ticker_map = proposals_by_theme.get(tid, {})
        for p in theme.get("proposals", []):
            tk = (p.get("ticker") or "").upper()
            if tk and tk in ticker_map:
                p["_proposal_id"] = ticker_map[tk]
        # theme_confidence는 result에서 이미 있음, theme_id_map에 병합
        tmeta["confidence"] = theme.get("confidence_score")

    # 룰 기반 스코어링
    picks = compute_rule_based_picks(
        session_id, themes, rec_cfg, theme_id_map, stage2_ids,
    )
    if not picks:
        print("[Stage 3] 룰 기반 후보 0건 — Top Picks 건너뜀")
        return

    # 표시 대상만 남겨 저장 (상위 top_n_display)
    display_picks = picks[:rec_cfg.top_n_display]
    # rank 재할당 (display 기준)
    for i, pk in enumerate(display_picks, 1):
        pk["rank"] = i

    save_top_picks(db_cfg, session_id, analysis_date, display_picks, source="rule")
    print(f"[Stage 3] 룰 기반 Top Picks {len(display_picks)}건 저장 완료")

    # AI 재정렬 (선택적)
    if not rec_cfg.enable_ai_rerank:
        print("[Stage 3] AI 재정렬 비활성화 (REC_ENABLE_AI_RERANK=false) — 룰 결과만 사용")
        return

    print(f"[Stage 3] AI 재정렬 시작 — 후보 {len(picks)}건 → 상위 {rec_cfg.ai_rerank_top_n}개 선정")
    try:
        ai_results = anyio.run(
            ai_rerank_picks,
            picks,  # 전체 후보를 후보군으로 전달 (AI가 재선별 가능)
            themes,
            result.get("market_summary", ""),
            result.get("risk_temperature", "medium"),
            rec_cfg.ai_rerank_top_n,
            rec_cfg.ai_rerank_max_turns,
            model,
        )
    except Exception as e:
        print(f"[Stage 3] AI 재정렬 실패 (룰 결과 유지): {e}")
        return

    if ai_results:
        update_top_picks_ai_rerank(db_cfg, analysis_date, ai_results)
    else:
        print("[Stage 3] AI 재정렬 결과 없음 — 룰 결과 유지")


def _print_summary(result: dict, session_id: int) -> None:
    """분석 결과 요약 출력"""
    issues = result.get("issues", [])
    themes = result.get("themes", [])

    print("\n" + "=" * 60)
    print("시장 환경 요약")
    print("=" * 60)
    print(result.get("market_summary", "(없음)"))
    risk = result.get("risk_temperature")
    if risk:
        label = {"high": "🔴 높음", "medium": "🟡 보통", "low": "🟢 낮음"}.get(risk, risk)
        print(f"리스크 온도: {label}")

    print("\n" + "=" * 60)
    print(f"글로벌 이슈 ({len(issues)}건)")
    print("=" * 60)
    for i, issue in enumerate(issues):
        imp = "★" * issue.get("importance", 3)
        print(f"  {i+1}. [{issue.get('category', '?')}] {issue.get('title', '')} ({imp})")
        if issue.get("impact_short"):
            print(f"      단기: {issue['impact_short'][:80]}")

    print("\n" + "=" * 60)
    print(f"투자 테마 ({len(themes)}건)")
    print("=" * 60)
    total_alloc = 0
    for theme in themes:
        score = theme.get("confidence_score", 0)
        horizon = theme.get("time_horizon", "?")
        ttype = theme.get("theme_type", "?")
        validity = theme.get("theme_validity", "?")
        indicators = ", ".join(theme.get("key_indicators", []))
        print(f"\n▶ {theme['theme_name']} (신뢰도: {score}, 시계: {horizon}, "
              f"유형: {ttype}, 유효성: {validity})")
        print(f"  {theme.get('description', '')[:200]}")
        if indicators:
            print(f"  모니터링: {indicators}")

        # 시나리오 출력
        for sc in theme.get("scenarios", []):
            print(f"  [{sc['scenario_type'].upper()} {sc.get('probability', '?')}%] "
                  f"{sc.get('description', '')[:100]}")

        # 제안 출력
        for p in theme.get("proposals", []):
            conv = p.get("conviction", "?")
            alloc = p.get("target_allocation", 0)
            total_alloc += alloc
            line = (f"  - [{p['action'].upper()}] {p['asset_name']} ({p.get('ticker', '?')}) "
                    f"@ {p.get('market', '?')} — 비중 {alloc}%, 확신도: {conv}")
            # 심층분석 결과가 있으면 표시
            if p.get("quant_score"):
                line += f", 퀀트: {p['quant_score']}/5.0"
            if p.get("sentiment_score") is not None:
                line += f", 센티먼트: {p['sentiment_score']}"
            if p.get("target_price_low") and p.get("target_price_high"):
                curr = p.get("currency", "")
                line += f", 목표가: {curr}{p['target_price_low']}~{p['target_price_high']}"
            print(line)

    print(f"\n  총 포트폴리오 비중: {total_alloc:.1f}%")
    print(f"\n[완료] 세션 #{session_id} 저장됨")


if __name__ == "__main__":
    sys.exit(main())
