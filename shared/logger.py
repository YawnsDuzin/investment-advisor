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
    """

    def emit(self, record: logging.LogRecord) -> None:
        if _db_cfg is None:
            return
        try:
            from shared.db import get_connection
            conn = get_connection(_db_cfg)
            try:
                with conn.cursor() as cur:
                    # extra에서 추가 필드 추출
                    run_id = getattr(record, 'run_id', _current_run_id)
                    stage = getattr(record, 'stage', record.name)
                    detail = getattr(record, 'detail', None)
                    if record.exc_info and not detail:
                        detail = self.format(record)  # 트레이스백 포함

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
