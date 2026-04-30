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
# 공용 필터 빌더 — /run 과 /count 가 공유
# ──────────────────────────────────────────────
def _build_screener_filters(spec: dict):
    """spec → (where, params, join_ohlcv, join_foreign, delta_win, netbuy_win).

    /run 과 /count 가 동일 필터 로직을 공유. SELECT/ORDER BY/LIMIT 은 caller 가 결정.
    """
    where: list[str] = ["u.listed = TRUE", "u.has_preferred = FALSE"]
    params: list = []
    join_ohlcv = False  # 필요 시 ohlcv_metrics CTE LEFT JOIN
    # latest_fund CTE 는 항상 포함 — 비용 작고 응답에 PER/PBR/배당 노출하면 UX 향상

    # market 필터
    markets = spec.get("markets")
    if markets:
        where.append("UPPER(u.market) = ANY(%s)")
        params.append([str(m).upper() for m in markets])

    # exclude_tickers — 유사 종목 찾기에서 자기 자신 제외용
    exclude_tickers = spec.get("exclude_tickers")
    if exclude_tickers:
        where.append("UPPER(u.ticker) <> ALL(%s)")
        params.append([str(t).upper() for t in exclude_tickers])

    # 검색어 q — 티커/이름/영문이름 LIKE (2자 이상만)
    q = (spec.get("q") or "").strip()
    if len(q) >= 2:
        where.append(
            "(u.ticker ILIKE %s OR u.asset_name ILIKE %s OR u.asset_name_en ILIKE %s)"
        )
        pat = f"%{q}%"
        params.extend([pat, pat, pat])

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

    # 시총 버킷 (large/mid/small/micro)
    buckets = spec.get("market_cap_buckets")
    if buckets:
        where.append("u.market_cap_bucket = ANY(%s)")
        params.append([str(b) for b in buckets])

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

    # 기간별 수익률 범위 (return_ranges: {1m/3m/6m/1y/ytd: {min, max}})
    return_ranges = spec.get("return_ranges") or {}
    PERIOD_TO_COL = {"1m": "r1m", "3m": "r3m", "6m": "r6m", "1y": "r1y", "ytd": "ytd"}
    for period, col in PERIOD_TO_COL.items():
        rg = return_ranges.get(period) or {}
        if rg.get("min") is not None:
            join_ohlcv = True
            where.append(f"m.{col} IS NOT NULL AND m.{col} >= %s")
            params.append(float(rg["min"]))
        if rg.get("max") is not None:
            join_ohlcv = True
            where.append(f"m.{col} IS NOT NULL AND m.{col} <= %s")
            params.append(float(rg["max"]))

    # 60일 최대 낙폭 상한 (사용자 입력은 양수 절대값, SQL에선 음수로 비교)
    mdd = spec.get("max_drawdown_60d_pct")
    if mdd is not None:
        join_ohlcv = True
        where.append("m.drawdown_60d_pct IS NOT NULL AND m.drawdown_60d_pct >= %s")
        params.append(-float(mdd))

    # 200일 이동평균 근접도 하한
    ma200_prox = spec.get("ma200_proximity_min")
    if ma200_prox is not None:
        join_ohlcv = True
        where.append("m.ma200_proximity IS NOT NULL AND m.ma200_proximity >= %s")
        params.append(float(ma200_prox))

    # ── 펀더멘털 필터 (v39 stock_universe_fundamentals 기반) ──
    # latest_fund CTE 가 종목별 최근 7일 내 최신 snapshot 1행 보유.
    # 결측 종목은 IS NOT NULL 가드로 자연스레 제외 (max/min 필터 시).
    if spec.get("min_per") is not None:
        where.append("f.per IS NOT NULL AND f.per >= %s")
        params.append(float(spec["min_per"]))
    if spec.get("max_per") is not None:
        where.append("f.per IS NOT NULL AND f.per > 0 AND f.per <= %s")
        params.append(float(spec["max_per"]))

    if spec.get("min_pbr") is not None:
        where.append("f.pbr IS NOT NULL AND f.pbr >= %s")
        params.append(float(spec["min_pbr"]))
    if spec.get("max_pbr") is not None:
        where.append("f.pbr IS NOT NULL AND f.pbr > 0 AND f.pbr <= %s")
        params.append(float(spec["max_pbr"]))

    if spec.get("min_dividend_yield_pct") is not None:
        where.append("f.dividend_yield IS NOT NULL AND f.dividend_yield >= %s")
        params.append(float(spec["min_dividend_yield_pct"]))

    # exclude_negative_eps 는 펀더 결측 종목도 통과 (관대 정책)
    if spec.get("exclude_negative_eps"):
        where.append("(f.eps IS NULL OR f.eps > 0)")

    # ── 외국인 수급 필터 (v44 stock_universe_foreign_flow) ──
    join_foreign = False

    # 윈도우 화이트리스트 (SQL injection 방어 — f-string 인터폴레이션 안전)
    delta_win = spec.get("delta_window_days") or 20
    netbuy_win = spec.get("net_buy_window_days") or 20
    try:
        delta_win = int(delta_win)
        netbuy_win = int(netbuy_win)
    except (TypeError, ValueError):
        delta_win = netbuy_win = 20
    if delta_win not in (5, 20, 60):
        delta_win = 20
    if netbuy_win not in (5, 20, 60):
        netbuy_win = 20

    if spec.get("min_foreign_ownership_pct") is not None:
        join_foreign = True
        where.append("ff.own_latest IS NOT NULL AND ff.own_latest >= %s")
        params.append(float(spec["min_foreign_ownership_pct"]))

    if spec.get("min_foreign_ownership_delta_pp") is not None:
        join_foreign = True
        where.append(
            f"ff.own_latest IS NOT NULL AND ff.own_d{delta_win} IS NOT NULL "
            f"AND (ff.own_latest - ff.own_d{delta_win}) >= %s"
        )
        params.append(float(spec["min_foreign_ownership_delta_pp"]))

    if spec.get("min_foreign_net_buy_krw") is not None:
        join_foreign = True
        where.append(f"ff.net_buy_{netbuy_win}d IS NOT NULL AND ff.net_buy_{netbuy_win}d >= %s")
        params.append(float(spec["min_foreign_net_buy_krw"]))

    sort_key = spec.get("sort") or ""
    if sort_key in ("foreign_ownership_desc", "foreign_delta_desc", "foreign_net_buy_desc"):
        join_foreign = True

    return where, params, join_ohlcv, join_foreign, delta_win, netbuy_win


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

    where, params, join_ohlcv, join_foreign, delta_win, netbuy_win = _build_screener_filters(spec)

    sort_map = {
        "market_cap_desc":   "u.market_cap_krw DESC NULLS LAST",
        "market_cap_asc":    "u.market_cap_krw ASC NULLS LAST",
        "r1m_desc":          "m.r1m DESC NULLS LAST" if join_ohlcv else "u.market_cap_krw DESC NULLS LAST",
        "r3m_desc":          "m.r3m DESC NULLS LAST" if join_ohlcv else "u.market_cap_krw DESC NULLS LAST",
        "r6m_desc":          "m.r6m DESC NULLS LAST" if join_ohlcv else "u.market_cap_krw DESC NULLS LAST",
        "r1y_desc":          "m.r1y DESC NULLS LAST" if join_ohlcv else "u.market_cap_krw DESC NULLS LAST",
        "ytd_desc":          "m.ytd DESC NULLS LAST" if join_ohlcv else "u.market_cap_krw DESC NULLS LAST",
        "volume_surge_desc": "m.volume_ratio DESC NULLS LAST" if join_ohlcv else "u.market_cap_krw DESC NULLS LAST",
        "liquidity_desc":    "m.avg_daily_value DESC NULLS LAST" if join_ohlcv else "u.market_cap_krw DESC NULLS LAST",
        "drawdown_asc":      "m.drawdown_60d_pct ASC NULLS LAST" if join_ohlcv else "u.market_cap_krw DESC NULLS LAST",
        "name_asc":          "u.asset_name ASC",
        # 펀더 정렬 — latest_fund 항상 LEFT JOIN 됐으니 join_ohlcv 무관하게 가능
        "per_asc":            "f.per ASC NULLS LAST",
        "pbr_asc":            "f.pbr ASC NULLS LAST",
        "dividend_yield_desc":"f.dividend_yield DESC NULLS LAST",
    }
    # 외국인 정렬 — join_foreign 여부에 따라 동적 매핑 (윈도우 자동 연동)
    sort_map["foreign_ownership_desc"] = (
        "ff.own_latest DESC NULLS LAST" if join_foreign else "u.market_cap_krw DESC NULLS LAST"
    )
    sort_map["foreign_delta_desc"] = (
        f"(ff.own_latest - ff.own_d{delta_win}) DESC NULLS LAST"
        if join_foreign else "u.market_cap_krw DESC NULLS LAST"
    )
    sort_map["foreign_net_buy_desc"] = (
        f"ff.net_buy_{netbuy_win}d DESC NULLS LAST"
        if join_foreign else "u.market_cap_krw DESC NULLS LAST"
    )
    order_by = sort_map.get(spec.get("sort") or "", "u.market_cap_krw DESC NULLS LAST")

    where_sql = " AND ".join(where)

    # ── 외국인 수급 CTE / SELECT / JOIN 조각 (lazy: join_foreign=False 면 전부 "") ──
    foreign_flow_cte = (f""",
        foreign_flow_ranked AS (
            SELECT ticker, UPPER(market) AS market, snapshot_date,
                   foreign_ownership_pct::float AS ownership_pct,
                   foreign_net_buy_value AS net_buy,
                   ROW_NUMBER() OVER (PARTITION BY ticker, UPPER(market)
                                      ORDER BY snapshot_date DESC) AS rn
            FROM stock_universe_foreign_flow
            WHERE snapshot_date >= CURRENT_DATE - 90
        ),
        foreign_flow_metrics AS (
            SELECT ticker, market,
                   MAX(CASE WHEN rn=1   THEN ownership_pct END) AS own_latest,
                   MAX(CASE WHEN rn=6   THEN ownership_pct END) AS own_d5,
                   MAX(CASE WHEN rn=21  THEN ownership_pct END) AS own_d20,
                   MAX(CASE WHEN rn=61  THEN ownership_pct END) AS own_d60,
                   SUM(net_buy) FILTER (WHERE rn<=5)  AS net_buy_5d,
                   SUM(net_buy) FILTER (WHERE rn<=20) AS net_buy_20d,
                   SUM(net_buy) FILTER (WHERE rn<=60) AS net_buy_60d
            FROM foreign_flow_ranked
            GROUP BY ticker, market
        )""") if join_foreign else ""

    foreign_select_tail = (
        f", ff.own_latest AS foreign_ownership_pct,"
        f" (ff.own_latest - ff.own_d{delta_win}) AS foreign_ownership_delta_pp,"
        f" ff.net_buy_{netbuy_win}d AS foreign_net_buy_krw,"
        f" {delta_win}::int AS foreign_ownership_delta_window_days,"
        f" {netbuy_win}::int AS foreign_net_buy_window_days"
    ) if join_foreign else ""

    foreign_join_tail = ("""
        LEFT JOIN foreign_flow_metrics ff
          ON UPPER(u.ticker) = UPPER(ff.ticker) AND UPPER(u.market) = ff.market
    """) if join_foreign else ""

    # 공통 CTE — 항상 포함
    # 워치리스트 user_id 는 int 강제 변환 후 SQL 인터폴레이션 (positional %s 와 섞이지 않게).
    # 비로그인 시 0 → 어떤 user_watchlist row 도 매칭 안 됨.
    wl_user_id = int(user.id) if user else 0
    common_ctes = f"""
        latest_fund AS (
            -- 최근 7일 내 가장 최신 snapshot 1 row per (ticker, market). 결측은 NULL JOIN.
            SELECT DISTINCT ON (ticker, market)
                   ticker, UPPER(market) AS market,
                   per::float AS per, pbr::float AS pbr,
                   eps::float AS eps, bps::float AS bps,
                   dps::float AS dps,
                   dividend_yield::float AS dividend_yield,
                   snapshot_date AS fund_snapshot_date
            FROM stock_universe_fundamentals
            WHERE snapshot_date >= CURRENT_DATE - INTERVAL '7 days'
            ORDER BY ticker, market, snapshot_date DESC
        ),
        top_picks_recent AS (
            -- 가장 최근 analysis_date (최근 7일 이내) 의 Top Picks 종목 식별
            SELECT DISTINCT UPPER(p.ticker) AS ticker, UPPER(p.market) AS market
            FROM investment_proposals p
            JOIN daily_top_picks d ON d.proposal_id = p.id
            WHERE d.analysis_date = (
                SELECT MAX(analysis_date)
                FROM daily_top_picks
                WHERE analysis_date >= CURRENT_DATE - INTERVAL '7 days'
            )
        ),
        my_watchlist AS (
            -- 본인 워치리스트 (ticker 기반 — user_watchlist 는 (user_id, ticker) UNIQUE).
            SELECT UPPER(ticker) AS ticker
            FROM user_watchlist
            WHERE user_id = {wl_user_id}
        )
    """

    common_select_tail = """
        f.per, f.pbr, f.eps, f.dividend_yield, f.fund_snapshot_date,
        (tp.ticker IS NOT NULL) AS is_top_pick,
        (wl.ticker IS NOT NULL) AS is_in_watchlist
    """

    common_join_tail = """
        LEFT JOIN latest_fund f
          ON UPPER(u.ticker) = UPPER(f.ticker) AND UPPER(u.market) = f.market
        LEFT JOIN top_picks_recent tp
          ON tp.ticker = UPPER(u.ticker) AND tp.market = UPPER(u.market)
        LEFT JOIN my_watchlist wl
          ON wl.ticker = UPPER(u.ticker)
    """

    if join_ohlcv:
        cte = f"""
        WITH ranked AS (
            SELECT ticker, UPPER(market) AS market, trade_date,
                   close::float AS close, volume,
                   change_pct::float AS change_pct,
                   ROW_NUMBER() OVER (PARTITION BY ticker, UPPER(market)
                                      ORDER BY trade_date DESC) AS rn
            FROM stock_universe_ohlcv
            WHERE trade_date >= CURRENT_DATE - 400
        ),
        metrics AS (
            SELECT ticker, market,
                MAX(CASE WHEN rn=1   THEN close END) AS close_latest,
                MAX(CASE WHEN rn=21  THEN close END) AS close_1m,
                MAX(CASE WHEN rn=63  THEN close END) AS close_3m,
                MAX(CASE WHEN rn=126 THEN close END) AS close_6m,
                MAX(CASE WHEN rn=252 THEN close END) AS close_1y,
                MAX(close) FILTER (WHERE rn<=252) AS high_252d,
                MIN(close) FILTER (WHERE rn<=252) AS low_252d,
                MAX(close) FILTER (WHERE rn<=60)  AS high_60d,
                MIN(close) FILTER (WHERE rn<=60)  AS low_60d,
                AVG(close)  FILTER (WHERE rn<=200) AS ma200,
                AVG(close)  FILTER (WHERE rn<=60)  AS ma60,
                AVG(close)  FILTER (WHERE rn<=20)  AS ma20,
                AVG(close*volume) FILTER (WHERE rn<=60) AS avg_daily_value,
                STDDEV(LEAST(GREATEST(change_pct,-50),50)) FILTER (WHERE rn<=60) AS vol60_pct,
                AVG(volume) FILTER (WHERE rn<=20) AS v20,
                AVG(volume) FILTER (WHERE rn<=60) AS v60,
                ARRAY_AGG(close ORDER BY trade_date DESC) FILTER (WHERE rn<=60) AS sparkline_60d
            FROM ranked GROUP BY ticker, market
        ),
        ytd_anchor AS (
            SELECT DISTINCT ON (ticker, mkt)
                   ticker, mkt AS market, close::float AS close_ytd
            FROM (
                SELECT ticker, UPPER(market) AS mkt, trade_date, close
                FROM stock_universe_ohlcv
                WHERE trade_date <  DATE_TRUNC('year', CURRENT_DATE)
                  AND trade_date >= DATE_TRUNC('year', CURRENT_DATE) - INTERVAL '30 days'
            ) t
            ORDER BY ticker, mkt, trade_date DESC
        ),
        ohlcv_metrics AS (
            SELECT m.ticker, m.market, m.close_latest, m.high_252d, m.low_252d,
                   m.high_60d, m.low_60d, m.ma20, m.ma60, m.ma200,
                   m.avg_daily_value, m.vol60_pct, m.sparkline_60d,
                   y.close_ytd,
                   (m.close_latest - m.close_1m) / NULLIF(m.close_1m,0) * 100 AS r1m,
                   (m.close_latest - m.close_3m) / NULLIF(m.close_3m,0) * 100 AS r3m,
                   (m.close_latest - m.close_6m) / NULLIF(m.close_6m,0) * 100 AS r6m,
                   (m.close_latest - m.close_1y) / NULLIF(m.close_1y,0) * 100 AS r1y,
                   (m.close_latest - y.close_ytd) / NULLIF(y.close_ytd,0) * 100 AS ytd,
                   -- 60d 고점 대비 현재 낙폭 (peak-to-current). 음수가 정상.
                   (m.close_latest - m.high_60d) / NULLIF(m.high_60d,0) * 100 AS drawdown_60d_pct,
                   m.close_latest / NULLIF(m.ma200,0) AS ma200_proximity,
                   m.close_latest / NULLIF(m.high_252d,0) AS high_52w_proximity,
                   CASE WHEN m.v60>0 THEN m.v20/m.v60 END AS volume_ratio
            FROM metrics m
            LEFT JOIN ytd_anchor y ON y.ticker=m.ticker AND y.market=m.market
        ),
        {common_ctes.strip().rstrip(',')}
        {foreign_flow_cte}
        """
        sql = f"""
        {cte}
        SELECT u.ticker, u.market, u.asset_name, u.asset_name_en, u.sector_norm,
               u.market_cap_krw, u.market_cap_bucket, u.last_price, u.last_price_ccy,
               m.close_latest, m.high_252d, m.low_252d,
               m.high_60d, m.low_60d, m.ma20, m.ma60, m.ma200,
               m.avg_daily_value, m.vol60_pct, m.volume_ratio,
               m.high_52w_proximity, m.ma200_proximity, m.drawdown_60d_pct,
               m.r1m, m.r3m, m.r6m, m.r1y, m.ytd,
               m.sparkline_60d,
               {common_select_tail}{foreign_select_tail}
        FROM stock_universe u
        LEFT JOIN ohlcv_metrics m
          ON UPPER(u.ticker) = UPPER(m.ticker) AND UPPER(u.market) = UPPER(m.market)
        {common_join_tail}{foreign_join_tail}
        WHERE {where_sql}
        ORDER BY {order_by}
        LIMIT %s
        """
    else:
        sql = f"""
        WITH {common_ctes.strip().rstrip(',')}
        {foreign_flow_cte}
        SELECT u.ticker, u.market, u.asset_name, u.asset_name_en, u.sector_norm,
               u.market_cap_krw, u.market_cap_bucket, u.last_price, u.last_price_ccy,
               {common_select_tail}{foreign_select_tail}
        FROM stock_universe u
        {common_join_tail}{foreign_join_tail}
        WHERE {where_sql}
        ORDER BY {order_by}
        LIMIT %s
        """
    params.append(int(limit))

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    include_sparkline = bool(spec.get("include_sparkline", False))
    result_rows = [_serialize_row(r) for r in rows]
    if not include_sparkline:
        for row in result_rows:
            row.pop("sparkline_60d", None)

    return {
        "count": len(result_rows),
        "tier": tier,
        "limit_applied": limit,
        "rows": result_rows,
    }


