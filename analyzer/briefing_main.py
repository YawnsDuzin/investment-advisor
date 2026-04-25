"""프리마켓 브리핑 배치 엔트리포인트 (KST 06:30).

미국 장 마감(KST ~05:00 EDT / ~06:00 EST) 직후 OHLCV 데이터를 기반으로
한국 시장 개장(09:00) 전 투자자에게 전달할 브리핑을 생성한다.

파이프라인:
  1. 미국 OHLCV 집계 (`analyzer.overnight_us`)
  2. 시장 레짐 스냅샷 (`analyzer.regime` — B2 인프라 재사용)
  3. KR 수혜 후보군 추출 (sector_norm 공통키)
  4. Claude SDK 브리핑 LLM 쿼리
  5. `pre_market_briefings` UPSERT
  6. 워치리스트/구독 매칭 → `user_notifications` 생성

DB 저장 실패해도 LLM 결과는 stdout 로그에 남음.
재실행 안전 — UPSERT(briefing_date PK)로 중복 회피.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
import anyio
from datetime import date
from typing import Any, Optional

from psycopg2.extras import Json, RealDictCursor

from shared.config import AppConfig, DatabaseConfig
from shared.db import get_connection, init_db
from shared.logger import get_logger, init_logger, start_run, finish_run

from analyzer.overnight_us import (
    compute_us_overnight_summary,
    fetch_kr_beneficiaries_by_sectors,
    format_us_summary_text,
    format_kr_candidates_text,
)
from analyzer.regime import (
    compute_regime,
    format_regime_text,
    infer_positioning_hint,
)


# 후보군 추출 시 우선 매핑할 sector_norm 화이트리스트
# (US에서 평균 등락 큰 섹터부터 KR 후보를 뽑음 — 모든 섹터 다 안 뽑음)
_TOP_SECTOR_LIMIT = 6


def main() -> int:
    cfg = AppConfig()

    try:
        init_db(cfg.db)
    except Exception as e:
        print(f"[에러] DB 연결 실패: {e}")
        return 1

    init_logger(cfg.db)
    log = get_logger("briefing")

    today = str(date.today())
    run_id = start_run(cfg.db, run_type="briefing", meta={"date": today})

    log.info("=" * 60)
    log.info(f"[프리마켓 브리핑] {today} 시작")
    log.info("=" * 60)

    try:
        result = run_briefing_pipeline(cfg, today, log)
    except Exception as e:
        log.error(f"브리핑 파이프라인 예외: {e}", extra={"detail": traceback.format_exc()})
        finish_run(cfg.db, run_id, status="failure",
                   error_message=f"{type(e).__name__}: {e}")
        _save_briefing(cfg.db, today, status="failed", error_message=str(e))
        return 1

    status = result.get("status", "success")
    log.info(f"[프리마켓 브리핑] 완료 — status={status} "
             f"groups={len(result.get('briefing_data', {}).get('us_summary', {}).get('groups', []))} "
             f"kr_impact={len(result.get('briefing_data', {}).get('kr_impact', []))}")

    summary_msg = (
        f"trade_date={result.get('source_trade_date')} "
        f"sectors={len(result.get('us_summary', {}).get('sector_aggregates', []))} "
        f"kr_picks={_count_kr_picks(result.get('briefing_data') or {})}"
    )
    finish_run(cfg.db, run_id, status="success" if status == "success" else "warning",
               summary=summary_msg)
    return 0 if status in ("success", "partial") else 1


def run_briefing_pipeline(cfg: AppConfig, today: str, log) -> dict:
    """프리마켓 브리핑 전체 파이프라인.

    Returns:
        {
          "status": "success" | "partial" | "skipped" | "failed",
          "source_trade_date": str | None,
          "us_summary": dict,
          "regime_snapshot": dict,
          "briefing_data": dict | None,
        }
    """
    db_cfg = cfg.db

    # 1) 미국 OHLCV 집계
    log.info("[1/5] 미국 오버나이트 집계...")
    us_summary = compute_us_overnight_summary(db_cfg)
    if not us_summary:
        log.warning("미국 OHLCV 데이터 없음 — 브리핑 스킵")
        _save_briefing(db_cfg, today, status="skipped",
                       error_message="no_us_ohlcv_data")
        return {"status": "skipped", "us_summary": {}, "briefing_data": None,
                "regime_snapshot": {}, "source_trade_date": None}

    source_trade_date = us_summary.get("trade_date")

    # 2) 시장 레짐
    log.info("[2/5] 시장 레짐 스냅샷...")
    regime = compute_regime(db_cfg) or {}
    positioning_hint = infer_positioning_hint(regime)

    # 3) KR 수혜 후보군 — Top 섹터에 대해서만
    top_sectors = [
        s["sector_norm"] for s in us_summary.get("sector_aggregates", [])
        if s["sector_norm"] != "_uncategorized"
    ][:_TOP_SECTOR_LIMIT]
    log.info(f"[3/5] KR 수혜 후보군 추출 — sectors={top_sectors}")
    kr_candidates = fetch_kr_beneficiaries_by_sectors(db_cfg, top_sectors)

    # 4) 프롬프트 구성
    us_text = format_us_summary_text(us_summary)
    regime_text = format_regime_text(regime) or "(레짐 데이터 없음)"
    if positioning_hint:
        regime_text = f"{regime_text}\n- 종합 포지셔닝 힌트: {positioning_hint}"
    regime_section = f"## 시장 레짐\n{regime_text}"
    kr_text = format_kr_candidates_text(kr_candidates)

    # 5) Claude SDK 호출
    log.info("[4/5] Claude SDK 브리핑 쿼리...")
    briefing_data = anyio.run(
        _call_briefing_llm,
        today, source_trade_date, us_text, regime_section, kr_text,
        cfg.analyzer.model_analysis,
        cfg.analyzer.query_timeout,
    )

    if not briefing_data:
        log.warning("LLM 브리핑 생성 실패 — us_summary만 저장하고 종료")
        _save_briefing(
            db_cfg, today,
            source_trade_date=source_trade_date,
            status="partial",
            us_summary=us_summary,
            regime_snapshot=regime,
            briefing_data=None,
            error_message="llm_failed",
        )
        return {
            "status": "partial",
            "source_trade_date": source_trade_date,
            "us_summary": us_summary,
            "regime_snapshot": regime,
            "briefing_data": None,
        }

    # 5-1) 후보 화이트리스트 검증 — AI 환각 차단
    briefing_data = _validate_kr_picks(briefing_data, kr_candidates, log)

    # 5-2) DB 저장
    log.info("[5/5] DB 저장 + 알림 생성...")
    _save_briefing(
        db_cfg, today,
        source_trade_date=source_trade_date,
        status="success",
        us_summary=us_summary,
        regime_snapshot=regime,
        briefing_data=briefing_data,
    )

    # 5-3) 알림 생성
    try:
        n = _generate_briefing_notifications(db_cfg, today, briefing_data)
        log.info(f"브리핑 알림 {n}건 생성")
    except Exception as e:
        log.warning(f"알림 생성 실패 (무시): {e}")

    return {
        "status": "success",
        "source_trade_date": source_trade_date,
        "us_summary": us_summary,
        "regime_snapshot": regime,
        "briefing_data": briefing_data,
    }


async def _call_briefing_llm(
    today: str,
    trade_date: str,
    us_text: str,
    regime_section: str,
    kr_text: str,
    model: str,
    timeout_sec: int,
) -> Optional[dict]:
    """Claude SDK 호출 → JSON 파싱.

    실패 시 None 반환. 호출자는 partial 상태로 저장.
    """
    # 지연 임포트 — analyzer/analyzer.py 의 SDK 헬퍼 재사용
    from analyzer.analyzer import _query_claude, _parse_json_response
    from analyzer.prompts import BRIEFING_SYSTEM, BRIEFING_PROMPT

    prompt = BRIEFING_PROMPT.format(
        date=today,
        trade_date=trade_date,
        regime_section=regime_section,
        us_summary_section=us_text,
        kr_candidates_section=kr_text,
    )

    log = get_logger("briefing")
    try:
        response = await _query_claude(
            prompt=prompt,
            system_prompt=BRIEFING_SYSTEM,
            max_turns=1,
            model=model,
            max_retries=2,
            timeout_sec=timeout_sec,
            archive_stage="briefing",
            archive_target_key=today,
        )
    except Exception as e:
        log.error(f"SDK 쿼리 실패: {e}")
        return None

    parsed = _parse_json_response(response)
    if parsed.get("error"):
        log.warning(f"브리핑 JSON 파싱 실패: {parsed.get('error')}")
        return None

    # 진단 메타 제거 (저장 깔끔하게)
    for k in ("_truncated", "_parse_status", "_parse_error", "_dropped_partial"):
        parsed.pop(k, None)

    return parsed


def _validate_kr_picks(briefing: dict, kr_candidates: dict[str, list[dict]], log) -> dict:
    """LLM이 추천한 한국 종목이 후보 풀(whitelist) 안에 있는지 검증.

    풀 밖 종목은 제거하고 WARNING 로그. asset_name이 다르면 후보 풀 값으로 교정.
    """
    whitelist: dict[str, dict] = {}
    for members in kr_candidates.values():
        for m in members:
            tk = (m.get("ticker") or "").upper()
            if tk:
                whitelist[tk] = m

    impact = briefing.get("kr_impact")
    if not isinstance(impact, list):
        return briefing

    invalid_total = 0
    for grp in impact:
        picks = grp.get("korean_picks") or []
        valid = []
        for p in picks:
            tk = (p.get("ticker") or "").upper()
            if not tk or tk not in whitelist:
                invalid_total += 1
                continue
            ref = whitelist[tk]
            # asset_name 교정
            p["asset_name"] = ref["asset_name"]
            p["market"] = ref["market"]
            p["ticker"] = tk
            valid.append(p)
        grp["korean_picks"] = valid

    if invalid_total:
        log.warning(f"브리핑: 화이트리스트 밖 한국 종목 {invalid_total}건 제거")
    return briefing


def _save_briefing(
    db_cfg: DatabaseConfig,
    briefing_date: str,
    *,
    source_trade_date: Optional[str] = None,
    status: str = "success",
    us_summary: Optional[dict] = None,
    regime_snapshot: Optional[dict] = None,
    briefing_data: Optional[dict] = None,
    error_message: Optional[str] = None,
) -> None:
    """pre_market_briefings UPSERT (PK = briefing_date)."""
    sql = """
    INSERT INTO pre_market_briefings (
        briefing_date, source_trade_date, status,
        us_summary, briefing_data, regime_snapshot, error_message,
        generated_at, updated_at
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
    ON CONFLICT (briefing_date) DO UPDATE SET
        source_trade_date = EXCLUDED.source_trade_date,
        status = EXCLUDED.status,
        us_summary = EXCLUDED.us_summary,
        briefing_data = EXCLUDED.briefing_data,
        regime_snapshot = EXCLUDED.regime_snapshot,
        error_message = EXCLUDED.error_message,
        updated_at = NOW()
    """
    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (
                briefing_date,
                source_trade_date,
                status,
                Json(us_summary) if us_summary else None,
                Json(briefing_data) if briefing_data else None,
                Json(regime_snapshot) if regime_snapshot else None,
                error_message,
            ))
        conn.commit()
    except Exception as e:
        conn.rollback()
        get_logger("briefing").error(f"브리핑 DB 저장 실패: {e}")
    finally:
        conn.close()


def _generate_briefing_notifications(
    db_cfg: DatabaseConfig,
    briefing_date: str,
    briefing: dict,
) -> int:
    """브리핑에 포함된 KR 종목/섹터에 대해 구독자 알림 생성.

    매칭 규칙:
      - sub_type='ticker' AND sub_key=ticker: 해당 ticker가 korean_picks에 등장
      - sub_type='theme'  AND sub_key=sector_norm: 해당 섹터 그룹에 picks 1개 이상

    중복 방지: 같은 (user_id, sub_id, briefing_date) 조합은 1건만.
    """
    impact = briefing.get("kr_impact") or []
    if not impact:
        return 0

    # 매칭 후보 수집
    ticker_to_label: dict[str, str] = {}
    sector_to_summary: dict[str, dict] = {}

    for grp in impact:
        sec = grp.get("sector_norm")
        if sec:
            sector_to_summary[sec] = {
                "label": grp.get("label") or sec,
                "strength": grp.get("strength"),
                "picks_count": len(grp.get("korean_picks") or []),
            }
        for p in grp.get("korean_picks") or []:
            tk = (p.get("ticker") or "").upper()
            if tk:
                ticker_to_label[tk] = p.get("asset_name") or tk

    if not ticker_to_label and not sector_to_summary:
        return 0

    conn = get_connection(db_cfg)
    inserted = 0
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 구독 조회
            cur.execute(
                """
                SELECT id, user_id, sub_type, sub_key
                FROM user_subscriptions
                WHERE (sub_type = 'ticker' AND UPPER(sub_key) = ANY(%s))
                   OR (sub_type = 'theme'  AND sub_key = ANY(%s))
                """,
                (list(ticker_to_label.keys()) or [""],
                 list(sector_to_summary.keys()) or [""]),
            )
            subs = cur.fetchall()

            for sub in subs:
                if sub["sub_type"] == "ticker":
                    tk = (sub["sub_key"] or "").upper()
                    label = ticker_to_label.get(tk)
                    if not label:
                        continue
                    title = f"📈 [프리마켓] {label} ({tk}) 갭 상승 후보"
                    detail = f"미국 장 영향 분석에서 오늘 모니터링 대상으로 선정됨."
                else:
                    sec = sub["sub_key"]
                    info = sector_to_summary.get(sec)
                    if not info:
                        continue
                    title = f"🌅 [프리마켓] {info['label']} 섹터 수혜 후보"
                    detail = f"한국 종목 {info['picks_count']}개 매핑 — strength={info.get('strength')}"

                cur.execute(
                    """
                    INSERT INTO user_notifications
                        (user_id, sub_id, session_id, title, detail, link, is_read, created_at)
                    SELECT %s, %s, NULL, %s, %s, %s, FALSE, NOW()
                    WHERE NOT EXISTS (
                        SELECT 1 FROM user_notifications un
                        WHERE un.user_id = %s
                          AND un.sub_id  = %s
                          AND un.title   = %s
                          AND un.created_at::date = %s::date
                    )
                    """,
                    (
                        sub["user_id"], sub["id"], title, detail,
                        f"/pages/briefing/{briefing_date}",
                        sub["user_id"], sub["id"], title, briefing_date,
                    ),
                )
                inserted += cur.rowcount or 0

        conn.commit()
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()
    return inserted


def _count_kr_picks(briefing: dict) -> int:
    return sum(
        len(g.get("korean_picks") or [])
        for g in (briefing.get("kr_impact") or [])
    )


if __name__ == "__main__":
    sys.exit(main())
