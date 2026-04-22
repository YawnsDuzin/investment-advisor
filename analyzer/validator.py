"""Evidence Validation Layer (Phase 3) — recommendation-engine-redesign.

AI(Stage 1-B/2)가 제시한 시총·섹터·현재가가 실측 데이터(`stock_universe` + 실시간)와
일치하는지 검증한다. 불일치는 `proposal_validation_log`에 기록되고, 일정 임계값 이상이면
Top Picks 스코어에서 감점된다.

검증 규칙 (계획서 §3.2):
| 필드          | 검증 방법                              | 불일치 기준 |
|---------------|----------------------------------------|-------------|
| market_cap    | stock_universe vs AI 제시값            | ±20% 초과   |
| sector        | stock_universe.sector_norm vs AI 제시값| 불일치       |
| current_price | 실시간(price_source) vs AI 추정         | ±5% 초과    |

설계 참조: _docs/20260422172248_recommendation-engine-redesign.md §3
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from psycopg2.extras import RealDictCursor, execute_values

from shared.config import DatabaseConfig, ValidationConfig
from shared.db import get_connection
from shared.logger import get_logger
from shared.sector_mapping import normalize_sector


_log = get_logger("validator")


@dataclass
class ValidationFinding:
    """단일 필드 검증 결과."""
    field_name: str               # "market_cap" | "sector" | "current_price"
    ai_value: str | None
    actual_value: str | None
    evidence_source: str          # 예: "stock_universe", "yfinance_realtime", "pykrx_20260422"
    mismatch: bool
    mismatch_pct: float | None = None  # 수치 필드의 괴리율 (%)


@dataclass
class ProposalValidation:
    """제안 단위 검증 결과 묶음."""
    findings: list[ValidationFinding] = field(default_factory=list)

    @property
    def mismatch_count(self) -> int:
        return sum(1 for f in self.findings if f.mismatch)


# ── 유니버스 메타 lookup ───────────────────────────

def _fetch_universe_meta(db_cfg: DatabaseConfig, ticker_market_pairs: set[tuple[str, str]]) -> dict:
    """(ticker, market) 집합 → universe row dict 매핑.

    market 표기 정규화 시도: KRX/KSE → KOSPI 등은 caller가 처리. 여기서는 정확 매칭 + 티커 only fallback.

    Returns:
        {(ticker, market): {sector_norm, market_cap_krw, last_price, last_price_ccy, ...}}
    """
    if not ticker_market_pairs:
        return {}

    # 1차: 정확 매칭
    tickers = list({t for t, _ in ticker_market_pairs})
    conn = get_connection(db_cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT ticker, market, asset_name, sector_norm, sector_krx, sector_gics,
                       market_cap_krw, market_cap_bucket, last_price, last_price_ccy,
                       last_price_at, listed
                FROM stock_universe
                WHERE ticker = ANY(%s)
                """,
                (tickers,),
            )
            rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    by_pair: dict[tuple[str, str], dict] = {}
    by_ticker: dict[str, list[dict]] = {}
    for r in rows:
        key = (r["ticker"], r["market"])
        by_pair[key] = r
        by_ticker.setdefault(r["ticker"], []).append(r)

    out: dict[tuple[str, str], dict] = {}
    for tk, mk in ticker_market_pairs:
        if (tk, mk) in by_pair:
            out[(tk, mk)] = by_pair[(tk, mk)]
            continue
        # market 표기 보정 시도
        candidates = by_ticker.get(tk, [])
        if candidates:
            # 한국 시장 별칭 통일: KRX/KSE/KOSPI/KOSDAQ
            mk_upper = (mk or "").upper()
            if mk_upper in ("KRX", "KSE", "KOSPI"):
                pick = next((c for c in candidates if c["market"] == "KOSPI"), None)
            elif mk_upper in ("KQ", "KOSDAQ"):
                pick = next((c for c in candidates if c["market"] == "KOSDAQ"), None)
            elif mk_upper in ("US", "AMEX", "NYSEARCA"):
                pick = next((c for c in candidates if c["market"] in ("NYSE", "NASDAQ")), None)
            else:
                pick = candidates[0]
            if pick:
                out[(tk, mk)] = pick
    return out


