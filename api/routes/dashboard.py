"""대시보드(/) 페이지 라우트 — B1: pages.py에서 이전.

복잡도: dashboard 함수는 약 300줄 — 어제 대비 변화·테마 요약·발굴유형 분포·
sector 카운트·all_proposals 정렬 등 단일 페이지 다수 쿼리. 본문은 무변경 이전.
"""
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from psycopg2.extras import RealDictCursor

from shared.tier_limits import TIER_INFO
from api.serialization import serialize_row as _serialize_row
from api.templates_provider import templates
from api.deps import get_db_conn, make_page_ctx

_logger = logging.getLogger(__name__)

# market_indices_ohlcv index_code → 표시 라벨 (analyzer/regime.py 와 동일 매핑)
_MARKET_QUOTE_INDEX_CODES = ("KOSPI", "KOSDAQ", "SP500", "NDX100")
_MARKET_QUOTE_LABELS = {
    "KOSPI": "KOSPI",
    "KOSDAQ": "KOSDAQ",
    "SP500": "S&P 500",
    "NDX100": "Nasdaq 100",
}
_MARKET_QUOTE_KR_CODES = ("KOSPI", "KOSDAQ")
_MARKET_QUOTE_US_CODES = ("SP500", "NDX100")
_MARKET_QUOTE_WINDOW = 21        # sparkline 포인트 수 (영업일)
_MARKET_QUOTE_LOOKBACK_DAYS = 60  # 영업일 21개 확보용 캘린더 윈도우


def _fetch_market_quotes(cur) -> dict:
    """market_indices_ohlcv 에서 4개 인덱스 × 21영업일 EOD 데이터를 조회해
    대시보드 시세 바 카드 렌더용 dict 로 가공.

    Returns
    -------
    {
        "indices": [
            {"code", "label", "trade_date", "close", "change_pct",
             "change_abs", "spark_points", "trend"},
            ...
        ],
        "meta": {"kr_trade_date", "us_trade_date"},
    }

    결측 정책 (spec §6 참조):
      - 0 row: indices=[] 반환 → 호출자는 partial 자체 비표시
      - 1 row: change_pct=None, spark_points=[close 1개], trend="flat"
      - 2~ row: change_pct/spark_points 모두 가용한 만큼
      - SQL 예외: 호출자가 try/except 로 처리 (helper 내부 catch 안 함)
    """
    cur.execute(
        """
        WITH recent AS (
            SELECT index_code, trade_date, close::float AS close,
                   ROW_NUMBER() OVER (PARTITION BY index_code ORDER BY trade_date DESC) AS rn
            FROM market_indices_ohlcv
            WHERE index_code = ANY(%s)
              AND trade_date >= CURRENT_DATE - %s
        )
        SELECT index_code, trade_date, close
        FROM recent
        WHERE rn <= %s
        ORDER BY index_code, trade_date ASC
        """,
        (list(_MARKET_QUOTE_INDEX_CODES), _MARKET_QUOTE_LOOKBACK_DAYS, _MARKET_QUOTE_WINDOW),
    )
    rows = cur.fetchall()

    by_code: dict[str, list] = {}
    for r in rows:
        by_code.setdefault(r["index_code"], []).append(r)

    indices = []
    for code in _MARKET_QUOTE_INDEX_CODES:
        bucket = by_code.get(code)
        if not bucket:
            continue
        spark_points = [float(r["close"]) for r in bucket]
        close = spark_points[-1]
        if len(spark_points) >= 2:
            prev = spark_points[-2]
            change_abs = close - prev
            change_pct = round((close - prev) / prev * 100, 2) if prev else None
        else:
            change_abs = None
            change_pct = None
        if change_pct is None:
            trend = "flat"
        elif change_pct > 0:
            trend = "up"
        elif change_pct < 0:
            trend = "down"
        else:
            trend = "flat"
        indices.append({
            "code": code,
            "label": _MARKET_QUOTE_LABELS[code],
            "trade_date": bucket[-1]["trade_date"],
            "close": close,
            "change_abs": change_abs,
            "change_pct": change_pct,
            "spark_points": spark_points,
            "trend": trend,
        })

    def _latest_for(codes):
        dates = [ix["trade_date"] for ix in indices if ix["code"] in codes]
        return max(dates) if dates else None

    return {
        "indices": indices,
        "meta": {
            "kr_trade_date": _latest_for(_MARKET_QUOTE_KR_CODES),
            "us_trade_date": _latest_for(_MARKET_QUOTE_US_CODES),
        },
    }


pages_router = APIRouter(tags=["대시보드"])


