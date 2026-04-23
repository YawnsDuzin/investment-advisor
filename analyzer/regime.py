"""시장 레짐 판별 레이어 (로드맵 B2).

Stage 1 분석 진입 시점에 시장 국면을 수치화하여 프롬프트 컨텍스트에 주입한다.
LLM이 강세/약세·고변동/저변동 국면을 자의적으로 "느낌"으로 판단하지 않고,
실측 지표에 근거한 포지셔닝(공격/방어, 컨트래리안 허용 여부 등)을 취하도록 가이드.

계산 지표 (per index)
  - `close`: 최신 종가
  - `above_200ma`: 현재가가 200일 이평선 위인지 (bool)
  - `pct_from_ma200`: 200일 이평 대비 이격도(%)
  - `vol60_pct`: 60일 일별 변동률 STDDEV (±10% clamp 후)
  - `vol_regime`: "low"(<1%) / "mid"(1~2%) / "high"(>2%)
  - `drawdown_from_52w_high_pct`: 52주 고점 대비 낙폭(%)
  - `return_1m_pct` / `return_3m_pct`: 1개월·3개월 수익률

추가 지표
  - `breadth_kr_pct`: KRX universe 중 최근 20일 수익률 > 0 인 종목 비율 (시장폭)
    → stock_universe_ohlcv 집계

결측 대응: market_indices_ohlcv에 해당 index_code 데이터가 없으면 그 index는 결과에서 제외.
모든 인덱스 결측이면 빈 dict 반환 → 호출자는 regime 섹션 생략.
"""
from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from shared.config import DatabaseConfig
from shared.db import get_connection
from shared.logger import get_logger

_KST = ZoneInfo("Asia/Seoul")
_log = get_logger("regime")


# index_code → 표시용 한글 이름
_INDEX_LABELS = {
    "KOSPI": "KOSPI",
    "KOSDAQ": "KOSDAQ",
    "SP500": "S&P 500",
    "NDX100": "Nasdaq 100",
}

# breadth 계산에 사용할 시장
_BREADTH_MARKETS_KR = ("KOSPI", "KOSDAQ")


def _classify_vol(v: float | None) -> str:
    if v is None:
        return "unknown"
    if v < 1.0:
        return "low"
    if v < 2.0:
        return "mid"
    return "high"


