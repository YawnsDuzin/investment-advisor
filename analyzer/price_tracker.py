"""추천 후 실제 수익률 추적 모듈 — OHLCV 이력 우선 (로드맵 A3)

동작 순서:
  1. 추적 대상 proposal (entry_price 有 + action=buy + 추천 1y 이내) SELECT
  2. 종목별 OHLCV 이력(추천일~오늘) 배치 조회
  3. 각 proposal에 대해:
       a. OHLCV 있으면 → post_return_1m/3m/6m/1y + max_drawdown_pct 계산 (외부 API 호출 無)
       b. OHLCV 없으면 → 기존 방식(live 조회 + proposal_price_snapshots) 폴백
  4. 선택적으로 오늘 스냅샷도 snapshots에 기록 (히스토리 보존 목적)

OHLCV 경로는 외부 API 호출이 사라지므로 배치 시간·안정성 대폭 개선.
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


def _fetch_ohlcv_range(
    db_cfg: DatabaseConfig,
    tickers: list[tuple[str, str]],
    from_date: date,
    to_date: date,
) -> "dict[tuple[str, str], list[tuple[date, float]]]":
    """다중 (ticker, market)에 대해 [from_date, to_date] 범위 (trade_date, close) 이력 일괄 조회.

    Returns:
        {(ticker_upper, market_upper): [(trade_date, close_float), ...] 오래된 순}
        OHLCV 결측 종목은 딕셔너리에서 제외.
    """
    if not tickers:
        return {}

    pairs = [(t.strip().upper(), (m or "").strip().upper()) for t, m in tickers]
    placeholders = ",".join(["(%s, %s)"] * len(pairs))
    flat_args: list = []
    for tk, mk in pairs:
        flat_args.extend([tk, mk])
    flat_args.extend([from_date, to_date])

    sql = f"""
    WITH targets (ticker, market) AS (
        VALUES {placeholders}
    )
    SELECT UPPER(o.ticker) AS ticker, UPPER(o.market) AS market,
           o.trade_date, o.close::float
    FROM stock_universe_ohlcv o
    JOIN targets t
      ON UPPER(o.ticker) = t.ticker
     AND (t.market = '' OR UPPER(o.market) = t.market)
    WHERE o.trade_date BETWEEN %s AND %s
    ORDER BY ticker, market, o.trade_date
    """

    history_map: dict[tuple[str, str], list[tuple[date, float]]] = {}
    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, flat_args)
            rows = cur.fetchall()
    except Exception as e:
        get_logger("가격추적").warning(f"OHLCV 배치 조회 실패: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return {}
    finally:
        conn.close()

    # market이 비어 있는 target은 (ticker, '') 키로, DB는 실제 market 값 → 기본 매칭 키는 실 market
    # 호출자가 (ticker, '')로 입력해도 OHLCV의 실제 market 값과 pairing 가능하도록 두 키 모두 저장
    for ticker, market, trade_date, close in rows:
        history_map.setdefault((ticker, market), []).append((trade_date, float(close)))
        # 빈 market로도 찾을 수 있게
        history_map.setdefault((ticker, ""), []).append((trade_date, float(close)))

    # dedup (빈 market 매핑은 중복될 수 있음)
    for k, v in history_map.items():
        if k[1] == "":
            # 중복 제거: 동일 trade_date가 다른 market에서 올 수 있음 (보통은 1개)
            seen = set()
            unique = []
            for d, p in v:
                if d not in seen:
                    seen.add(d)
                    unique.append((d, p))
            unique.sort()
            history_map[k] = unique

    return history_map


def _price_on_or_near(
    history: "list[tuple[date, float]]", target: date, tolerance_days: int
) -> "tuple[date, float] | None":
    """target 일자 ± tolerance_days 안의 가장 가까운 거래일 (date, close) 반환. 없으면 None."""
    best: tuple[date, float] | None = None
    best_diff = tolerance_days + 1
    for trade_date, close in history:
        diff = abs((trade_date - target).days)
        if diff <= tolerance_days and diff < best_diff:
            best = (trade_date, close)
            best_diff = diff
            if diff == 0:
                break
    return best


def _compute_returns_from_ohlcv(
    history: "list[tuple[date, float]]",
    entry_price: float,
    analysis_date: date,
    today: date,
) -> dict:
    """OHLCV 이력에서 post_return_* + max_drawdown 계산.

    Returns:
        {
            "post_return_1m_pct": float|None, ...,
            "max_drawdown_pct": float|None,
            "max_drawdown_date": date|None,
        }
        history가 비어있거나 entry_price <= 0이면 모든 값 None.
    """
    result: dict = {
        "post_return_1m_pct": None,
        "post_return_3m_pct": None,
        "post_return_6m_pct": None,
        "post_return_1y_pct": None,
        "max_drawdown_pct": None,
        "max_drawdown_date": None,
    }
    if not history or entry_price <= 0:
        return result

    # 추천일 이후 이력만 사용 (추천일 당일 포함)
    post = [(d, p) for d, p in history if d >= analysis_date]
    if not post:
        return result

    days_elapsed = (today - analysis_date).days

    # post_return_* 계산
    for col, target_days, tolerance in _RETURN_PERIODS:
        if days_elapsed < target_days - tolerance:
            continue
        target_date = analysis_date + timedelta(days=target_days)
        pick = _price_on_or_near(post, target_date, tolerance)
        if pick:
            _, close = pick
            result[col] = round((close - entry_price) / entry_price * 100, 2)

    # max_drawdown — 추천일 이후 최저 close 대비 entry_price
    low_date, low_price = min(post, key=lambda x: x[1])
    if low_price < entry_price:
        result["max_drawdown_pct"] = round((low_price - entry_price) / entry_price * 100, 2)
        result["max_drawdown_date"] = low_date
    else:
        # 한 번도 entry 아래로 간 적 없음 → drawdown 0.0 (None보다 의미 있음)
        result["max_drawdown_pct"] = 0.0
        result["max_drawdown_date"] = low_date

    return result


# ── Live fallback (OHLCV 결측 종목용) ──

def _fetch_current_price_live(ticker: str, market: str) -> dict | None:
    """단일 종목 현재가 조회 — OHLCV 결측 종목 폴백용"""
    from analyzer.stock_data import fetch_momentum_check
    result = fetch_momentum_check(ticker, market)
    if result and result.get("current_price"):
        return {
            "price": result["current_price"],
            "price_source": result.get("price_source", "unknown"),
        }
    return None


def _compute_returns_from_snapshots(
    cur,
    proposal_id: int,
    entry_price: float,
    analysis_date: date,
    today: date,
) -> dict:
    """legacy 경로 — proposal_price_snapshots에서 post_return 계산.

    OHLCV가 없는 종목(신규 상장·상폐 등)에 대한 폴백. max_drawdown도 snapshots 기반 산출.
    """
    result: dict = {
        "post_return_1m_pct": None,
        "post_return_3m_pct": None,
        "post_return_6m_pct": None,
        "post_return_1y_pct": None,
        "max_drawdown_pct": None,
        "max_drawdown_date": None,
    }
    if entry_price <= 0:
        return result

    days_elapsed = (today - analysis_date).days

    for col, target_days, tolerance in _RETURN_PERIODS:
        if days_elapsed < target_days - tolerance:
            continue
        target_date = analysis_date + timedelta(days=target_days)
        cur.execute("""
            SELECT price FROM proposal_price_snapshots
            WHERE proposal_id = %s
              AND snapshot_date BETWEEN %s AND %s
            ORDER BY ABS(snapshot_date - %s::date)
            LIMIT 1
        """, (proposal_id,
              target_date - timedelta(days=tolerance),
              target_date + timedelta(days=tolerance),
              target_date))
        row = cur.fetchone()
        if row:
            snap_price = float(row[0])
            result[col] = round((snap_price - entry_price) / entry_price * 100, 2)

    # max_drawdown (스냅샷 기반)
    cur.execute("""
        SELECT snapshot_date, price FROM proposal_price_snapshots
        WHERE proposal_id = %s AND snapshot_date >= %s
        ORDER BY price ASC
        LIMIT 1
    """, (proposal_id, analysis_date))
    row = cur.fetchone()
    if row:
        low_date, low_price = row[0], float(row[1])
        if low_price < entry_price:
            result["max_drawdown_pct"] = round((low_price - entry_price) / entry_price * 100, 2)
            result["max_drawdown_date"] = low_date
        else:
            result["max_drawdown_pct"] = 0.0
            result["max_drawdown_date"] = low_date

    return result


def run_price_tracking(db_cfg: DatabaseConfig) -> dict:
    """추적 대상 제안의 post_return + max_drawdown 계산·갱신.

    소스 우선순위: OHLCV 이력 → live + snapshots 폴백.

    Returns:
        {"tracked": int, "snapshots_saved": int, "returns_updated": int,
         "ohlcv_source_count": int, "live_fallback_count": int}
    """
    log = get_logger("price_tracker")
    today = date.today()
    cutoff = today - timedelta(days=365)

    # 1) 추적 대상 조회
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
        return {
            "tracked": 0, "snapshots_saved": 0, "returns_updated": 0,
            "ohlcv_source_count": 0, "live_fallback_count": 0,
        }

    # 2) 고유 (ticker, market) 목록 + 각 종목의 가장 이른 analysis_date
    earliest_by_key: dict[tuple[str, str], date] = {}
    for t in targets:
        key = (t["ticker"].strip().upper(), (t["market"] or "").strip().upper())
        ad = t["analysis_date"]
        if key not in earliest_by_key or ad < earliest_by_key[key]:
            earliest_by_key[key] = ad

    unique_tickers = list(earliest_by_key.keys())
    from_date = min(earliest_by_key.values())

    log.info(
        f"[가격추적] 대상 제안 {len(targets)}건, 고유 종목 {len(unique_tickers)}건, "
        f"OHLCV 범위 {from_date}~{today}"
    )

    # 3) OHLCV 이력 배치 조회
    history_map = _fetch_ohlcv_range(db_cfg, unique_tickers, from_date, today)
    ohlcv_covered = {k for k in history_map if history_map[k]}  # (ticker, market)
    log.info(
        f"[가격추적] OHLCV 이력 확보 {len([k for k in unique_tickers if k in ohlcv_covered or (k[0], '') in ohlcv_covered])}"
        f"/{len(unique_tickers)}건"
    )

    # 4) OHLCV 결측 종목 live 폴백 (오늘 가격만, snapshots UPSERT 용도)
    missing_tickers = [
        (tk, mk) for (tk, mk) in unique_tickers
        if (tk, mk) not in ohlcv_covered and (tk, "") not in ohlcv_covered
    ]
    live_prices: dict[tuple[str, str], dict] = {}
    if missing_tickers:
        log.info(f"[가격추적] OHLCV 결측 {len(missing_tickers)}건 → live 조회")
        with ThreadPoolExecutor(max_workers=min(len(missing_tickers), 6)) as pool:
            futures = {
                pool.submit(_fetch_current_price_live, tk, mk): (tk, mk)
                for tk, mk in missing_tickers
            }
            for f in as_completed(futures):
                tk, mk = futures[f]
                try:
                    r = f.result()
                    if r:
                        live_prices[(tk, mk)] = r
                except Exception as e:
                    log.warning(f"[가격추적] {tk} live 조회 실패: {e}")

    # 5) 제안별 계산 + UPDATE
    snapshots_saved = 0
    returns_updated = 0
    ohlcv_source_count = 0
    live_fallback_count = 0

    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            for t in targets:
                tk = t["ticker"].strip().upper()
                mk = (t["market"] or "").strip().upper()
                entry_price = float(t["entry_price"]) if t["entry_price"] else 0.0
                analysis_date = t["analysis_date"]
                if entry_price <= 0:
                    continue

                history = history_map.get((tk, mk)) or history_map.get((tk, ""))
                metrics: dict
                if history:
                    metrics = _compute_returns_from_ohlcv(
                        history, entry_price, analysis_date, today
                    )
                    ohlcv_source_count += 1
                else:
                    # legacy: snapshots 기반
                    live = live_prices.get((tk, mk))
                    if live:
                        # 오늘 스냅샷 UPSERT (결측 종목만)
                        cur.execute("""
                            INSERT INTO proposal_price_snapshots
                                (proposal_id, snapshot_date, price, price_source)
                            VALUES (%s, %s, %s, %s)
                            ON CONFLICT (proposal_id, snapshot_date) DO UPDATE
                                SET price = EXCLUDED.price,
                                    price_source = EXCLUDED.price_source
                        """, (t["id"], today, live["price"], live["price_source"]))
                        snapshots_saved += 1
                    metrics = _compute_returns_from_snapshots(
                        cur, t["id"], entry_price, analysis_date, today
                    )
                    live_fallback_count += 1

                # UPDATE — NULL이 아닌 값만 반영
                set_parts: list[str] = []
                params: list = []
                for col in ("post_return_1m_pct", "post_return_3m_pct",
                            "post_return_6m_pct", "post_return_1y_pct",
                            "max_drawdown_pct"):
                    if metrics.get(col) is not None:
                        set_parts.append(f"{col} = %s")
                        params.append(metrics[col])
                if metrics.get("max_drawdown_date") is not None:
                    set_parts.append("max_drawdown_date = %s")
                    params.append(metrics["max_drawdown_date"])
                if set_parts:
                    params.append(t["id"])
                    cur.execute(
                        f"UPDATE investment_proposals SET {', '.join(set_parts)} WHERE id = %s",
                        params,
                    )
                    returns_updated += 1

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    log.info(
        f"[가격추적] 수익률 {returns_updated}건 갱신 "
        f"(출처: ohlcv={ohlcv_source_count} live_fallback={live_fallback_count}) "
        f"+ 결측 스냅샷 {snapshots_saved}건"
    )
    return {
        "tracked": len(targets),
        "snapshots_saved": snapshots_saved,
        "returns_updated": returns_updated,
        "ohlcv_source_count": ohlcv_source_count,
        "live_fallback_count": live_fallback_count,
    }
