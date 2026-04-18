"""추천 후 실제 수익률 추적 모듈

매일 배치에서 실행되어:
1. 추적 대상 제안(entry_price 있고, 추천 후 1년 이내)의 현재가 스냅샷 저장
2. 경과 기간에 따라 post_return_*_pct 계산·갱신
"""
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from shared.config import DatabaseConfig
from shared.db import get_connection
from shared.logger import get_logger
from psycopg2.extras import RealDictCursor

# 기간 정의: (컬럼명, 목표 거래일 수, 허용 오차일)
_RETURN_PERIODS = [
    ("post_return_1m_pct", 30, 5),
    ("post_return_3m_pct", 90, 7),
    ("post_return_6m_pct", 180, 10),
    ("post_return_1y_pct", 365, 14),
]


def _fetch_current_price(ticker: str, market: str) -> dict | None:
    """단일 종목 현재가 조회 — stock_data의 기존 함수 재활용"""
    from analyzer.stock_data import fetch_momentum_check
    result = fetch_momentum_check(ticker, market)
    if result and result.get("current_price"):
        return {
            "price": result["current_price"],
            "price_source": result.get("price_source", "unknown"),
        }
    return None


def run_price_tracking(db_cfg: DatabaseConfig) -> dict:
    """추적 대상 제안의 가격 스냅샷 저장 + post_return 계산

    Returns:
        {"tracked": int, "snapshots_saved": int, "returns_updated": int}
    """
    log = get_logger("price_tracker")
    today = date.today()
    cutoff = today - timedelta(days=365)

    # 1) 추적 대상 조회: entry_price 있고, 추천일 1년 이내, buy 제안
    conn = get_connection(db_cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT p.id, p.ticker, p.market, p.entry_price,
                       s.analysis_date
                FROM investment_proposals p
                JOIN investment_themes t ON p.theme_id = t.id
                JOIN analysis_sessions s ON t.session_id = s.id
                WHERE p.entry_price IS NOT NULL
                  AND p.action = 'buy'
                  AND s.analysis_date >= %s
                ORDER BY s.analysis_date DESC
            """, (cutoff,))
            targets = cur.fetchall()
    finally:
        conn.close()

    if not targets:
        log.info("[가격추적] 추적 대상 없음")
        return {"tracked": 0, "snapshots_saved": 0, "returns_updated": 0}

    # 중복 티커 제거 (같은 종목 여러 제안 가능 → 1회만 조회)
    unique_tickers = {}
    for t in targets:
        key = (t["ticker"], t["market"] or "")
        if key not in unique_tickers:
            unique_tickers[key] = None

    log.info(f"[가격추적] 대상 제안 {len(targets)}건, 고유 종목 {len(unique_tickers)}건")

    # 2) 병렬 현재가 조회
    prices = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {}
        for (ticker, market) in unique_tickers:
            f = pool.submit(_fetch_current_price, ticker, market)
            futures[f] = (ticker, market)

        for f in as_completed(futures):
            ticker, market = futures[f]
            try:
                result = f.result()
                if result:
                    prices[(ticker, market)] = result
            except Exception as e:
                log.warning(f"[가격추적] {ticker} 조회 실패: {e}")

    log.info(f"[가격추적] {len(prices)}/{len(unique_tickers)}건 가격 조회 성공")

    # 3) 스냅샷 저장 + post_return 계산
    snapshots_saved = 0
    returns_updated = 0

    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            for t in targets:
                key = (t["ticker"], t["market"] or "")
                price_data = prices.get(key)
                if not price_data:
                    continue

                current_price = price_data["price"]
                price_source = price_data["price_source"]

                # 스냅샷 저장 (UPSERT)
                cur.execute("""
                    INSERT INTO proposal_price_snapshots
                        (proposal_id, snapshot_date, price, price_source)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (proposal_id, snapshot_date) DO UPDATE
                        SET price = EXCLUDED.price,
                            price_source = EXCLUDED.price_source
                """, (t["id"], today, current_price, price_source))
                snapshots_saved += 1

                # post_return 계산
                entry_price = float(t["entry_price"])
                analysis_date = t["analysis_date"]
                if entry_price <= 0:
                    continue

                days_elapsed = (today - analysis_date).days
                updated = False

                for col, target_days, tolerance in _RETURN_PERIODS:
                    # 아직 해당 기간이 안 됐으면 스킵
                    if days_elapsed < target_days - tolerance:
                        continue

                    # 스냅샷에서 해당 기간에 가장 가까운 가격 조회
                    target_date = analysis_date + timedelta(days=target_days)
                    cur.execute("""
                        SELECT price FROM proposal_price_snapshots
                        WHERE proposal_id = %s
                          AND snapshot_date BETWEEN %s AND %s
                        ORDER BY ABS(snapshot_date - %s::date)
                        LIMIT 1
                    """, (t["id"],
                          target_date - timedelta(days=tolerance),
                          target_date + timedelta(days=tolerance),
                          target_date))
                    row = cur.fetchone()

                    if row:
                        snap_price = float(row[0])
                        ret_pct = round((snap_price - entry_price) / entry_price * 100, 2)
                        cur.execute(
                            f"UPDATE investment_proposals SET {col} = %s WHERE id = %s",
                            (ret_pct, t["id"])
                        )
                        updated = True

                # 추천 후 경과가 충분하지 않은 기간은 오늘 스냅샷으로 최신 계산
                # (아직 1M 안 됐어도 "현재까지 수익률" 계산 → 가장 짧은 미달 기간에 기록)
                if not updated and days_elapsed >= 1:
                    # 최소 기간(1M)에도 못 미치면, 최신 가격 기반 임시 수익률은 저장하지 않음
                    # → post_return은 정확한 기간 도달 시에만 기록
                    pass

                if updated:
                    returns_updated += 1

        conn.commit()
    finally:
        conn.close()

    log.info(f"[가격추적] 스냅샷 {snapshots_saved}건 저장, 수익률 {returns_updated}건 갱신")
    return {
        "tracked": len(targets),
        "snapshots_saved": snapshots_saved,
        "returns_updated": returns_updated,
    }