# ──────────────────────────────────────────────
# Dashboard (Home) — 어제 대비 변화 + 투자 신호
# ──────────────────────────────────────────────
@pages_router.get("/")
def dashboard(conn = Depends(get_db_conn), ctx: dict = Depends(make_page_ctx("dashboard"))):
    # 인증 활성 + 비로그인 → 랜딩 페이지로 안내 (UI-16)
    if ctx["auth_enabled"] and ctx["_user"] is None:
        return RedirectResponse(url="/pages/landing", status_code=302)

    user = ctx["_user"]
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # 시세 바 (regime banner 위 — spec §3.1) — early return 분기에서도 안전하도록 미리 None 초기화
        market_quotes = None

        # 최신 세션
        cur.execute("SELECT * FROM analysis_sessions ORDER BY analysis_date DESC LIMIT 1")
        session = cur.fetchone()
        if not session:
            return templates.TemplateResponse(
                request=ctx["request"], name="dashboard.html",
                context={**ctx, "session": None, "market_quotes": market_quotes},
            )

        session_id = session["id"]
        today_date = session["analysis_date"]

        # 이슈 수
        cur.execute("SELECT COUNT(*) AS cnt FROM global_issues WHERE session_id = %s", (session_id,))
        issue_count = cur.fetchone()["cnt"]

        # 테마 (요약만 — 시나리오/제안 상세는 세션 상세에서)
        cur.execute(
            "SELECT * FROM investment_themes WHERE session_id = %s ORDER BY confidence_score DESC",
            (session_id,)
        )
        themes = cur.fetchall()

        buy_count = 0
        total_alloc = 0.0
        high_conviction_count = 0
        early_signal_count = 0
        discovery_counts = {}  # discovery_type별 카운트
        sector_counts = {}    # 섹터별 카운트
        all_proposals = []
        for theme in themes:
            cur.execute(
                "SELECT * FROM investment_proposals WHERE theme_id = %s ORDER BY target_allocation DESC",
                (theme["id"],)
            )
            proposals = cur.fetchall()
            theme["proposals"] = [_serialize_row(p) for p in proposals]
            for p in proposals:
                if p.get("action") == "buy":
                    buy_count += 1
                total_alloc += float(p.get("target_allocation") or 0)
                # 추가 통계
                if p.get("conviction") == "high":
                    high_conviction_count += 1
                dt = p.get("discovery_type") or "unknown"
                discovery_counts[dt] = discovery_counts.get(dt, 0) + 1
                if dt == "early_signal":
                    early_signal_count += 1
                sec = p.get("sector")
                if sec:
                    sector_counts[sec] = sector_counts.get(sec, 0) + 1
                all_proposals.append(p)

        # 상위 섹터 (최대 5개)
        top_sectors = sorted(sector_counts.items(), key=lambda x: -x[1])[:5]
        # 평균 신뢰도
        avg_confidence = 0.0
        if themes:
            avg_confidence = sum(float(t.get("confidence_score") or 0) for t in themes) / len(themes)

        # ── 추적 데이터 ──
        cur.execute("""
            SELECT * FROM theme_tracking WHERE last_seen_date = %s
            ORDER BY streak_days DESC, appearances DESC
        """, (today_date,))
        active_tracking = [_serialize_row(r) for r in cur.fetchall()]

        # 소멸 테마
        cur.execute("""
            SELECT * FROM theme_tracking
            WHERE last_seen_date < %s
              AND last_seen_date >= %s::date - INTERVAL '3 days'
            ORDER BY last_seen_date DESC
        """, (today_date, today_date))
        disappeared_themes = [_serialize_row(r) for r in cur.fetchall()]

        # ── 뉴스 기사 (카테고리별 그룹핑) ──
        cur.execute("""
            SELECT category, source, title, title_ko, summary, summary_ko, link, published
            FROM news_articles
            WHERE session_id = %s
            ORDER BY category, id
        """, (session_id,))
        raw_news = cur.fetchall()

        # 워치리스트 + 알림 구독 (로그인 사용자)
        watched_tickers = set()
        subscribed_tickers = set()
        subscribed_theme_keys = set()
        if user:
            cur.execute("SELECT ticker FROM user_watchlist WHERE user_id = %s", (user.id,))
            watched_tickers = {r["ticker"] for r in cur.fetchall()}

            cur.execute(
                "SELECT sub_type, sub_key FROM user_subscriptions WHERE user_id = %s",
                (user.id,),
            )
            for r in cur.fetchall():
                if r["sub_type"] == "ticker":
                    subscribed_tickers.add((r["sub_key"] or "").upper())
                elif r["sub_type"] == "theme":
                    subscribed_theme_keys.add(r["sub_key"])

        # ── Top Picks 조회 (v15) ──
        cur.execute("""
            SELECT dtp.rank, dtp.proposal_id, dtp.score_rule, dtp.score_final,
                   dtp.score_breakdown, dtp.rationale_text, dtp.key_risk, dtp.source,
                   p.ticker, p.asset_name, p.sector, p.currency, p.action,
                   p.conviction, p.discovery_type, p.price_momentum_check,
                   p.current_price, p.target_price_low, p.target_price_high,
                   p.upside_pct, p.price_source, p.target_allocation,
                   p.return_1m_pct, p.return_3m_pct, p.return_6m_pct, p.return_1y_pct,
                   p.rationale AS proposal_rationale, p.market,
                   p.foreign_net_buy_signal, p.squeeze_risk,
                   t.theme_name, t.theme_key, t.confidence_score AS theme_confidence,
                   EXISTS(SELECT 1 FROM stock_analyses sa WHERE sa.proposal_id = p.id) AS has_stock_analysis,
                   u.sector_norm, u.market_cap_bucket
            FROM daily_top_picks dtp
            JOIN investment_proposals p ON p.id = dtp.proposal_id
            JOIN investment_themes t    ON t.id = p.theme_id
            LEFT JOIN stock_universe u  ON UPPER(u.ticker) = UPPER(p.ticker)
                                       AND UPPER(u.market)  = UPPER(p.market)
            WHERE dtp.analysis_date = %s
            ORDER BY dtp.rank
        """, (today_date,))
        top_picks_raw = cur.fetchall()

        # 국채 금리 데이터 (bond_yields 테이블 — v20)
        bond_yields = None
        try:
            cur.execute("""
                SELECT * FROM bond_yields
                WHERE session_id = %s
                ORDER BY snapshot_date DESC LIMIT 1
            """, (session_id,))
            by_row = cur.fetchone()
            if by_row:
                bond_yields = _serialize_row(by_row)
        except Exception:
            pass  # v20 미적용 환경(테이블 미존재)에서도 동작

        # ── 전일 세션 대비 변화량 (delta) ──
        prev_issue_count = 0
        prev_theme_count = 0
        prev_buy_count = 0
        cur.execute("""
            SELECT id FROM analysis_sessions
            WHERE analysis_date < %s
            ORDER BY analysis_date DESC LIMIT 1
        """, (today_date,))
        prev_session = cur.fetchone()
        if prev_session:
            prev_sid = prev_session["id"]
            cur.execute("SELECT COUNT(*) AS cnt FROM global_issues WHERE session_id = %s", (prev_sid,))
            prev_issue_count = cur.fetchone()["cnt"]
            cur.execute("SELECT COUNT(*) AS cnt FROM investment_themes WHERE session_id = %s", (prev_sid,))
            prev_theme_count = cur.fetchone()["cnt"]
            cur.execute("""
                SELECT COUNT(*) AS cnt FROM investment_proposals ip
                JOIN investment_themes it ON ip.theme_id = it.id
                WHERE it.session_id = %s AND ip.action = 'buy'
            """, (prev_sid,))
            prev_buy_count = cur.fetchone()["cnt"]

        # ── 최근 7일 히스토리 (스파크라인) ──
        cur.execute("""
            SELECT s.analysis_date,
                   (SELECT COUNT(*) FROM global_issues WHERE session_id = s.id) AS issue_cnt,
                   (SELECT COUNT(*) FROM investment_themes WHERE session_id = s.id) AS theme_cnt,
                   (SELECT COUNT(*) FROM investment_proposals ip
                    JOIN investment_themes it ON ip.theme_id = it.id
                    WHERE it.session_id = s.id AND ip.action = 'buy') AS buy_cnt
            FROM analysis_sessions s
            ORDER BY s.analysis_date DESC LIMIT 7
        """)
        history_rows = cur.fetchall()

        # 워치리스트 ∩ 오늘 분석 크로스 매칭
        watched_in_today = []
        if user and watched_tickers:
            for ticker in watched_tickers:
                for theme in themes:
                    for p in (theme.get("proposals") or []):
                        if (p.get("ticker") or "").upper() == ticker.upper():
                            pick_rank = None
                            for pk_raw in top_picks_raw:
                                if (pk_raw.get("ticker") or "").upper() == ticker.upper():
                                    pick_rank = pk_raw.get("rank")
                                    break
                            watched_in_today.append({
                                "ticker": p.get("ticker"),
                                "asset_name": p.get("asset_name"),
                                "current_price": p.get("current_price"),
                                "currency": p.get("currency"),
                                "market": p.get("market"),
                                "action": p.get("action"),
                                "in_top_picks": pick_rank is not None,
                                "pick_rank": pick_rank,
                            })
                            break

        # ── 시세 바 (regime banner 위 — spec §3.1, §6) ──
        try:
            market_quotes = _fetch_market_quotes(cur)
        except Exception as e:
            # market_indices_ohlcv 미존재 환경(백필 이전)에서도 페이지 동작.
            # spec §6: SQL 예외 → 로그 WARNING + 배너 자체 비표시.
            _logger.warning("market_quotes 조회 실패 — 배너 비표시: %s", e)
            market_quotes = None

    # Top Picks 직렬화 + 개인화 플래그 주입
    top_picks = []
    for row in top_picks_raw:
        pk = _serialize_row(row)
        tk = (pk.get("ticker") or "").upper()
        pk["is_watched"] = tk in watched_tickers
        pk["is_subscribed"] = (
            tk in subscribed_tickers or pk.get("theme_key") in subscribed_theme_keys
        )
        # 불릿 차트용: 현재가/목표가 비율
        cp = float(pk.get("current_price") or 0)
        tp = float(pk.get("target_price_low") or 0)
        pk["price_pct"] = round(cp / tp * 100, 1) if tp > 0 and cp > 0 else None
        top_picks.append(pk)

    # 뉴스를 카테고리별로 그룹핑
    from analyzer.news_collector import CATEGORY_LABELS
    news_by_category = {}
    for row in raw_news:
        cat = row["category"]
        if cat not in news_by_category:
            news_by_category[cat] = {
                "label": CATEGORY_LABELS.get(cat, cat),
                "articles": [],
            }
        news_by_category[cat]["articles"].append(_serialize_row(row))

    # 스파크라인 SVG 좌표 생성 (최근→과거 역순 → 시간순으로 뒤집기)
    def _spark_points(values, w=60, h=18):
        if not values or len(values) < 2:
            return ""
        mn, mx = min(values), max(values)
        rng = mx - mn if mx != mn else 1
        pts = []
        for i, v in enumerate(values):
            x = round(i / (len(values) - 1) * w, 1)
            y = round(h - (v - mn) / rng * (h - 2) - 1, 1)
            pts.append(f"{x},{y}")
        return " ".join(pts)

    hist_reversed = list(reversed(history_rows))  # 시간순
    spark_issues = _spark_points([r["issue_cnt"] for r in hist_reversed])
    spark_themes = _spark_points([r["theme_cnt"] for r in hist_reversed])
    spark_buys = _spark_points([r["buy_cnt"] for r in hist_reversed])

    # 리스크 온도 → 게이지 수치 매핑
    risk_temp = (_serialize_row(session) if session else {}).get("risk_temperature", "")
    risk_map = {"low": 20, "medium": 55, "high": 85}
    risk_pct = risk_map.get((risk_temp or "").lower(), 50)

    # 테마 뷰 한도 (tier_limits 참조)
    from shared.tier_limits import THEME_VIEW_LIMITS, normalize_tier
    theme_view_limit = THEME_VIEW_LIMITS.get(normalize_tier(ctx.get("tier")), None)

    return templates.TemplateResponse(request=ctx["request"], name="dashboard.html", context={
        **ctx,
        "session": _serialize_row(session),
        "themes": [_serialize_row(t) for t in themes],
        "issue_count": issue_count,
        "theme_count": len(themes),
        "buy_count": buy_count,
        "total_alloc": total_alloc,
        "high_conviction_count": high_conviction_count,
        "early_signal_count": early_signal_count,
        "discovery_counts": discovery_counts,
        "top_sectors": top_sectors,
        "avg_confidence": avg_confidence,
        "active_tracking": active_tracking,
        "disappeared_themes": disappeared_themes,
        "news_by_category": news_by_category,
        "watched_tickers": watched_tickers,
        "top_picks": top_picks,
        "bond_yields": bond_yields,
        # 신규 데이터
        "issue_delta": issue_count - prev_issue_count,
        "theme_delta": len(themes) - prev_theme_count,
        "buy_delta": buy_count - prev_buy_count,
        "spark_issues": spark_issues,
        "spark_themes": spark_themes,
        "spark_buys": spark_buys,
        "risk_pct": risk_pct,
        "watched_in_today": watched_in_today,
        "theme_view_limit": theme_view_limit,
        "market_quotes": market_quotes,
    })
