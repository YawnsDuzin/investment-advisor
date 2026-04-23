"""결정적 스크리너 (Stage 1-B2) — Phase 2.

Stage 1-B1이 생성한 투자 스펙(JSON)을 SQL로 변환하여 `stock_universe`에서
후보 종목을 추출한다. AI 호출 없음. <1초 목표.

설계 참조: _docs/20260422172248_recommendation-engine-redesign.md §2.2

스펙 스키마 (Stage 1-B1 출력 → screener 입력):
    {
        "theme_key": str,
        "thesis": str,
        "value_chain_tier": ["primary"|"secondary"|"tertiary", ...],
        "sector_norm": ["semiconductors", ...],
        "market_cap_bucket": ["small", "mid", ...],
        "market_cap_range_krw": [low_int, high_int],
        "required_keywords": [str, ...],     # asset_name / industry / aliases ILIKE 매칭
        "exclude_keywords": [str, ...],      # 위와 동일하되 제외
        "quality_filters": {
            "min_roe_pct": float | null,     # 미사용 (universe에 ROE 미보관)
            "max_debt_ratio_pct": float | null,  # 미사용
        },
        "expected_catalyst_window_months": int,
        "max_candidates": int,
        "markets": ["KOSPI", "KOSDAQ", "NASDAQ", "NYSE"],  # 옵션
    }

반환 후보 스키마:
    {
        "ticker": str,
        "market": str,
        "asset_name": str,
        "sector_norm": str,
        "market_cap_krw": int,
        "market_cap_bucket": str,
        "last_price": float,
        "last_price_ccy": str,
        "industry": str | None,
        "screener_match_reason": str,        # ILIKE 매칭된 키워드 등 디버깅용
    }
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from psycopg2.extras import RealDictCursor

from shared.config import DatabaseConfig, ScreenerConfig
from shared.db import get_connection
from shared.logger import get_logger


_log = get_logger("screener")


# ── 매칭 컬럼 ───────────────────────────────────────
# ILIKE 매칭 시 검사할 컬럼들. aliases는 JSONB라 별도 처리.
# sector_krx는 한글 업종명(예: "전기·전자", "화학", "제약")이라 한글 키워드 매칭에 필수.
# industry는 universe_sync가 채우지 못하는 경우가 많아(pykrx sector API 한계)
# sector_krx가 실질적인 한글 매칭 앵커 역할을 한다.
_TEXT_MATCH_COLUMNS = ("asset_name", "asset_name_en", "industry", "sector_krx")


@dataclass
class ScreenResult:
    """스크리너 실행 결과 — 후보 목록 + 메타."""
    candidates: list[dict]
    matched_count: int
    fallback_applied: list[str]          # 적용된 fallback 단계 설명
    spec_used: dict                      # 최종 fallback 후 사용된 스펙 (로깅/감사용)
    duration_sec: float


def _quote_like(s: str) -> str:
    """ILIKE 패턴 안전화 — % 와 _ 이스케이프."""
    return s.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


def _build_where_clauses(
    spec: dict,
    *,
    cfg: ScreenerConfig | None = None,
    include_ohlcv_filters: bool = False,
) -> tuple[str, list]:
    """스펙 → SQL WHERE 절 + 파라미터 리스트.

    Args:
        include_ohlcv_filters: True면 ohlcv_metrics 서브쿼리(`m.*`)를 참조하는 조건 추가.
            호출부는 FROM 절에 `LEFT JOIN ohlcv_metrics m` 가 있어야 한다.
        cfg: ScreenerConfig — OHLCV 임계값 주입. None이면 기본값.

    Returns:
        (where_sql, params)
    """
    where: list[str] = ["u.listed = TRUE", "u.has_preferred = FALSE"]
    params: list = []

    markets = spec.get("markets")
    if markets:
        where.append("u.market = ANY(%s)")
        params.append(list(markets))

    sectors = spec.get("sector_norm")
    if sectors:
        where.append("u.sector_norm = ANY(%s)")
        params.append(list(sectors))

    mcap_range = spec.get("market_cap_range_krw")
    if mcap_range and len(mcap_range) == 2:
        low, high = mcap_range
        if low is not None and low > 0:
            where.append("u.market_cap_krw >= %s")
            params.append(int(low))
        if high is not None and high > 0:
            where.append("u.market_cap_krw <= %s")
            params.append(int(high))

    buckets = spec.get("market_cap_bucket")
    if buckets:
        where.append("u.market_cap_bucket = ANY(%s)")
        params.append(list(buckets))

    # required_keywords: 적어도 하나는 매칭되어야 함 (asset_name / asset_name_en / industry / aliases)
    required = [k for k in (spec.get("required_keywords") or []) if k and k.strip()]
    if required:
        kw_clauses: list[str] = []
        for kw in required:
            pat = f"%{_quote_like(kw)}%"
            sub_clauses = [f"COALESCE(u.{col}, '') ILIKE %s" for col in _TEXT_MATCH_COLUMNS]
            # JSONB aliases 검사 — JSONB::text ILIKE
            sub_clauses.append("COALESCE(u.aliases::text, '') ILIKE %s")
            kw_clauses.append("(" + " OR ".join(sub_clauses) + ")")
            params.extend([pat] * (len(_TEXT_MATCH_COLUMNS) + 1))
        where.append("(" + " OR ".join(kw_clauses) + ")")

    # exclude_keywords: 모두 매칭되지 않아야 함
    excluded = [k for k in (spec.get("exclude_keywords") or []) if k and k.strip()]
    if excluded:
        for kw in excluded:
            pat = f"%{_quote_like(kw)}%"
            sub_clauses = [f"COALESCE(u.{col}, '') NOT ILIKE %s" for col in _TEXT_MATCH_COLUMNS]
            sub_clauses.append("COALESCE(u.aliases::text, '') NOT ILIKE %s")
            where.append("(" + " AND ".join(sub_clauses) + ")")
            params.extend([pat] * (len(_TEXT_MATCH_COLUMNS) + 1))

    # ── OHLCV 이력 필터 (로드맵 A2) ──
    # OHLCV 결측 종목은 m.* IS NULL → 조건 관대하게 통과 (백필 이전 호환)
    if include_ohlcv_filters and cfg is not None:
        # 유동성 (시장별 통화 분기)
        if cfg.min_daily_value_krw > 0 or cfg.min_daily_value_usd > 0:
            where.append(
                "(m.avg_daily_value IS NULL OR "
                " (u.market IN ('KOSPI', 'KOSDAQ', 'KONEX') AND m.avg_daily_value >= %s)"
                " OR (u.market IN ('NASDAQ', 'NYSE', 'AMEX') AND m.avg_daily_value >= %s)"
                " OR u.market NOT IN ('KOSPI', 'KOSDAQ', 'KONEX', 'NASDAQ', 'NYSE', 'AMEX'))"
            )
            params.extend([int(cfg.min_daily_value_krw), int(cfg.min_daily_value_usd)])

        # 60일 고점 대비 낙폭 — (latest_close / high_60d) >= (1 - max_drawdown/100)
        if cfg.max_drawdown_60d_pct > 0:
            threshold = 1.0 - (cfg.max_drawdown_60d_pct / 100.0)
            where.append(
                "(m.high_60d IS NULL OR m.latest_close IS NULL OR m.high_60d <= 0 OR "
                " (m.latest_close / m.high_60d) >= %s)"
            )
            params.append(threshold)

    where_sql = " AND ".join(where)
    return where_sql, params


def _ohlcv_metrics_cte(window_days: int) -> tuple[str, list]:
    """stock_universe_ohlcv 기반 종목별 메트릭 CTE.

    윈도우: 최근 window_days (기본 90) 거래일.
    60일 고점(high_60d)은 최근 60일, 유동성(avg_daily_value)은 최근 60일.
    latest_close는 윈도우 내 가장 최근 trade_date의 close.

    Returns:
        (cte_sql, params)  — cte_sql은 'WITH ohlcv_metrics AS (...)' 형태
    """
    sql = """
    WITH
    ohlcv_base AS (
        SELECT ticker, market, trade_date, close, volume,
               ROW_NUMBER() OVER (PARTITION BY ticker, market ORDER BY trade_date DESC) AS rn
        FROM stock_universe_ohlcv
        WHERE trade_date >= CURRENT_DATE - (%s::int)
    ),
    ohlcv_metrics AS (
        SELECT ticker, market,
               AVG(close * volume) FILTER (WHERE rn <= 60) AS avg_daily_value,
               MAX(close) FILTER (WHERE rn <= 60) AS high_60d,
               MAX(CASE WHEN rn = 1 THEN close END) AS latest_close
        FROM ohlcv_base
        GROUP BY ticker, market
    )
    """
    return sql, [int(window_days)]


def _execute_screen(
    db_cfg: DatabaseConfig,
    spec: dict,
    *,
    limit: int,
    cfg: ScreenerConfig | None = None,
    include_ohlcv_filters: bool = False,
) -> list[dict]:
    """단일 SELECT 실행. fallback 없음.

    Args:
        include_ohlcv_filters: True면 OHLCV 메트릭 CTE + LEFT JOIN 추가 후 필터 적용.
    """
    where_sql, where_params = _build_where_clauses(
        spec, cfg=cfg, include_ohlcv_filters=include_ohlcv_filters
    )

    if include_ohlcv_filters and cfg is not None:
        cte_sql, cte_params = _ohlcv_metrics_cte(cfg.ohlcv_window_days)
        sql = f"""
            {cte_sql}
            SELECT u.ticker, u.market, u.asset_name, u.asset_name_en, u.sector_norm,
                   u.sector_krx, u.sector_gics, u.industry, u.market_cap_krw,
                   u.market_cap_bucket, u.last_price, u.last_price_ccy, u.last_price_at,
                   u.aliases
            FROM stock_universe u
            LEFT JOIN ohlcv_metrics m
              ON UPPER(u.ticker) = UPPER(m.ticker) AND UPPER(u.market) = UPPER(m.market)
            WHERE {where_sql}
            ORDER BY u.market_cap_krw ASC NULLS LAST
            LIMIT %s
        """
        params_with_limit = cte_params + where_params + [int(limit)]
    else:
        sql = f"""
            SELECT u.ticker, u.market, u.asset_name, u.asset_name_en, u.sector_norm,
                   u.sector_krx, u.sector_gics, u.industry, u.market_cap_krw,
                   u.market_cap_bucket, u.last_price, u.last_price_ccy, u.last_price_at,
                   u.aliases
            FROM stock_universe u
            WHERE {where_sql}
            ORDER BY u.market_cap_krw ASC NULLS LAST
            LIMIT %s
        """
        params_with_limit = where_params + [int(limit)]

    conn = get_connection(db_cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params_with_limit)
            rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    # match reason annotation
    required = [k for k in (spec.get("required_keywords") or []) if k and k.strip()]
    for r in rows:
        matched: list[str] = []
        haystack = " | ".join([
            str(r.get("asset_name") or ""),
            str(r.get("asset_name_en") or ""),
            str(r.get("industry") or ""),
            str(r.get("sector_krx") or ""),
            str(r.get("aliases") or ""),
        ]).lower()
        for kw in required:
            if kw.lower() in haystack:
                matched.append(kw)
        r["screener_match_reason"] = ",".join(matched) if matched else "sector/cap_only"

    return rows


def _expand_market_cap_range(spec: dict, expand_pct: int) -> dict | None:
    """market_cap_range를 ±expand_pct% 확장. 변경 사항 없으면 None."""
    mcap = spec.get("market_cap_range_krw")
    if not mcap or len(mcap) != 2:
        return None
    low, high = mcap
    new_low = int(low * (1 - expand_pct / 100)) if low else low
    new_high = int(high * (1 + expand_pct / 100)) if high else high
    if new_low == low and new_high == high:
        return None
    out = dict(spec)
    out["market_cap_range_krw"] = [new_low, new_high]
    return out


def _drop_one_required_keyword(spec: dict) -> dict | None:
    """required_keywords에서 마지막 키워드 1개 제거. 비어 있으면 None."""
    required = list(spec.get("required_keywords") or [])
    if not required:
        return None
    out = dict(spec)
    out["required_keywords"] = required[:-1]
    return out


def screen(
    db_cfg: DatabaseConfig,
    spec: dict,
    *,
    cfg: ScreenerConfig | None = None,
) -> ScreenResult:
    """스펙 → 후보 추출. 매칭 부족 시 fallback 단계적 적용.

    Fallback 순서 (계획서 §2.2):
        1. market_cap_range를 ±expand_pct% 확장 (반복적용 가능)
        2. required_keywords 마지막 키워드 1개 제거
        3. 위 1단계 다시 시도
        ... 최대 max_retries회 반복 후 실패

    Args:
        cfg: ScreenerConfig (None이면 기본값 사용)

    Returns:
        ScreenResult — candidates 비어 있으면 매칭 실패
    """
    import time
    if cfg is None:
        cfg = ScreenerConfig()

    started = time.time()
    max_candidates = int(spec.get("max_candidates") or cfg.candidates_max)

    fallback_log: list[str] = []
    current_spec = dict(spec)
    # 1차: OHLCV 필터 ON (cfg.ohlcv_filters_enabled 기본 True)
    use_ohlcv = bool(cfg.ohlcv_filters_enabled)
    rows = _execute_screen(
        db_cfg, current_spec, limit=max_candidates,
        cfg=cfg, include_ohlcv_filters=use_ohlcv,
    )

    retries = 0
    while not rows and retries < cfg.spec_screener_max_retries:
        # Try 1: expand market cap range
        expanded = _expand_market_cap_range(current_spec, cfg.spec_screener_fallback_expand_pct)
        if expanded:
            current_spec = expanded
            fallback_log.append(f"market_cap_range ±{cfg.spec_screener_fallback_expand_pct}% 확장")
            rows = _execute_screen(
                db_cfg, current_spec, limit=max_candidates,
                cfg=cfg, include_ohlcv_filters=use_ohlcv,
            )
            retries += 1
            if rows:
                break

        # Try 2: drop one required keyword
        dropped = _drop_one_required_keyword(current_spec)
        if dropped:
            dropped_kw = (current_spec.get("required_keywords") or [])[-1]
            current_spec = dropped
            fallback_log.append(f"required_keyword 제거: '{dropped_kw}'")
            rows = _execute_screen(
                db_cfg, current_spec, limit=max_candidates,
                cfg=cfg, include_ohlcv_filters=use_ohlcv,
            )
            retries += 1
            if rows:
                break
        else:
            # required_keywords가 이미 비어있음 → 마지막 수단: market_cap_range도 제거하고 sector/listed만으로 시도
            if current_spec.get("market_cap_range_krw"):
                current_spec = dict(current_spec)
                current_spec.pop("market_cap_range_krw", None)
                fallback_log.append("market_cap_range 제거 (sector만으로 최종 시도)")
                rows = _execute_screen(
                    db_cfg, current_spec, limit=max_candidates,
                    cfg=cfg, include_ohlcv_filters=use_ohlcv,
                )
                retries += 1
                if rows:
                    break
            # 최후의 수단: OHLCV 필터 해제 (백필 결측·지나치게 엄격한 임계치 보호)
            if use_ohlcv:
                use_ohlcv = False
                fallback_log.append("OHLCV 필터 해제 (최후 수단)")
                rows = _execute_screen(
                    db_cfg, current_spec, limit=max_candidates,
                    cfg=cfg, include_ohlcv_filters=False,
                )
                retries += 1
                if rows:
                    break
            fallback_log.append("모든 fallback 소진")
            break

    duration = time.time() - started

    if not rows:
        _log.warning(
            f"[screener] '{spec.get('theme_key', '?')}' — 매칭 0건 "
            f"(fallback 시도 {retries}회: {fallback_log})"
        )
    else:
        msg = f"[screener] '{spec.get('theme_key', '?')}' — {len(rows)}건 매칭"
        if fallback_log:
            msg += f" (fallback 적용: {fallback_log})"
        msg += f" / {duration*1000:.0f}ms"
        _log.info(msg)

    return ScreenResult(
        candidates=rows,
        matched_count=len(rows),
        fallback_applied=fallback_log,
        spec_used=current_spec,
        duration_sec=duration,
    )


# ── 후보 → AI 프롬프트용 텍스트 변환 ──────────────────

def candidates_to_prompt_table(candidates: Iterable[dict], *, max_rows: int = 25) -> str:
    """Stage 1-B3 프롬프트에 삽입할 후보 표 마크다운 생성."""
    rows = list(candidates)[:max_rows]
    if not rows:
        return "(후보 없음)"
    lines = [
        "| ticker | market | name | sector_norm | market_cap | last_price | match |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in rows:
        mcap = c.get("market_cap_krw")
        mcap_text = f"{int(mcap)/1e12:.2f}조원" if mcap and mcap > 1e12 else (
            f"{int(mcap)/1e8:.0f}억원" if mcap else "-"
        )
        price = c.get("last_price")
        ccy = c.get("last_price_ccy") or ""
        price_text = f"{float(price):,.2f}{ccy}" if price else "-"
        lines.append(
            f"| {c.get('ticker', '')} | {c.get('market', '')} | "
            f"{(c.get('asset_name') or '')[:30]} | {c.get('sector_norm') or '-'} | "
            f"{mcap_text} | {price_text} | {c.get('screener_match_reason') or '-'} |"
        )
    return "\n".join(lines)
