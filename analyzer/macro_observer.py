"""매크로 관측 일배치 (Tier 2 #4 인프라).

yfinance 의 EOD 종가를 매일 수집하여 `macro_observations` 에 UPSERT.
시나리오 진행 추적의 데이터 레이어.

수집 변수 (variable_name → yfinance ticker):
  - us_10y_yield : ^TNX  (US 10년 국채 수익률 인덱스, 단위 %)
  - usdkrw       : KRW=X (USD/KRW 환율)
  - wti          : CL=F  (WTI 원유 선물)
  - vix          : ^VIX  (CBOE 변동성 지수)
  - gold         : GC=F  (금 선물)

호출 방식: `python -m analyzer.macro_observer` 또는 systemd timer (별도 unit 으로 등록).

설계 원칙:
  - yfinance 실패는 silent fallback — 분석 파이프라인 본체에 영향 0
  - UPSERT 로 멱등 — 같은 날짜 재실행 안전
  - 단일 호출에 다수 ticker 일괄 fetch (yfinance.download(symbols))
"""
from __future__ import annotations

import sys
import time
from datetime import date, timedelta
from typing import Iterable

from shared.config import AppConfig, DatabaseConfig
from shared.db import get_connection, init_db
from shared.logger import get_logger, init_logger, start_run, finish_run

_log = get_logger("macro")


# variable_name → yfinance symbol
YFINANCE_VARIABLES: dict[str, str] = {
    "us_10y_yield": "^TNX",
    "usdkrw": "KRW=X",
    "wti": "CL=F",
    "vix": "^VIX",
    "gold": "GC=F",
}

# UI 표시용 한글 라벨 (route/template 공유)
MACRO_LABELS_KR: dict[str, str] = {
    "us_10y_yield": "미 10Y 금리",
    "usdkrw": "USD/KRW",
    "wti": "WTI 원유",
    "vix": "VIX 변동성",
    "gold": "금 (USD)",
}

# 단위 (UI 포맷용)
MACRO_UNITS: dict[str, str] = {
    "us_10y_yield": "%",
    "usdkrw": "₩",
    "wti": "$",
    "vix": "",
    "gold": "$",
}


def fetch_and_store(
    db_cfg: DatabaseConfig,
    *,
    variables: Iterable[str] | None = None,
    lookback_days: int = 5,
) -> dict[str, int]:
    """yfinance EOD 종가 일괄 fetch → macro_observations UPSERT.

    Args:
        variables: 수집할 variable_name 리스트. None → 전체.
        lookback_days: yfinance 조회 윈도우 (휴장 보정용 — 5 일이면 충분).

    Returns:
        {variable_name: rows_upserted}
    """
    started = time.time()
    targets = list(variables) if variables else list(YFINANCE_VARIABLES.keys())
    targets = [v for v in targets if v in YFINANCE_VARIABLES]
    if not targets:
        _log.warning("[macro] 수집 대상 없음")
        return {}

    try:
        import yfinance as yf
    except Exception as e:
        _log.error(f"[macro] yfinance import 실패: {e}")
        return {}

    end = date.today() + timedelta(days=1)
    start = end - timedelta(days=max(lookback_days, 5))

    counts: dict[str, int] = {}
    rows_to_upsert: list[tuple] = []

    for var_name in targets:
        symbol = YFINANCE_VARIABLES[var_name]
        try:
            # auto_adjust=True 는 dividends/splits 보정 — 매크로 변수는 무관하지만 일관성
            df = yf.download(
                symbol,
                start=start.isoformat(),
                end=end.isoformat(),
                progress=False,
                auto_adjust=False,
                actions=False,
            )
        except Exception as e:
            _log.warning(f"[macro] {var_name}({symbol}) yf.download 실패: {e}")
            counts[var_name] = 0
            continue

        if df is None or df.empty:
            _log.info(f"[macro] {var_name} 데이터 0건 (휴장·심볼 변경 가능)")
            counts[var_name] = 0
            continue

        # MultiIndex (yfinance 0.2+ 단일 ticker 도 MultiIndex 가능) → flatten
        try:
            close_series = df["Close"]
            if hasattr(close_series, "columns"):
                close_series = close_series.iloc[:, 0]
        except KeyError:
            _log.warning(f"[macro] {var_name} Close 컬럼 없음")
            counts[var_name] = 0
            continue

        var_rows = 0
        for ts, val in close_series.items():
            try:
                if val is None or (isinstance(val, float) and (val != val)):  # NaN 가드
                    continue
                obs_date = ts.date() if hasattr(ts, "date") else ts
                rows_to_upsert.append((var_name, obs_date, float(val), "yfinance"))
                var_rows += 1
            except Exception:
                continue
        counts[var_name] = var_rows

    if not rows_to_upsert:
        _log.warning("[macro] 모든 변수 수집 실패 — UPSERT 스킵")
        return counts

    # UPSERT
    sql = """
    INSERT INTO macro_observations (variable_name, observed_at, value, source)
    VALUES %s
    ON CONFLICT (variable_name, observed_at) DO UPDATE SET
        value = EXCLUDED.value,
        source = EXCLUDED.source,
        fetched_at = NOW()
    """
    conn = get_connection(db_cfg)
    try:
        from psycopg2.extras import execute_values
        with conn.cursor() as cur:
            execute_values(cur, sql, rows_to_upsert, page_size=500)
        conn.commit()
    except Exception as e:
        conn.rollback()
        _log.error(f"[macro] UPSERT 실패: {e}")
        conn.close()
        return counts
    finally:
        conn.close()

    duration = time.time() - started
    _log.info(
        f"[macro] {sum(counts.values())}건 수집·저장 ({counts}) / {duration*1000:.0f}ms"
    )
    return counts


def main() -> int:
    cfg = AppConfig()
    try:
        init_db(cfg.db)
    except Exception as e:
        print(f"[에러] DB 초기화 실패: {e}", file=sys.stderr)
        return 1

    init_logger(cfg.db)
    run_id = start_run(cfg.db, run_type="macro_observer", meta={})
    try:
        counts = fetch_and_store(cfg.db)
        finish_run(
            cfg.db, run_id,
            status="success" if any(counts.values()) else "warning",
            summary=f"vars={len(counts)} total={sum(counts.values())}",
        )
        return 0
    except Exception as e:
        finish_run(cfg.db, run_id, status="failure", error_message=str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
