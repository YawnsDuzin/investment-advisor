"""분석 파이프라인 검증 유틸 — 제안/세션의 이상징후 종합 리포트 생성.

각 stage에서 흩어져 있던 경고(가격 이상, 티커 오류, 데이터 누락)를
한 곳에서 집계하여 `shared.logger.save_incident_report()` 에 전달할
표준 포맷으로 반환한다.
"""
from __future__ import annotations
from typing import Any


# ── penny stock / 가격 이상 감지 임계값 (통화별) ──

_PENNY_THRESHOLDS: dict[str, float] = {
    "USD": 1.0, "EUR": 1.0, "GBP": 0.5, "CAD": 1.0, "AUD": 1.0,
    "KRW": 100.0, "JPY": 10.0, "HKD": 1.0, "TWD": 10.0, "CNY": 1.0,
}

_MARKET_CURRENCY = {
    "KRX": "KRW", "KOSPI": "KRW", "KSE": "KRW", "KOSDAQ": "KRW", "KQ": "KRW",
    "NYSE": "USD", "NASDAQ": "USD", "NYSEARCA": "USD", "AMEX": "USD",
    "TSE": "JPY", "JPX": "JPY", "TYO": "JPY",
    "HKEX": "HKD", "HKG": "HKD", "HKSE": "HKD",
    "TWSE": "TWD", "TPE": "TWD",
    "SSE": "CNY", "SZSE": "CNY", "SHA": "CNY", "SHE": "CNY",
    "LSE": "GBP", "LON": "GBP",
    "FSE": "EUR", "FRA": "EUR", "XETRA": "EUR",
}


def infer_currency(proposal: dict) -> str | None:
    """제안에서 통화 코드 추출 — 명시 currency 우선, 없으면 market으로 추론."""
    if proposal.get("currency"):
        return str(proposal["currency"]).upper()
    market = (proposal.get("market") or "").strip().upper()
    return _MARKET_CURRENCY.get(market)


def validate_price(proposal: dict) -> list[str]:
    """제안의 현재가 이상 여부 검증. 이상 플래그 목록 반환."""
    flags: list[str] = []
    price = proposal.get("current_price")
    if price is None:
        return flags
    try:
        price = float(price)
    except (TypeError, ValueError):
        return flags
    if price <= 0:
        flags.append("non_positive_price")
        return flags

    currency = infer_currency(proposal)
    threshold = _PENNY_THRESHOLDS.get(currency) if currency else None
    if threshold is not None and price < threshold:
        flags.append(f"penny_stock(<{threshold}{currency})")

    return flags


# ── 종목 커버리지 검증 ────────────────────────────

def validate_ticker_coverage(all_proposals: list[dict]) -> dict:
    """제안 전체의 티커·가격 커버리지 요약.

    Returns:
        {"total": int,
         "no_price": [{asset_name, ticker, market}],
         "price_anomaly": [{asset_name, ticker, market, flags}],
         "no_ticker": [asset_name]}
    """
    no_price: list[dict] = []
    anomalies: list[dict] = []
    no_ticker: list[str] = []
    total = 0

    for p in all_proposals:
        if p.get("asset_type") != "stock":
            continue
        total += 1
        if not p.get("ticker"):
            no_ticker.append(p.get("asset_name", "?"))
            continue

        item = {
            "asset_name": p.get("asset_name", ""),
            "ticker": p.get("ticker", ""),
            "market": p.get("market", ""),
        }

        if p.get("current_price") is None:
            no_price.append(item)
            continue

        price_flags = validate_price(p) + list(p.get("price_anomaly") or [])
        price_flags = [f for f in price_flags if f]  # 중복 제거 아님 — 플래그 보존
        if price_flags:
            anomalies.append({**item, "flags": sorted(set(price_flags)),
                              "current_price": p.get("current_price")})

    return {
        "total": total,
        "no_price": no_price,
        "price_anomaly": anomalies,
        "no_ticker": no_ticker,
    }


# ── Stage 2 결과 검증 ────────────────────────────

def validate_stage2_completeness(all_proposals: list[dict]) -> dict:
    """Stage 2 결과 상태 집계 (A-1에서 proposal.stage2_status를 기록).

    Returns:
        {"analyzed": int, "ok": int,
         "incomplete": [{asset_name, ticker, missing}],
         "errors": [{asset_name, ticker, error}]}
    """
    analyzed = 0
    ok = 0
    incomplete: list[dict] = []
    errors: list[dict] = []

    for p in all_proposals:
        status = p.get("stage2_status")
        if not status:
            continue
        analyzed += 1
        if status == "ok":
            ok += 1
        elif status == "incomplete":
            incomplete.append({
                "asset_name": p.get("asset_name", ""),
                "ticker": p.get("ticker", ""),
                "missing": p.get("stage2_missing") or [],
            })
        else:  # error / exception
            errors.append({
                "asset_name": p.get("asset_name", ""),
                "ticker": p.get("ticker", ""),
                "status": status,
                "error": p.get("stage2_error", ""),
            })

    return {
        "analyzed": analyzed, "ok": ok,
        "incomplete": incomplete, "errors": errors,
    }