def _compute_index_regime(db_cfg: DatabaseConfig, index_code: str) -> dict | None:
    """단일 인덱스의 regime 지표 계산."""
    sql = """
    WITH ranked AS (
        SELECT trade_date, close::float AS close, change_pct::float AS change_pct,
               ROW_NUMBER() OVER (ORDER BY trade_date DESC) AS rn
        FROM market_indices_ohlcv
        WHERE index_code = %s
          AND trade_date >= CURRENT_DATE - 400
    ),
    agg AS (
        SELECT
            MAX(CASE WHEN rn = 1 THEN close END) AS close_latest,
            AVG(close) FILTER (WHERE rn <= 200) AS ma200,
            MAX(close) FILTER (WHERE rn <= 252) AS high_52w,
            STDDEV(LEAST(GREATEST(change_pct, -10), 10)) FILTER (WHERE rn <= 60) AS vol60,
            MAX(CASE WHEN rn = 22 THEN close END) AS close_1m,
            MAX(CASE WHEN rn = 66 THEN close END) AS close_3m
        FROM ranked
    )
    SELECT close_latest, ma200, high_52w, vol60, close_1m, close_3m FROM agg
    """
    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (index_code,))
            row = cur.fetchone()
    except Exception as e:
        _log.warning(f"[regime/{index_code}] 집계 실패: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    finally:
        conn.close()

    if not row or row[0] is None:
        return None
    close_latest, ma200, high_52w, vol60, close_1m, close_3m = row

    def _pct_from(base: float | None) -> float | None:
        if base is None or float(base) <= 0:
            return None
        return round((float(close_latest) - float(base)) / float(base) * 100, 2)

    above_ma200 = None
    if ma200 is not None and float(ma200) > 0:
        above_ma200 = float(close_latest) >= float(ma200)

    return {
        "close": round(float(close_latest), 2),
        "above_200ma": above_ma200,
        "pct_from_ma200": _pct_from(ma200),
        "vol60_pct": round(float(vol60), 3) if vol60 is not None else None,
        "vol_regime": _classify_vol(float(vol60)) if vol60 is not None else "unknown",
        "drawdown_from_52w_high_pct": _pct_from(high_52w),
        "return_1m_pct": _pct_from(close_1m),
        "return_3m_pct": _pct_from(close_3m),
    }


def _compute_breadth_kr(db_cfg: DatabaseConfig) -> float | None:
    """KRX universe 중 최근 20거래일 수익률 > 0인 종목 비율(%)."""
    sql = """
    WITH ranked AS (
        SELECT o.ticker, o.market, o.trade_date, o.close::float AS close,
               ROW_NUMBER() OVER (PARTITION BY o.ticker, UPPER(o.market) ORDER BY o.trade_date DESC) AS rn
        FROM stock_universe_ohlcv o
        WHERE trade_date >= CURRENT_DATE - 60
          AND UPPER(o.market) = ANY(%s)
    ),
    endpoints AS (
        SELECT ticker, market,
               MAX(CASE WHEN rn = 1 THEN close END) AS c_latest,
               MAX(CASE WHEN rn = 20 THEN close END) AS c_20d
        FROM ranked
        GROUP BY ticker, market
    )
    SELECT
        COUNT(*) FILTER (WHERE c_20d IS NOT NULL AND c_20d > 0 AND c_latest > c_20d) AS up_count,
        COUNT(*) FILTER (WHERE c_20d IS NOT NULL AND c_20d > 0) AS total
    FROM endpoints
    """
    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (list(_BREADTH_MARKETS_KR),))
            row = cur.fetchone()
    except Exception as e:
        _log.warning(f"[regime/breadth_kr] 집계 실패: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    finally:
        conn.close()
    if not row or not row[1]:
        return None
    up, total = int(row[0] or 0), int(row[1] or 0)
    if total <= 0:
        return None
    return round(up / total * 100, 1)


def compute_regime(db_cfg: DatabaseConfig) -> dict:
    """전체 regime 스냅샷 반환.

    Returns:
        {
          "indices": {"KOSPI": {...}, "SP500": {...}, ...},  # 결측 인덱스는 제외
          "breadth_kr_pct": float | None,
          "computed_at": ISO datetime,
        }
        데이터가 전무하면 빈 dict {}.
    """
    started = time.time()
    indices: dict[str, dict] = {}
    for code in _INDEX_LABELS:
        r = _compute_index_regime(db_cfg, code)
        if r:
            indices[code] = r

    if not indices:
        _log.info("[regime] market_indices_ohlcv 데이터 없음 — 스냅샷 빈 상태 반환")
        return {}

    breadth = _compute_breadth_kr(db_cfg)

    snap = {
        "indices": indices,
        "breadth_kr_pct": breadth,
        "computed_at": datetime.now(_KST).isoformat(timespec="seconds"),
    }
    _log.info(
        f"[regime] 계산 완료 — indices={list(indices.keys())} breadth_kr={breadth} "
        f"({(time.time() - started) * 1000:.0f}ms)"
    )
    return snap


def format_regime_text(snap: dict) -> str:
    """regime dict → STAGE1 프롬프트에 삽입할 한글 텍스트."""
    if not snap or not snap.get("indices"):
        return ""

    lines: list[str] = []
    for code, data in snap["indices"].items():
        label = _INDEX_LABELS.get(code, code)
        close = data.get("close")
        above = data.get("above_200ma")
        ma_pct = data.get("pct_from_ma200")
        vol60 = data.get("vol60_pct")
        vol_rg = data.get("vol_regime")
        dd = data.get("drawdown_from_52w_high_pct")
        r1 = data.get("return_1m_pct")
        r3 = data.get("return_3m_pct")

        parts: list[str] = [f"{label}: {close}"]
        if above is not None and ma_pct is not None:
            direction = "위" if above else "아래"
            parts.append(f"200일 이평 {direction}({ma_pct:+.2f}%)")
        if vol60 is not None:
            vol_ko = {"low": "저변동", "mid": "중변동", "high": "고변동"}.get(vol_rg or "", "")
            parts.append(f"60일 변동성 {vol60:.2f}% ({vol_ko})")
        if dd is not None:
            parts.append(f"52주 고점 대비 {dd:+.2f}%")
        if r1 is not None:
            parts.append(f"1M {r1:+.2f}%")
        if r3 is not None:
            parts.append(f"3M {r3:+.2f}%")
        lines.append("- " + " / ".join(parts))

    breadth = snap.get("breadth_kr_pct")
    if breadth is not None:
        lines.append(f"- KRX 시장폭(20일 상승 종목 비율): {breadth:.1f}%")

    return "\n".join(lines)


def infer_positioning_hint(snap: dict) -> str:
    """regime 지표로부터 짧은 포지셔닝 힌트 생성 — 프롬프트 가이드용.

    절대적 규칙이 아니라 LLM에 "이런 국면이다"라는 1줄 서술만 제공.
    """
    if not snap or not snap.get("indices"):
        return ""

    indices = snap["indices"]
    kospi = indices.get("KOSPI") or {}
    sp500 = indices.get("SP500") or {}

    signals: list[str] = []
    for label, data in (("한국", kospi), ("미국", sp500)):
        if not data:
            continue
        above = data.get("above_200ma")
        vol_rg = data.get("vol_regime")
        dd = data.get("drawdown_from_52w_high_pct") or 0
        if above is True and vol_rg in ("low", "mid") and dd > -10:
            signals.append(f"{label} 추세 강세·안정")
        elif above is False and dd < -15:
            signals.append(f"{label} 하락 추세·약세")
        elif vol_rg == "high":
            signals.append(f"{label} 고변동 국면")
        else:
            signals.append(f"{label} 혼조")

    breadth = snap.get("breadth_kr_pct")
    if breadth is not None:
        if breadth >= 60:
            signals.append(f"KRX 시장폭 강함({breadth:.0f}%)")
        elif breadth <= 40:
            signals.append(f"KRX 시장폭 약함({breadth:.0f}%)")

    return " · ".join(signals) if signals else ""
