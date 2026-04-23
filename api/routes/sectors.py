"""섹터 로테이션 집계 API (로드맵 B3).

`stock_universe` × `stock_universe_ohlcv` JOIN으로 섹터(`sector_norm`)별
기간별 평균 수익률을 계산한다. UI-3 대시보드 섹터 모멘텀 히트맵의 소스.

현재는 단일 엔드포인트 `/api/sectors/momentum`만 노출. 별도 프리컴퓨트 테이블
없이 매 요청 시 집계 (~100~500ms 예상, 필요 시 B4에서 캐싱 도입).
"""
from fastapi import APIRouter, Query, Depends
from psycopg2.extras import RealDictCursor

from api.deps import get_db_conn

router = APIRouter(prefix="/api/sectors", tags=["섹터 로테이션"])


@router.get("/momentum")
def get_sector_momentum(
    conn = Depends(get_db_conn),
    min_stocks: int = Query(default=3, ge=1, le=100,
                            description="섹터별 최소 종목 수 (너무 작은 섹터 제외)"),
    market_group: str | None = Query(default=None,
                                     description="KRX | US (미지정 시 전체)"),
):
    """섹터 × 기간(1m/3m/6m/12m) 평균 수익률.

    Returns:
        {
          "periods": ["r1m", "r3m", "r6m", "r12m"],
          "sectors": [
            {"sector": "semiconductors", "n": 42,
             "r1m": 5.2, "r3m": 18.4, "r6m": 35.1, "r12m": 42.0},
            ...
          ]
        }
    """
    market_filter_sql = ""
    params: list = [300]  # 윈도우 일수 (252 + 여유)
    if market_group and market_group.upper() == "KRX":
        market_filter_sql = "AND UPPER(u.market) = ANY(%s)"
        params.append(["KOSPI", "KOSDAQ", "KONEX"])
    elif market_group and market_group.upper() == "US":
        market_filter_sql = "AND UPPER(u.market) = ANY(%s)"
        params.append(["NASDAQ", "NYSE", "AMEX"])

    sql = f"""
    WITH ranked AS (
        SELECT u.sector_norm, o.ticker, UPPER(o.market) AS market,
               o.trade_date, o.close::float AS close,
               ROW_NUMBER() OVER (
                   PARTITION BY o.ticker, UPPER(o.market)
                   ORDER BY o.trade_date DESC
               ) AS rn
        FROM stock_universe_ohlcv o
        JOIN stock_universe u
          ON UPPER(u.ticker) = UPPER(o.ticker)
         AND UPPER(u.market) = UPPER(o.market)
        WHERE o.trade_date >= CURRENT_DATE - (%s::int)
          AND u.listed = TRUE
          AND u.sector_norm IS NOT NULL
          AND u.sector_norm <> ''
          {market_filter_sql}
    ),
    endpoints AS (
        SELECT sector_norm, ticker, market,
               MAX(CASE WHEN rn = 1   THEN close END) AS c_latest,
               MAX(CASE WHEN rn = 22  THEN close END) AS c_1m,
               MAX(CASE WHEN rn = 66  THEN close END) AS c_3m,
               MAX(CASE WHEN rn = 132 THEN close END) AS c_6m,
               MAX(CASE WHEN rn = 252 THEN close END) AS c_12m
        FROM ranked
        GROUP BY sector_norm, ticker, market
    ),
    stock_returns AS (
        SELECT sector_norm,
               CASE WHEN c_1m  IS NOT NULL AND c_1m  > 0 THEN (c_latest / c_1m  - 1) * 100 END AS r1m,
               CASE WHEN c_3m  IS NOT NULL AND c_3m  > 0 THEN (c_latest / c_3m  - 1) * 100 END AS r3m,
               CASE WHEN c_6m  IS NOT NULL AND c_6m  > 0 THEN (c_latest / c_6m  - 1) * 100 END AS r6m,
               CASE WHEN c_12m IS NOT NULL AND c_12m > 0 THEN (c_latest / c_12m - 1) * 100 END AS r12m
        FROM endpoints
        WHERE c_latest IS NOT NULL
    )
    SELECT sector_norm,
           COUNT(*) AS n,
           ROUND(AVG(r1m )::numeric, 2) AS r1m,
           ROUND(AVG(r3m )::numeric, 2) AS r3m,
           ROUND(AVG(r6m )::numeric, 2) AS r6m,
           ROUND(AVG(r12m)::numeric, 2) AS r12m
    FROM stock_returns
    GROUP BY sector_norm
    HAVING COUNT(*) >= %s
    ORDER BY r3m DESC NULLS LAST
    """
    params.append(int(min_stocks))

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    def _float(v):
        return float(v) if v is not None else None

    return {
        "periods": ["r1m", "r3m", "r6m", "r12m"],
        "market_group": (market_group or "ALL").upper(),
        "sectors": [
            {
                "sector": r["sector_norm"],
                "n": int(r["n"] or 0),
                "r1m": _float(r.get("r1m")),
                "r3m": _float(r.get("r3m")),
                "r6m": _float(r.get("r6m")),
                "r12m": _float(r.get("r12m")),
            }
            for r in rows
        ],
    }