# ──────────────────────────────────────────────
# POST /api/screener/count — 필터 스펙의 매칭 종목 수 (행 데이터 X, 라이브 카운터용)
# ──────────────────────────────────────────────
@router.post("/count")
def count_screener(
    spec: dict = Body(...),
    user: Optional[UserInDB] = Depends(get_current_user),
    conn = Depends(get_db_conn),
):
    """spec 매칭 종목 수만 반환. /run 의 SELECT/ORDER BY/LIMIT 부담 없는 경량 쿼리.

    UI 사이드패널 라이브 카운트용 (입력 변경 시 디바운스 후 호출).
    tier_limit 무시 — 실제 매칭 총 수 반환 (limit_applied 표시는 /run 결과).
    """
    where, params, join_ohlcv, join_foreign, delta_win, netbuy_win = _build_screener_filters(spec)
    where_sql = " AND ".join(where)

    # 외국인 CTE — join_foreign=True 시만 생성 (run_screener 와 동일 로직)
    foreign_flow_cte = (f""",
        foreign_flow_ranked AS (
            SELECT ticker, UPPER(market) AS market, snapshot_date,
                   foreign_ownership_pct::float AS ownership_pct,
                   foreign_net_buy_value AS net_buy,
                   ROW_NUMBER() OVER (PARTITION BY ticker, UPPER(market)
                                      ORDER BY snapshot_date DESC) AS rn
            FROM stock_universe_foreign_flow
            WHERE snapshot_date >= CURRENT_DATE - 90
        ),
        foreign_flow_metrics AS (
            SELECT ticker, market,
                   MAX(CASE WHEN rn=1   THEN ownership_pct END) AS own_latest,
                   MAX(CASE WHEN rn=6   THEN ownership_pct END) AS own_d5,
                   MAX(CASE WHEN rn=21  THEN ownership_pct END) AS own_d20,
                   MAX(CASE WHEN rn=61  THEN ownership_pct END) AS own_d60,
                   SUM(net_buy) FILTER (WHERE rn<=5)  AS net_buy_5d,
                   SUM(net_buy) FILTER (WHERE rn<=20) AS net_buy_20d,
                   SUM(net_buy) FILTER (WHERE rn<=60) AS net_buy_60d
            FROM foreign_flow_ranked
            GROUP BY ticker, market
        )""") if join_foreign else ""
    foreign_join_tail = ("""
        LEFT JOIN foreign_flow_metrics ff
          ON UPPER(u.ticker) = UPPER(ff.ticker) AND UPPER(u.market) = ff.market
    """) if join_foreign else ""

    # latest_fund — 펀더 WHERE 가드용 (항상 LEFT JOIN, 미사용 시도 비용 작음)
    fund_cte = """
        latest_fund AS (
            SELECT DISTINCT ON (ticker, market)
                   ticker, UPPER(market) AS market,
                   per::float AS per, pbr::float AS pbr,
                   eps::float AS eps,
                   dividend_yield::float AS dividend_yield
            FROM stock_universe_fundamentals
            WHERE snapshot_date >= CURRENT_DATE - INTERVAL '7 days'
            ORDER BY ticker, market, snapshot_date DESC
        )
    """
    fund_join = """
        LEFT JOIN latest_fund f
          ON UPPER(u.ticker) = UPPER(f.ticker) AND UPPER(u.market) = f.market
    """

    if join_ohlcv:
        # ohlcv_metrics 만 (sparkline_60d ARRAY_AGG 빼고 — count 에 불필요)
        cte = f"""
        WITH ranked AS (
            SELECT ticker, UPPER(market) AS market, trade_date,
                   close::float AS close, volume,
                   change_pct::float AS change_pct,
                   ROW_NUMBER() OVER (PARTITION BY ticker, UPPER(market)
                                      ORDER BY trade_date DESC) AS rn
            FROM stock_universe_ohlcv
            WHERE trade_date >= CURRENT_DATE - 400
        ),
        metrics AS (
            SELECT ticker, market,
                MAX(CASE WHEN rn=1   THEN close END) AS close_latest,
                MAX(CASE WHEN rn=21  THEN close END) AS close_1m,
                MAX(CASE WHEN rn=63  THEN close END) AS close_3m,
                MAX(CASE WHEN rn=126 THEN close END) AS close_6m,
                MAX(CASE WHEN rn=252 THEN close END) AS close_1y,
                MAX(close) FILTER (WHERE rn<=252) AS high_252d,
                MIN(close) FILTER (WHERE rn<=252) AS low_252d,
                MAX(close) FILTER (WHERE rn<=60)  AS high_60d,
                AVG(close)  FILTER (WHERE rn<=200) AS ma200,
                AVG(close*volume) FILTER (WHERE rn<=60) AS avg_daily_value,
                STDDEV(LEAST(GREATEST(change_pct,-50),50)) FILTER (WHERE rn<=60) AS vol60_pct,
                AVG(volume) FILTER (WHERE rn<=20) AS v20,
                AVG(volume) FILTER (WHERE rn<=60) AS v60
            FROM ranked GROUP BY ticker, market
        ),
        ytd_anchor AS (
            SELECT DISTINCT ON (ticker, mkt)
                   ticker, mkt AS market, close::float AS close_ytd
            FROM (
                SELECT ticker, UPPER(market) AS mkt, trade_date, close
                FROM stock_universe_ohlcv
                WHERE trade_date <  DATE_TRUNC('year', CURRENT_DATE)
                  AND trade_date >= DATE_TRUNC('year', CURRENT_DATE) - INTERVAL '30 days'
            ) t
            ORDER BY ticker, mkt, trade_date DESC
        ),
        ohlcv_metrics AS (
            SELECT m.ticker, m.market, m.close_latest, m.high_252d, m.low_252d,
                   m.high_60d, m.ma200,
                   m.avg_daily_value, m.vol60_pct,
                   y.close_ytd,
                   (m.close_latest - m.close_1m) / NULLIF(m.close_1m,0) * 100 AS r1m,
                   (m.close_latest - m.close_3m) / NULLIF(m.close_3m,0) * 100 AS r3m,
                   (m.close_latest - m.close_6m) / NULLIF(m.close_6m,0) * 100 AS r6m,
                   (m.close_latest - m.close_1y) / NULLIF(m.close_1y,0) * 100 AS r1y,
                   (m.close_latest - y.close_ytd) / NULLIF(y.close_ytd,0) * 100 AS ytd,
                   (m.close_latest - m.high_60d) / NULLIF(m.high_60d,0) * 100 AS drawdown_60d_pct,
                   m.close_latest / NULLIF(m.ma200,0) AS ma200_proximity,
                   m.close_latest / NULLIF(m.high_252d,0) AS high_52w_proximity,
                   CASE WHEN m.v60>0 THEN m.v20/m.v60 END AS volume_ratio
            FROM metrics m
            LEFT JOIN ytd_anchor y ON y.ticker=m.ticker AND y.market=m.market
        ),
        {fund_cte.strip()}{foreign_flow_cte}
        """
        sql = f"""
        {cte}
        SELECT COUNT(*) AS n
        FROM stock_universe u
        LEFT JOIN ohlcv_metrics m
          ON UPPER(u.ticker) = UPPER(m.ticker) AND UPPER(u.market) = UPPER(m.market)
        {fund_join}{foreign_join_tail}
        WHERE {where_sql}
        """
    else:
        sql = f"""
        WITH {fund_cte.strip()}{foreign_flow_cte}
        SELECT COUNT(*) AS n
        FROM stock_universe u
        {fund_join}{foreign_join_tail}
        WHERE {where_sql}
        """

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()

    return {"count": int(row["n"]) if row and row.get("n") is not None else 0}


