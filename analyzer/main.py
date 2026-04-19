#!/usr/bin/env python3
"""
투자 테마 분석 시스템 — 메인 엔트리포인트

매일 지정 시간에 실행되어:
1. RSS 피드에서 글로벌 뉴스 수집
2. Claude Code SDK 멀티스테이지 분석 수행
   - Stage 1: 이슈 분석 + 테마 발굴 (시나리오·매크로 포함)
   - Stage 2: 핵심 종목 심층분석 (펀더멘털·퀀트·센티먼트)
3. 결과를 PostgreSQL에 저장
4. 실행 로그를 app_runs/app_logs 테이블에 기록
"""
import sys
import traceback
import anyio
from datetime import date
from shared.config import AppConfig, RecommendationConfig, DatabaseConfig
from shared.db import (
    init_db, save_analysis, save_news_articles, get_latest_news_titles,
    get_connection, save_top_picks, update_top_picks_ai_rerank,
)
from shared.logger import (
    init_logger, start_run, finish_run, get_logger, save_incident_report,
)
from psycopg2.extras import RealDictCursor
from analyzer.news_collector import collect_news_structured
from analyzer.analyzer import run_full_analysis, translate_news
from analyzer.recommender import compute_rule_based_picks, ai_rerank_picks
from analyzer.price_tracker import run_price_tracking
from analyzer.validators import build_incident_report, summarize_ai_queries


def main() -> int:
    # --fresh 옵션: 체크포인트 무시하고 처음부터 실행
    force_fresh = "--fresh" in sys.argv

    cfg = AppConfig()

    # DB 초기화 (스키마 마이그레이션 포함)
    try:
        init_db(cfg.db)
    except Exception as e:
        print(f"[에러] DB 연결 실패: {e}")
        return 1

    # 로거 초기화 (DB 핸들러 포함)
    init_logger(cfg.db)
    log = get_logger("main")

    # 실행(run) 시작 기록
    today = str(date.today())
    run_id = start_run(cfg.db, run_type="analyzer", meta={"date": today, "fresh": force_fresh})

    log.info("=" * 60)
    log.info(f"[시작] {today} 투자 분석 (멀티스테이지)")
    if force_fresh:
        log.info("[옵션] --fresh 모드: 체크포인트 무시, 처음부터 실행")
    log.info("=" * 60)

    try:
        return _run_analysis(cfg, today, run_id, log, force_fresh)
    except Exception as e:
        log.error(f"분석 중 예상치 못한 오류: {e}", extra={"detail": traceback.format_exc()})
        finish_run(cfg.db, run_id, status="failure",
                   error_message=f"{type(e).__name__}: {e}")
        return 1


