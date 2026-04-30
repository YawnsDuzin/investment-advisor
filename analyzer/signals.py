"""이상 시그널 탐지 모듈 (로드맵 Step 3-2).

`stock_universe_ohlcv` 이력에서 당일(또는 지정 날짜) 기준 주목할 만한
이벤트를 단일 CTE 배치 쿼리로 추출하여 `market_signals` 테이블에 UPSERT한다.

UI-3 "오늘의 이상 시그널" 카드와 UI-5 워치리스트 알림의 데이터 소스.

탐지 시그널 (MVP, 필요 시 확장)
  - new_52w_high: 오늘 close = MAX(close) OVER (rn <= 252)
  - new_52w_low:  오늘 close = MIN(close) OVER (rn <= 252)
  - volume_surge: 오늘 volume >= AVG(volume rn 2~21) × 3
  - above_200ma_cross: 어제 close < ma200, 오늘 close >= ma200 (골든크로스 단순형)
  - below_200ma_cross: 어제 close > ma200, 오늘 close <= ma200 (데드크로스 단순형)
  - gap_up:   오늘 open >= 어제 close × 1.03
  - gap_down: 오늘 open <= 어제 close × 0.97

저장은 PK `(signal_date, signal_type, ticker, market)` — 재실행·백필 시 UPSERT로 멱등.
"""
from __future__ import annotations

import json
import time
from datetime import date

from shared.config import DatabaseConfig
from shared.db import get_connection
from shared.logger import get_logger

_log = get_logger("signals")


_UPSERT_SIGNAL_SQL = """
INSERT INTO market_signals (signal_date, signal_type, ticker, market, metric)
VALUES %s
ON CONFLICT (signal_date, signal_type, ticker, market) DO UPDATE SET
    metric = EXCLUDED.metric,
    created_at = NOW()
"""


# ── 탐지 쿼리 (단일 CTE로 6가지 시그널 동시 추출) ──
_DETECT_SQL = """
WITH ranked AS (
    SELECT ticker, UPPER(market) AS market, trade_date, open, high, low, close, volume,
           ROW_NUMBER() OVER (
               PARTITION BY ticker, UPPER(market)
               ORDER BY trade_date DESC
           ) AS rn
    FROM stock_universe_ohlcv
    WHERE trade_date >= CURRENT_DATE - 300
),
enriched AS (
    SELECT ticker, market,
           MAX(CASE WHEN rn = 1 THEN trade_date END)             AS latest_date,
           MAX(CASE WHEN rn = 1 THEN close::float END)           AS close_latest,
           MAX(CASE WHEN rn = 1 THEN open::float END)            AS open_latest,
           MAX(CASE WHEN rn = 1 THEN volume END)                 AS volume_latest,
           MAX(CASE WHEN rn = 2 THEN close::float END)           AS close_prev,
           MAX(close::float) FILTER (WHERE rn <= 252)            AS high_252d,
           MIN(close::float) FILTER (WHERE rn <= 252)            AS low_252d,
           AVG(close::float) FILTER (WHERE rn <= 200)            AS ma200_latest,
           AVG(close::float) FILTER (WHERE rn BETWEEN 2 AND 201) AS ma200_prev,
           AVG(volume::float) FILTER (WHERE rn BETWEEN 2 AND 21) AS volume_avg_20
    FROM ranked
    GROUP BY ticker, market
)
SELECT ticker, market, latest_date,
       close_latest, open_latest, volume_latest, close_prev,
       high_252d, low_252d,
       ma200_latest, ma200_prev, volume_avg_20
FROM enriched
WHERE latest_date = %s
  AND close_latest IS NOT NULL
"""