# ──────────────────────────────────────────────
# 프리셋 CRUD (인증 필수 — Pro 이상 저장 가능, Free는 공개 프리셋만 read)
# ──────────────────────────────────────────────
@router.get("/presets/seeds")
def list_seed_presets(response: Response, conn = Depends(get_db_conn)):
    """시드 프리셋(거장 + 운영 자동) 공개 목록 — 인증 불필요.

    UI '빠른 시작' 카드 그리드용. is_seed=TRUE 만. spec 포맷은 UI SpecBuilder 가
    바로 toDOM(spec) 가능하도록 routes/screener.py /run 의 입력 포맷 그대로.
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, strategy_key, name, description,
                   persona, persona_summary, markets_supported, risk_warning,
                   spec
            FROM screener_presets
            WHERE is_seed = TRUE
            ORDER BY
                CASE WHEN persona = '운영 자동' THEN 1 ELSE 0 END,  -- 거장 먼저
                id ASC
            """
        )
        rows = cur.fetchall()
    response.headers["Cache-Control"] = "public, max-age=900"
    return {"count": len(rows), "seeds": [_serialize_row(r) for r in rows]}


@router.get("/presets")
def list_presets(
    user: UserInDB = Depends(get_current_user_required),
    conn = Depends(get_db_conn),
):
    """본인 프리셋 + 공개 프리셋 (다른 유저 포함) 목록. 시드 프리셋은 별도 /presets/seeds."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT p.*, u.email AS owner_email,
                   (p.user_id = %s) AS owned
            FROM screener_presets p
            LEFT JOIN users u ON u.id = p.user_id
            WHERE (p.user_id = %s OR p.is_public = TRUE)
              AND p.is_seed = FALSE
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
