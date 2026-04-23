"""투자 테마 조회 API + 테마 페이지 라우트"""
from typing import Optional
from fastapi import APIRouter, Query, Depends
from psycopg2.extras import RealDictCursor
from api.serialization import serialize_row as _serialize_row
from api.templates_provider import templates
from api.deps import get_db_conn, make_page_ctx
from api.auth.dependencies import get_current_user_required
from api.auth.models import UserInDB

router = APIRouter(prefix="/themes", tags=["테마"])
pages_router = APIRouter(prefix="/pages/themes", tags=["테마 페이지"])


@router.get("")
def list_themes(
    conn = Depends(get_db_conn),
    limit: int = Query(default=20, ge=1, le=100),
    horizon: str | None = Query(default=None, description="short|mid|long"),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    theme_type: str | None = Query(default=None, description="structural|cyclical"),
    validity: str | None = Query(default=None, description="strong|medium|weak"),
    _user: Optional[UserInDB] = Depends(get_current_user_required),
):
    """투자 테마 목록 (최신순, 필터 가능)"""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        query = """
            SELECT t.*, s.analysis_date
            FROM investment_themes t
            JOIN analysis_sessions s ON t.session_id = s.id
            WHERE t.confidence_score >= %s
        """
        params: list = [min_confidence]

        if horizon:
            query += " AND t.time_horizon = %s"
            params.append(horizon)
        if theme_type:
            query += " AND t.theme_type = %s"
            params.append(theme_type)
        if validity:
            query += " AND t.theme_validity = %s"
            params.append(validity)

        query += " ORDER BY s.analysis_date DESC, t.confidence_score DESC LIMIT %s"
        params.append(limit)

        cur.execute(query, params)
        themes = cur.fetchall()

        for theme in themes:
            # 시나리오 분석
            cur.execute(
                """SELECT * FROM theme_scenarios WHERE theme_id = %s
                   ORDER BY probability DESC""",
                (theme["id"],)
            )
            theme["scenarios"] = [_serialize_row(s) for s in cur.fetchall()]

            # 매크로 영향
            cur.execute(
                "SELECT * FROM macro_impacts WHERE theme_id = %s",
                (theme["id"],)
            )
            theme["macro_impacts"] = [_serialize_row(m) for m in cur.fetchall()]

            # 투자 제안
            cur.execute(
                """SELECT * FROM investment_proposals WHERE theme_id = %s
                   ORDER BY target_allocation DESC""",
                (theme["id"],)
            )
            theme["proposals"] = [_serialize_row(p) for p in cur.fetchall()]

    return [_serialize_row(t) for t in themes]