# ── 단일 검증 ──────────────────────────────────────

def _safe_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _validate_market_cap(proposal: dict, universe: dict, tolerance_pct: float) -> ValidationFinding | None:
    """AI가 시총을 명시한 경우만 검증 (Stage 1-B는 시총 필드를 보통 출력하지 않으므로 N/A)."""
    ai_mcap = _safe_float(proposal.get("market_cap"))
    if ai_mcap is None or ai_mcap <= 0:
        return None
    actual_mcap = _safe_float(universe.get("market_cap_krw"))
    if actual_mcap is None or actual_mcap <= 0:
        return None
    diff_pct = abs(ai_mcap - actual_mcap) / actual_mcap * 100
    return ValidationFinding(
        field_name="market_cap",
        ai_value=str(int(ai_mcap)),
        actual_value=str(int(actual_mcap)),
        evidence_source="stock_universe",
        mismatch=diff_pct > tolerance_pct,
        mismatch_pct=round(diff_pct, 2),
    )


def _validate_sector(proposal: dict, universe: dict) -> ValidationFinding | None:
    """AI 제시 sector를 정규화하여 universe.sector_norm과 비교."""
    ai_sector_raw = proposal.get("sector")
    if not ai_sector_raw or not isinstance(ai_sector_raw, str) or not ai_sector_raw.strip():
        return None
    actual_norm = universe.get("sector_norm")
    if not actual_norm:
        return None
    # AI 제시값을 sector_norm으로 정규화 (KRX/GICS/industry 모두 시도)
    ai_norm = normalize_sector(
        sector_krx=ai_sector_raw,
        sector_gics=ai_sector_raw,
        industry=ai_sector_raw,
        warn_on_miss=False,
    )
    return ValidationFinding(
        field_name="sector",
        ai_value=ai_sector_raw,
        actual_value=actual_norm,
        evidence_source="stock_universe",
        mismatch=(ai_norm != actual_norm and ai_norm != "other"),
    )


def _validate_current_price(proposal: dict, tolerance_pct: float) -> ValidationFinding | None:
    """가격 출처가 실시간(yfinance/pykrx)인지 vs AI 추정(price_source 없음)인지 확인.

    Stage 1 모멘텀 체크 단계에서 실시간 가격이 주입되면 price_source가 채워진다.
    Stage 1 AI가 별도로 추정한 가격이 있다면 비교 가능하지만, 현재 _validate_proposal이
    price_source 없는 가격은 이미 NULL로 만든다 → 여기서는 유의미한 비교가 어려움.
    실측 출처가 신뢰할 만한 형태로 들어왔는지만 기록한다.
    """
    cur_price = _safe_float(proposal.get("current_price"))
    if cur_price is None:
        return None
    src = proposal.get("price_source")
    return ValidationFinding(
        field_name="current_price",
        ai_value=None,                          # AI 단독 추정값은 _validate_proposal 단계에서 제거됨
        actual_value=str(cur_price),
        evidence_source=src or "unknown",
        mismatch=False,                         # 추정-실측 비교 불가 → 기록만
    )


def validate_proposal(
    proposal: dict, universe_meta: dict | None,
    cfg: ValidationConfig,
) -> ProposalValidation:
    """단일 proposal에 대한 모든 필드 검증.

    universe_meta가 None이면 universe에 등록되지 않은 종목 — 검증 자체가 의미 없음.
    """
    pv = ProposalValidation()

    if universe_meta:
        f = _validate_market_cap(proposal, universe_meta, cfg.market_cap_tolerance_pct)
        if f:
            pv.findings.append(f)

        f = _validate_sector(proposal, universe_meta)
        if f:
            pv.findings.append(f)

    f = _validate_current_price(proposal, cfg.price_tolerance_pct)
    if f:
        pv.findings.append(f)

    return pv