def _run_analysis(cfg: AppConfig, today: str, run_id: int | None, log,
                   force_fresh: bool = False) -> int:
    """분석 파이프라인 실행 — 정상 흐름과 에러 처리를 분리"""
    from analyzer.checkpoint import CheckpointManager, compute_news_fingerprint

    # 1) 뉴스 수집
    try:
        news_text, news_articles = collect_news_structured(cfg.news)
    except Exception as e:
        log.error(f"뉴스 수집 실패: {e}", extra={"detail": traceback.format_exc()})
        finish_run(cfg.db, run_id, status="failure", error_message=f"뉴스 수집 실패: {e}")
        return 1

    if not news_text:
        log.warning("수집된 뉴스가 없습니다. 종료합니다.")
        finish_run(cfg.db, run_id, status="skipped", summary="수집된 뉴스 없음")
        return 1

    # 1-1) 뉴스 세트 지문 비교 — 신규 뉴스가 임계값 미만이면 스킵
    min_new = cfg.analyzer.min_new_news
    try:
        prev_titles = set(get_latest_news_titles(cfg.db))
        curr_titles = {a["title"] for a in news_articles}
        new_titles = curr_titles - prev_titles
        new_count = len(new_titles)
        log.info(f"[지문] 수집 {len(curr_titles)}건 중 신규 {new_count}건 "
                 f"(이전 세션 {len(prev_titles)}건과 비교)")

        if new_count < min_new and prev_titles:
            log.info(f"[스킵] 신규 뉴스 {new_count}건 < 임계값 {min_new}건 — 분석 생략")
            finish_run(cfg.db, run_id, status="skipped",
                       summary=f"신규 뉴스 {new_count}건 < 임계값 {min_new}건")
            return 0
    except Exception as e:
        log.warning(f"[지문] 비교 실패 (무시하고 분석 진행): {e}")

    # 1-2) 체크포인트 관리자 초기화 (뉴스 지문 기반)
    news_fp = compute_news_fingerprint(news_articles)
    checkpoint = CheckpointManager(
        analysis_date=today,
        news_fingerprint=news_fp,
        force_fresh=force_fresh,
    )
    last_stage = checkpoint.last_completed_stage()
    if last_stage:
        log.info(f"[체크포인트] 마지막 완료 Stage: {last_stage} — 이어서 작업")

    # 2) 뉴스 한글 번역 (분석 전 완료 — SDK 프로세스 경합 방지)
    log.info("[번역] 뉴스 한글 번역 시작...")
    try:
        news_articles = translate_news(news_articles, cfg.analyzer.model_translate)
    except Exception as e:
        log.warning(f"[번역] 번역 실패 (원문 유지): {e}")

    # 3) 멀티스테이지 분석 (체크포인트 전달)
    log.info("[분석] Claude Code SDK 멀티스테이지 파이프라인 시작...")
    try:
        result = run_full_analysis(
            news_text=news_text,
            date=today,
            cfg=cfg.analyzer,
            db_cfg=cfg.db,
            checkpoint=checkpoint,
        )
    except Exception as e:
        log.error(f"분석 파이프라인 예외: {e}", extra={"detail": traceback.format_exc()})
        finish_run(cfg.db, run_id, status="failure",
                   error_message=f"분석 파이프라인 예외: {e}")
        return 1

    if result.get("error"):
        log.error(f"분석 실패: {result['error']}")
        finish_run(cfg.db, run_id, status="failure",
                   error_message=result["error"])
        return 1

    issues = result.get("issues", [])
    themes = result.get("themes", [])
    total_proposals = sum(len(t.get("proposals", [])) for t in themes)
    log.info(f"[분석] 전체 완료 — 이슈 {len(issues)}건, 테마 {len(themes)}건, 제안 {total_proposals}건")

    # 5) DB 저장
    session_id = None
    try:
        session_id = save_analysis(cfg.db, today, result)
        news_count = save_news_articles(cfg.db, session_id, news_articles)
        log.info(f"[DB] 뉴스 기사 {news_count}건 저장 완료")
        # B-4: DB 저장 성공 → 체크포인트를 아카이브로 보존 후 작업 디렉토리 정리
        checkpoint.clear(archive=True)
    except Exception as e:
        log.error(f"DB 저장 실패: {e}", extra={"detail": traceback.format_exc()})
        log.info("[체크포인트] DB 저장 실패 — 체크포인트 유지 (재실행 시 이어서 작업 가능)")
        finish_run(cfg.db, run_id, status="failure",
                   error_message=f"DB 저장 실패: {e}")
        return 1

    # 6) Stage 3: Top Picks 추천 엔진
    try:
        _run_top_picks_stage(
            cfg.db, cfg.recommendation, cfg.analyzer.model_analysis,
            session_id, today, result,
        )
    except Exception as e:
        log.warning(f"[Stage 3] Top Picks 생성 중 오류 (분석 결과에는 영향 없음): {e}")

    # 7) Stage 4: 추천 후 실제 수익률 추적 (가격 스냅샷 + post_return 갱신)
    try:
        tracker_result = run_price_tracking(cfg.db)
        if tracker_result["tracked"] > 0:
            log.info(f"[Stage 4] 가격추적 완료 — 대상 {tracker_result['tracked']}건, "
                     f"스냅샷 {tracker_result['snapshots_saved']}건, "
                     f"수익률갱신 {tracker_result['returns_updated']}건")
    except Exception as e:
        log.warning(f"[Stage 4] 가격추적 중 오류 (분석 결과에는 영향 없음): {e}")

    # 8) 결과 요약 출력
    _print_summary(result, session_id)

    # 9) 사건 보고서 생성 (B-3) — 가격 이상/Stage 2 실패/AI 파싱 이슈 종합
    try:
        ai_stats = summarize_ai_queries(cfg.db, run_id) if run_id else {}
        report = build_incident_report(
            result=result,
            ai_query_stats=ai_stats,
            ticker_validation=result.get("_ticker_validation"),
        )
        severity = report.get("severity", "info")
        save_incident_report(cfg.db, run_id, session_id, report, severity=severity)
        counts = report.get("counts", {})
        if severity == "critical":
            log.warning(
                f"[사건 보고] 🔴 critical — 가격이상 {counts.get('price_anomalies', 0)}, "
                f"Stage 2 실패 {counts.get('stage2_errors', 0)}, "
                f"AI 파싱 실패 {counts.get('ai_query_failed', 0)}"
            )
        elif severity == "warn":
            log.info(
                f"[사건 보고] 🟡 warn — 가격미조회 {counts.get('no_price', 0)}, "
                f"Stage 2 incomplete {counts.get('stage2_incomplete', 0)}, "
                f"AI truncated {counts.get('ai_query_truncated', 0)}, "
                f"티커 미등록 {counts.get('ticker_invalid', 0)}"
            )
        else:
            log.info("[사건 보고] 🟢 info — 특이사항 없음")
    except Exception as e:
        log.warning(f"[사건 보고] 생성 실패 (무시): {e}")

    # 실행 완료 기록
    summary = (f"이슈 {len(issues)}건, 테마 {len(themes)}건, "
               f"제안 {total_proposals}건, 세션 #{session_id}")
    finish_run(cfg.db, run_id, status="success", summary=summary)
    return 0


