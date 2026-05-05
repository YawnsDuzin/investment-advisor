"""외국인 수급 인사이트 (Tier 1 — Stage 2 정량 컨텍스트 보강).

`stock_universe_foreign_flow` (v44, KRX 한정) 의 PIT 시계열에서
- 외국인 보유율 latest + 5/20/60일 delta (%p)
- 외국인 순매수 5/20/60일 누적 (KRW)
를 batch 로 계산하여 Stage 2 프롬프트의 정량 팩터 섹션에 합류시킨다.

KRX 외 시장(NYSE/NASDAQ)은 데이터 부재 → 결과에서 제외(조용한 누락).

참고
- foreign_ownership_pct 는 KSD T+2 결제 룰로 인해 보통 snapshot_date - 2영업일의 보유 상태.
  format 텍스트에 안내 문구 포함.
- 출력 포맷은 factor_engine.format_factor_snapshot_text 와 결합되도록 설계
  (양쪽 모두 같은 quant_factors_section 안에 들어감).

공개 API
- compute_foreign_flow_snapshots(db_cfg, [(ticker, market), ...]) -> dict
- format_foreign_flow_text(snap) -> str
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Iterable
from zoneinfo import ZoneInfo

from psycopg2.extras import RealDictCursor

from shared.config import DatabaseConfig
from shared.db import get_connection
from shared.logger import get_logger

_KST = ZoneInfo("Asia/Seoul")
_log = get_logger("foreign_flow_insight")

# v44 데이터가 KRX 만 커버하므로 그 외 시장은 조용히 누락
_KRX_MARKETS = {"KOSPI", "KOSDAQ"}

# delta/누적 계산 windows — 거래일 offset 으로 사용
_WINDOW_OFFSETS = {"5d": 5, "20d": 20, "60d": 60}


def compute_foreign_flow_snapshots(
    db_cfg: DatabaseConfig,
    tickers: Iterable[tuple[str, str]],
    *,
    window_days: int = 90,
) -> dict[tuple[str, str], dict]:
    """KRX 종목별 외국인 수급 스냅샷 batch 계산.

    Args:
        tickers: [(ticker_upper, market), ...] — KRX 외 market 은 자동 제외
        window_days: PIT 조회 윈도우 (기본 90 — 60일 누적 위해 여유)

    Returns:
        {(ticker_upper, market_upper): {
            "snapshot_date": ISO date str,
            "own_latest_pct": float | None,
            "own_delta_5d_pp": float | None,
            "own_delta_20d_pp": float | None,
            "own_delta_60d_pp": float | None,
            "net_buy_5d_krw": int | None,
            "net_buy_20d_krw": int | None,
            "net_buy_60d_krw": int | None,
            "computed_at": ISO datetime,
        }}
        결측 종목·KRX 외 시장은 결과에서 제외.
    """
    krx_pairs = [
        (t.strip().upper(), (m or "").strip().upper())
        for t, m in tickers
    ]
    krx_pairs = [(t, m) for t, m in krx_pairs if t and m in _KRX_MARKETS]
    if not krx_pairs:
        return {}

    sql = f"""
    WITH targets(ticker, market) AS (
        SELECT * FROM UNNEST(%s::text[], %s::text[])
    ),
    ranked AS (
        SELECT UPPER(f.ticker) AS ticker,
               UPPER(f.market) AS market,
               f.snapshot_date,
               f.foreign_ownership_pct::float AS own_pct,
               f.foreign_net_buy_value,
               ROW_NUMBER() OVER (
                   PARTITION BY UPPER(f.ticker), UPPER(f.market)
                   ORDER BY f.snapshot_date DESC
               ) AS rn
        FROM stock_universe_foreign_flow f
        JOIN targets t
          ON UPPER(f.ticker) = UPPER(t.ticker)
         AND UPPER(f.market) = UPPER(t.market)
        WHERE f.snapshot_date >= CURRENT_DATE - (%s::int)
    ),
    agg AS (
        SELECT ticker, market,
               MAX(CASE WHEN rn = 1 THEN snapshot_date END) AS snapshot_date,
               MAX(CASE WHEN rn = 1                                  THEN own_pct END) AS own_latest,
               MAX(CASE WHEN rn = {_WINDOW_OFFSETS['5d']}             THEN own_pct END) AS own_5d,
               MAX(CASE WHEN rn = {_WINDOW_OFFSETS['20d']}            THEN own_pct END) AS own_20d,
               MAX(CASE WHEN rn = {_WINDOW_OFFSETS['60d']}            THEN own_pct END) AS own_60d,
               SUM(foreign_net_buy_value) FILTER (WHERE rn <= {_WINDOW_OFFSETS['5d']})  AS net_buy_5d,
               SUM(foreign_net_buy_value) FILTER (WHERE rn <= {_WINDOW_OFFSETS['20d']}) AS net_buy_20d,
               SUM(foreign_net_buy_value) FILTER (WHERE rn <= {_WINDOW_OFFSETS['60d']}) AS net_buy_60d
        FROM ranked
        GROUP BY ticker, market
    )
    SELECT ticker, market, snapshot_date,
           own_latest, own_5d, own_20d, own_60d,
           net_buy_5d, net_buy_20d, net_buy_60d
    FROM agg
    WHERE own_latest IS NOT NULL
    """

    started = time.time()
    tickers_arr = [t for t, _ in krx_pairs]
    markets_arr = [m for _, m in krx_pairs]

    conn = get_connection(db_cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (tickers_arr, markets_arr, int(window_days)))
            rows = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        _log.warning(f"[foreign_flow_insight] batch 집계 실패: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return {}
    finally:
        conn.close()

    computed_at = datetime.now(_KST).isoformat(timespec="seconds")
    out: dict[tuple[str, str], dict] = {}
    for r in rows:
        own_latest = r.get("own_latest")
        if own_latest is None:
            continue

        def _delta(prior_key: str) -> float | None:
            prior = r.get(prior_key)
            if prior is None:
                return None
            return round(float(own_latest) - float(prior), 3)

        def _int_or_none(v) -> int | None:
            if v is None:
                return None
            try:
                return int(v)
            except (ValueError, TypeError):
                return None

        snap = {
            "snapshot_date": r["snapshot_date"].isoformat() if r.get("snapshot_date") else None,
            "own_latest_pct": round(float(own_latest), 3),
            "own_delta_5d_pp": _delta("own_5d"),
            "own_delta_20d_pp": _delta("own_20d"),
            "own_delta_60d_pp": _delta("own_60d"),
            "net_buy_5d_krw": _int_or_none(r.get("net_buy_5d")),
            "net_buy_20d_krw": _int_or_none(r.get("net_buy_20d")),
            "net_buy_60d_krw": _int_or_none(r.get("net_buy_60d")),
            "computed_at": computed_at,
        }
        out[(r["ticker"], r["market"])] = snap

    _log.info(
        f"[foreign_flow_insight] snapshot {len(out)}/{len(krx_pairs)}건 "
        f"({(time.time() - started) * 1000:.0f}ms)"
    )
    return out


def _format_krw_billion(v: int | None) -> str:
    """원 단위 정수 → '+1,234억' 식 표기. None 이면 '-'."""
    if v is None:
        return "-"
    eok = v / 1_0000_0000  # 1억
    sign = "+" if eok > 0 else ("" if eok == 0 else "")
    return f"{sign}{eok:,.0f}억"


def format_foreign_flow_text(snap: dict) -> str:
    """Stage 2 프롬프트 정량 팩터 섹션에 삽입할 한글 텍스트.

    factor_engine.format_factor_snapshot_text 와 같은 섹션에 들어가도록 라벨 통일.
    """
    if not snap:
        return ""

    own = snap.get("own_latest_pct")
    d5 = snap.get("own_delta_5d_pp")
    d20 = snap.get("own_delta_20d_pp")
    d60 = snap.get("own_delta_60d_pp")
    nb5 = snap.get("net_buy_5d_krw")
    nb20 = snap.get("net_buy_20d_krw")
    nb60 = snap.get("net_buy_60d_krw")
    sd = snap.get("snapshot_date")

    lines: list[str] = []

    own_parts = []
    if own is not None:
        own_parts.append(f"보유율 {own:.2f}%")
    if d5 is not None:
        own_parts.append(f"5D {d5:+.2f}%p")
    if d20 is not None:
        own_parts.append(f"20D {d20:+.2f}%p")
    if d60 is not None:
        own_parts.append(f"60D {d60:+.2f}%p")
    if own_parts:
        lines.append("- 외국인 수급 (KRX, T-2 결제 기준): " + " / ".join(own_parts))

    nb_parts = []
    if nb5 is not None:
        nb_parts.append(f"5D {_format_krw_billion(nb5)}")
    if nb20 is not None:
        nb_parts.append(f"20D {_format_krw_billion(nb20)}")
    if nb60 is not None:
        nb_parts.append(f"60D {_format_krw_billion(nb60)}")
    if nb_parts:
        lines.append("- 외국인 누적 순매수: " + " / ".join(nb_parts))

    if sd:
        lines.append(f"- (외국인 데이터 기준일: {sd})")

    return "\n".join(lines)
