"""US 오버나이트 시장 요약 (프리마켓 브리핑 D1).

US 장 마감 직후(KST 06:00~) `stock_universe_ohlcv` 의 최신 미국 OHLCV에서
섹터별 평균 등락률·Top movers를 집계한다. LLM 브리핑 프롬프트의 실측 데이터로 사용.

설계 원칙
  - 단일 SQL 패스로 sector_norm 집계와 Top movers 추출 (대용량 OHLCV 스캔 회피)
  - 실측 change_pct만 사용 — LLM은 해석만 담당
  - 섹터 미분류(`sector_norm IS NULL`) 종목은 _미분류_ 버킷으로 분리, 합계 왜곡 방지
  - 결측 대응: latest US OHLCV가 없으면 빈 dict 반환 → 호출자는 브리핑 생략

공개 API
  - compute_us_overnight_summary(db_cfg) -> dict
  - format_us_summary_text(snap) -> str
"""
from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from shared.config import DatabaseConfig
from shared.db import get_connection
from shared.logger import get_logger

_KST = ZoneInfo("Asia/Seoul")
_log = get_logger("overnight_us")

# 미국 시장 식별자 (대문자)
_US_MARKETS = ("NASDAQ", "NYSE", "AMEX")

# 인덱스 매핑 — market_indices_ohlcv (B2 인프라)
_US_INDEX_CODES = ("SP500", "NDX100")

# 임계값 — 환경변수로 오버라이드 가능 (직접 import 안 함, 호출자가 dict 인자로 전달 가능)
_DEFAULT_TOP_N = 8           # 전체 Top movers 수
_DEFAULT_PER_SECTOR_TOP = 3  # 섹터당 대표 종목 수
_DEFAULT_MIN_SECTOR_N = 3    # 섹터 집계 최소 종목 수 (작은 섹터 노이즈 제거)
_DEFAULT_SURGE_PCT = 2.0     # 섹터 평균이 이 값 이상이면 "surge" 후보


