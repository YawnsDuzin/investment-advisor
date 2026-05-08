"""유사 종목 추천 (Tier 1 #5).

target 종목의 6축 percentile 팩터 벡터(`r1m/r3m/r6m/r12m/low_vol/volume`)를
같은 시장 그룹(KRX/US) universe 와 비교하여 Euclidean 거리 최소 Top-K 를 반환.

핵심 아이디어:
  - 같은 sector_norm 우선 — 결과 부족 시 그룹 전체 fallback
  - percentile (0~1) 기반 거리 → 원시값 스케일 차이 정규화
  - NULL percentile 은 0.5(중립) 으로 임퓨테이션 — 결측 종목도 후보 유지

목표 페이지: stock_cockpit.html 하단 "비슷한 종목" 카드.
"""
from __future__ import annotations

import math
from typing import Optional

from psycopg2.extras import RealDictCursor

from shared.config import DatabaseConfig
from shared.db import get_connection
from shared.logger import get_logger

# factor_engine 의 private 헬퍼를 직접 사용 — 동일 그룹의 universe 전체 percentile
# 이 한 번의 SQL 로 계산되어 있어 재산출 없이 거리만 비교한다.
from analyzer.factor_engine import (
    _compute_group_factors,
    _market_group,
    _MARKET_GROUPS,
)

_log = get_logger("similar")

# 거리 계산에 사용할 6축 — percentile 키
_PCTILE_KEYS = (
    "r1m_pctile",
    "r3m_pctile",
    "r6m_pctile",
    "r12m_pctile",
    "low_vol_pctile",
    "volume_pctile",
)


def _vec(snap: dict) -> list[float]:
    """percentile 벡터 추출. NULL 은 0.5 (중립) 로 임퓨테이션."""
    out: list[float] = []
    for k in _PCTILE_KEYS:
        v = snap.get(k)
        out.append(float(v) if v is not None else 0.5)
    return out


def _euclidean(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _to_similarity(distance: float) -> float:
    """Euclidean(0..√6) → similarity(0..1, 1=동일).

    √6 ≈ 2.449 가 최대거리. 1 - d/√6 으로 정규화. round 4자리.
    """
    max_d = math.sqrt(len(_PCTILE_KEYS))
    sim = 1.0 - (distance / max_d)
    return round(max(0.0, min(1.0, sim)), 4)


def _fetch_meta_batch(
    conn,
    keys: list[tuple[str, str]],
) -> dict[tuple[str, str], dict]:
    """`stock_universe` 메타 일괄 조회 — sector / asset_name / market_cap_krw / last_price."""
    if not keys:
        return {}
    sql = """
        SELECT UPPER(ticker) AS ticker, UPPER(market) AS market,
               asset_name, sector_norm, market_cap_krw,
               last_price, last_price_ccy, listed
        FROM stock_universe
        WHERE (UPPER(ticker), UPPER(market)) IN %s
    """
    out: dict[tuple[str, str], dict] = {}
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, (tuple(keys),))
        for r in cur.fetchall():
            out[(r["ticker"], r["market"])] = dict(r)
    return out


