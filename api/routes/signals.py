"""이상 시그널 조회 API (로드맵 Step 3-3).

`market_signals` 테이블(v32)에서 클라이언트에게 제공하는 얇은 조회 레이어.
UI-3 대시보드 '오늘의 이상 시그널' 카드와 향후 시그널 상세 페이지·워치리스트
알림 센터가 소비.
"""
from fastapi import APIRouter, Query, Depends
from psycopg2.extras import RealDictCursor

from api.deps import get_db_conn
from api.serialization import serialize_row as _serialize_row


router = APIRouter(prefix="/api/signals", tags=["이상 시그널"])


# 각 signal_type의 한글 레이블 (프론트 표시용)
SIGNAL_LABELS = {
    "new_52w_high": "52주 신고가",
    "new_52w_low": "52주 신저가",
    "volume_surge": "거래량 폭증",
    "above_200ma_cross": "200MA 상향 돌파",
    "below_200ma_cross": "200MA 하향 돌파",
    "gap_up": "갭 상승",
    "gap_down": "갭 하락",
}


# 긍정/부정 톤 — UI 색상 분기
SIGNAL_TONE = {
    "new_52w_high": "positive",
    "new_52w_low": "negative",
    "volume_surge": "neutral",
    "above_200ma_cross": "positive",
    "below_200ma_cross": "negative",
    "gap_up": "positive",
    "gap_down": "negative",
}


@router.get("/today")
def get_today_signals(
    conn = Depends(get_db_conn),
    limit: int = Query(default=30, ge=1, le=200),
):
    """OHLCV 최신일의 시그널을 타입별 그룹핑하여 반환 — 대시보드 카드용.

    Returns:
        {
            "signal_date": "YYYY-MM-DD",
            "total": int,
            "groups": [
                {"type": "new_52w_high", "label": "52주 신고가", "tone": "positive",
                 "count": int, "samples": [{ticker, market, metric}, ...]},
                ...
            ]
        }
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT MAX(signal_date) AS d FROM market_signals")
        row = cur.fetchone()
        latest = row["d"] if row else None
        if latest is None:
            return {"signal_date": None, "total": 0, "groups": []}

        cur.execute(
            """
            SELECT signal_type, ticker, market, metric
            FROM market_signals
            WHERE signal_date = %s
            ORDER BY signal_type, ticker
            LIMIT %s
            """,
            (latest, int(limit) * len(SIGNAL_LABELS)),
        )
        rows = cur.fetchall()

    # 타입별 그룹
    groups: dict[str, list] = {}
    for r in rows:
        st = r["signal_type"]
        groups.setdefault(st, []).append({
            "ticker": r["ticker"],
            "market": r["market"],
            "metric": r["metric"] or {},
        })

    # 정렬: 긍정 → 부정 → 중립, 그 안에서는 count 많은 순
    order_tone = {"positive": 0, "negative": 1, "neutral": 2}
    group_list = [
        {
            "type": st,
            "label": SIGNAL_LABELS.get(st, st),
            "tone": SIGNAL_TONE.get(st, "neutral"),
            "count": len(rows_of),
            "samples": rows_of[:limit],
        }
        for st, rows_of in groups.items()
    ]
    group_list.sort(key=lambda g: (order_tone.get(g["tone"], 3), -g["count"]))

    return {
        "signal_date": latest.isoformat() if latest else None,
        "total": len(rows),
        "groups": group_list,
    }


@router.get("")
def list_signals(
    conn = Depends(get_db_conn),
    signal_type: str | None = Query(default=None),
    ticker: str | None = Query(default=None),
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=100, ge=1, le=500),
):
    """필터 기반 시그널 목록 — 향후 상세 페이지·워치리스트 API용."""
    clauses = ["signal_date >= CURRENT_DATE - (%s::int)"]
    params: list = [days]
    if signal_type:
        clauses.append("signal_type = %s")
        params.append(signal_type)
    if ticker:
        clauses.append("UPPER(ticker) = %s")
        params.append(ticker.upper())
    where_sql = " AND ".join(clauses)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT signal_date, signal_type, ticker, market, metric, created_at
            FROM market_signals
            WHERE {where_sql}
            ORDER BY signal_date DESC, signal_type, ticker
            LIMIT %s
            """,
            params + [int(limit)],
        )
        rows = cur.fetchall()

    return {
        "count": len(rows),
        "items": [_serialize_row(r) for r in rows],
    }