# ── 배치 검증 + DB 저장 ───────────────────────────

def validate_and_persist(
    db_cfg: DatabaseConfig,
    proposals_with_id: list[tuple[int, dict]],
    cfg: ValidationConfig | None = None,
) -> dict:
    """저장된 proposals를 검증하고 결과를 proposal_validation_log에 일괄 INSERT.

    Args:
        proposals_with_id: [(proposal_id, proposal_dict), ...]
        cfg: ValidationConfig (None이면 기본값)

    Returns:
        {"checked": N, "mismatches": N, "by_field": {field: count}, "duration_sec": float}
    """
    if cfg is None:
        cfg = ValidationConfig()
    if not cfg.enabled:
        _log.info("Evidence validation 비활성화 — 스킵")
        return {"checked": 0, "mismatches": 0, "by_field": {}, "duration_sec": 0.0}

    started = datetime.now(timezone.utc)
    pairs = {(p.get("ticker"), p.get("market")) for _, p in proposals_with_id
             if p.get("ticker") and p.get("market")}
    universe_map = _fetch_universe_meta(db_cfg, pairs)

    rows: list[tuple] = []
    by_field: dict[str, int] = {}
    total_mismatches = 0
    universe_hits = 0

    for proposal_id, proposal in proposals_with_id:
        key = (proposal.get("ticker"), proposal.get("market"))
        meta = universe_map.get(key)
        if meta:
            universe_hits += 1
        pv = validate_proposal(proposal, meta, cfg)
        for f in pv.findings:
            rows.append((
                proposal_id, f.field_name, f.ai_value, f.actual_value,
                f.evidence_source, f.mismatch, f.mismatch_pct,
            ))
            if f.mismatch:
                total_mismatches += 1
                by_field[f.field_name] = by_field.get(f.field_name, 0) + 1

    if not rows:
        duration = (datetime.now(timezone.utc) - started).total_seconds()
        _log.info(f"검증 대상 없음 ({len(proposals_with_id)}건 입력 / universe 매칭 {universe_hits}건)")
        return {"checked": 0, "mismatches": 0, "by_field": {},
                "duration_sec": duration, "universe_hits": universe_hits}

    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """INSERT INTO proposal_validation_log
                   (proposal_id, field_name, ai_value, actual_value,
                    evidence_source, mismatch, mismatch_pct)
                   VALUES %s""",
                rows,
                page_size=200,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    duration = (datetime.now(timezone.utc) - started).total_seconds()
    _log.info(
        f"검증 완료: {len(proposals_with_id)}건 proposal / {len(rows)}건 검증 / "
        f"{total_mismatches}건 mismatch (by_field={by_field}) / "
        f"universe 매칭 {universe_hits}건 / {duration*1000:.0f}ms"
    )
    return {
        "checked": len(rows),
        "mismatches": total_mismatches,
        "by_field": by_field,
        "universe_hits": universe_hits,
        "duration_sec": duration,
    }


# ── 스코어링 연동 헬퍼 ─────────────────────────────

def fetch_mismatch_counts(db_cfg: DatabaseConfig, proposal_ids: list[int]) -> dict[int, int]:
    """proposal_id → mismatch_count 매핑 (recommender 스코어 감점에 사용)."""
    if not proposal_ids:
        return {}
    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT proposal_id, COUNT(*) AS mismatch_count
                FROM proposal_validation_log
                WHERE proposal_id = ANY(%s) AND mismatch = TRUE
                GROUP BY proposal_id
                """,
                (proposal_ids,),
            )
            return {int(r[0]): int(r[1]) for r in cur.fetchall()}
    finally:
        conn.close()
