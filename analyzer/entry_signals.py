"""진입 신호 계산 — C-7(진입 부적합 라벨) + B-5(진입 매력 컴퍼짓 점수).

session_detail UI 가 추천 종목을 표시할 때 "추천이지만 지금 사기에는 부적합" /
"지금 진입 매력 있음" 신호를 함께 노출하기 위한 데이터 함수.

데이터 출처:
- `stock_universe_fundamentals` (v39) — PER / dividend_yield 최신 스냅샷
- `stock_universe_foreign_flow`  (v44) — KRX 60일 누적 외국인 순매수
- `stock_universe_ohlcv`         (v27) — 60일 고점 대비 낙폭

설계 원칙:
- **AI 호출 없음** — 결정적 SQL 한 방.
- 데이터 결측 종목은 침묵 폴백 (블록·신호 모두 비움) — 신규 종목/펀더 sync 미완 케이스.
- 외국인 수급은 KRX 한정 — US 종목은 외국인 신호 자체를 평가하지 않음.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from psycopg2.extras import RealDictCursor

from shared.config import DatabaseConfig
from shared.db import get_connection
from shared.logger import get_logger

_log = get_logger("entry_signals")

# ── 임계값 (필요 시 환경변수로 빼낼 수 있음) ──
PER_HIGH_BLOCK = 100.0      # PER > 100 = 고평가 진입 부적합
PER_LOW_SIGNAL = 20.0       # PER < 20 = 저평가 진입 매력
DIV_YIELD_SIGNAL = 3.0      # 배당률 > 3% = 진입 매력 (배당주)
DRAWDOWN_BLOCK_PCT = 30.0   # 60일 고점 대비 -30% = 낙폭 진입 부적합
FOREIGN_60D_THRESHOLD = 0   # 부호만 사용 (순매수/순매도)

_KRX_MARKETS = ("KOSPI", "KOSDAQ", "KONEX")


@dataclass
class EntrySignal:
    """종목별 진입 신호 결과."""
    blocks: list[str] = field(default_factory=list)   # 진입 부적합 사유
    signals: list[str] = field(default_factory=list)  # 진입 매력 신호
    score: int = 0                                     # 0~3 (signals 개수)
    has_data: bool = False                             # 펀더/수급 데이터 존재 여부

    def to_dict(self) -> dict:
        return {
            "blocks": self.blocks,
            "signals": self.signals,
            "score": self.score,
            "has_data": self.has_data,
        }


_QUERY_SQL = """
WITH targets AS (
    SELECT UPPER(t.ticker) AS ticker, UPPER(t.market) AS market
    FROM UNNEST(%s::text[], %s::text[]) AS t(ticker, market)
),
latest_fund AS (
    SELECT DISTINCT ON (UPPER(f.ticker), UPPER(f.market))
           UPPER(f.ticker) AS ticker,
           UPPER(f.market) AS market,
           f.per, f.dividend_yield, f.snapshot_date
    FROM stock_universe_fundamentals f
    JOIN targets t
      ON UPPER(f.ticker) = t.ticker AND UPPER(f.market) = t.market
    WHERE f.snapshot_date >= CURRENT_DATE - 30
    ORDER BY UPPER(f.ticker), UPPER(f.market), f.snapshot_date DESC
),
ff_60d AS (
    SELECT UPPER(ff.ticker) AS ticker,
           UPPER(ff.market) AS market,
           SUM(ff.foreign_net_buy_value) AS foreign_net_60d
    FROM stock_universe_foreign_flow ff
    JOIN targets t
      ON UPPER(ff.ticker) = t.ticker AND UPPER(ff.market) = t.market
    WHERE ff.snapshot_date >= CURRENT_DATE - 60
    GROUP BY UPPER(ff.ticker), UPPER(ff.market)
),
ohlcv_ranked AS (
    SELECT UPPER(o.ticker) AS ticker, UPPER(o.market) AS market,
           o.close, o.high, o.trade_date,
           ROW_NUMBER() OVER (PARTITION BY UPPER(o.ticker), UPPER(o.market)
                              ORDER BY o.trade_date DESC) AS rn
    FROM stock_universe_ohlcv o
    JOIN targets t
      ON UPPER(o.ticker) = t.ticker AND UPPER(o.market) = t.market
    WHERE o.trade_date >= CURRENT_DATE - 90
),
ohlcv_60d AS (
    SELECT ticker, market,
           MAX(high) FILTER (WHERE rn <= 60) AS high_60d,
           MAX(CASE WHEN rn = 1 THEN close END) AS latest_close
    FROM ohlcv_ranked
    GROUP BY ticker, market
)
SELECT t.ticker, t.market,
       lf.per::float        AS per,
       lf.dividend_yield::float AS dividend_yield,
       lf.snapshot_date     AS fund_date,
       ff.foreign_net_60d::bigint AS foreign_net_60d,
       oh.high_60d::float   AS high_60d,
       oh.latest_close::float AS latest_close