def _run_top_picks_stage(
    db_cfg: DatabaseConfig, rec_cfg: RecommendationConfig, model: str,
    session_id: int, analysis_date: str, result: dict,
) -> None:
    """Stage 3: 저장된 분석 결과로부터 Top Picks를 계산하여 영속화"""
    log = get_logger("Stage3")
    themes = result.get("themes", [])
    if not themes:
        log.info("테마 없음 — Top Picks 건너뜀")
        return

    # DB에서 proposal_id, theme_id, streak_days 조회하여 메모리 트리에 병합
    conn = get_connection(db_cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
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

            for tr in theme_rows:
                theme_id_map[tr["theme_name"]] = {
                    "id": tr["id"],
                    "streak_days": streak_map.get(tr["theme_key"], 1),
                }

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
        tmeta["confidence"] = theme.get("confidence_score")

    # 룰 기반 스코어링
    picks = compute_rule_based_picks(
        session_id, themes, rec_cfg, theme_id_map, stage2_ids,
    )
    if not picks:
        log.info("룰 기반 후보 0건 — Top Picks 건너뜀")
        return

    display_picks = picks[:rec_cfg.top_n_display]
    for i, pk in enumerate(display_picks, 1):
        pk["rank"] = i

    save_top_picks(db_cfg, session_id, analysis_date, display_picks, source="rule")
    log.info(f"룰 기반 Top Picks {len(display_picks)}건 저장 완료")

    # AI 재정렬 (선택적)
    if not rec_cfg.enable_ai_rerank:
        log.info("AI 재정렬 비활성화 (REC_ENABLE_AI_RERANK=false) — 룰 결과만 사용")
        return

    log.info(f"AI 재정렬 시작 — 후보 {len(picks)}건 → 상위 {rec_cfg.ai_rerank_top_n}개 선정")
    try:
        ai_results = anyio.run(
            ai_rerank_picks,
            picks, themes,
            result.get("market_summary", ""),
            result.get("risk_temperature", "medium"),
            rec_cfg.ai_rerank_top_n,
            rec_cfg.ai_rerank_max_turns,
            model,
        )
    except Exception as e:
        log.warning(f"AI 재정렬 실패 (룰 결과 유지): {e}")
        return

    if ai_results:
        update_top_picks_ai_rerank(db_cfg, analysis_date, ai_results)
    else:
        log.info("AI 재정렬 결과 없음 — 룰 결과 유지")


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

        for sc in theme.get("scenarios", []):
            print(f"  [{sc['scenario_type'].upper()} {sc.get('probability', '?')}%] "
                  f"{sc.get('description', '')[:100]}")

        for p in theme.get("proposals", []):
            conv = p.get("conviction", "?")
            alloc = p.get("target_allocation", 0)
            total_alloc += alloc
            line = (f"  - [{p['action'].upper()}] {p['asset_name']} ({p.get('ticker', '?')}) "
                    f"@ {p.get('market', '?')} — 비중 {alloc}%, 확신도: {conv}")
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