def compute_us_overnight_summary(
    db_cfg: DatabaseConfig,
    *,
    top_n: int = _DEFAULT_TOP_N,
    per_sector_top: int = _DEFAULT_PER_SECTOR_TOP,
    min_sector_n: int = _DEFAULT_MIN_SECTOR_N,
) -> dict:
    """US OHLCV 최신일 기준 오버나이트 요약 스냅샷.

    Returns:
        {
          "trade_date": "YYYY-MM-DD",   # 미국 장 거래일
          "universe_size": int,         # 집계 대상 미국 종목 수
          "top_movers": [
            {"ticker", "asset_name", "market", "sector_norm",
             "close", "change_pct", "volume"}, ...
          ],
          "top_losers": [...],          # 같은 스키마, 하락률 큰 순
          "sector_aggregates": [
            {"sector_norm", "label", "n",
             "avg_change_pct", "median_change_pct", "max_change_pct",
             "top_stocks": [...]}, ...
          ],
          "indices": {
            "SP500": {"close": float, "change_pct": float, "trade_date": str},
            "NDX100": {...},
          },
          "computed_at": ISO datetime str,
        }

    데이터 결측이면 빈 dict {}.
    """
    started = time.time()
    latest = _get_latest_us_trade_date(db_cfg)
    if latest is None:
        _log.info("[overnight_us] 미국 OHLCV 데이터 없음 — 빈 스냅샷 반환")
        return {}

    rows = _fetch_us_daily_changes(db_cfg, latest)
    if not rows:
        _log.warning(f"[overnight_us] {latest} 데이터 0건 — 빈 스냅샷 반환")
        return {}

    # change_pct 정렬 — 상승/하락 Top
    sorted_up = sorted(
        (r for r in rows if r["change_pct"] is not None),
        key=lambda r: float(r["change_pct"]), reverse=True,
    )
    sorted_dn = sorted(
        (r for r in rows if r["change_pct"] is not None),
        key=lambda r: float(r["change_pct"]),
    )
    top_movers = [_serialize_row(r) for r in sorted_up[:top_n]]
    top_losers = [_serialize_row(r) for r in sorted_dn[:top_n]]

    # 섹터 집계
    sector_groups: dict[str, list] = {}
    for r in rows:
        if r["change_pct"] is None:
            continue
        key = r.get("sector_norm") or "_uncategorized"
        sector_groups.setdefault(key, []).append(r)

    sector_aggregates = []
    for sector, members in sector_groups.items():
        n = len(members)
        if n < min_sector_n:
            continue  # 노이즈 제거
        changes = [float(m["change_pct"]) for m in members]
        changes_sorted = sorted(changes)
        avg = sum(changes) / n
        median = changes_sorted[n // 2] if n % 2 == 1 else (
            (changes_sorted[n // 2 - 1] + changes_sorted[n // 2]) / 2
        )
        members_sorted = sorted(members, key=lambda r: float(r["change_pct"]), reverse=True)
        sector_aggregates.append({
            "sector_norm": sector,
            "label": _sector_label(sector),
            "n": n,
            "avg_change_pct": round(avg, 2),
            "median_change_pct": round(median, 2),
            "max_change_pct": round(changes_sorted[-1], 2),
            "min_change_pct": round(changes_sorted[0], 2),
            "top_stocks": [_serialize_row(r) for r in members_sorted[:per_sector_top]],
        })
    sector_aggregates.sort(key=lambda s: s["avg_change_pct"], reverse=True)

    # 인덱스 (S&P500/NDX100) 변동 — 별도 테이블
    indices = _fetch_us_indices(db_cfg)

    snap = {
        "trade_date": latest.isoformat() if hasattr(latest, "isoformat") else str(latest),
        "universe_size": len(rows),
        "top_movers": top_movers,
        "top_losers": top_losers,
        "sector_aggregates": sector_aggregates,
        "indices": indices,
        "computed_at": datetime.now(_KST).isoformat(timespec="seconds"),
    }

    _log.info(
        f"[overnight_us] 요약 완료 — trade_date={snap['trade_date']} "
        f"universe={len(rows)} top_movers={len(top_movers)} "
        f"sectors={len(sector_aggregates)} indices={list(indices.keys())} "
        f"({(time.time() - started) * 1000:.0f}ms)"
    )
    return snap


def _get_latest_us_trade_date(db_cfg: DatabaseConfig):
    """미국 OHLCV의 최신 거래일 조회."""
    sql = """
    SELECT MAX(trade_date) AS d
    FROM stock_universe_ohlcv
    WHERE UPPER(market) = ANY(%s)
    """
    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (list(_US_MARKETS),))
            row = cur.fetchone()
            return row[0] if row else None
    except Exception as e:
        _log.warning(f"[overnight_us] 최신 거래일 조회 실패: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    finally:
        conn.close()


def _fetch_us_daily_changes(db_cfg: DatabaseConfig, trade_date) -> list[dict]:
    """특정 거래일 미국 종목별 OHLCV + 메타 (sector_norm·asset_name) JOIN.

    listed=TRUE 종목만. change_pct NULL은 제거하지 않고 전달 — 호출자에서 필터.
    """
    sql = """
    SELECT o.ticker, UPPER(o.market) AS market,
           o.close::float AS close,
           o.change_pct::float AS change_pct,
           o.volume,
           u.asset_name, u.asset_name_en, u.sector_norm, u.sector_gics, u.industry,
           u.market_cap_krw
    FROM stock_universe_ohlcv o
    JOIN stock_universe u
      ON UPPER(u.ticker) = UPPER(o.ticker)
     AND UPPER(u.market) = UPPER(o.market)
    WHERE o.trade_date = %s
      AND UPPER(o.market) = ANY(%s)
      AND u.listed = TRUE
    """
    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (trade_date, list(_US_MARKETS)))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            return rows
    except Exception as e:
        _log.warning(f"[overnight_us] 일별 등락 조회 실패: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return []
    finally:
        conn.close()


def _fetch_us_indices(db_cfg: DatabaseConfig) -> dict:
    """SP500/NDX100 최신 OHLCV 한 줄 — change_pct 위주."""
    sql = """
    SELECT index_code, trade_date,
           close::float AS close,
           change_pct::float AS change_pct
    FROM market_indices_ohlcv mi
    WHERE index_code = ANY(%s)
      AND trade_date = (
          SELECT MAX(trade_date) FROM market_indices_ohlcv mi2
          WHERE mi2.index_code = mi.index_code
      )
    """
    conn = get_connection(db_cfg)
    out: dict = {}
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (list(_US_INDEX_CODES),))
            for code, td, close, ch in cur.fetchall():
                out[code] = {
                    "trade_date": td.isoformat() if hasattr(td, "isoformat") else str(td),
                    "close": float(close) if close is not None else None,
                    "change_pct": float(ch) if ch is not None else None,
                }
    except Exception as e:
        _log.warning(f"[overnight_us] 인덱스 조회 실패: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()
    return out


def _serialize_row(r: dict) -> dict:
    """DB row → API 친화 dict. None/Decimal 정리."""
    name = r.get("asset_name_en") or r.get("asset_name") or r.get("ticker")
    return {
        "ticker": r.get("ticker"),
        "asset_name": name,
        "market": r.get("market"),
        "sector_norm": r.get("sector_norm"),
        "industry": r.get("industry"),
        "close": _f(r.get("close")),
        "change_pct": _f(r.get("change_pct")),
        "volume": int(r["volume"]) if r.get("volume") is not None else None,
    }


def _f(x):
    return float(x) if x is not None else None


# sector_norm 영문 키 → 한글 라벨 (UI/프롬프트 표시용).
# `_uncategorized`는 sector_norm이 NULL인 종목을 모은 별도 버킷.
_SECTOR_LABELS = {
    "semiconductors": "반도체",
    "ai_infra_power": "AI 전력·인프라",
    "software_internet": "소프트웨어·인터넷",
    "biotech_pharma": "바이오·제약",
    "biotech": "바이오",
    "pharma": "제약",
    "healthcare_services": "헬스케어 서비스",
    "medical_devices": "의료기기",
    "ev_battery": "전기차·배터리",
    "auto": "자동차",
    "renewable_energy": "신재생에너지",
    "oil_gas": "석유·가스",
    "utilities": "유틸리티",
    "banks": "은행",
    "insurance": "보험",
    "asset_management": "자산운용",
    "consumer_discretionary": "경기소비재",
    "consumer_staples": "필수소비재",
    "retail": "소매",
    "ecommerce": "이커머스",
    "media_entertainment": "미디어·엔터",
    "telecom": "통신",
    "industrials": "산업재",
    "aerospace_defense": "항공·방산",
    "shipping_logistics": "해운·물류",
    "construction": "건설",
    "materials": "소재",
    "metals_mining": "금속·광물",
    "real_estate": "부동산",
    "_uncategorized": "미분류",
}


def _sector_label(sector_norm: str) -> str:
    return _SECTOR_LABELS.get(sector_norm, sector_norm)


def fetch_kr_beneficiaries_by_sectors(
    db_cfg: DatabaseConfig,
    sectors: list[str],
    *,
    per_sector: int = 6,
    market_cap_min_krw: int = 100_000_000_000,  # 1,000억원 이상 — 너무 작은 종목 제외
) -> dict[str, list[dict]]:
    """주어진 sector_norm 리스트에 매칭되는 KR(KOSPI/KOSDAQ) 종목 후보 추출.

    각 섹터별로 시총 상위 + 최근 1개월 수익률 보유 종목을 시총 desc로 정렬.
    LLM이 "이 미국 섹터 급등 → 한국에서 어떤 종목이 수혜받을 수 있나"를 판단할
    실측 후보 풀로 사용된다 (할루시네이션 방지).

    Args:
        sectors: ['semiconductors', 'ai_infra_power', ...]
        per_sector: 섹터당 최대 후보 수
        market_cap_min_krw: 시총 하한 (기본 1,000억)

    Returns:
        {sector_norm: [{ticker, asset_name, market, market_cap_krw,
                        last_price, r1m_pct}, ...]}
        섹터별 매칭 0건이면 빈 list로 반환 (키는 보존).
    """
    if not sectors:
        return {}

    # KR sector별 종목 + 1개월 수익률 동시 조회
    sql = """
    WITH ranked AS (
        SELECT u.ticker, u.market, u.asset_name, u.sector_norm,
               u.market_cap_krw, u.last_price,
               o.close::float AS close,
               o.trade_date,
               ROW_NUMBER() OVER (
                   PARTITION BY u.ticker, UPPER(u.market)
                   ORDER BY o.trade_date DESC
               ) AS rn
        FROM stock_universe u
        LEFT JOIN stock_universe_ohlcv o
          ON UPPER(u.ticker) = UPPER(o.ticker)
         AND UPPER(u.market) = UPPER(o.market)
         AND o.trade_date >= CURRENT_DATE - 60
        WHERE u.listed = TRUE
          AND UPPER(u.market) IN ('KOSPI', 'KOSDAQ')
          AND u.sector_norm = ANY(%s)
          AND COALESCE(u.market_cap_krw, 0) >= %s
    ),
    endpoints AS (
        SELECT ticker, market, asset_name, sector_norm, market_cap_krw, last_price,
               MAX(CASE WHEN rn = 1  THEN close END) AS c_latest,
               MAX(CASE WHEN rn = 22 THEN close END) AS c_1m
        FROM ranked
        GROUP BY ticker, market, asset_name, sector_norm, market_cap_krw, last_price
    ),
    enriched AS (
        SELECT *,
               CASE WHEN c_1m IS NOT NULL AND c_1m > 0
                    THEN (c_latest - c_1m) / c_1m * 100 END AS r1m_pct,
               ROW_NUMBER() OVER (
                   PARTITION BY sector_norm
                   ORDER BY market_cap_krw DESC NULLS LAST
               ) AS sector_rank
        FROM endpoints
    )
    SELECT ticker, market, asset_name, sector_norm, market_cap_krw,
           last_price, c_latest, r1m_pct
    FROM enriched
    WHERE sector_rank <= %s
    ORDER BY sector_norm, market_cap_krw DESC NULLS LAST
    """
    conn = get_connection(db_cfg)
    out: dict[str, list[dict]] = {s: [] for s in sectors}
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (list(sectors), int(market_cap_min_krw), int(per_sector)))
            for r in cur.fetchall():
                ticker, market, name, sec, cap, last_price, c_latest, r1m = r
                out.setdefault(sec, []).append({
                    "ticker": ticker,
                    "asset_name": name,
                    "market": (market or "").upper(),
                    "sector_norm": sec,
                    "market_cap_krw": int(cap) if cap is not None else None,
                    "last_price": _f(last_price) or _f(c_latest),
                    "r1m_pct": round(float(r1m), 2) if r1m is not None else None,
                })
    except Exception as e:
        _log.warning(f"[overnight_us] KR 후보군 조회 실패: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()
    return out


def format_kr_candidates_text(candidates: dict[str, list[dict]]) -> str:
    """KR 수혜 후보군 dict → 프롬프트 삽입 텍스트.

    LLM이 화이트리스트로만 종목 추천하도록 강제 — 새 티커 발명 금지.
    """
    if not candidates:
        return "(KR 후보군 없음)"

    lines: list[str] = ["## 한국 시장 수혜 후보 (sector_norm 매칭, 시총 상위)"]
    for sector, members in candidates.items():
        if not members:
            continue
        label = _sector_label(sector)
        lines.append(f"\n### {label} ({sector})")
        for m in members:
            cap_str = f"{m['market_cap_krw']/1e8:,.0f}억" if m.get("market_cap_krw") else "-"
            r1m_str = f"1M {m['r1m_pct']:+.1f}%" if m.get("r1m_pct") is not None else "1M -"
            price_str = f"{m['last_price']:,.0f}" if m.get("last_price") else "-"
            lines.append(
                f"- {m['ticker']} ({m['market']}) {m['asset_name']} — "
                f"시총 {cap_str} / 현재가 {price_str} / {r1m_str}"
            )
    return "\n".join(lines)


def format_us_summary_text(snap: dict) -> str:
    """US 오버나이트 스냅샷 → LLM 프롬프트 삽입용 한글 텍스트.

    LLM은 이 텍스트를 읽고 "어떤 섹터가 어떤 카탈리스트로 움직였는지" 해석.
    """
    if not snap or not snap.get("trade_date"):
        return "(미국 OHLCV 데이터 없음)"

    lines: list[str] = []
    td = snap["trade_date"]
    universe_size = snap.get("universe_size", 0)
    lines.append(f"## 미국 시장 ({td}, 집계 {universe_size}종목)")

    # 인덱스
    indices = snap.get("indices", {}) or {}
    if indices:
        idx_parts = []
        for code in _US_INDEX_CODES:
            d = indices.get(code)
            if not d or d.get("change_pct") is None:
                continue
            label = "S&P 500" if code == "SP500" else "Nasdaq 100"
            idx_parts.append(f"{label} {d['change_pct']:+.2f}% (close {d.get('close')})")
        if idx_parts:
            lines.append("- 지수: " + " / ".join(idx_parts))

    # Top movers
    movers = snap.get("top_movers", [])
    if movers:
        lines.append("- Top 상승: " + ", ".join(
            f"{m['ticker']} {m['change_pct']:+.2f}%" for m in movers[:6]
        ))
    losers = snap.get("top_losers", [])
    if losers:
        lines.append("- Top 하락: " + ", ".join(
            f"{m['ticker']} {m['change_pct']:+.2f}%" for m in losers[:5]
        ))

    # 섹터 집계 (상위/하위 5개)
    sectors = snap.get("sector_aggregates", [])
    if sectors:
        lines.append("\n### 섹터별 평균 등락률 (n≥최소)")
        for s in sectors[:8]:
            top_str = ", ".join(
                f"{t['ticker']} {t['change_pct']:+.2f}%"
                for t in (s.get("top_stocks") or [])[:3]
            )
            lines.append(
                f"- {s['label']} ({s['sector_norm']}, n={s['n']}): "
                f"평균 {s['avg_change_pct']:+.2f}% / 중앙값 {s['median_change_pct']:+.2f}% — {top_str}"
            )
        # 하락 섹터 — 마지막 3개
        if len(sectors) > 8:
            tail = sectors[-3:]
            for s in tail:
                if s["avg_change_pct"] >= 0:
                    continue
                lines.append(
                    f"- (하락) {s['label']} ({s['sector_norm']}, n={s['n']}): "
                    f"평균 {s['avg_change_pct']:+.2f}%"
                )

    return "\n".join(lines)
