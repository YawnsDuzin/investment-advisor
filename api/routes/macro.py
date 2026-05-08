"""매크로 관측 시계열 조회 API (Tier 2 #4).

`macro_observations` 테이블(v50) 의 얇은 read 레이어. UI 매크로 카드·시나리오 진행 추적
파셜이 소비. yfinance 직조회 없이 DB 만 사용 — 페이지 응답 속도 보장.
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from psycopg2.extras import RealDictCursor

from analyzer.macro_observer import (
    MACRO_LABELS_KR,
    MACRO_UNITS,
    YFINANCE_VARIABLES,
)
from api.deps import get_db_conn

router = APIRouter(prefix="/api/macro", tags=["매크로 관측"])

_DEFAULT_VARS = list(YFINANCE_VARIABLES.keys())


@router.get("/recent")
def recent_macros(
    conn=Depends(get_db_conn),
    days: int = Query(default=30, ge=1, le=365),
    variables: str = Query(default="", description="콤마 구분 variable_name. 빈 값=전체"),
):
    """최근 N일 매크로 관측치 그룹 응답.

    Returns:
        {
            "as_of": "YYYY-MM-DD",  # latest observed_at (전체 변수 합집합 max)
            "items": [
                {
                    "variable_name": "us_10y_yield",
                    "label": "미 10Y 금리",
                    "unit": "%",
                    "latest_value": float,
                    "prev_value": float | None,
                    "change_abs": float | None,
                    "change_pct": float | None,
                    "spark_points": [float, ...],   # 최대 30 포인트
                    "first_value": float,            # spark 의 첫 점
                },
                ...
            ]
        }
        DB 미가용·테이블 부재 → {"as_of": null, "items": []}
    """
    targets = [v.strip() for v in variables.split(",") if v.strip()] or _DEFAULT_VARS
    targets = [v for v in targets if v in YFINANCE_VARIABLES]
    if not targets:
        return {"as_of": None, "items": []}

    since = date.today() - timedelta(days=days)
    out_items: list[dict] = []
    latest_overall: date | None = None

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            for var in targets:
                cur.execute(
                    """
                    SELECT observed_at, value
                    FROM macro_observations
                    WHERE variable_name = %s AND observed_at >= %s
                    ORDER BY observed_at ASC
                    """,
                    (var, since),
                )
                rows = cur.fetchall()
                if not rows:
                    continue
                values = [float(r["value"]) for r in rows if r["value"] is not None]
                if not values:
                    continue
                latest_value = values[-1]
                prev_value = values[-2] if len(values) >= 2 else None
                first_value = values[0]
                change_abs = latest_value - prev_value if prev_value is not None else None
                change_pct = (
                    round((latest_value - prev_value) / prev_value * 100, 3)
                    if prev_value not in (None, 0) else None
                )
                last_date = rows[-1]["observed_at"]
                if latest_overall is None or last_date > latest_overall:
                    latest_overall = last_date
                out_items.append({
                    "variable_name": var,
                    "label": MACRO_LABELS_KR.get(var, var),
                    "unit": MACRO_UNITS.get(var, ""),
                    "latest_value": latest_value,
                    "prev_value": prev_value,
                    "change_abs": change_abs,
                    "change_pct": change_pct,
                    "spark_points": values[-30:],  # 최대 30 포인트
                    "first_value": first_value,
                    "trend": "up" if change_abs and change_abs > 0 else
                             ("down" if change_abs and change_abs < 0 else "flat"),
                })
    except Exception:
        # 테이블/컬럼 미존재 (마이그레이션 v50 이전) — 빈 결과 안전 폴백
        return {"as_of": None, "items": []}

    return {
        "as_of": latest_overall.isoformat() if latest_overall else None,
        "items": out_items,
    }
