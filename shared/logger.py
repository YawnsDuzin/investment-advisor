"""범용 DB 로그 시스템 — app_logs / app_runs 테이블 기반

모든 모듈(analyzer, api, shared)에서 사용 가능한 로깅 인프라.
콘솔 출력 + DB 저장을 동시에 수행하여, 추후 웹에서 로그를 조회하고
문제를 진단할 수 있다.

사용 예:
    from shared.logger import init_logger, start_run, finish_run, get_logger

    # 초기화 (앱 시작 시 1회)
    init_logger(db_cfg)

    # 실행(run) 단위 추적 (배치/API 요청 등)
    run_id = start_run(db_cfg, run_type="analyzer", meta={"date": "2026-04-18"})
    log = get_logger("Stage1")
    log.info("테마 분석 시작")
    log.error("SDK 타임아웃", extra={"detail": traceback.format_exc()})
    finish_run(db_cfg, run_id, status="success", summary="이슈 8건, 테마 5건")
"""
import logging
import traceback as tb_module


# ── 모듈 상태 ─────────────────────────────────────
_db_cfg = None
_current_run_id: int | None = None
_initialized = False


class DBLogHandler(logging.Handler):
    """로그 레코드를 app_logs 테이블에 저장하는 핸들러

    DB 장애 시 로그 저장 실패를 무시하여 애플리케이션을 중단하지 않는다.
    B-5: extra={"context": {...}} 전달 시 JSONB 컬럼에 저장 (v23+).
    """

    def emit(self, record: logging.LogRecord) -> None:
        if _db_cfg is None:
            return
        try:
            import json as _json
            from shared.db import get_connection
            conn = get_connection(_db_cfg)
            try:
                with conn.cursor() as cur:
                    # extra에서 추가 필드 추출
                    run_id = getattr(record, 'run_id', _current_run_id)
                    stage = getattr(record, 'stage', record.name)
                    detail = getattr(record, 'detail', None)
                    context = getattr(record, 'context', None)
                    if record.exc_info and not detail:
                        detail = self.format(record)  # 트레이스백 포함

                    context_json = None
                    if context is not None:
                        try:
                            context_json = _json.dumps(context, ensure_ascii=False, default=str)
                        except Exception:
                            context_json = None

                    # v23+: context 컬럼 포함. 없으면 graceful fallback.
                    try:
                        cur.execute(
                            """INSERT INTO app_logs
                               (run_id, level, source, message, detail, context)
                               VALUES (%s, %s, %s, %s, %s, %s::jsonb)""",
                            (run_id, record.levelname, stage,
                             record.getMessage(), detail, context_json),
                        )
                    except Exception:
                        conn.rollback()
                        cur.execute(
                            """INSERT INTO app_logs
                               (run_id, level, source, message, detail)
                               VALUES (%s, %s, %s, %s, %s)""",
                            (run_id, record.levelname, stage,
                             record.getMessage(), detail),
                        )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            # DB 저장 실패 — 콘솔 핸들러가 이미 출력하므로 무시
            pass


def init_logger(db_cfg=None, level: int = logging.INFO) -> logging.Logger:
    """범용 로거 초기화 — 콘솔 + DB 핸들러 등록

    Args:
        db_cfg: DatabaseConfig 인스턴스. None이면 콘솔만 출력.
        level: 최소 로그 레벨 (기본 INFO)

    Returns:
        'app' 로거 인스턴스
    """
    global _db_cfg, _initialized
    _db_cfg = db_cfg

    logger = logging.getLogger("app")
    if _initialized:
        return logger

    logger.setLevel(level)
    logger.propagate = False  # 루트 로거 중복 출력 방지

    # 콘솔 핸들러
    console = logging.StreamHandler()
    console.setLevel(level)
    fmt = logging.Formatter("[%(levelname)s] %(message)s")
    console.setFormatter(fmt)
    logger.addHandler(console)

    # DB 핸들러 (db_cfg가 있을 때만)
    if db_cfg is not None:
        db_handler = DBLogHandler()
        db_handler.setLevel(logging.INFO)  # INFO 이상 DB 저장
        logger.addHandler(db_handler)

    _initialized = True
    return logger


def get_logger(source: str | None = None) -> logging.Logger | logging.LoggerAdapter:
    """소스(모듈/스테이지) 태그가 붙은 로거 반환

    Args:
        source: 로그 출처 태그 (예: "Stage1", "news_collector", "api.chat")

    Returns:
        LoggerAdapter (source가 있을 때) 또는 Logger
    """
    logger = logging.getLogger("app")
    if source:
        return logging.LoggerAdapter(logger, {"stage": source, "source": source})
    return logger


# ── 실행(run) 추적 ─────────────────────────────────