# ── 전체 사건 보고서 생성 (B-3) ──────────────────

def build_incident_report(
    *,
    result: dict,
    ai_query_stats: dict | None = None,
    ticker_validation: dict | None = None,
) -> dict:
    """run 종료 시 표준 사건 보고서 생성.

    Args:
        result: run_pipeline() 반환값 (themes/issues 포함)
        ai_query_stats: {"total": N, "failed": M, "truncated": K, "empty": L}
        ticker_validation: stock_data.validate_krx_tickers() 결과

    Returns:
        {severity, counts, price_anomalies, no_price_tickers,
         stage2_incomplete, stage2_errors, ai_query_issues, ticker_issues}
    """
    all_proposals: list[dict] = []
    for theme in result.get("themes", []):
        for p in theme.get("proposals", []):
            # 참조용 테마명 부착
            p_copy = dict(p)
            p_copy["_theme_name"] = theme.get("theme_name", "")
            all_proposals.append(p_copy)

    coverage = validate_ticker_coverage(all_proposals)
    s2 = validate_stage2_completeness(all_proposals)

    # AI 쿼리 이슈 요약
    aiq = ai_query_stats or {}
    ai_issues = {
        "total": aiq.get("total", 0),
        "failed": aiq.get("failed", 0),
        "truncated_recovered": aiq.get("truncated", 0),
        "empty": aiq.get("empty", 0),
        "timeout": aiq.get("timeout", 0),
    }

    ticker_issues = {
        "corrected": (ticker_validation or {}).get("corrected", 0),
        "invalid": (ticker_validation or {}).get("invalid", 0),
        "details": (ticker_validation or {}).get("details", []),
    }

    # 심각도 판정
    severity = "info"
    critical_count = (
        len(coverage["price_anomaly"])
        + len(s2["errors"])
        + ai_issues["failed"]
        + ai_issues["empty"]
    )
    warn_count = (
        len(coverage["no_price"])
        + len(s2["incomplete"])
        + ai_issues["truncated_recovered"]
        + ticker_issues["invalid"]
    )
    if critical_count > 0:
        severity = "critical"
    elif warn_count > 0:
        severity = "warn"

    return {
        "severity": severity,
        "counts": {
            "themes": len(result.get("themes", [])),
            "issues": len(result.get("issues", [])),
            "proposals_total": coverage["total"],
            "stage2_analyzed": s2["analyzed"],
            "stage2_ok": s2["ok"],
            "price_anomalies": len(coverage["price_anomaly"]),
            "no_price": len(coverage["no_price"]),
            "stage2_incomplete": len(s2["incomplete"]),
            "stage2_errors": len(s2["errors"]),
            "ai_query_failed": ai_issues["failed"],
            "ai_query_truncated": ai_issues["truncated_recovered"],
            "ticker_invalid": ticker_issues["invalid"],
            "ticker_corrected": ticker_issues["corrected"],
        },
        "price_anomalies": coverage["price_anomaly"],
        "no_price_tickers": coverage["no_price"],
        "stage2_incomplete": s2["incomplete"],
        "stage2_errors": s2["errors"],
        "ai_query_issues": ai_issues,
        "ticker_issues": ticker_issues,
    }


def summarize_ai_queries(db_cfg, run_id: int) -> dict:
    """ai_query_archive 에서 run_id 통계 집계 (incident_report에 병합용)."""
    if db_cfg is None or run_id is None:
        return {}
    try:
        from shared.db import get_connection
        conn = get_connection(db_cfg)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT parse_status, COUNT(*)
                       FROM ai_query_archive
                       WHERE run_id = %s
                       GROUP BY parse_status""",
                    (run_id,),
                )
                rows = cur.fetchall()
        finally:
            conn.close()

        stats: dict[str, int] = {}
        total = 0
        for status, cnt in rows:
            stats[status or "unknown"] = int(cnt)
            total += int(cnt)

        return {
            "total": total,
            "failed": stats.get("failed", 0) + stats.get("sdk_error", 0),
            "truncated": stats.get("truncated_recovered", 0),
            "empty": stats.get("empty", 0),
            "timeout": stats.get("timeout_partial", 0) + stats.get("timeout_exhausted", 0),
            "by_status": stats,
        }
    except Exception:
        return {}
