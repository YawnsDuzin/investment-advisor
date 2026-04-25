"""프리미엄 스크리너 API + 페이지 (로드맵 UI-6).

동작
  - POST /api/screener/run: 동적 필터 스펙 → 단일 SQL 실행 → 후보 목록
  - GET  /api/screener/presets: 본인 + 공개 프리셋 조회
  - POST /api/screener/presets: 신규 프리셋 저장 (티어 제한)
  - PUT  /api/screener/presets/{id}: 수정
  - DELETE /api/screener/presets/{id}: 삭제

필터 스펙 (JSON body)
  {
    "markets": ["KOSPI", "NASDAQ", ...],              # ticker market 필터
    "sectors": ["semiconductors", ...],                # sector_norm 필터
    "market_cap_krw": {"min": 1e11, "max": 1e14},      # 시총 범위 (KRW)
    "min_daily_value_krw": 1e9,                        # KRX 일평균 거래대금 하한
    "min_daily_value_usd": 500000,                     # US 일평균 거래대금 하한
    "return_1y_range": {"min": -20, "max": 100},       # 1y 수익률 범위(%)
    "volume_ratio_min": 1.2,                           # 20일/60일 평균 거래량 비율
    "max_vol60_pct": 3.0,                              # 60일 변동성 상한(%)
    "high_52w_proximity_min": 0.8,                     # 52주 고점 근접도 하한 (0~1)
    "sort": "market_cap_desc" | "r1y_desc" | "volume_surge_desc" | "name_asc",
    "limit": 50                                        # 티어 한도 내에서 재검증
  }
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Body, Response
from psycopg2.extras import RealDictCursor
import json

from api.auth.dependencies import get_current_user, get_current_user_required
from api.auth.models import UserInDB
from api.deps import get_db_conn, make_page_ctx
from api.templates_provider import templates
from api.serialization import serialize_row as _serialize_row
from shared.tier_limits import (
    SCREENER_PRESETS_MAX, SCREENER_RESULT_ROW_LIMIT,
    TIER_FREE,
)

router = APIRouter(prefix="/api/screener", tags=["스크리너"])
pages_router = APIRouter(prefix="/pages/screener", tags=["스크리너 페이지"])

# sector_norm → 한국어 라벨 사전 (28개 버킷)
SECTOR_LABELS: dict[str, str] = {
    "semiconductors": "반도체",
    "energy": "에너지",
    "financials": "금융",
    "healthcare": "헬스케어",
    "biotech": "바이오",
    "internet": "인터넷",
    "software": "소프트웨어",
    "hardware": "하드웨어",
    "ai": "AI",
    "cloud": "클라우드",
    "ev": "전기차",
    "battery": "배터리",
    "auto": "자동차",
    "consumer": "소비재",
    "retail": "유통",
    "media": "미디어",
    "telecom": "통신",
    "utilities": "유틸리티",
    "real_estate": "부동산",
    "materials": "소재",
    "chemicals": "화학",
    "steel": "철강",
    "shipbuilding": "조선",
    "aerospace": "항공우주",
    "defense": "방산",
    "construction": "건설",
    "logistics": "물류",
    "robotics": "로봇",
}


def _tier_of(user: Optional[UserInDB]) -> str:
    if not user:
        return TIER_FREE
    try:
        return user.effective_tier() or TIER_FREE
    except Exception:
        return TIER_FREE


# ──────────────────────────────────────────────
# GET /api/screener/sectors — sector_norm 분포 (드롭다운 옵션)
# ──────────────────────────────────────────────
@router.get("/sectors")
def list_sectors(response: Response, conn=Depends(get_db_conn)):
    """sector_norm 분포 (드롭다운 옵션). 30분 캐시."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT sector_norm, COUNT(*) AS count
            FROM stock_universe
            WHERE listed = TRUE AND has_preferred = FALSE
              AND sector_norm IS NOT NULL AND sector_norm <> ''
            GROUP BY sector_norm
            ORDER BY count DESC
            """
        )
        rows = cur.fetchall()
    sectors = [
        {
            "key": r["sector_norm"],
            "label": SECTOR_LABELS.get(r["sector_norm"], r["sector_norm"]),
            "count": int(r["count"]),
        }
        for r in rows
    ]
    response.headers["Cache-Control"] = "public, max-age=1800"
    return {"count": len(sectors), "sectors": sectors}


# ──────────────────────────────────────────────
# POST /api/screener/run — 필터 스펙으로 후보 조회
# ──────────────────────────────────────────────
@router.post("/run")
def run_screener(
    spec: dict = Body(...),
    user: Optional[UserInDB] = Depends(get_current_user),
    conn = Depends(get_db_conn),
):
    tier = _tier_of(user)
    tier_limit = SCREENER_RESULT_ROW_LIMIT.get(tier) or 50
    requested_limit = int(spec.get("limit") or tier_limit)
    limit = min(requested_limit, tier_limit)

    where: list[str] = ["u.listed = TRUE", "u.has_preferred = FALSE"]
    params: list = []
    join_ohlcv = False  # 필요 시 ohlcv_metrics CTE LEFT JOIN

    # market 필터
    markets = spec.get("markets")
    if markets:
        where.append("UPPER(u.market) = ANY(%s)")
        params.append([str(m).upper() for m in markets])

    # sector_norm
    sectors = spec.get("sectors")
    if sectors:
        where.append("u.sector_norm = ANY(%s)")
        params.append(list(sectors))

    # 시총 범위
    mcap = spec.get("market_cap_krw") or {}
    if mcap.get("min") is not None:
        where.append("u.market_cap_krw >= %s")
        params.append(float(mcap["min"]))
    if mcap.get("max") is not None:
        where.append("u.market_cap_krw <= %s")
        params.append(float(mcap["max"]))

    # OHLCV 기반 필터 (m.*)
    if spec.get("min_daily_value_krw") is not None:
        join_ohlcv = True
        where.append(
            "(m.avg_daily_value IS NULL "
            "OR UPPER(u.market) NOT IN ('KOSPI','KOSDAQ','KONEX') "
            "OR m.avg_daily_value >= %s)"
        )
        params.append(float(spec["min_daily_value_krw"]))

    if spec.get("min_daily_value_usd") is not None:
        join_ohlcv = True
        where.append(
            "(m.avg_daily_value IS NULL "
            "OR UPPER(u.market) NOT IN ('NASDAQ','NYSE','AMEX') "
            "OR m.avg_daily_value >= %s)"
        )
        params.append(float(spec["min_daily_value_usd"]))

    r1y = spec.get("return_1y_range") or {}
    if r1y.get("min") is not None or r1y.get("max") is not None:
        join_ohlcv = True
        if r1y.get("min") is not None:
            where.append("m.r1y IS NOT NULL AND m.r1y >= %s")
            params.append(float(r1y["min"]))
        if r1y.get("max") is not None:
            where.append("m.r1y IS NOT NULL AND m.r1y <= %s")
            params.append(float(r1y["max"]))

    if spec.get("volume_ratio_min") is not None:
        join_ohlcv = True
        where.append("m.volume_ratio IS NOT NULL AND m.volume_ratio >= %s")
        params.append(float(spec["volume_ratio_min"]))

    if spec.get("max_vol60_pct") is not None:
        join_ohlcv = True
        where.append("m.vol60_pct IS NOT NULL AND m.vol60_pct <= %s")
        params.append(float(spec["max_vol60_pct"]))

    if spec.get("high_52w_proximity_min") is not None:
        join_ohlcv = True
        where.append(
            "m.high_252d IS NOT NULL AND m.close_latest IS NOT NULL AND m.high_252d > 0 "
            "AND (m.close_latest / m.high_252d) >= %s"
        )
        params.append(float(spec["high_52w_proximity_min"]))

    sort_map = {
        "market_cap_desc":    "u.market_cap_krw DESC NULLS LAST",
        "market_cap_asc":     "u.market_cap_krw ASC NULLS LAST",
        "r1y_desc":           "m.r1y DESC NULLS LAST" if join_ohlcv else "u.market_cap_krw DESC NULLS LAST",
        "volume_surge_desc":  "m.volume_ratio DESC NULLS LAST" if join_ohlcv else "u.market_cap_krw DESC NULLS LAST",
        "name_asc":           "u.asset_name ASC",
    }
    order_by = sort_map.get(spec.get("sort") or "", "u.market_cap_krw DESC NULLS LAST")

    where_sql = " AND ".join(where)

    if join_ohlcv:
        cte = """
        WITH ranked AS (
            SELECT ticker, UPPER(market) AS market, trade_date, close::float AS close, volume, change_pct::float AS change_pct,
                   ROW_NUMBER() OVER (PARTITION BY ticker, UPPER(market) ORDER BY trade_date DESC) AS rn
            FROM stock_universe_ohlcv
            WHERE trade_date >= CURRENT_DATE - 300
        ),
        metrics AS (
            SELECT ticker, market,
                   MAX(CASE WHEN rn = 1   THEN close END)             AS close_latest,
                   MAX(CASE WHEN rn = 252 THEN close END)             AS close_1y,
                   MAX(close) FILTER (WHERE rn <= 252)                AS high_252d,
                   AVG(close * volume) FILTER (WHERE rn <= 60)        AS avg_daily_value,
                   STDDEV(LEAST(GREATEST(change_pct, -50), 50)) FILTER (WHERE rn <= 60) AS vol60_pct,
                   AVG(volume) FILTER (WHERE rn <= 20)                AS v20,
                   AVG(volume) FILTER (WHERE rn <= 60)                AS v60
            FROM ranked
            GROUP BY ticker, market
        ),
        ohlcv_metrics AS (
            SELECT ticker, market, close_latest, high_252d, avg_daily_value, vol60_pct,
                   CASE WHEN v60 > 0 THEN v20 / v60 END AS volume_ratio,
                   CASE WHEN close_1y IS NOT NULL AND close_1y > 0
                        THEN (close_latest - close_1y) / close_1y * 100 END AS r1y
            FROM metrics
        )
        """
        sql = f"""
        {cte}
        SELECT u.ticker, u.market, u.asset_name, u.asset_name_en, u.sector_norm,
               u.market_cap_krw, u.market_cap_bucket, u.last_price, u.last_price_ccy,
               m.close_latest, m.high_252d, m.avg_daily_value, m.vol60_pct,
               m.volume_ratio, m.r1y
        FROM stock_universe u
        LEFT JOIN ohlcv_metrics m
          ON UPPER(u.ticker) = UPPER(m.ticker) AND UPPER(u.market) = UPPER(m.market)
        WHERE {where_sql}
        ORDER BY {order_by}
        LIMIT %s
        """
    else:
        sql = f"""
        SELECT u.ticker, u.market, u.asset_name, u.asset_name_en, u.sector_norm,
               u.market_cap_krw, u.market_cap_bucket, u.last_price, u.last_price_ccy
        FROM stock_universe u
        WHERE {where_sql}
        ORDER BY {order_by}
        LIMIT %s
        """
    params.append(int(limit))

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    return {
        "count": len(rows),
        "tier": tier,
        "limit_applied": limit,
        "rows": [_serialize_row(r) for r in rows],
    }


# ──────────────────────────────────────────────
# 프리셋 CRUD (인증 필수 — Pro 이상 저장 가능, Free는 공개 프리셋만 read)
# ──────────────────────────────────────────────
@router.get("/presets")
def list_presets(
    user: UserInDB = Depends(get_current_user_required),
    conn = Depends(get_db_conn),
):
    """본인 프리셋 + 공개 프리셋 (다른 유저 포함) 목록."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT p.*, u.email AS owner_email,
                   (p.user_id = %s) AS owned
            FROM screener_presets p
            LEFT JOIN users u ON u.id = p.user_id
            WHERE p.user_id = %s OR p.is_public = TRUE
            ORDER BY owned DESC, p.updated_at DESC
            LIMIT 200
            """,
            (user.id, user.id),
        )
        rows = cur.fetchall()
    return {"count": len(rows), "presets": [_serialize_row(r) for r in rows]}


@router.post("/presets")
def create_preset(
    body: dict = Body(...),
    user: UserInDB = Depends(get_current_user_required),
    conn = Depends(get_db_conn),
):
    tier = _tier_of(user)
    tier_max = SCREENER_PRESETS_MAX.get(tier)
    if tier_max is not None and tier_max == 0:
        raise HTTPException(status_code=403, detail="Free 티어는 프리셋 저장 불가 — Pro/Premium으로 업그레이드하세요.")

    name = (body.get("name") or "").strip()
    spec = body.get("spec")
    if not name:
        raise HTTPException(status_code=400, detail="name 필수")
    if not isinstance(spec, dict):
        raise HTTPException(status_code=400, detail="spec (dict) 필수")

    description = body.get("description")
    is_public = bool(body.get("is_public", False))

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # 한도 체크
        cur.execute("SELECT COUNT(*) AS c FROM screener_presets WHERE user_id = %s", (user.id,))
        count_row = cur.fetchone() or {}
        if tier_max is not None and int(count_row.get("c") or 0) >= tier_max:
            raise HTTPException(
                status_code=403,
                detail=f"프리셋 한도 초과 — {tier} 티어는 최대 {tier_max}개까지 저장 가능합니다.",
            )

        try:
            cur.execute(
                """
                INSERT INTO screener_presets (user_id, name, description, spec, is_public)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING *
                """,
                (user.id, name, description, json.dumps(spec, ensure_ascii=False), is_public),
            )
            row = cur.fetchone()
            conn.commit()
        except Exception as e:
            conn.rollback()
            # UNIQUE (user_id, name) 위반
            if "duplicate key" in str(e).lower():
                raise HTTPException(status_code=409, detail=f"이미 '{name}' 이름의 프리셋이 있습니다.")
            raise
    return _serialize_row(row) if row else {"ok": True}


@router.put("/presets/{preset_id}")
def update_preset(
    preset_id: int,
    body: dict = Body(...),
    user: UserInDB = Depends(get_current_user_required),
    conn = Depends(get_db_conn),
):
    fields: list[str] = []
    params: list = []
    for field, coerce in (
        ("name", str),
        ("description", lambda v: None if v is None else str(v)),
        ("is_public", bool),
    ):
        if field in body:
            fields.append(f"{field} = %s")
            params.append(coerce(body[field]))
    if "spec" in body:
        if not isinstance(body["spec"], dict):
            raise HTTPException(status_code=400, detail="spec은 dict여야 합니다")
        fields.append("spec = %s")
        params.append(json.dumps(body["spec"], ensure_ascii=False))
    if not fields:
        raise HTTPException(status_code=400, detail="업데이트할 필드가 없습니다")

    fields.append("updated_at = NOW()")
    params.extend([preset_id, user.id])
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            UPDATE screener_presets SET {', '.join(fields)}
            WHERE id = %s AND user_id = %s
            RETURNING *
            """,
            params,
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="프리셋을 찾을 수 없거나 권한이 없습니다")
        conn.commit()
    return _serialize_row(row)


@router.delete("/presets/{preset_id}")
def delete_preset(
    preset_id: int,
    user: UserInDB = Depends(get_current_user_required),
    conn = Depends(get_db_conn),
):
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM screener_presets WHERE id = %s AND user_id = %s",
            (preset_id, user.id),
        )
        affected = cur.rowcount
        conn.commit()
    if not affected:
        raise HTTPException(status_code=404, detail="프리셋을 찾을 수 없거나 권한이 없습니다")
    return {"ok": True, "deleted_id": preset_id}


# ──────────────────────────────────────────────
# HTML 페이지
# ──────────────────────────────────────────────
@pages_router.get("")
def screener_page(ctx: dict = Depends(make_page_ctx("screener"))):
    """스크리너 페이지 — 클라이언트에서 /api/screener/run fetch."""
    return templates.TemplateResponse(
        request=ctx["request"], name="screener.html", context=ctx,
    )