def compute_similar(
    db_cfg: DatabaseConfig,
    ticker: str,
    market: str,
    *,
    top_k: int = 5,
    window_days: int = 300,
    same_sector_only: bool = False,
) -> dict:
    """유사 종목 Top-K 계산.

    Args:
        ticker, market: 기준 종목
        top_k: 반환할 유사 종목 수 (기본 5)
        window_days: factor 계산 OHLCV 윈도우
        same_sector_only: True 면 그룹 전체 fallback 비활성화

    Returns:
        {
            "ticker": str, "market": str, "sector": str|None, "market_group": str|None,
            "universe_size": int,
            "items": [
                {ticker, market, asset_name, sector, market_cap_krw, last_price, currency,
                 distance, similarity, factor_snapshot, fallback (bool)},
                ...
            ]
        }
        결측·미지원 시장은 빈 items.
    """
    tk = ticker.strip().upper()
    mk = (market or "").strip().upper()
    grp = _market_group(mk)
    if not grp:
        return {"ticker": tk, "market": mk, "sector": None, "market_group": None,
                "universe_size": 0, "items": []}

    members = _MARKET_GROUPS[grp]
    grp_data = _compute_group_factors(db_cfg, members=members, window_days=window_days)
    if not grp_data:
        return {"ticker": tk, "market": mk, "sector": None, "market_group": grp,
                "universe_size": 0, "items": []}

    universe: dict[tuple[str, str], dict] = grp_data["per_ticker"]
    universe_size = int(grp_data.get("universe_size", len(universe)))
    target_key = (tk, mk)
    target_snap = universe.get(target_key)
    if not target_snap:
        return {"ticker": tk, "market": mk, "sector": None, "market_group": grp,
                "universe_size": universe_size, "items": []}

    target_vec = _vec(target_snap)

    conn = get_connection(db_cfg)
    target_sector: Optional[str] = None
    try:
        # 1) target sector 조회
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT sector_norm FROM stock_universe "
                "WHERE UPPER(ticker) = %s AND UPPER(market) = %s "
                "LIMIT 1",
                (tk, mk),
            )
            row = cur.fetchone()
            if row:
                target_sector = (row.get("sector_norm") or "").strip() or None

        # 2) 후보 풀 — universe 의 모든 키. target/제외.
        candidate_keys = [k for k in universe.keys() if k != target_key]

        # 3) 메타 일괄 조회 (sector_norm + asset_name + market_cap)
        meta = _fetch_meta_batch(conn, candidate_keys)
    finally:
        conn.close()

    # 4) 같은 sector 우선 후보 / 그룹 전체 fallback
    same_sector: list[tuple[float, tuple[str, str]]] = []
    other_in_group: list[tuple[float, tuple[str, str]]] = []
    for key in candidate_keys:
        snap = universe[key]
        meta_row = meta.get(key, {})
        if not meta_row.get("listed", True):  # 상폐 제외
            continue
        d = _euclidean(target_vec, _vec(snap))
        cand_sector = (meta_row.get("sector_norm") or "").strip() or None
        if target_sector and cand_sector == target_sector:
            same_sector.append((d, key))
        else:
            other_in_group.append((d, key))

    same_sector.sort(key=lambda x: x[0])
    other_in_group.sort(key=lambda x: x[0])

    picked: list[tuple[float, tuple[str, str], bool]] = []
    for d, k in same_sector[:top_k]:
        picked.append((d, k, False))
    if not same_sector_only and len(picked) < top_k:
        need = top_k - len(picked)
        for d, k in other_in_group[:need]:
            picked.append((d, k, True))

    items = []
    for d, key, is_fallback in picked:
        snap = universe[key]
        m = meta.get(key, {})
        items.append({
            "ticker": key[0],
            "market": key[1],
            "asset_name": m.get("asset_name"),
            "sector": (m.get("sector_norm") or "").strip() or None,
            "market_cap_krw": m.get("market_cap_krw"),
            "last_price": float(m["last_price"]) if m.get("last_price") is not None else None,
            "currency": m.get("last_price_ccy"),
            "distance": round(d, 4),
            "similarity": _to_similarity(d),
            "factor_snapshot": {k: snap.get(k) for k in (
                "r1m_pct", "r3m_pct", "r6m_pct", "r12m_pct",
                "vol60_pct", "volume_ratio",
                *_PCTILE_KEYS,
            )},
            "fallback": is_fallback,
        })

    _log.info(
        f"[similar] {tk}/{mk} sector={target_sector!r} → "
        f"same={len(same_sector)} other={len(other_in_group)} → top {len(items)}"
    )

    return {
        "ticker": tk,
        "market": mk,
        "sector": target_sector,
        "market_group": grp,
        "universe_size": universe_size,
        "items": items,
    }
