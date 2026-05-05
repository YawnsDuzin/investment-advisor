"""섹터 로테이션 시그널 (Tier 1 — 인사이트 보강).

Stage 1-A2 (테마 발굴) 진입 전에 sector_norm 별 1m/3m/6m 평균 수익률 +
20일 시장폭 (breadth) 을 사전 계산하여 프롬프트 컨텍스트로 주입한다.
AI 가 "어느 섹터가 회전 중"인지 매번 추론하지 않도록 실측값을 제공.

집계 단위
  - 시장 그룹: KRX(KOSPI+KOSDAQ+KONEX) / US(NYSE+NASDAQ+AMEX) 분리
  - 섹터 단위: stock_universe.sector_norm
  - min sample size: 5종목 미만 섹터는 제외 (cross-section 의미 없음)

공개 API
  - compute_sector_rotation(db_cfg) -> dict
  - format_sector_rotation_text(snap) -> str  (Stage 1-A2 프롬프트 삽입용)
  - infer_rotation_hint(snap) -> str          (한 줄 요약, 헤더 가이드)
"""
from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from psycopg2.extras import RealDictCursor

from shared.config import DatabaseConfig
from shared.db import get_connection
from shared.logger import get_logger

_KST = ZoneInfo("Asia/Seoul")
_log = get_logger("sector_rotation")


_MARKET_GROUPS: dict[str, tuple[str, ...]] = {
    "KRX": ("KOSPI", "KOSDAQ", "KONEX"),
    "US": ("NASDAQ", "NYSE", "AMEX"),
}

_GROUP_LABELS = {"KRX": "한국", "US": "미국"}

# 섹터 표본이 이 미만이면 cross-section 의미가 약하므로 제외
_MIN_SAMPLE_SIZE = 5

# 텍스트 출력 시 강세·약세 섹터 노출 개수
_TOP_N_LEADING = 5
_TOP_N_LAGGING = 5

# 데이터 윈도우 — 6m 수익률(132 거래일)에 여유 포함
_DEFAULT_WINDOW_DAYS = 200