def start_run(db_cfg, run_type: str, meta: dict | None = None) -> int | None:
    """실행(run) 시작 기록 → run_id 반환

    Args:
        db_cfg: DatabaseConfig
        run_type: 실행 유형 (예: "analyzer", "api_request", "translate", "admin_task")
        meta: 추가 메타데이터 (JSON으로 저장)

    Returns:
        run_id (DB 저장 실패 시 None)
    """
    global _current_run_id
    import json

    try:
        from shared.db import get_connection
        conn = get_connection(db_cfg)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO app_runs
                       (run_type, status, meta, started_at)
                       VALUES (%s, 'running', %s, NOW()) RETURNING id""",
                    (run_type,
                     json.dumps(meta, ensure_ascii=False) if meta else None),
                )
                _current_run_id = cur.fetchone()[0]
            conn.commit()
            return _current_run_id
        finally:
            conn.close()
    except Exception as e:
        get_logger().warning(f"run 시작 기록 실패: {e}")
        return None


def finish_run(db_cfg, run_id: int | None, status: str,
               summary: str | None = None,
               error_message: str | None = None) -> None:
    """실행 완료 기록

    Args:
        status: "success", "failure", "partial", "skipped"
        summary: 실행 결과 요약 (예: "이슈 8건, 테마 5건, 제안 42건")
        error_message: 실패 시 에러 메시지
    """
    global _current_run_id
    if run_id is None:
        _current_run_id = None
        return

    try:
        from shared.db import get_connection
        conn = get_connection(db_cfg)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE app_runs SET
                           status = %s,
                           finished_at = NOW(),
                           duration_sec = EXTRACT(EPOCH FROM (NOW() - started_at)),
                           summary = %s,
                           error_message = %s
                       WHERE id = %s""",
                    (status, summary, error_message, run_id),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass
    finally:
        if _current_run_id == run_id:
            _current_run_id = None


def get_recent_runs(db_cfg, run_type: str | None = None,
                    limit: int = 20) -> list[dict]:
    """최근 실행 이력 조회 (웹 UI 표시용)

    Args:
        run_type: 필터링할 실행 유형 (None이면 전체)
        limit: 조회 건수

    Returns:
        [{"id", "run_type", "status", "started_at", "duration_sec", "summary", ...}]
    """
    from psycopg2.extras import RealDictCursor
    try:
        from shared.db import get_connection
        conn = get_connection(db_cfg)
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if run_type:
                    cur.execute(
                        """SELECT * FROM app_runs
                           WHERE run_type = %s
                           ORDER BY started_at DESC LIMIT %s""",
                        (run_type, limit),
                    )
                else:
                    cur.execute(
                        "SELECT * FROM app_runs ORDER BY started_at DESC LIMIT %s",
                        (limit,),
                    )
                return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        return []


def get_run_logs(db_cfg, run_id: int, level: str | None = None,
                 limit: int = 500) -> list[dict]:
    """특정 run의 로그 조회

    Args:
        run_id: 실행 ID
        level: 필터링할 로그 레벨 (None이면 전체)
        limit: 조회 건수

    Returns:
        [{"id", "level", "source", "message", "detail", "created_at"}]
    """
    from psycopg2.extras import RealDictCursor
    try:
        from shared.db import get_connection
        conn = get_connection(db_cfg)
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if level:
                    cur.execute(
                        """SELECT * FROM app_logs
                           WHERE run_id = %s AND level = %s
                           ORDER BY created_at LIMIT %s""",
                        (run_id, level.upper(), limit),
                    )
                else:
                    cur.execute(
                        """SELECT * FROM app_logs
                           WHERE run_id = %s
                           ORDER BY created_at LIMIT %s""",
                        (run_id, limit),
                    )
                return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        return []


# ── AI 쿼리 아카이브 (B-1) ────────────────────────