FROM targets t
LEFT JOIN latest_fund lf USING (ticker, market)
LEFT JOIN ff_60d      ff USING (ticker, market)
LEFT JOIN ohlcv_60d   oh USING (ticker, market);
"""


def compute_entry_signals(
    db_cfg: DatabaseConfig,
    proposals: Iterable[dict],
) -> dict[tuple[str, str], dict]:
    """proposals 리스트에 대해 진입 가드/매력 신호를 일괄 계산.

    Args:
        proposals: 각 dict 에 `ticker`, `market` 키가 있어야 함. 추가로
                   `price_momentum_check` 가 있으면 AI 판단 신호도 반영.

    Returns:
        dict 키 `(TICKER_UPPER, MARKET_UPPER)` → `EntrySignal.to_dict()`.
        데이터 없는 종목도 빈 EntrySignal 로 포함 (has_data=False).
    """
    # 입력 정규화 — 중복 제거 + AI 모멘텀 판단 보존
    seen: dict[tuple[str, str], str | None] = {}
    for p in proposals:
        tk = (p.get("ticker") or "").strip().upper()
        mk = (p.get("market") or "").strip().upper()
        if not tk or not mk:
            continue
        seen.setdefault((tk, mk), p.get("price_momentum_check"))

    if not seen:
        return {}

    tickers = [k[0] for k in seen.keys()]
    markets = [k[1] for k in seen.keys()]

    # 빈 결과 컨테이너 초기화 (데이터 없는 종목도 entry_signals.has_data=False 로 반환)
    out: dict[tuple[str, str], EntrySignal] = {
        key: EntrySignal() for key in seen.keys()
    }

    conn = get_connection(db_cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(_QUERY_SQL, (tickers, markets))
            rows = cur.fetchall()
    except Exception as e:
        # 펀더/수급 테이블 미존재 (v39/v44 이전) 또는 SQL 오류 시 모두 빈 신호로 폴백
        _log.warning(f"entry_signals SQL 실패 — 빈 신호로 폴백: {e}")
        conn.close()
        return {key: out[key].to_dict() for key in out}
    finally:
        try:
            conn.close()
        except Exception:
            pass

    for row in rows:
        key = (row["ticker"], row["market"])
        sig = out.get(key)
        if sig is None:
            continue

        per = row.get("per")
        div = row.get("dividend_yield")
        foreign_60d = row.get("foreign_net_60d")
        high_60d = row.get("high_60d")
        latest = row.get("latest_close")
        ai_momentum = seen.get(key)
        is_krx = key[1] in _KRX_MARKETS

        # 데이터 존재 플래그 — 펀더/수급/OHLCV 중 하나라도 있으면 True
        sig.has_data = any(v is not None for v in (per, foreign_60d, high_60d))

        # ── 진입 부적합 (blocks) ──
        if per is not None:
            if per > PER_HIGH_BLOCK:
                sig.blocks.append(f"고PER({per:.0f})")
            elif per < 0:
                sig.blocks.append(f"적자(PER {per:.1f})")
        if is_krx and foreign_60d is not None and foreign_60d < 0:
            # 단위: 원 → 억원 환산해 가독성 ↑
            amt_eok = abs(foreign_60d) / 100_000_000
            sig.blocks.append(f"외국인 60일 순매도(-{amt_eok:.0f}억)")
        if high_60d and latest and high_60d > 0:
            drawdown = (1.0 - latest / high_60d) * 100.0
            if drawdown >= DRAWDOWN_BLOCK_PCT:
                sig.blocks.append(f"60일 낙폭 -{drawdown:.0f}%")

        # ── 진입 매력 (signals, 0~3 점) ──
        # 1) AI 모멘텀 판단 — undervalued
        if ai_momentum == "undervalued":
            sig.signals.append("AI: 미반영 종목")
        # 2) 펀더: 저PER 또는 고배당
        if per is not None and 0 < per < PER_LOW_SIGNAL:
            sig.signals.append(f"저PER({per:.0f})")
        elif div is not None and div >= DIV_YIELD_SIGNAL:
            sig.signals.append(f"고배당({div:.1f}%)")
        # 3) 외국인 매수 (KRX 한정)
        if is_krx and foreign_60d is not None and foreign_60d > 0:
            amt_eok = foreign_60d / 100_000_000
            sig.signals.append(f"외국인 60일 순매수(+{amt_eok:.0f}억)")

        sig.score = min(3, len(sig.signals))

    return {key: sig.to_dict() for key, sig in out.items()}
