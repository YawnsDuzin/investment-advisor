"""S&P 500 + Nasdaq-100 종목 리스트를 Wikipedia에서 1회 fetch → 시드 JSON 생성.

분석 파이프라인은 매번 외부 fetch를 하지 않는다. 본 도구로 생성된
`shared/seeds_data/us_universe.json`을 universe_sync가 정적 시드로 읽는다.
인덱스 구성종목이 변경되면(분기 리밸런싱 등) 본 도구를 재실행하여 시드를 갱신한다.

사용:
    python -m tools.refresh_us_universe
    python -m tools.refresh_us_universe --output shared/seeds_data/us_universe.json

설계 참조: _docs/20260422172248_recommendation-engine-redesign.md §1.1 (Phase 1b)
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NDX100_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"

# Wikipedia는 기본 urllib UA를 차단하므로 명시 UA 필요
_HEADERS = {
    "User-Agent": "investment-advisor-bot/1.0 (+https://github.com/yawnsduzin/investment-advisor) python-httpx",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

DEFAULT_OUTPUT = Path("shared/seeds_data/us_universe.json")


def _fetch_html(url: str) -> str:
    resp = httpx.get(url, headers=_HEADERS, timeout=30.0, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def fetch_sp500() -> list[dict]:
    """S&P500 구성종목 fetch.

    Wikipedia 표 컬럼 (2026 현재): Symbol, Security, GICS Sector, GICS Sub-Industry,
    Headquarters, Date added, CIK, Founded.
    """
    html = _fetch_html(SP500_URL)
    tables = pd.read_html(io.StringIO(html), attrs={"id": "constituents"})
    if not tables:
        raise RuntimeError("S&P500: constituents 테이블을 찾을 수 없음")
    df = tables[0]
    out = []
    for _, row in df.iterrows():
        ticker = str(row.get("Symbol", "")).strip()
        if not ticker:
            continue
        # Wikipedia는 BRK.B / BF.B 표기, yfinance는 BRK-B / BF-B
        ticker_yf = ticker.replace(".", "-")
        out.append({
            "ticker": ticker_yf,
            "ticker_wiki": ticker,
            "asset_name": str(row.get("Security", "")).strip() or ticker,
            "sector_gics": str(row.get("GICS Sector", "")).strip() or None,
            "industry": str(row.get("GICS Sub-Industry", "")).strip() or None,
            "indices": ["SP500"],
        })
    return out


def fetch_nasdaq100() -> list[dict]:
    """Nasdaq-100 구성종목 fetch.

    Wikipedia 표 컬럼 (2026 현재): Ticker, Company, GICS Sector, GICS Sub-Industry.
    """
    # 페이지에 여러 테이블이 있어 attrs로 특정. id="constituents"가 표준.
    html = _fetch_html(NDX100_URL)
    tables = pd.read_html(io.StringIO(html), attrs={"id": "constituents"})
    if not tables:
        raise RuntimeError("Nasdaq100: constituents 테이블을 찾을 수 없음")
    df = tables[0]
    # 컬럼명 정규화 (페이지 변경 대비)
    cols = {c.lower(): c for c in df.columns}
    ticker_col = cols.get("ticker") or cols.get("symbol")
    name_col = cols.get("company") or cols.get("security")
    sector_col = cols.get("gics sector")
    industry_col = cols.get("gics sub-industry")
    if not ticker_col:
        raise RuntimeError(f"Nasdaq100: ticker 컬럼 없음 (cols={list(df.columns)})")
    out = []
    for _, row in df.iterrows():
        ticker = str(row.get(ticker_col, "")).strip()
        if not ticker:
            continue
        ticker_yf = ticker.replace(".", "-")
        out.append({
            "ticker": ticker_yf,
            "ticker_wiki": ticker,
            "asset_name": str(row.get(name_col, "")).strip() if name_col else ticker,
            "sector_gics": str(row.get(sector_col, "")).strip() if sector_col else None,
            "industry": str(row.get(industry_col, "")).strip() if industry_col else None,
            "indices": ["NDX100"],
        })
    return out


def merge_dedup(sp500: list[dict], ndx100: list[dict]) -> list[dict]:
    """티커 기준 중복 제거. 양 인덱스에 모두 있으면 indices 합집합."""
    by_ticker: dict[str, dict] = {}
    for entry in sp500 + ndx100:
        tk = entry["ticker"]
        if tk in by_ticker:
            existing = by_ticker[tk]
            merged_indices = sorted(set(existing["indices"]) | set(entry["indices"]))
            existing["indices"] = merged_indices
            # sector/industry: 비어있으면 채움
            for k in ("sector_gics", "industry", "asset_name"):
                if not existing.get(k) and entry.get(k):
                    existing[k] = entry[k]
        else:
            by_ticker[tk] = dict(entry)
    return sorted(by_ticker.values(), key=lambda e: e["ticker"])


def write_seed(constituents: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {"sp500": SP500_URL, "ndx100": NDX100_URL},
        "count": len(constituents),
        "indices_count": {
            "sp500": sum(1 for c in constituents if "SP500" in c["indices"]),
            "ndx100": sum(1 for c in constituents if "NDX100" in c["indices"]),
            "both": sum(1 for c in constituents if "SP500" in c["indices"] and "NDX100" in c["indices"]),
        },
        "constituents": constituents,
    }
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="S&P500 + Nasdaq-100 시드 갱신")
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                   help=f"출력 JSON 경로 (기본: {DEFAULT_OUTPUT})")
    args = p.parse_args(argv)

    print(f"[refresh_us_universe] S&P500 fetch -> {SP500_URL}")
    sp500 = fetch_sp500()
    print(f"  S&P500: {len(sp500)}종목")

    print(f"[refresh_us_universe] Nasdaq-100 fetch -> {NDX100_URL}")
    ndx = fetch_nasdaq100()
    print(f"  Nasdaq-100: {len(ndx)}종목")

    merged = merge_dedup(sp500, ndx)
    print(f"  병합 후 unique: {len(merged)}종목")
    write_seed(merged, args.output)
    print(f"[refresh_us_universe] 저장 완료 -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
