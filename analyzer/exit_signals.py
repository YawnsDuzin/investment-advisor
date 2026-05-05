"""매도/익절 시그널 평가 + 알림 생성 (Tier 2 — 인사이트 #4).

`price_tracker` 가 post_return / max_drawdown 을 갱신한 직후 호출되어
다음 룰을 평가하고, 미발송(NULL) proposal 에 한해 user_notifications 를 fan-out.

룰
  - target_hit  : 가장 짧은 가용 post_return >= max(proposal.upside_pct, default_target_pct)
  - stop_loss   : max_drawdown_pct <= stop_loss_pct  OR  최신 post_return <= stop_loss_pct

dedup
  - investment_proposals.{target_hit_notified_at, stop_loss_notified_at}
    NOW() 로 갱신 → 재발송 차단

알림 fan-out 대상
  - user_watchlist.ticker == p.ticker (case-insensitive)
  - user_subscriptions.sub_type='ticker' AND sub_key == p.ticker

타이틀 포맷
  - "[익절시그널] {asset_name} ({ticker}) — 추천 후 +N.N% 도달"
  - "[손절시그널] {asset_name} ({ticker}) — 추천 후 -N.N% 하락"
link 는 `/stocks/{ticker}`.
"""
from __future__ import annotations

import os

from psycopg2.extras import RealDictCursor

from shared.config import DatabaseConfig
from shared.db import get_connection
from shared.logger import get_logger


_log = get_logger("exit_signals")


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


# 환경변수 오버라이드 — 운영기에서 재배포 없이 임계 조정 가능
_DEFAULT_TARGET_PCT = _env_float("EXIT_TARGET_PCT", 30.0)
_DEFAULT_STOP_LOSS_PCT = _env_float("EXIT_STOP_LOSS_PCT", -15.0)


def _shortest_available_return(row: dict, columns: tuple[str, ...]) -> float | None:
    """row 에서 가장 짧은 측정기간의 post_return (없으면 다음 긴 기간) 을 float 으로 반환."""
    for col in columns:
        v = row.get(col)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def _fan_out_notification(
    cur,
    *,
    ticker: str,
    title: str,
    detail: str,
    link: str,
) -> int:
    """워치리스트 + ticker 구독자에게 알림 INSERT. fan-out 수 반환."""
    cur.execute(
        """
        SELECT DISTINCT user_id FROM (
            SELECT user_id FROM user_watchlist WHERE UPPER(ticker) = UPPER(%s)
            UNION
            SELECT user_id FROM user_subscriptions
             WHERE sub_type = 'ticker' AND UPPER(sub_key) = UPPER(%s)
        ) AS recipients
        WHERE user_id IS NOT NULL
        """,
        (ticker, ticker),
    )
    rows = cur.fetchall()
    if not rows:
        return 0

    user_ids = [r[0] if isinstance(r, tuple) else r["user_id"] for r in rows]
    inserted = 0
    for uid in user_ids:
        cur.execute(
            """
            INSERT INTO user_notifications (user_id, title, detail, link)
            VALUES (%s, %s, %s, %s)
            """,
            (uid, title, detail, link),
        )
        inserted += 1
    return inserted


def _format_target_hit(asset_name: str, ticker: str, return_pct: float, threshold: float) -> tuple[str, str]:
    title = f"[익절시그널] {asset_name} ({ticker}) — 추천 후 +{return_pct:.1f}% 도달"
    detail = (
        f"목표 +{threshold:.1f}% 초과. 진입가 대비 현재 수익률을 확인하고 "
        "익절 또는 부분 매도 검토 권장."
    )
    return title, detail


def _format_stop_loss(asset_name: str, ticker: str, return_pct: float, threshold: float) -> tuple[str, str]:
    title = f"[손절시그널] {asset_name} ({ticker}) — 추천 후 {return_pct:.1f}% 하락"
    detail = (
        f"손절 임계 {threshold:.1f}% 도달 (post_return 또는 max_drawdown). "
        "투자 가설 재검증 + 리스크 재평가 권장."
    )
    return title, detail


_RETURN_COLUMNS = (
    "post_return_1m_pct", "post_return_3m_pct",
    "post_return_6m_pct", "post_return_1y_pct",
)