def detect_signals(db_cfg: DatabaseConfig, *, as_of: date | None = None,
                   volume_multiplier: float = 3.0,
                   gap_threshold_pct: float = 3.0) -> dict[str, int]:
    """as_of(기본: OHLCV 최신일) 기준 이상 시그널 일괄 탐지·UPSERT.

    Returns: {signal_type: count}
    """
    started = time.time()

    # as_of 미지정 시 OHLCV 테이블의 최신 trade_date 사용
    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            if as_of is None:
                cur.execute("SELECT MAX(trade_date) FROM stock_universe_ohlcv")
                row = cur.fetchone()
                as_of = row[0] if row and row[0] else None
                if as_of is None:
                    _log.warning("[signals] stock_universe_ohlcv 비어 있음 — 탐지 불가")
                    return {}
            cur.execute(_DETECT_SQL, (as_of,))
            rows = cur.fetchall()
    except Exception as e:
        _log.error(f"[signals] 탐지 쿼리 실패: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        conn.close()
        return {}
    finally:
        # 탐지 쿼리는 read-only — 여기선 닫지 말고 UPSERT까지 재사용
        pass

    signals_to_upsert: list[tuple] = []
    counts: dict[str, int] = {}

    for r in rows:
        (ticker, market, latest_date,
         close_latest, open_latest, volume_latest, close_prev,
         high_252d, low_252d,
         ma200_latest, ma200_prev, volume_avg_20) = r

        def _push(signal_type: str, metric: dict):
            signals_to_upsert.append(
                (latest_date, signal_type, ticker, market, json.dumps(metric, ensure_ascii=False))
            )
            counts[signal_type] = counts.get(signal_type, 0) + 1

        # 52주 신고가/신저가
        if high_252d is not None and close_latest >= high_252d - 1e-9:
            _push("new_52w_high", {"close": close_latest, "high_252d": high_252d})
        if low_252d is not None and close_latest <= low_252d + 1e-9:
            _push("new_52w_low", {"close": close_latest, "low_252d": low_252d})

        # 거래량 폭증
        if (volume_latest is not None and volume_avg_20 is not None
                and volume_avg_20 > 0
                and volume_latest >= volume_avg_20 * volume_multiplier):
            _push("volume_surge", {
                "volume": int(volume_latest),
                "avg20": round(volume_avg_20, 0),
                "ratio": round(float(volume_latest) / float(volume_avg_20), 2),
            })

        # 200MA 상/하향 돌파 (당일 close vs ma200, 전일 대비 교차)
        if (ma200_latest is not None and ma200_prev is not None
                and close_prev is not None):
            # 골든크로스: 전일 close<ma200_prev, 오늘 close>=ma200_latest
            if close_prev < ma200_prev and close_latest >= ma200_latest:
                _push("above_200ma_cross", {
                    "close": close_latest, "ma200": round(ma200_latest, 2),
                    "close_prev": close_prev, "ma200_prev": round(ma200_prev, 2),
                })
            # 데드크로스
            elif close_prev > ma200_prev and close_latest <= ma200_latest:
                _push("below_200ma_cross", {
                    "close": close_latest, "ma200": round(ma200_latest, 2),
                    "close_prev": close_prev, "ma200_prev": round(ma200_prev, 2),
                })

        # Gap up/down (당일 open vs 전일 close)
        if (close_prev is not None and open_latest is not None
                and close_prev > 0):
            gap_pct = (open_latest - close_prev) / close_prev * 100
            if gap_pct >= gap_threshold_pct:
                _push("gap_up", {
                    "open": open_latest, "prev_close": close_prev,
                    "gap_pct": round(gap_pct, 2),
                })
            elif gap_pct <= -gap_threshold_pct:
                _push("gap_down", {
                    "open": open_latest, "prev_close": close_prev,
                    "gap_pct": round(gap_pct, 2),
                })

    if signals_to_upsert:
        try:
            from psycopg2.extras import execute_values
            with conn.cursor() as cur:
                execute_values(cur, _UPSERT_SIGNAL_SQL, signals_to_upsert, page_size=1000)
            conn.commit()
        except Exception as e:
            conn.rollback()
            _log.error(f"[signals] UPSERT 실패: {e}")
            conn.close()
            return counts
    conn.close()

    duration = time.time() - started
    total = sum(counts.values())
    _log.info(
        f"[signals] {as_of} 기준 시그널 {total}건 탐지·저장 "
        f"({ {k: v for k, v in sorted(counts.items(), key=lambda x: -x[1])} }) / {duration*1000:.0f}ms"
    )
    return counts


# 워치리스트 알림 생성용 레이블 (routes/signals.py의 SIGNAL_LABELS와 동기)
_SIGNAL_LABELS_KR = {
    "new_52w_high": "52주 신고가",
    "new_52w_low": "52주 신저가",
    "volume_surge": "거래량 폭증",
    "above_200ma_cross": "200일 이평 상향 돌파",
    "below_200ma_cross": "200일 이평 하향 돌파",
    "gap_up": "갭 상승",
    "gap_down": "갭 하락",
}


def generate_watchlist_notifications(
    db_cfg: DatabaseConfig, *, as_of: date | None = None,
) -> int:
    """워치리스트에 등록된 ticker에 대해 오늘 탐지된 market_signals을
    `user_notifications`로 자동 발행 (UI-5 — 로드맵 Step 3-3).

    중복 방지: 같은 유저·같은 title·당일 created_at인 row가 이미 있으면 스킵.
    session_id / sub_id는 NULL (시스템 자동 알림).

    Returns: 생성된 알림 수
    """
    sql_signals = """
        SELECT signal_type, ticker, market, metric
        FROM market_signals
        WHERE signal_date = COALESCE(%s::date, (SELECT MAX(signal_date) FROM market_signals))
    """
    sql_watchers = """
        SELECT uw.user_id, uw.ticker, uw.asset_name
        FROM user_watchlist uw
        WHERE UPPER(uw.ticker) = %s
    """
    sql_insert = """
        INSERT INTO user_notifications (user_id, title, detail, link)
        SELECT %s, %s, %s, %s
        WHERE NOT EXISTS (
            SELECT 1 FROM user_notifications
            WHERE user_id = %s
              AND title = %s
              AND created_at::date = CURRENT_DATE
        )
    """

    conn = get_connection(db_cfg)
    created = 0
    try:
        with conn.cursor() as cur:
            cur.execute(sql_signals, (as_of,))
            signals = cur.fetchall()
            if not signals:
                return 0

            for signal_type, ticker, _market, metric in signals:
                label = _SIGNAL_LABELS_KR.get(signal_type, signal_type)
                ticker_upper = str(ticker).strip().upper()

                cur.execute(sql_watchers, (ticker_upper,))
                watchers = cur.fetchall()
                if not watchers:
                    continue

                for user_id, wl_ticker, asset_name in watchers:
                    display_name = asset_name or wl_ticker
                    title = f"{display_name} ({ticker_upper}) — {label}"
                    # metric을 한 줄 상세로
                    detail_parts: list[str] = []
                    if isinstance(metric, dict):
                        for k in ("close", "ratio", "gap_pct", "ma200", "high_252d", "low_252d"):
                            if k in metric and metric[k] is not None:
                                detail_parts.append(f"{k}={metric[k]}")
                    detail = ", ".join(detail_parts) if detail_parts else None
                    link = f"/pages/stocks/{ticker_upper}"

                    cur.execute(sql_insert, (
                        user_id, title, detail, link,
                        user_id, title,
                    ))
                    created += cur.rowcount
        conn.commit()
    except Exception as e:
        conn.rollback()
        _log.warning(f"[signals] 워치리스트 알림 생성 실패: {e}")
        return created
    finally:
        conn.close()

    _log.info(f"[signals] 워치리스트 매칭 알림 {created}건 생성")
    return created


def cleanup_old_signals(db_cfg: DatabaseConfig, retention_days: int = 90) -> int:
    """retention_days 초과 old signal 제거."""
    sql = "DELETE FROM market_signals WHERE signal_date < CURRENT_DATE - (%s::int)"
    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (retention_days,))
            deleted = cur.rowcount
        conn.commit()
    except Exception as e:
        conn.rollback()
        _log.warning(f"[signals] cleanup 실패: {e}")
        return 0
    finally:
        conn.close()
    _log.info(f"[signals] retention 초과 {deleted}건 삭제")
    return deleted