def archive_ai_query(
    stage: str,
    target_key: str | None,
    model: str | None,
    prompt_system: str,
    prompt_user: str,
    response_raw: str,
    elapsed_sec: float,
    parse_status: str,
    parse_error: str | None = None,
    recovered_fields: dict | None = None,
    run_id: int | None = None,
) -> int | None:
    """Claude SDK 쿼리의 원본 프롬프트+응답을 ai_query_archive에 영구 보존.

    JSON 파싱 실패·빈 복구·타임아웃 등 사후 재현·재분석이 가능해진다.

    Args:
        stage: 'stage1a', 'stage1b', 'stage2', 'translate' 등
        target_key: 테마명 또는 ticker — 디버깅 식별자
        model: 사용 모델
        prompt_system: 시스템 프롬프트
        prompt_user: 사용자 프롬프트
        response_raw: Claude 원본 응답 전문 (truncate 안 함)
        elapsed_sec: 쿼리 소요 시간(초)
        parse_status: 'success' | 'truncated_recovered' | 'failed' | 'empty' | 'timeout_partial'
        parse_error: JSON 파싱 에러 메시지
        recovered_fields: 복구 시 살아남은 필드 요약 (예: {"themes": 0, "issues": 0})
        run_id: 현재 run_id (기본: 글로벌 _current_run_id)

    Returns:
        archive id (저장 실패 시 None)
    """
    if _db_cfg is None:
        return None

    effective_run_id = run_id if run_id is not None else _current_run_id

    try:
        import json as _json
        from shared.db import get_connection
        conn = get_connection(_db_cfg)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO ai_query_archive
                       (run_id, stage, target_key, model,
                        prompt_system, prompt_user, response_raw,
                        response_chars, elapsed_sec, parse_status,
                        parse_error, recovered_fields)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                       RETURNING id""",
                    (
                        effective_run_id, stage, target_key, model,
                        prompt_system, prompt_user, response_raw,
                        len(response_raw or ""), elapsed_sec, parse_status,
                        parse_error,
                        _json.dumps(recovered_fields, ensure_ascii=False)
                            if recovered_fields else None,
                    ),
                )
                archive_id = cur.fetchone()[0]
            conn.commit()
            return archive_id
        finally:
            conn.close()
    except Exception:
        # 아카이브 실패는 분석을 중단시키지 않음 (콘솔 로그로만)
        try:
            get_logger("archive").warning("AI 쿼리 아카이브 저장 실패 (무시)")
        except Exception:
            pass
        return None


def get_run_ai_queries(db_cfg, run_id: int, failed_only: bool = False,
                       limit: int = 200) -> list[dict]:
    """특정 run의 AI 쿼리 아카이브 목록 조회 (진단 UI용)"""
    from psycopg2.extras import RealDictCursor
    try:
        from shared.db import get_connection
        conn = get_connection(db_cfg)
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if failed_only:
                    cur.execute(
                        """SELECT id, run_id, stage, target_key, model,
                                  response_chars, elapsed_sec, parse_status,
                                  parse_error, recovered_fields, created_at
                           FROM ai_query_archive
                           WHERE run_id = %s AND parse_status != 'success'
                           ORDER BY created_at LIMIT %s""",
                        (run_id, limit),
                    )
                else:
                    cur.execute(
                        """SELECT id, run_id, stage, target_key, model,
                                  response_chars, elapsed_sec, parse_status,
                                  parse_error, recovered_fields, created_at
                           FROM ai_query_archive
                           WHERE run_id = %s
                           ORDER BY created_at LIMIT %s""",
                        (run_id, limit),
                    )
                return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        return []


def get_ai_query_raw(db_cfg, query_id: int) -> dict | None:
    """단일 AI 쿼리의 raw 응답 전문 반환 (진단용)"""
    from psycopg2.extras import RealDictCursor
    try:
        from shared.db import get_connection
        conn = get_connection(db_cfg)
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM ai_query_archive WHERE id = %s",
                    (query_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            conn.close()
    except Exception:
        return None


# ── 사건 보고서 (B-3) ────────────────────────────

def save_incident_report(db_cfg, run_id: int, session_id: int | None,
                         report: dict, severity: str = "info") -> None:
    """실행 종료 시 사건 보고서를 DB에 저장 (incident_reports).

    Args:
        report: {"truncated_queries": [...], "price_anomalies": [...],
                 "invalid_tickers": [...], "stage2_failures": [...], ...}
        severity: 'info' | 'warn' | 'critical'
    """
    if db_cfg is None or run_id is None:
        return
    try:
        import json as _json
        from shared.db import get_connection
        # 보고서 항목 개수 계산
        issue_count = 0
        for v in report.values():
            if isinstance(v, list):
                issue_count += len(v)

        conn = get_connection(db_cfg)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO incident_reports
                       (run_id, session_id, severity, issue_count, report)
                       VALUES (%s, %s, %s, %s, %s::jsonb)
                       ON CONFLICT (run_id) DO UPDATE SET
                         severity = EXCLUDED.severity,
                         issue_count = EXCLUDED.issue_count,
                         report = EXCLUDED.report""",
                    (run_id, session_id, severity, issue_count,
                     _json.dumps(report, ensure_ascii=False, default=str)),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def get_incident_report(db_cfg, run_id: int) -> dict | None:
    """run_id 의 사건 보고서 조회"""
    from psycopg2.extras import RealDictCursor
    try:
        from shared.db import get_connection
        conn = get_connection(db_cfg)
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM incident_reports WHERE run_id = %s",
                    (run_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            conn.close()
    except Exception:
        return None
