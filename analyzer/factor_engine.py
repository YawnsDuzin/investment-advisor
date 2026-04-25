"""정량 팩터 엔진 (로드맵 B1).

OHLCV 이력(`stock_universe_ohlcv`)에서 추출한 팩터를 universe cross-section
percentile과 함께 계산하여, Stage 2 프롬프트에 실측값으로 주입한다.

핵심 아이디어
  - AI가 수치를 "추정"하지 않도록 실제 분포상의 백분위를 함께 제공
  - z-score 대신 percentile(0~1) 사용 — LLM이 해석하기 쉽고 극단 outlier에 강함

집계 팩터
  - r1m/r3m/r6m/r12m_pct: 기간별 수익률(%)
  - vol60_pct: 60일 일별 change_pct STDDEV (극단값 ±50% clamp 후)
  - volume_ratio: 20일 평균 거래량 / 60일 평균 거래량 (거래 증가 = >1)

각 팩터의 cross-section percentile:
  - r*_pctile: 같은 거래소(KRX or US) universe 내 백분위 (0~1, 1이 상위)
  - low_vol_pctile: vol60이 낮을수록 상위 (반전)
  - volume_pctile: volume_ratio 백분위 (상위 = 거래 증가)

공개 API
  - compute_factor_snapshots(db_cfg, tickers) -> dict[(ticker, market)] -> dict
  - format_factor_snapshot_text(snap) -> str  (STAGE2 프롬프트 삽입용)
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
_log = get_logger("factor")


# 거래일 기준 offset (22/66/132/252) — stock_data._calc_period_returns와 정합
_PERIOD_OFFSETS = {
    "r1m": 22,
    "r3m": 66,
    "r6m": 132,
    "r12m": 252,
}


# cross-section 계산에 사용할 시장 그룹 (통화권 분리)
_MARKET_GROUPS = {
    "KRX": ("KOSPI", "KOSDAQ", "KONEX"),
    "US": ("NASDAQ", "NYSE", "AMEX"),
}


def _market_group(market: str) -> str | None:
    """ticker market을 KRX / US 그룹으로 매핑. 그 외는 None."""
    mk = (market or "").upper()
    for grp, members in _MARKET_GROUPS.items():
        if mk in members:
            return grp
    return None


def compute_factor_snapshots(
    db_cfg: DatabaseConfig,
    tickers: Iterable[tuple[str, str]],
    *,
    window_days: int = 300,
) -> dict[tuple[str, str], dict]:
    """요청된 (ticker, market)들의 팩터 스냅샷을 계산.

    universe 전체 cross-section 집계를 한 번 수행하고, 요청된 종목만 추출한다.
    시장 그룹(KRX/US)별로 분리 집계하여 통화·거래시간 왜곡을 피한다.

    Args:
        tickers: [(ticker_upper, market), ...] 요청 목록
        window_days: OHLCV 조회 윈도우 (기본 300 — 252일 수익률 확보 위해 여유)

    Returns:
        {(ticker_upper, market_upper): {
            "r1m_pct": float|None, ..., "r12m_pct": float|None,
            "r1m_pctile": float|None, ..., "r12m_pctile": float|None,
            "vol60_pct": float|None, "low_vol_pctile": float|None,
            "volume_ratio": float|None, "volume_pctile": float|None,
            "market_group": "KRX"|"US",
            "universe_size": int,
            "computed_at": ISO datetime str,
        }}

    OHLCV 결측 or universe 밖 종목은 결과에서 제외 (호출자가 적절히 처리).
    DB 오류 시 빈 dict 반환 + WARNING 로그.
    """
    pairs = [(t.strip().upper(), (m or "").strip().upper()) for t, m in tickers]
    requested = {(t, m) for t, m in pairs if t}
    if not requested:
        return {}

    # 요청 목록을 시장 그룹별로 분리 — 각 그룹 집계는 독립
    by_group: dict[str, set[tuple[str, str]]] = {}
    for tk, mk in requested:
        grp = _market_group(mk)
        if grp is None:
            continue
        by_group.setdefault(grp, set()).add((tk, mk))

    if not by_group:
        return {}

    started = time.time()
    results: dict[tuple[str, str], dict] = {}
    computed_at = datetime.now(_KST).isoformat(timespec="seconds")

    for grp, grp_members in by_group.items():
        members = _MARKET_GROUPS[grp]
        group_result = _compute_group_factors(
            db_cfg, members=members, window_days=window_days,
        )
        if not group_result:
            continue

        universe_size = group_result["universe_size"]
        for tk, mk in grp_members:
            row = group_result["per_ticker"].get((tk, mk))
            if not row:
                continue
            row["market_group"] = grp
            row["universe_size"] = universe_size
            row["computed_at"] = computed_at
            results[(tk, mk)] = row

    duration = time.time() - started
    _log.info(
        f"[factor] snapshot {len(results)}/{len(requested)}건 계산 완료 "
        f"(KRX={len(by_group.get('KRX', set()))} US={len(by_group.get('US', set()))} / {duration*1000:.0f}ms)"
    )
    return results


def _compute_group_factors(
    db_cfg: DatabaseConfig,
    *,
    members: tuple[str, ...],
    window_days: int,
) -> dict | None:
    """단일 시장 그룹에 대한 cross-section 집계 실행.

    Returns:
        {"universe_size": int, "per_ticker": {(ticker, market): {factor dict}}}
        or None (DB 오류 or 결과 없음)
    """
    # 기간 offset 값을 Python 쪽에서 안전하게 삽입 (정수 상수)
    sql = f"""
    WITH ranked AS (
        SELECT ticker, UPPER(market) AS market, trade_date, close, volume, change_pct,
               ROW_NUMBER() OVER (
                   PARTITION BY ticker, UPPER(market)
                   ORDER BY trade_date DESC
               ) AS rn
        FROM stock_universe_ohlcv
        WHERE trade_date >= CURRENT_DATE - (%s::int)
          AND UPPER(market) = ANY(%s)
    ),
    univ AS (
        SELECT ticker, market,
               MAX(CASE WHEN rn = 1 THEN close END) AS close_latest,
               MAX(CASE WHEN rn = {_PERIOD_OFFSETS['r1m']} THEN close END) AS close_1m,
               MAX(CASE WHEN rn = {_PERIOD_OFFSETS['r3m']} THEN close END) AS close_3m,
               MAX(CASE WHEN rn = {_PERIOD_OFFSETS['r6m']} THEN close END) AS close_6m,
               MAX(CASE WHEN rn = {_PERIOD_OFFSETS['r12m']} THEN close END) AS close_12m,
               STDDEV(LEAST(GREATEST(change_pct, -50), 50))
                   FILTER (WHERE rn <= 60) AS vol60,
               AVG(volume) FILTER (WHERE rn <= 20) AS vol_avg_20,
               AVG(volume) FILTER (WHERE rn <= 60) AS vol_avg_60
        FROM ranked
        GROUP BY ticker, market
    ),
    factors AS (
        SELECT ticker, market,
               CASE WHEN close_1m  IS NOT NULL AND close_1m  > 0 THEN (close_latest - close_1m)  / close_1m  * 100 END AS r1m,
               CASE WHEN close_3m  IS NOT NULL AND close_3m  > 0 THEN (close_latest - close_3m)  / close_3m  * 100 END AS r3m,
               CASE WHEN close_6m  IS NOT NULL AND close_6m  > 0 THEN (close_latest - close_6m)  / close_6m  * 100 END AS r6m,
               CASE WHEN close_12m IS NOT NULL AND close_12m > 0 THEN (close_latest - close_12m) / close_12m * 100 END AS r12m,
               vol60,
               CASE WHEN vol_avg_60 IS NOT NULL AND vol_avg_60 > 0
                    THEN vol_avg_20 / vol_avg_60 END AS volume_ratio
        FROM univ
        WHERE close_latest IS NOT NULL
    ),
    ranked_pctile AS (
        SELECT f.*,
               PERCENT_RANK() OVER (ORDER BY r1m  NULLS FIRST) AS r1m_pctile,
               PERCENT_RANK() OVER (ORDER BY r3m  NULLS FIRST) AS r3m_pctile,
               PERCENT_RANK() OVER (ORDER BY r6m  NULLS FIRST) AS r6m_pctile,
               PERCENT_RANK() OVER (ORDER BY r12m NULLS FIRST) AS r12m_pctile,
               -- vol60 낮을수록 "저변동 상위" → DESC 정렬의 역
               1 - PERCENT_RANK() OVER (ORDER BY vol60 DESC NULLS LAST) AS low_vol_pctile,
               PERCENT_RANK() OVER (ORDER BY volume_ratio NULLS FIRST) AS volume_pctile,
               COUNT(*) OVER () AS universe_size
        FROM factors f
    )
    SELECT ticker, market,
           r1m, r3m, r6m, r12m,
           vol60, volume_ratio,
           r1m_pctile, r3m_pctile, r6m_pctile, r12m_pctile,
           low_vol_pctile, volume_pctile,
           universe_size
    FROM ranked_pctile
    """

    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (int(window_days), list(members)))
            rows = cur.fetchall()
    except Exception as e:
        _log.warning(f"[factor] {members} 집계 실패: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    finally:
        conn.close()

    if not rows:
        return None

    universe_size = int(rows[0][-1]) if rows else 0
    per_ticker: dict[tuple[str, str], dict] = {}
    for r in rows:
        (ticker, market,
         r1m, r3m, r6m, r12m,
         vol60, vol_ratio,
         r1m_p, r3m_p, r6m_p, r12m_p,
         low_vol_p, vol_p,
         _size) = r

        def _fnone(x, digits=2):
            return round(float(x), digits) if x is not None else None

        per_ticker[(ticker.strip().upper(), market.strip().upper())] = {
            "r1m_pct": _fnone(r1m),
            "r3m_pct": _fnone(r3m),
            "r6m_pct": _fnone(r6m),
            "r12m_pct": _fnone(r12m),
            "vol60_pct": _fnone(vol60, 3),
            "volume_ratio": _fnone(vol_ratio, 3),
            "r1m_pctile": _fnone(r1m_p, 4),
            "r3m_pctile": _fnone(r3m_p, 4),
            "r6m_pctile": _fnone(r6m_p, 4),
            "r12m_pctile": _fnone(r12m_p, 4),
            "low_vol_pctile": _fnone(low_vol_p, 4),
            "volume_pctile": _fnone(vol_p, 4),
        }

    return {"universe_size": universe_size, "per_ticker": per_ticker}


def compute_sector_pctiles(
    db_cfg: DatabaseConfig,
    ticker: str,
    market: str,
    *,
    window_days: int = 300,
    min_sector_size: int = 5,
) -> dict | None:
    """단일 종목에 대한 섹터 내 6축 팩터 분위 계산.

    같은 sector_norm + 같은 시장 그룹(KRX/US) 안에서 cross-section PERCENT_RANK.
    섹터 표본이 min_sector_size 미만이면 pctile NULL (원시값은 채움).

    Returns:
        {
            "ticker": str, "sector": str | None, "sector_size": int,
            "ranks": {
                "r1m": {"value_pct": float|None, "sector_pctile": float|None, "sector_top_pct": int|None},
                "r3m": {...}, "r6m": {...}, "r12m": {...},
                "low_vol": {"value_pct": float|None, "sector_pctile": ..., "sector_top_pct": ...},
                "volume": {"value_ratio": float|None, "sector_pctile": ..., "sector_top_pct": ...},
            },
            "computed_at": ISO datetime str,
        }
        or None if ticker 가 stock_universe 에 없거나 sector_norm NULL.
    """
    tk = ticker.strip().upper()
    mk = (market or "").strip().upper()
    grp = _market_group(mk)
    if not grp:
        return None
    members = _MARKET_GROUPS[grp]
    sector = None  # 로그에서 NameError 회피

    conn = get_connection(db_cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 1) 종목의 sector_norm 조회
            cur.execute(
                "SELECT sector_norm AS sector FROM stock_universe "
                "WHERE UPPER(ticker) = %s AND UPPER(market) = %s "
                "  AND sector_norm IS NOT NULL LIMIT 1",
                (tk, mk),
            )
            sector_row = cur.fetchone()
            if not sector_row or not sector_row.get("sector"):
                return None
            sector = sector_row["sector"]

            # 2) 섹터 cross-section
            sql = f"""
            WITH ranked AS (
                SELECT o.ticker, UPPER(o.market) AS market, o.trade_date, o.close,
                       o.volume, o.change_pct,
                       ROW_NUMBER() OVER (
                           PARTITION BY o.ticker, UPPER(o.market)
                           ORDER BY o.trade_date DESC
                       ) AS rn
                FROM stock_universe_ohlcv o
                JOIN stock_universe u
                  ON UPPER(u.ticker) = UPPER(o.ticker)
                 AND UPPER(u.market) = UPPER(o.market)
                WHERE o.trade_date >= CURRENT_DATE - (%s::int)
                  AND UPPER(o.market) = ANY(%s)
                  AND u.sector_norm = %s
                  AND u.listed = TRUE
            ),
            univ AS (
                SELECT ticker, market,
                       MAX(CASE WHEN rn = 1 THEN close END) AS close_latest,
                       MAX(CASE WHEN rn = {_PERIOD_OFFSETS['r1m']} THEN close END) AS close_1m,
                       MAX(CASE WHEN rn = {_PERIOD_OFFSETS['r3m']} THEN close END) AS close_3m,
                       MAX(CASE WHEN rn = {_PERIOD_OFFSETS['r6m']} THEN close END) AS close_6m,
                       MAX(CASE WHEN rn = {_PERIOD_OFFSETS['r12m']} THEN close END) AS close_12m,
                       STDDEV(LEAST(GREATEST(change_pct, -50), 50))
                           FILTER (WHERE rn <= 60) AS vol60,
                       AVG(volume) FILTER (WHERE rn <= 20) AS vol_avg_20,
                       AVG(volume) FILTER (WHERE rn <= 60) AS vol_avg_60
                FROM ranked
                GROUP BY ticker, market
            ),
            factors AS (
                SELECT ticker, market,
                       CASE WHEN close_1m  IS NOT NULL AND close_1m  > 0 THEN (close_latest - close_1m)  / close_1m  * 100 END AS r1m,
                       CASE WHEN close_3m  IS NOT NULL AND close_3m  > 0 THEN (close_latest - close_3m)  / close_3m  * 100 END AS r3m,
                       CASE WHEN close_6m  IS NOT NULL AND close_6m  > 0 THEN (close_latest - close_6m)  / close_6m  * 100 END AS r6m,
                       CASE WHEN close_12m IS NOT NULL AND close_12m > 0 THEN (close_latest - close_12m) / close_12m * 100 END AS r12m,
                       vol60,
                       CASE WHEN vol_avg_60 IS NOT NULL AND vol_avg_60 > 0
                            THEN vol_avg_20 / vol_avg_60 END AS volume_ratio
                FROM univ
                WHERE close_latest IS NOT NULL
            ),
            ranked_pctile AS (
                SELECT f.*,
                       PERCENT_RANK() OVER (ORDER BY r1m  NULLS FIRST) AS r1m_pctile,
                       PERCENT_RANK() OVER (ORDER BY r3m  NULLS FIRST) AS r3m_pctile,
                       PERCENT_RANK() OVER (ORDER BY r6m  NULLS FIRST) AS r6m_pctile,
                       PERCENT_RANK() OVER (ORDER BY r12m NULLS FIRST) AS r12m_pctile,
                       1 - PERCENT_RANK() OVER (ORDER BY vol60 DESC NULLS LAST) AS low_vol_pctile,
                       PERCENT_RANK() OVER (ORDER BY volume_ratio NULLS FIRST) AS volume_pctile,
                       COUNT(*) OVER () AS sector_size
                FROM factors f
            )
            SELECT ticker, market, r1m, r3m, r6m, r12m, vol60, volume_ratio,
                   r1m_pctile, r3m_pctile, r6m_pctile, r12m_pctile,
                   low_vol_pctile, volume_pctile, sector_size
            FROM ranked_pctile
            WHERE UPPER(ticker) = %s AND UPPER(market) = %s
            """
            cur.execute(sql, (int(window_days), list(members), sector, tk, mk))
            rows = cur.fetchall()
    except Exception as e:
        _log.warning(f"[factor] sector_pctile {tk}/{mk}/{sector} 실패: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    finally:
        conn.close()

    if not rows:
        return {
            "ticker": tk, "sector": sector, "sector_size": 0,
            "ranks": {k: {"value_pct": None, "value_ratio": None,
                          "sector_pctile": None, "sector_top_pct": None}
                      for k in ("r1m", "r3m", "r6m", "r12m", "low_vol", "volume")},
            "computed_at": datetime.now(_KST).isoformat(timespec="seconds"),
        }

    r = rows[0]

    def _g(k):
        return r[k]

    sector_size = int(_g("sector_size") or 0)
    sufficient = sector_size >= min_sector_size

    def _pctile_pkg(value_key, value_label, pctile_key):
        v = _g(value_key)
        p = _g(pctile_key) if sufficient else None
        return {
            value_label: float(v) if v is not None else None,
            "sector_pctile": float(p) if p is not None else None,
            "sector_top_pct": int(round((1 - float(p)) * 100)) if p is not None else None,
        }

    return {
        "ticker": tk,
        "sector": sector,
        "sector_size": sector_size,
        "ranks": {
            "r1m":     _pctile_pkg("r1m", "value_pct", "r1m_pctile"),
            "r3m":     _pctile_pkg("r3m", "value_pct", "r3m_pctile"),
            "r6m":     _pctile_pkg("r6m", "value_pct", "r6m_pctile"),
            "r12m":    _pctile_pkg("r12m", "value_pct", "r12m_pctile"),
            "low_vol": _pctile_pkg("vol60", "value_pct", "low_vol_pctile"),
            "volume":  _pctile_pkg("volume_ratio", "value_ratio", "volume_pctile"),
        },
        "computed_at": datetime.now(_KST).isoformat(timespec="seconds"),
    }


def _pctile_top_pct(pctile: float | None) -> str:
    """percentile(0~1) → '상위 N%' 텍스트. None이면 '-'."""
    if pctile is None:
        return "-"
    top = (1.0 - float(pctile)) * 100.0
    return f"상위 {top:.0f}%"


def format_factor_snapshot_text(snap: dict) -> str:
    """스냅샷 dict → STAGE2 프롬프트에 삽입할 한글 텍스트.

    AI가 이 텍스트를 읽고 해석만 하도록 구성. 수치는 실측값.
    """
    if not snap:
        return "(팩터 스냅샷 없음 — OHLCV 이력 결측)"

    size = snap.get("universe_size")
    grp = snap.get("market_group") or "?"
    lines = [
        f"- universe: {grp} 전체 {size}종목 cross-section 기준",
        f"- 단기 수익률(1m): {snap.get('r1m_pct'):+.2f}% ({_pctile_top_pct(snap.get('r1m_pctile'))})" if snap.get("r1m_pct") is not None else "- 단기 수익률(1m): 결측",
        f"- 중기 수익률(3m): {snap.get('r3m_pct'):+.2f}% ({_pctile_top_pct(snap.get('r3m_pctile'))})" if snap.get("r3m_pct") is not None else "- 중기 수익률(3m): 결측",
        f"- 반기 수익률(6m): {snap.get('r6m_pct'):+.2f}% ({_pctile_top_pct(snap.get('r6m_pctile'))})" if snap.get("r6m_pct") is not None else "- 반기 수익률(6m): 결측",
        f"- 장기 수익률(12m): {snap.get('r12m_pct'):+.2f}% ({_pctile_top_pct(snap.get('r12m_pctile'))})" if snap.get("r12m_pct") is not None else "- 장기 수익률(12m): 결측",
    ]
    v60 = snap.get("vol60_pct")
    lv_p = snap.get("low_vol_pctile")
    if v60 is not None:
        lines.append(
            f"- 변동성(60일 일별 STDDEV): {v60:.2f}% (저변동 {_pctile_top_pct(lv_p)})"
        )
    vr = snap.get("volume_ratio")
    vp = snap.get("volume_pctile")
    if vr is not None:
        lines.append(
            f"- 거래량 추세(20일/60일 평균): {vr:.2f}배 (거래증가 {_pctile_top_pct(vp)})"
        )
    return "\n".join(lines)
