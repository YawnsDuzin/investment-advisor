"""펀더 시계열 인사이트 (Tier 2 — Stage 2 컨텍스트 보강).

`stock_universe_fundamentals` (v39, KR + US PIT 시계열) 에서:
  - PER/PBR latest + 12M 분포 mean/std/percentile (역사적 고/저평가 위치)
  - EPS YoY (12개월 전 EPS 대비 변화율)
  - 배당률 latest

를 batch 로 계산하여 Stage 2 정량 섹션에 합류시킨다.

설계 원칙
- factor_engine 과 동일한 패턴 — compute / format / proposal 에 snapshot 영속화
- AI 가 "PER 추정" 하지 않도록 latest + 12M percentile 함께 제공
- KR (pykrx PER/PBR) + US (yfinance trailingPE/priceToBook) 모두 커버
- 결측 대응: per/pbr 둘 다 NULL 이면 결과에서 제외 (의미 없음)

공개 API
- compute_fundamentals_snapshots(db_cfg, [(ticker, market)]) -> dict
- format_fundamentals_text(snap) -> str  (STAGE2 프롬프트 삽입)
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Iterable
from zoneinfo import ZoneInfo

from psycopg2.extras import RealDictCursor

from shared.config import DatabaseConfig
from shared.db import get_connection
from shared.logger import get_logger

_KST = ZoneInfo("Asia/Seoul")
_log = get_logger("fundamentals_engine")

# 12개월 윈도우 (거래일 ≈ 252일, 여유 포함)
_DEFAULT_WINDOW_DAYS = 270

# 12M 분포 percentile 계산 시 최소 표본 수 — 미만이면 percentile NULL
_MIN_SAMPLE_FOR_PCTILE = 30


def compute_fundamentals_snapshots(
    db_cfg: DatabaseConfig,
    tickers: Iterable[tuple[str, str]],
    *,
    window_days: int = _DEFAULT_WINDOW_DAYS,
) -> dict[tuple[str, str], dict]:
    """종목별 펀더 시계열 snapshot batch.

    Args:
        tickers: [(ticker_upper, market), ...]
        window_days: PIT 조회 윈도우 (기본 270 — 12M percentile 위해)

    Returns:
        {(ticker_upper, market_upper): {
            "snapshot_date": ISO date | None,
            "per_latest": float | None,
            "per_12m_mean": float | None,
            "per_12m_pctile": float | None,   # 0~1, 1이 분포 상위(고평가)
            "per_12m_top_pct": int | None,    # "분포 상위 N%" 표기용 (N=낮을수록 비싸다)
            "pbr_latest": float | None,
            "pbr_12m_mean": float | None,
            "pbr_12m_pctile": float | None,
            "pbr_12m_top_pct": int | None,
            "eps_latest": float | None,
            "eps_12m_ago": float | None,
            "eps_yoy_pct": float | None,
            "dividend_yield_latest": float | None,
            "sample_size": int,
            "computed_at": ISO datetime,
        }}
        per/pbr 모두 NULL 이거나 row 없는 종목은 제외.
    """
    pairs = [
        (t.strip().upper(), (m or "").strip().upper())
        for t, m in tickers
    ]
    pairs = [(t, m) for t, m in pairs if t and m]
    if not pairs:
        return {}

    sql = """
    WITH targets(ticker, market) AS (
        SELECT * FROM UNNEST(%s::text[], %s::text[])
    ),
    ranked AS (
        SELECT UPPER(f.ticker) AS ticker,
               UPPER(f.market) AS market,
               f.snapshot_date,
               f.per::float AS per,
               f.pbr::float AS pbr,
               f.eps::float AS eps,
               f.dividend_yield::float AS dy,
               ROW_NUMBER() OVER (
                   PARTITION BY UPPER(f.ticker), UPPER(f.market)
                   ORDER BY f.snapshot_date DESC
               ) AS rn
        FROM stock_universe_fundamentals f
        JOIN targets t
          ON UPPER(f.ticker) = UPPER(t.ticker)
         AND UPPER(f.market) = UPPER(t.market)
        WHERE f.snapshot_date >= CURRENT_DATE - (%s::int)
    ),
    agg AS (
        SELECT ticker, market,
               MAX(CASE WHEN rn = 1   THEN snapshot_date END) AS snapshot_date,
               MAX(CASE WHEN rn = 1   THEN per END)           AS per_latest,
               MAX(CASE WHEN rn = 1   THEN pbr END)           AS pbr_latest,
               MAX(CASE WHEN rn = 1   THEN eps END)           AS eps_latest,
               MAX(CASE WHEN rn = 1   THEN dy END)            AS dy_latest,
               -- 12M ≈ 252 거래일. snapshot_date 가 일별 sync 일 때 정확하게 252.
               -- sync 누락일 있어도 ±5 tolerance 로 가까운 행 채택.
               MAX(CASE WHEN rn BETWEEN 247 AND 257 THEN eps END) AS eps_12m_ago,
               AVG(per) FILTER (WHERE per IS NOT NULL AND per > 0)  AS per_12m_mean,
               AVG(pbr) FILTER (WHERE pbr IS NOT NULL AND pbr > 0)  AS pbr_12m_mean,
               COUNT(*) FILTER (WHERE per IS NOT NULL AND per > 0)  AS per_n,
               COUNT(*) FILTER (WHERE pbr IS NOT NULL AND pbr > 0)  AS pbr_n,
               COUNT(*) AS sample_size
        FROM ranked
        GROUP BY ticker, market
    ),
    pctile_per AS (
        SELECT ticker, market,
               PERCENT_RANK() OVER (PARTITION BY ticker, market ORDER BY per NULLS FIRST) AS per_pctile,
               rn
        FROM ranked
        WHERE per IS NOT NULL AND per > 0
    ),
    pctile_pbr AS (
        SELECT ticker, market,
               PERCENT_RANK() OVER (PARTITION BY ticker, market ORDER BY pbr NULLS FIRST) AS pbr_pctile,
               rn
        FROM ranked
        WHERE pbr IS NOT NULL AND pbr > 0
    ),
    latest_pctile AS (
        SELECT a.ticker, a.market,
               (SELECT per_pctile FROM pctile_per p
                WHERE p.ticker = a.ticker AND p.market = a.market AND p.rn = 1)  AS per_latest_pctile,
               (SELECT pbr_pctile FROM pctile_pbr p
                WHERE p.ticker = a.ticker AND p.market = a.market AND p.rn = 1)  AS pbr_latest_pctile
        FROM agg a
    )
    SELECT a.ticker, a.market, a.snapshot_date,
           a.per_latest, a.per_12m_mean, lp.per_latest_pctile, a.per_n,
           a.pbr_latest, a.pbr_12m_mean, lp.pbr_latest_pctile, a.pbr_n,
           a.eps_latest, a.eps_12m_ago, a.dy_latest, a.sample_size
    FROM agg a
    LEFT JOIN latest_pctile lp
      ON lp.ticker = a.ticker AND lp.market = a.market
    WHERE a.per_latest IS NOT NULL OR a.pbr_latest IS NOT NULL
    """

    started = time.time()
    tickers_arr = [t for t, _ in pairs]
    markets_arr = [m for _, m in pairs]

    conn = get_connection(db_cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (tickers_arr, markets_arr, int(window_days)))
            rows = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        _log.warning(f"[fundamentals_engine] batch 집계 실패: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return {}
    finally:
        conn.close()

    computed_at = datetime.now(_KST).isoformat(timespec="seconds")
    out: dict[tuple[str, str], dict] = {}

    def _fnone(v, digits=2):
        return round(float(v), digits) if v is not None else None

    def _pctile_to_top_pct(p: float | None) -> int | None:
        if p is None:
            return None
        # PER/PBR 분포에서 percentile=0 은 가장 낮은(저평가), 1 은 가장 높은(고평가).
        # "현재 분포 상위 N%" = (1 - percentile) × 100.
        # 사용자 표시는 "상위 N% (싼 쪽)" 같은 직관 라벨로 format 단계에서 처리.
        return int(round((1.0 - float(p)) * 100))

    for r in rows:
        per_n = int(r.get("per_n") or 0)
        pbr_n = int(r.get("pbr_n") or 0)

        per_pctile = r.get("per_latest_pctile")
        if per_n < _MIN_SAMPLE_FOR_PCTILE:
            per_pctile = None
        pbr_pctile = r.get("pbr_latest_pctile")
        if pbr_n < _MIN_SAMPLE_FOR_PCTILE:
            pbr_pctile = None

        eps_latest = r.get("eps_latest")
        eps_prev = r.get("eps_12m_ago")
        eps_yoy = None
        if eps_latest is not None and eps_prev is not None and float(eps_prev) != 0:
            eps_yoy = round((float(eps_latest) - float(eps_prev)) / abs(float(eps_prev)) * 100, 2)

        snap = {
            "snapshot_date": r["snapshot_date"].isoformat() if r.get("snapshot_date") else None,
            "per_latest": _fnone(r.get("per_latest")),
            "per_12m_mean": _fnone(r.get("per_12m_mean")),
            "per_12m_pctile": _fnone(per_pctile, 4),
            "per_12m_top_pct": _pctile_to_top_pct(per_pctile),
            "pbr_latest": _fnone(r.get("pbr_latest")),
            "pbr_12m_mean": _fnone(r.get("pbr_12m_mean")),
            "pbr_12m_pctile": _fnone(pbr_pctile, 4),
            "pbr_12m_top_pct": _pctile_to_top_pct(pbr_pctile),
            "eps_latest": _fnone(eps_latest, 4),
            "eps_12m_ago": _fnone(eps_prev, 4),
            "eps_yoy_pct": eps_yoy,
            "dividend_yield_latest": _fnone(r.get("dy_latest"), 3),
            "sample_size": int(r.get("sample_size") or 0),
            "computed_at": computed_at,
        }
        out[(r["ticker"], r["market"])] = snap

    _log.info(
        f"[fundamentals_engine] snapshot {len(out)}/{len(pairs)}건 "
        f"({(time.time() - started) * 1000:.0f}ms)"
    )
    return out


def _pctile_label(top_pct: int | None) -> str:
    """top_pct → 직관 라벨. 0이 가장 비싸다(역사적 고평가), 100이 가장 싸다."""
    if top_pct is None:
        return "-"
    # "분포 상위 X% (X 작을수록 고평가, X 클수록 저평가)" 가 직관적.
    # AI 가 해석하기 쉽도록 "고평가/저평가" 라벨도 함께 줌.
    if top_pct <= 20:
        tone = " — 12M 고평가 구간"
    elif top_pct >= 80:
        tone = " — 12M 저평가 구간"
    else:
        tone = ""
    return f"분포 상위 {top_pct}%{tone}"


def format_fundamentals_text(snap: dict) -> str:
    """Stage 2 프롬프트 삽입용 한글 텍스트.

    AI 가 그대로 인용하여 "PER 12 — 12M 저평가 구간" 식 해석을 작성하도록 구성.
    """
    if not snap:
        return ""

    per = snap.get("per_latest")
    per_mean = snap.get("per_12m_mean")
    per_top = snap.get("per_12m_top_pct")
    pbr = snap.get("pbr_latest")
    pbr_mean = snap.get("pbr_12m_mean")
    pbr_top = snap.get("pbr_12m_top_pct")
    eps_yoy = snap.get("eps_yoy_pct")
    dy = snap.get("dividend_yield_latest")
    sd = snap.get("snapshot_date")
    n = snap.get("sample_size")

    body_lines: list[str] = []

    if per is not None:
        parts = [f"PER {per:.2f}"]
        if per_mean is not None:
            parts.append(f"12M 평균 {per_mean:.2f}")
        if per_top is not None:
            parts.append(_pctile_label(per_top))
        body_lines.append("- " + " / ".join(parts))

    if pbr is not None:
        parts = [f"PBR {pbr:.2f}"]
        if pbr_mean is not None:
            parts.append(f"12M 평균 {pbr_mean:.2f}")
        if pbr_top is not None:
            parts.append(_pctile_label(pbr_top))
        body_lines.append("- " + " / ".join(parts))

    if eps_yoy is not None:
        sign = "+" if eps_yoy > 0 else ""
        body_lines.append(f"- EPS YoY: {sign}{eps_yoy:.2f}% (1년 전 EPS 대비)")

    if dy is not None:
        body_lines.append(f"- 배당수익률 latest: {dy:.2f}%")

    # 본문 라인이 0건이면 헤더·메타 출력해봐야 무의미 → 빈 문자열
    if not body_lines:
        return ""

    lines: list[str] = ["### 펀더멘털 시계열 (DB PIT 산출 — 그대로 인용)"]
    lines.extend(body_lines)

    if sd or n:
        meta = []
        if sd:
            meta.append(f"기준일 {sd}")
        if n:
            meta.append(f"표본 {n}일")
        lines.append(f"- ({' / '.join(meta)})")

    return "\n".join(lines)