def evaluate_exit_signals(
    db_cfg: DatabaseConfig,
    *,
    default_target_pct: float | None = None,
    stop_loss_pct: float | None = None,
) -> dict:
    """매도/익절 시그널 룰 평가 + 알림 fan-out.

    Args:
        default_target_pct: proposal.upside_pct 결측 시 적용할 기본 익절 임계 (%, 양수)
        stop_loss_pct: 손절 임계 (%, 음수). post_return 또는 max_drawdown 둘 중 하나라도 초과 시 발동.

    Returns:
        {
            "target_hit_count": int,
            "stop_loss_count": int,
            "target_hit_notifications": int,
            "stop_loss_notifications": int,
        }
    """
    target = float(default_target_pct if default_target_pct is not None else _DEFAULT_TARGET_PCT)
    stop = float(stop_loss_pct if stop_loss_pct is not None else _DEFAULT_STOP_LOSS_PCT)

    target_hits = 0
    stop_losses = 0
    target_notifs = 0
    stop_notifs = 0

    conn = get_connection(db_cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # ── 1) target_hit 후보 (미발송) ──
            cur.execute(
                f"""
                SELECT p.id, p.ticker, p.market, p.asset_name,
                       p.upside_pct,
                       {", ".join("p." + c for c in _RETURN_COLUMNS)}
                FROM investment_proposals p
                WHERE p.target_hit_notified_at IS NULL
                  AND p.action = 'buy'
                  AND p.entry_price IS NOT NULL
                """,
            )
            for r in cur.fetchall():
                # proposal 자신의 upside 가 있으면 그것을 (단, 양수일 때만), 없거나 음수면 default
                upside = r.get("upside_pct")
                try:
                    upside_v = float(upside) if upside is not None else None
                except (TypeError, ValueError):
                    upside_v = None
                threshold = upside_v if (upside_v is not None and upside_v > 0) else target

                latest = _shortest_available_return(r, _RETURN_COLUMNS)
                if latest is None or latest < threshold:
                    continue

                title, detail = _format_target_hit(
                    asset_name=r["asset_name"] or r["ticker"],
                    ticker=r["ticker"],
                    return_pct=latest,
                    threshold=threshold,
                )
                fanout = _fan_out_notification(
                    cur, ticker=r["ticker"],
                    title=title, detail=detail,
                    link=f"/stocks/{r['ticker']}",
                )
                cur.execute(
                    "UPDATE investment_proposals SET target_hit_notified_at = NOW() WHERE id = %s",
                    (r["id"],),
                )
                target_hits += 1
                target_notifs += fanout

            # ── 2) stop_loss 후보 (미발송) ──
            cur.execute(
                f"""
                SELECT p.id, p.ticker, p.market, p.asset_name,
                       p.max_drawdown_pct,
                       {", ".join("p." + c for c in _RETURN_COLUMNS)}
                FROM investment_proposals p
                WHERE p.stop_loss_notified_at IS NULL
                  AND p.action = 'buy'
                  AND p.entry_price IS NOT NULL
                """,
            )
            for r in cur.fetchall():
                dd = r.get("max_drawdown_pct")
                try:
                    dd_v = float(dd) if dd is not None else None
                except (TypeError, ValueError):
                    dd_v = None
                latest = _shortest_available_return(r, _RETURN_COLUMNS)

                triggered_value: float | None = None
                if dd_v is not None and dd_v <= stop:
                    triggered_value = dd_v
                elif latest is not None and latest <= stop:
                    triggered_value = latest
                if triggered_value is None:
                    continue

                title, detail = _format_stop_loss(
                    asset_name=r["asset_name"] or r["ticker"],
                    ticker=r["ticker"],
                    return_pct=triggered_value,
                    threshold=stop,
                )
                fanout = _fan_out_notification(
                    cur, ticker=r["ticker"],
                    title=title, detail=detail,
                    link=f"/stocks/{r['ticker']}",
                )
                cur.execute(
                    "UPDATE investment_proposals SET stop_loss_notified_at = NOW() WHERE id = %s",
                    (r["id"],),
                )
                stop_losses += 1
                stop_notifs += fanout

        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

    _log.info(
        f"[exit_signals] target_hit={target_hits} (알림 {target_notifs}건), "
        f"stop_loss={stop_losses} (알림 {stop_notifs}건)"
    )
    return {
        "target_hit_count": target_hits,
        "stop_loss_count": stop_losses,
        "target_hit_notifications": target_notifs,
        "stop_loss_notifications": stop_notifs,
    }