@router.get("/search")
def search_themes(
    conn = Depends(get_db_conn),
    q: str = Query(description="테마명 또는 설명 검색어"),
    limit: int = Query(default=10, ge=1, le=50),
    _user: Optional[UserInDB] = Depends(get_current_user_required),
):
    """테마 검색 (테마명/설명에서 키워드 검색)"""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT t.*, s.analysis_date
            FROM investment_themes t
            JOIN analysis_sessions s ON t.session_id = s.id
            WHERE t.theme_name ILIKE %s OR t.description ILIKE %s
            ORDER BY s.analysis_date DESC
            LIMIT %s
        """, (f"%{q}%", f"%{q}%", limit))
        themes = cur.fetchall()

    return [_serialize_row(t) for t in themes]


# ──────────────────────────────────────────────
# 테마 페이지 라우트 (pages_router)
# ──────────────────────────────────────────────

@router.get("/by-key/{theme_key}/performance")
def get_theme_performance(
    theme_key: str,
    conn = Depends(get_db_conn),
):
    """테마 구성 종목의 기간별 평균 수익률 + 벤치마크(KOSPI/SP500) 대비 상대 성과.

    로드맵 UI-2 — 테마 히스토리 상단에 표시.

    1) theme_tracking의 theme_key로 investment_themes 매칭
    2) 해당 테마의 모든 proposals ticker 집합 수집
    3) stock_universe_ohlcv로 각 ticker의 최근 r1m/r3m/r6m_pct 계산
    4) 평균 + KOSPI / SP500 같은 기간 수익률 대비 스프레드 반환

    결측 OHLCV 종목은 집계에서 제외.
    """
    key = theme_key.strip()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # theme_key의 구성 종목 ticker/market 추출 (최근 30일 세션 기준)
        cur.execute(
            """
            SELECT DISTINCT UPPER(p.ticker) AS ticker, UPPER(p.market) AS market
            FROM investment_proposals p
            JOIN investment_themes t ON p.theme_id = t.id
            JOIN analysis_sessions s ON t.session_id = s.id
            WHERE (t.theme_key = %s
                OR LOWER(REPLACE(REPLACE(REPLACE(t.theme_name, ' ', ''), '-', ''), '·', '')) = %s)
              AND p.asset_type = 'stock'
              AND p.ticker IS NOT NULL
              AND s.analysis_date >= CURRENT_DATE - 30
            """,
            (key, key.lower()),
        )
        tickers = [(r["ticker"], r["market"] or "") for r in cur.fetchall()]

        if not tickers:
            return {
                "theme_key": key, "n_stocks": 0,
                "periods": {},
                "benchmark": None,
                "spreads": {},
            }

        # 테마 종목 기간별 평균 수익률 (동일 로직 factor_engine과 유사)
        placeholders = ",".join(["(%s, %s)"] * len(tickers))
        flat: list = []
        for t, m in tickers:
            flat.extend([t, m])
        sql = f"""
        WITH targets (ticker, market) AS (VALUES {placeholders}),
        ranked AS (
            SELECT o.ticker, UPPER(o.market) AS market, o.close::float AS close,
                   ROW_NUMBER() OVER (
                       PARTITION BY o.ticker, UPPER(o.market)
                       ORDER BY o.trade_date DESC
                   ) AS rn
            FROM stock_universe_ohlcv o
            JOIN targets t ON UPPER(o.ticker) = t.ticker
              AND (t.market = '' OR UPPER(o.market) = UPPER(t.market))
            WHERE o.trade_date >= CURRENT_DATE - 300
        ),
        endpoints AS (
            SELECT ticker, market,
                   MAX(CASE WHEN rn = 1   THEN close END) AS c_latest,
                   MAX(CASE WHEN rn = 22  THEN close END) AS c_1m,
                   MAX(CASE WHEN rn = 66  THEN close END) AS c_3m,
                   MAX(CASE WHEN rn = 132 THEN close END) AS c_6m
            FROM ranked
            GROUP BY ticker, market
        )
        SELECT
            COUNT(*) FILTER (WHERE c_1m  IS NOT NULL AND c_1m  > 0) AS n_1m,
            COUNT(*) FILTER (WHERE c_3m  IS NOT NULL AND c_3m  > 0) AS n_3m,
            COUNT(*) FILTER (WHERE c_6m  IS NOT NULL AND c_6m  > 0) AS n_6m,
            ROUND(AVG((c_latest / c_1m  - 1) * 100)::numeric, 2) FILTER (WHERE c_1m  > 0) AS r1m,
            ROUND(AVG((c_latest / c_3m  - 1) * 100)::numeric, 2) FILTER (WHERE c_3m  > 0) AS r3m,
            ROUND(AVG((c_latest / c_6m  - 1) * 100)::numeric, 2) FILTER (WHERE c_6m  > 0) AS r6m
        FROM endpoints
        """
        cur.execute(sql, flat)
        stats = cur.fetchone() or {}

        # 벤치마크: 테마에 KRX 종목이 더 많으면 KOSPI, 아니면 SP500
        krx_count = sum(1 for _, m in tickers if m in ("KOSPI", "KOSDAQ", "KONEX"))
        us_count = sum(1 for _, m in tickers if m in ("NASDAQ", "NYSE", "AMEX"))
        bench_code = "KOSPI" if krx_count >= us_count else "SP500"

        cur.execute(
            """
            WITH br AS (
                SELECT close::float AS close,
                       ROW_NUMBER() OVER (ORDER BY trade_date DESC) AS rn
                FROM market_indices_ohlcv
                WHERE index_code = %s
                  AND trade_date >= CURRENT_DATE - 300
            )
            SELECT
                MAX(CASE WHEN rn = 1   THEN close END) AS c_latest,
                MAX(CASE WHEN rn = 22  THEN close END) AS c_1m,
                MAX(CASE WHEN rn = 66  THEN close END) AS c_3m,
                MAX(CASE WHEN rn = 132 THEN close END) AS c_6m
            FROM br
            """,
            (bench_code,),
        )
        brow = cur.fetchone() or {}

    def _f(v):
        return float(v) if v is not None else None

    def _pct(latest, past):
        try:
            if latest is None or past is None or past <= 0:
                return None
            return round((float(latest) / float(past) - 1) * 100, 2)
        except (TypeError, ValueError):
            return None

    bench_r = {
        "r1m": _pct(brow.get("c_latest"), brow.get("c_1m")),
        "r3m": _pct(brow.get("c_latest"), brow.get("c_3m")),
        "r6m": _pct(brow.get("c_latest"), brow.get("c_6m")),
    }
    theme_r = {
        "r1m": _f(stats.get("r1m")),
        "r3m": _f(stats.get("r3m")),
        "r6m": _f(stats.get("r6m")),
    }
    spreads = {
        k: round(theme_r[k] - bench_r[k], 2) if (theme_r[k] is not None and bench_r[k] is not None) else None
        for k in ("r1m", "r3m", "r6m")
    }

    return {
        "theme_key": key,
        "n_stocks": len(tickers),
        "counts": {
            "r1m": int(stats.get("n_1m") or 0),
            "r3m": int(stats.get("n_3m") or 0),
            "r6m": int(stats.get("n_6m") or 0),
        },
        "theme_returns": theme_r,
        "benchmark": {"code": bench_code, "returns": bench_r},
        "spreads": spreads,
    }


@pages_router.get("/history/{theme_key}")
def theme_history_page(theme_key: str, conn = Depends(get_db_conn), ctx: dict = Depends(make_page_ctx("themes"))):
    """특정 테마의 일자별 추이"""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # 추적 정보
        cur.execute("SELECT * FROM theme_tracking WHERE theme_key = %s", (theme_key,))
        tracking = cur.fetchone()
        if not tracking:
            return templates.TemplateResponse(request=ctx["request"], name="theme_history.html",
                context={**ctx, "tracking": None, "history": []})

        # 일자별 테마 데이터 (이름이 유사한 것 모두)
        cur.execute("""
            SELECT t.*, s.analysis_date
            FROM investment_themes t
            JOIN analysis_sessions s ON t.session_id = s.id
            WHERE LOWER(REPLACE(REPLACE(REPLACE(t.theme_name, ' ', ''), '-', ''), '·', ''))
                  = %s
            ORDER BY s.analysis_date DESC
            LIMIT 30
        """, (theme_key,))
        history = cur.fetchall()

        for entry in history:
            cur.execute(
                "SELECT * FROM investment_proposals WHERE theme_id = %s ORDER BY target_allocation DESC",
                (entry["id"],)
            )
            entry["proposals"] = [_serialize_row(p) for p in cur.fetchall()]

            cur.execute(
                "SELECT * FROM theme_scenarios WHERE theme_id = %s ORDER BY probability DESC",
                (entry["id"],)
            )
            entry["scenarios"] = [_serialize_row(s) for s in cur.fetchall()]

    return templates.TemplateResponse(request=ctx["request"], name="theme_history.html", context={
        **ctx,
        "tracking": _serialize_row(tracking),
        "history": [_serialize_row(h) for h in history],
    })


@pages_router.get("")
def themes_page(
    conn = Depends(get_db_conn),
    horizon: str | None = Query(default=None),
    min_confidence: float = Query(default=0.0),
    q: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    ctx: dict = Depends(make_page_ctx("themes")),
):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        query = """
            SELECT t.*, s.analysis_date
            FROM investment_themes t
            JOIN analysis_sessions s ON t.session_id = s.id
            WHERE t.confidence_score >= %s
        """
        params: list = [min_confidence]

        if horizon:
            query += " AND t.time_horizon = %s"
            params.append(horizon)
        if q:
            query += " AND (t.theme_name ILIKE %s OR t.description ILIKE %s)"
            params.extend([f"%{q}%", f"%{q}%"])

        query += " ORDER BY s.analysis_date DESC, t.confidence_score DESC LIMIT %s"
        params.append(limit)
        cur.execute(query, params)
        themes = cur.fetchall()

        for theme in themes:
            # 시나리오
            cur.execute(
                "SELECT * FROM theme_scenarios WHERE theme_id = %s ORDER BY probability DESC",
                (theme["id"],)
            )
            theme["scenarios"] = [_serialize_row(s) for s in cur.fetchall()]
            # 매크로 영향
            cur.execute(
                "SELECT * FROM macro_impacts WHERE theme_id = %s",
                (theme["id"],)
            )
            theme["macro_impacts"] = [_serialize_row(m) for m in cur.fetchall()]
            # 투자 제안
            cur.execute(
                "SELECT * FROM investment_proposals WHERE theme_id = %s ORDER BY target_allocation DESC",
                (theme["id"],)
            )
            theme["proposals"] = [_serialize_row(p) for p in cur.fetchall()]

        # 추적 데이터 매핑
        cur.execute("SELECT * FROM theme_tracking ORDER BY last_seen_date DESC")
        tracking_map = {}
        for row in cur.fetchall():
            tracking_map[row["theme_key"]] = _serialize_row(row)

    return templates.TemplateResponse(request=ctx["request"], name="themes.html", context={
        **ctx,
        "themes": [_serialize_row(t) for t in themes],
        "tracking_map": tracking_map,
        "horizon": horizon,
        "min_confidence": min_confidence,
        "q": q,
    })