def _compute_group(db_cfg: DatabaseConfig, members: tuple[str, ...],
                   *, window_days: int) -> dict | None:
    """단일 시장 그룹 sector cross-section 집계."""
    sql = """
    WITH ranked AS (
        SELECT o.ticker, UPPER(o.market) AS market, o.trade_date,
               o.close::float AS close,
               u.sector_norm,
               ROW_NUMBER() OVER (
                   PARTITION BY o.ticker, UPPER(o.market)
                   ORDER BY o.trade_date DESC
               ) AS rn
        FROM stock_universe_ohlcv o
        JOIN stock_universe u
          ON UPPER(u.ticker) = UPPER(o.ticker)
         AND UPPER(u.market) = UPPER(o.market)
        WHERE o.trade_date >= CURRENT_DATE - (%s::int)
          AND UPPER(o.market) = ANY(%s)
          AND u.listed = TRUE
          AND u.sector_norm IS NOT NULL
    ),
    ticker_endpoints AS (
        SELECT ticker, market, sector_norm,
               MAX(CASE WHEN rn = 1   THEN close END) AS c_latest,
               MAX(CASE WHEN rn = 20  THEN close END) AS c_20d,
               MAX(CASE WHEN rn = 22  THEN close END) AS c_1m,
               MAX(CASE WHEN rn = 66  THEN close END) AS c_3m,
               MAX(CASE WHEN rn = 132 THEN close END) AS c_6m
        FROM ranked
        GROUP BY ticker, market, sector_norm
    ),
    ticker_factors AS (
        SELECT sector_norm,
               CASE WHEN c_1m  IS NOT NULL AND c_1m  > 0 THEN (c_latest - c_1m)  / c_1m  * 100 END AS r1m,
               CASE WHEN c_3m  IS NOT NULL AND c_3m  > 0 THEN (c_latest - c_3m)  / c_3m  * 100 END AS r3m,
               CASE WHEN c_6m  IS NOT NULL AND c_6m  > 0 THEN (c_latest - c_6m)  / c_6m  * 100 END AS r6m,
               CASE WHEN c_20d IS NOT NULL AND c_20d > 0 THEN 1 ELSE 0 END AS valid_20d,
               CASE WHEN c_20d IS NOT NULL AND c_20d > 0 AND c_latest > c_20d THEN 1 ELSE 0 END AS up_20d
        FROM ticker_endpoints
        WHERE c_latest IS NOT NULL
    )
    SELECT sector_norm,
           COUNT(*) AS sample_size,
           AVG(r1m) AS r1m_avg,
           AVG(r3m) AS r3m_avg,
           AVG(r6m) AS r6m_avg,
           SUM(up_20d)::float AS up_count,
           SUM(valid_20d)::float AS valid_count
    FROM ticker_factors
    GROUP BY sector_norm
    HAVING COUNT(*) >= %s
    ORDER BY AVG(r1m) DESC NULLS LAST
    """
    conn = get_connection(db_cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (int(window_days), list(members), _MIN_SAMPLE_SIZE))
            rows = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        _log.warning(f"[sector_rotation/{members}] 집계 실패: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    finally:
        conn.close()

    if not rows:
        return None

    sectors: list[dict] = []
    for r in rows:
        sample = int(r["sample_size"] or 0)
        valid_20 = int(r["valid_count"] or 0)
        up = int(r["up_count"] or 0)
        breadth = round(up / valid_20 * 100, 1) if valid_20 > 0 else None
        sectors.append({
            "sector": r["sector_norm"],
            "sample_size": sample,
            "r1m_avg_pct": round(float(r["r1m_avg"]), 2) if r["r1m_avg"] is not None else None,
            "r3m_avg_pct": round(float(r["r3m_avg"]), 2) if r["r3m_avg"] is not None else None,
            "r6m_avg_pct": round(float(r["r6m_avg"]), 2) if r["r6m_avg"] is not None else None,
            "breadth_20d_pct": breadth,
        })

    leading = [s["sector"] for s in sectors[:_TOP_N_LEADING]
               if s["r1m_avg_pct"] is not None]
    lagging = [s["sector"] for s in list(reversed(sectors))[:_TOP_N_LAGGING]
               if s["r1m_avg_pct"] is not None]

    return {
        "sector_count": len(sectors),
        "sectors": sectors,
        "leading_sectors": leading,
        "lagging_sectors": lagging,
    }


def compute_sector_rotation(db_cfg: DatabaseConfig,
                            *, window_days: int = _DEFAULT_WINDOW_DAYS) -> dict:
    """전체 섹터 로테이션 스냅샷.

    Returns:
        {
          "computed_at": ISO datetime,
          "groups": {
              "KRX": {sector_count, sectors[...], leading_sectors[...], lagging_sectors[...]},
              "US":  {...}
          }
        }
        그룹별 데이터가 전무하면 빈 dict {}.
    """
    started = time.time()
    groups: dict[str, dict] = {}
    for grp, members in _MARKET_GROUPS.items():
        result = _compute_group(db_cfg, members, window_days=window_days)
        if result:
            groups[grp] = result

    if not groups:
        _log.info("[sector_rotation] 데이터 없음 — 빈 스냅샷 반환")
        return {}

    snap = {
        "computed_at": datetime.now(_KST).isoformat(timespec="seconds"),
        "groups": groups,
    }
    summary = " / ".join(
        f"{grp}={data['sector_count']}섹터" for grp, data in groups.items()
    )
    _log.info(
        f"[sector_rotation] 계산 완료 — {summary} ({(time.time() - started) * 1000:.0f}ms)"
    )
    return snap


def format_sector_rotation_text(snap: dict) -> str:
    """Stage 1-A2 프롬프트 삽입용 한글 텍스트.

    각 시장 그룹별 강세 5섹터 + 약세 5섹터 + breadth 표시.
    AI 가 그대로 인용하여 테마/시나리오에 반영하도록 구성.
    """
    if not snap or not snap.get("groups"):
        return ""

    lines: list[str] = []
    for grp, data in snap["groups"].items():
        label = _GROUP_LABELS.get(grp, grp)
        sectors = data.get("sectors") or []
        if not sectors:
            continue

        leading = sectors[:_TOP_N_LEADING]
        lagging = list(reversed(sectors))[:_TOP_N_LAGGING]

        lines.append(f"### {label} 시장 ({grp}) — {len(sectors)}개 섹터 cross-section")

        lines.append("- 강세 섹터 (1M 평균 수익률 상위):")
        for s in leading:
            r1 = s.get("r1m_avg_pct")
            r3 = s.get("r3m_avg_pct")
            br = s.get("breadth_20d_pct")
            parts = [f"  · {s['sector']} (n={s['sample_size']})"]
            if r1 is not None:
                parts.append(f"1M {r1:+.2f}%")
            if r3 is not None:
                parts.append(f"3M {r3:+.2f}%")
            if br is not None:
                parts.append(f"20D 상승비율 {br:.0f}%")
            lines.append(" / ".join(parts))

        lines.append("- 약세 섹터 (1M 평균 수익률 하위):")
        for s in lagging:
            r1 = s.get("r1m_avg_pct")
            r3 = s.get("r3m_avg_pct")
            br = s.get("breadth_20d_pct")
            parts = [f"  · {s['sector']} (n={s['sample_size']})"]
            if r1 is not None:
                parts.append(f"1M {r1:+.2f}%")
            if r3 is not None:
                parts.append(f"3M {r3:+.2f}%")
            if br is not None:
                parts.append(f"20D 상승비율 {br:.0f}%")
            lines.append(" / ".join(parts))

    return "\n".join(lines)


def infer_rotation_hint(snap: dict) -> str:
    """간단한 한 줄 요약 — 프롬프트 헤더 가이드."""
    if not snap or not snap.get("groups"):
        return ""

    parts: list[str] = []
    for grp, data in snap["groups"].items():
        label = _GROUP_LABELS.get(grp, grp)
        leading = data.get("leading_sectors") or []
        lagging = data.get("lagging_sectors") or []
        if not leading and not lagging:
            continue
        lead_txt = "·".join(leading[:3]) if leading else "-"
        lag_txt = "·".join(lagging[:3]) if lagging else "-"
        parts.append(f"{label} 강세 [{lead_txt}] / 약세 [{lag_txt}]")
    return " | ".join(parts)
