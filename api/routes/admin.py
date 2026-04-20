"""관리자 API — 분석 실행 + SSE 실시간 로그 스트리밍 + 진단 (B-2)"""
import os
import sys
import subprocess
import threading
import queue
import time
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Request, Query, Depends, Body, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse, PlainTextResponse, FileResponse
from psycopg2.extras import RealDictCursor
from shared.config import AnalyzerConfig, AuthConfig
from api.templates_provider import templates
from shared.db import get_untranslated_news, update_news_title_ko, update_news_translation, get_connection
from shared.logger import (
    get_recent_runs, get_run_logs, get_run_ai_queries,
    get_ai_query_raw, get_incident_report,
)
from api.auth.dependencies import require_role, get_current_user, _get_auth_cfg
from api.auth.models import UserInDB
from api.deps import get_db_cfg as _get_cfg, get_db_conn

router = APIRouter(prefix="/admin", tags=["관리자"])

# ── 실행 상태 + 로그 보관 (메모리) ──────────────
_running = False
_process: subprocess.Popen | None = None
_log_lines: list[str] = []        # 누적 로그 (재접속 시 복원용)
_log_queue: queue.Queue[str | None] = queue.Queue()  # 실시간 스트리밍용
_subscribers: list[queue.Queue] = []  # 복수 SSE 클라이언트 지원
_lock = threading.Lock()


def _broadcast(msg: str | None):
    """모든 구독 클라이언트에 메시지 전달 + 로그 보관"""
    with _lock:
        if msg is not None:
            _log_lines.append(msg)
        for q in _subscribers:
            q.put(msg)


@router.get("")
def admin_page(request: Request, user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    """관리자 페이지"""
    from fastapi.responses import RedirectResponse
    if auth_cfg.enabled:
        if user is None:
            return RedirectResponse("/auth/login?next=/admin", status_code=302)
        if user.role != "admin":
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="접근 권한이 없습니다")
    return templates.TemplateResponse(request=request, name="admin.html", context={
        "request": request,
        "active_page": "admin",
        "is_running": _running,
        "current_user": user,
        "auth_enabled": auth_cfg.enabled,
    })


@router.get("/status")
def get_status(_admin: Optional[UserInDB] = Depends(require_role("admin"))):
    """현재 분석 실행 상태 + 기존 로그"""
    return {
        "running": _running,
        "log_count": len(_log_lines),
    }


@router.get("/logs")
def get_logs(after: int = Query(default=0, ge=0), _admin: Optional[UserInDB] = Depends(require_role("admin"))):
    """기존 로그 조회 (재접속 시 복원용)

    after: 이 인덱스 이후의 로그만 반환
    """
    return {
        "running": _running,
        "logs": _log_lines[after:],
        "total": len(_log_lines),
    }


@router.post("/run")
def run_analysis(_admin: Optional[UserInDB] = Depends(require_role("admin"))):
    """분석 파이프라인 실행 (SSE 스트리밍)"""
    # SSE stream — B2.5 Pattern A 예외: stream lifecycle과 FastAPI Depends yield 충돌 회피
    global _running, _process, _log_lines

    if _running:
        def already_running():
            yield "data: [경고] 분석이 이미 실행 중입니다.\n\n"
            yield "event: done\ndata: already_running\n\n"
        return StreamingResponse(already_running(), media_type="text/event-stream")

    # 이전 로그 초기화
    _log_lines = []

    def _run_subprocess():
        """별도 스레드에서 서브프로세스 실행"""
        global _running, _process
        try:
            python_exe = sys.executable
            env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
            _process = subprocess.Popen(
                [python_exe, "-u", "-m", "analyzer.main"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=".",
                bufsize=1,
                env=env,
            )

            for raw_line in iter(_process.stdout.readline, b""):
                text = raw_line.decode("utf-8", errors="replace").rstrip()
                if text:
                    _broadcast(text)

            _process.stdout.close()
            _process.wait()
            exit_code = _process.returncode

            if exit_code == 0:
                _broadcast("[완료] 분석이 성공적으로 완료되었습니다.")
            else:
                _broadcast(f"[오류] 분석 종료 (exit code: {exit_code})")

        except Exception as e:
            _broadcast(f"[오류] {str(e)}")
        finally:
            _running = False
            _process = None
            _broadcast(None)  # 종료 신호

    def stream_logs():
        """SSE 스트리밍 제너레이터"""
        global _running
        _running = True

        # 이 클라이언트 전용 큐 등록
        client_queue: queue.Queue[str | None] = queue.Queue()
        with _lock:
            _subscribers.append(client_queue)

        try:
            _broadcast("[시작] 분석 파이프라인을 실행합니다...")

            # 서브프로세스 스레드 시작
            thread = threading.Thread(target=_run_subprocess, daemon=True)
            thread.start()

            while True:
                try:
                    msg = client_queue.get(timeout=1.0)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue

                if msg is None:
                    break
                for sub in msg.split("\n"):
                    yield f"data: {sub}\n\n"

            yield "event: done\ndata: finished\n\n"
        finally:
            with _lock:
                _subscribers.remove(client_queue)

    return StreamingResponse(stream_logs(), media_type="text/event-stream")


@router.get("/stream")
def stream_existing(_admin: Optional[UserInDB] = Depends(require_role("admin"))):
    """실행 중인 분석의 로그를 실시간 구독 (재접속용)"""
    # SSE stream — B2.5 Pattern A 예외: stream lifecycle과 FastAPI Depends yield 충돌 회피
    if not _running:
        def not_running():
            yield "event: done\ndata: not_running\n\n"
        return StreamingResponse(not_running(), media_type="text/event-stream")

    client_queue: queue.Queue[str | None] = queue.Queue()
    with _lock:
        _subscribers.append(client_queue)

    def stream():
        try:
            while True:
                try:
                    msg = client_queue.get(timeout=1.0)
                except queue.Empty:
                    if not _running:
                        break
                    yield ": keepalive\n\n"
                    continue

                if msg is None:
                    break
                for sub in msg.split("\n"):
                    yield f"data: {sub}\n\n"

            yield "event: done\ndata: finished\n\n"
        finally:
            with _lock:
                if client_queue in _subscribers:
                    _subscribers.remove(client_queue)

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/stop")
def stop_analysis(_admin: Optional[UserInDB] = Depends(require_role("admin"))):
    """실행 중인 분석 중단"""
    global _process, _running
    if _process and _process.returncode is None:
        _process.terminate()
        try:
            _process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _process.kill()
        _broadcast("[중단] 사용자에 의해 분석이 중단되었습니다.")
        _running = False
        _process = None
        _broadcast(None)  # 구독자에게 종료 신호
        return {"message": "분석이 중단되었습니다"}
    _running = False
    _process = None
    return {"message": "실행 중인 분석이 없습니다"}


# ── 뉴스 한글 번역 ──────────────────────────────
_translating = False


@router.get("/translate-news/status")
def translate_status(_admin: Optional[UserInDB] = Depends(require_role("admin"))):
    """미번역 뉴스 건수 조회"""
    cfg = _get_cfg()
    articles = get_untranslated_news(cfg)
    return {"untranslated_count": len(articles), "translating": _translating}


@router.post("/translate-news")
def translate_existing_news(_admin: Optional[UserInDB] = Depends(require_role("admin"))):
    """기존 미번역 뉴스를 한글 번역 (SSE 스트리밍)"""
    # SSE stream — B2.5 Pattern A 예외: stream lifecycle과 FastAPI Depends yield 충돌 회피
    global _translating

    if _translating:
        def already():
            yield "data: [경고] 번역이 이미 실행 중입니다.\n\n"
            yield "event: done\ndata: already\n\n"
        return StreamingResponse(already(), media_type="text/event-stream")

    cfg = _get_cfg()
    articles = get_untranslated_news(cfg)

    if not articles:
        def none_found():
            yield "data: [완료] 번역할 뉴스가 없습니다.\n\n"
            yield "event: done\ndata: finished\n\n"
        return StreamingResponse(none_found(), media_type="text/event-stream")

    def stream():
        global _translating
        _translating = True
        try:
            import re
            import json
            import anyio
            from analyzer.analyzer import _query_claude, _parse_json_response

            analyzer_cfg = AnalyzerConfig()
            translate_model = analyzer_cfg.model_translate

            def _has_korean(text: str) -> bool:
                return bool(re.search(r'[\uac00-\ud7af]', text))

            total = len(articles)
            yield f"data: [시작] 미번역 뉴스 {total}건 번역을 시작합니다 (모델: {translate_model}).\n\n"

            # 이미 한글인 것 먼저 처리
            korean_updates = []
            to_translate = []
            for a in articles:
                title = a.get("title", "")
                summary = a.get("summary", "")
                title_is_ko = _has_korean(title)
                summary_is_ko = _has_korean(summary) or not summary

                if title_is_ko and summary_is_ko:
                    korean_updates.append((a["id"], title, summary))
                else:
                    to_translate.append(a)

            if korean_updates:
                update_news_translation(cfg, korean_updates)
                yield f"data: [한글] 이미 한글인 뉴스 {len(korean_updates)}건 처리 완료\n\n"

            if not to_translate:
                yield "data: [완료] 모든 뉴스가 한글입니다.\n\n"
                yield "event: done\ndata: finished\n\n"
                return

            # 30건씩 배치 번역 (제목+요약)
            BATCH_SIZE = 30
            translated_total = 0

            for batch_start in range(0, len(to_translate), BATCH_SIZE):
                batch = to_translate[batch_start:batch_start + BATCH_SIZE]
                batch_num = batch_start // BATCH_SIZE + 1
                total_batches = (len(to_translate) + BATCH_SIZE - 1) // BATCH_SIZE

                yield f"data: [번역] 배치 {batch_num}/{total_batches} — {len(batch)}건 제목+요약 번역 중...\n\n"

                # 프롬프트 구성 — 제목+요약 함께
                items_text = []
                for a in batch:
                    summary_short = (a.get("summary") or "")[:200]
                    items_text.append(f"{a['id']}:\nt: {a['title']}\ns: {summary_short}")

                prompt = f"""아래 뉴스의 제목(t)과 요약(s)을 한국어로 번역해주세요.

```
{"---".join(items_text)}
```

반드시 아래 JSON 형식으로만 응답:
{{"translations": {{{", ".join(f'"{a["id"]}": {{"t": "제목 번역", "s": "요약 번역"}}' for a in batch)}}}}}

이미 한글인 필드는 원문 그대로 반환하세요."""

                system_prompt = "뉴스 제목/요약 번역 전문가입니다. 간결하고 자연스러운 한국어로 번역합니다. JSON으로만 응답합니다."

                try:
                    response = anyio.run(
                        _query_claude, prompt, system_prompt, 1,
                        translate_model,
                    )
                    parsed = _parse_json_response(response)
                    translations = parsed.get("translations", {})

                    updates = []
                    for id_str, tr in translations.items():
                        try:
                            article_id = int(id_str)
                            if isinstance(tr, dict):
                                updates.append((
                                    article_id,
                                    tr.get("t", ""),
                                    tr.get("s", ""),
                                ))
                            elif isinstance(tr, str):
                                updates.append((article_id, tr, ""))
                        except ValueError:
                            continue

                    if updates:
                        update_news_translation(cfg, updates)
                        translated_total += len(updates)

                    yield f"data: [번역] 배치 {batch_num} 완료 — {len(updates)}건 번역됨\n\n"

                except Exception as e:
                    yield f"data: [오류] 배치 {batch_num} 번역 실패: {e}\n\n"

            yield f"data: [완료] 총 {translated_total + len(korean_updates)}건 번역 완료 (한글 {len(korean_updates)}건 + 번역 {translated_total}건)\n\n"
            yield "event: done\ndata: finished\n\n"

        except Exception as e:
            yield f"data: [오류] {str(e)}\n\n"
            yield "event: done\ndata: error\n\n"
        finally:
            _translating = False

    return StreamingResponse(stream(), media_type="text/event-stream")


# ── 전체 데이터 삭제 ──────────────────────────────

@router.post("/reset-all-data")
def reset_all_data(
    _admin: Optional[UserInDB] = Depends(require_role("admin")),
    conn=Depends(get_db_conn),
):
    """분석 데이터 전체 삭제 (CASCADE로 하위 테이블 포함)"""
    try:
        with conn.cursor() as cur:
            # CASCADE 관계에 의해 하위 테이블 자동 삭제
            # analysis_sessions → global_issues, investment_themes → theme_scenarios,
            #   macro_impacts, investment_proposals → stock_analyses, news_articles,
            #   user_notifications
            cur.execute("DELETE FROM analysis_sessions")
            deleted_sessions = cur.rowcount

            # 독립 추적 테이블
            cur.execute("DELETE FROM theme_tracking")
            cur.execute("DELETE FROM proposal_tracking")

            # 개인화 데이터 (메모는 proposal FK로 이미 삭제됨)
            cur.execute("DELETE FROM user_notifications")
            cur.execute("DELETE FROM user_proposal_memos")

        conn.commit()
        return {"message": f"전체 데이터 삭제 완료 (세션 {deleted_sessions}건 및 관련 데이터)"}
    except Exception as e:
        conn.rollback()
        return JSONResponse(status_code=500, content={"message": f"삭제 실패: {e}"})


# ── 원격 DB → 로컬 DB 데이터 복사 ──────────────────

# 복사 대상 테이블 (FK 의존 순서)
_COPY_TABLES = [
    "analysis_sessions",
    "global_issues",
    "investment_themes",
    "theme_scenarios",
    "macro_impacts",
    "investment_proposals",
    "stock_analyses",
    "proposal_price_snapshots",
    "investor_trading_data",
    "short_selling_data",
    "news_articles",
    "bond_yields",
    "daily_top_picks",
    "theme_tracking",
    "proposal_tracking",
]


@router.post("/copy-from-remote")
def copy_from_remote(
    host: str = Body(...),
    port: int = Body(5432),
    dbname: str = Body(...),
    user: str = Body(...),
    password: str = Body(...),
    _admin: Optional[UserInDB] = Depends(require_role("admin")),
):
    """원격 DB에서 분석 데이터를 로컬 DB로 복사 (SSE 스트리밍)"""
    # SSE stream — B2.5 Pattern A 예외: stream lifecycle과 FastAPI Depends yield 충돌 회피
    import psycopg2
    from psycopg2.extras import Json, RealDictCursor

    def stream():
        local_cfg = _get_cfg()
        remote_conn = None
        local_conn = None

        try:
            # 1) 원격 DB 연결
            yield "data: [연결] 원격 DB 연결 중...\n\n"
            try:
                remote_conn = psycopg2.connect(
                    host=host, port=port, dbname=dbname,
                    user=user, password=password,
                    connect_timeout=10,
                )
                remote_conn.set_session(readonly=True)
            except Exception as e:
                yield f"data: [오류] 원격 DB 연결 실패: {e}\n\n"
                yield "event: done\ndata: error\n\n"
                return

            yield "data: [연결] 원격 DB 연결 성공\n\n"

            # 2) 로컬 DB 연결
            local_conn = get_connection(local_cfg)

            # 3) 로컬 데이터 삭제 (역순)
            yield "data: [삭제] 로컬 데이터 초기화 중...\n\n"
            with local_conn.cursor() as cur:
                cur.execute("DELETE FROM analysis_sessions")  # CASCADE
                cur.execute("DELETE FROM theme_tracking")
                cur.execute("DELETE FROM proposal_tracking")
            local_conn.commit()
            yield "data: [삭제] 로컬 데이터 초기화 완료\n\n"

            # 4) 테이블별 복사
            total_rows = 0
            for table in _COPY_TABLES:
                try:
                    with remote_conn.cursor(cursor_factory=RealDictCursor) as rcur:
                        # 원격 테이블 존재 확인
                        rcur.execute(
                            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = %s)",
                            (table,),
                        )
                        if not rcur.fetchone()["exists"]:
                            yield f"data: [건너뜀] {table} — 원격에 없음\n\n"
                            continue

                        rcur.execute(f"SELECT * FROM {table} ORDER BY id")
                        rows = rcur.fetchall()

                    if not rows:
                        yield f"data: [건너뜀] {table} — 0건\n\n"
                        continue

                    # 컬럼 목록 + 타입 (JSONB 컬럼 감지용)
                    with local_conn.cursor() as lcur:
                        lcur.execute(
                            "SELECT column_name, data_type FROM information_schema.columns "
                            "WHERE table_name = %s",
                            (table,),
                        )
                        col_info = {r[0]: r[1] for r in lcur.fetchall()}
                    local_cols = set(col_info.keys())
                    jsonb_cols = {c for c, t in col_info.items() if t in ("jsonb", "json")}

                    # 원격·로컬 공통 컬럼만 사용
                    remote_cols = list(rows[0].keys())
                    common_cols = [c for c in remote_cols if c in local_cols]

                    if not common_cols:
                        yield f"data: [건너뜀] {table} — 공통 컬럼 없음\n\n"
                        continue

                    col_list = ", ".join(common_cols)
                    placeholders = ", ".join(["%s"] * len(common_cols))

                    with local_conn.cursor() as lcur:
                        for row in rows:
                            values = [
                                Json(row[c]) if (c in jsonb_cols and isinstance(row[c], (dict, list))) else row[c]
                                for c in common_cols
                            ]
                            lcur.execute(
                                f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
                                f"ON CONFLICT DO NOTHING",
                                values,
                            )

                    local_conn.commit()
                    total_rows += len(rows)
                    yield f"data: [복사] {table} — {len(rows)}건 완료\n\n"

                except Exception as e:
                    local_conn.rollback()
                    yield f"data: [오류] {table} 복사 실패: {e}\n\n"

            # 5) 시퀀스 재설정 (삽입된 ID 이후로)
            yield "data: [정리] 시퀀스 재설정 중...\n\n"
            with local_conn.cursor() as cur:
                for table in _COPY_TABLES:
                    try:
                        cur.execute(f"""
                            SELECT setval(
                                pg_get_serial_sequence('{table}', 'id'),
                                COALESCE((SELECT MAX(id) FROM {table}), 1),
                                true
                            )
                        """)
                    except Exception:
                        pass  # 시퀀스 없는 테이블은 무시
            local_conn.commit()

            yield f"data: [완료] 데이터 복사 완료 — 총 {total_rows}건 복사됨\n\n"
            yield "event: done\ndata: finished\n\n"

        except Exception as e:
            yield f"data: [오류] {e}\n\n"
            yield "event: done\ndata: error\n\n"
        finally:
            if remote_conn:
                remote_conn.close()
            if local_conn:
                local_conn.close()

    return StreamingResponse(stream(), media_type="text/event-stream")


# ── 진단 탭 (B-2) ─────────────────────────────────

@router.get("/diagnostics")
def diagnostics_page(
    request: Request,
    user: Optional[UserInDB] = Depends(get_current_user),
    auth_cfg: AuthConfig = Depends(_get_auth_cfg),
):
    """진단 페이지 (로그·쿼리·체크포인트·사건 보고서 조회)."""
    from fastapi.responses import RedirectResponse
    if auth_cfg.enabled:
        if user is None:
            return RedirectResponse("/auth/login?next=/admin/diagnostics", status_code=302)
        if user.role != "admin":
            raise HTTPException(status_code=403, detail="접근 권한이 없습니다")
    return templates.TemplateResponse(request=request, name="admin_diagnostics.html", context={
        "request": request,
        "active_page": "admin",
        "current_user": user,
        "auth_enabled": auth_cfg.enabled,
    })


@router.get("/runs")
def api_list_runs(
    run_type: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    _admin: Optional[UserInDB] = Depends(require_role("admin")),
):
    """최근 실행(app_runs) 목록."""
    cfg = _get_cfg()
    runs = get_recent_runs(cfg, run_type=run_type, limit=limit)
    # datetime/Decimal 직렬화
    for r in runs:
        for k, v in list(r.items()):
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
            elif hasattr(v, "__float__") and not isinstance(v, (int, float, bool)):
                try:
                    r[k] = float(v)
                except Exception:
                    r[k] = str(v)
    return {"runs": runs, "total": len(runs)}


@router.get("/runs/{run_id}")
def api_run_detail(
    run_id: int,
    _admin: Optional[UserInDB] = Depends(require_role("admin")),
    conn=Depends(get_db_conn),
):
    """단일 run 상세 (run + 사건보고서 + 통계)."""
    cfg = _get_cfg()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM app_runs WHERE id = %s", (run_id,))
        run = cur.fetchone()
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        cur.execute(
            """SELECT level, COUNT(*) AS cnt FROM app_logs
               WHERE run_id = %s GROUP BY level""",
            (run_id,),
        )
        log_stats = {r["level"]: int(r["cnt"]) for r in cur.fetchall()}
        cur.execute(
            """SELECT parse_status, COUNT(*) AS cnt FROM ai_query_archive
               WHERE run_id = %s GROUP BY parse_status""",
            (run_id,),
        )
        ai_stats = {r["parse_status"] or "unknown": int(r["cnt"]) for r in cur.fetchall()}

    incident = get_incident_report(cfg, run_id)

    def _serialize(obj):
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        if hasattr(obj, "__float__") and not isinstance(obj, (int, float, bool)):
            try:
                return float(obj)
            except Exception:
                return str(obj)
        return obj

    run_clean = {k: _serialize(v) for k, v in dict(run).items()}
    incident_clean = None
    if incident:
        incident_clean = {k: _serialize(v) for k, v in incident.items()}
    return {
        "run": run_clean,
        "log_stats": log_stats,
        "ai_query_stats": ai_stats,
        "incident": incident_clean,
    }


@router.get("/runs/{run_id}/logs")
def api_run_logs(
    run_id: int,
    level: Optional[str] = Query(default=None, description="INFO/WARNING/ERROR"),
    limit: int = Query(default=500, ge=1, le=5000),
    _admin: Optional[UserInDB] = Depends(require_role("admin")),
):
    """run의 app_logs 조회."""
    cfg = _get_cfg()
    logs = get_run_logs(cfg, run_id, level=level, limit=limit)
    for r in logs:
        for k, v in list(r.items()):
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
    return {"logs": logs, "total": len(logs)}


@router.get("/runs/{run_id}/queries")
def api_run_queries(
    run_id: int,
    failed_only: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=2000),
    _admin: Optional[UserInDB] = Depends(require_role("admin")),
):
    """run의 ai_query_archive 목록 (raw 응답은 제외)."""
    cfg = _get_cfg()
    queries = get_run_ai_queries(cfg, run_id, failed_only=failed_only, limit=limit)
    for r in queries:
        for k, v in list(r.items()):
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
            elif hasattr(v, "__float__") and not isinstance(v, (int, float, bool)):
                try:
                    r[k] = float(v)
                except Exception:
                    r[k] = str(v)
    return {"queries": queries, "total": len(queries)}


@router.get("/queries/{query_id}")
def api_query_detail(
    query_id: int,
    _admin: Optional[UserInDB] = Depends(require_role("admin")),
):
    """단일 AI 쿼리 상세 (프롬프트·응답 전문 포함)."""
    cfg = _get_cfg()
    row = get_ai_query_raw(cfg, query_id)
    if not row:
        raise HTTPException(status_code=404, detail="query not found")
    for k, v in list(row.items()):
        if hasattr(v, "isoformat"):
            row[k] = v.isoformat()
        elif hasattr(v, "__float__") and not isinstance(v, (int, float, bool)):
            try:
                row[k] = float(v)
            except Exception:
                row[k] = str(v)
    return row


@router.get("/queries/{query_id}/raw")
def api_query_raw_text(
    query_id: int,
    _admin: Optional[UserInDB] = Depends(require_role("admin")),
):
    """AI 쿼리 raw 응답 전문을 plain text로 다운로드."""
    cfg = _get_cfg()
    row = get_ai_query_raw(cfg, query_id)
    if not row:
        raise HTTPException(status_code=404, detail="query not found")
    stage = row.get("stage", "unknown")
    tk = (row.get("target_key") or "").replace("/", "_").replace(":", "_")[:80]
    filename = f"query_{query_id}_{stage}_{tk}.txt"
    body = (
        f"# AI Query Archive #{query_id}\n"
        f"stage: {stage}\n"
        f"target: {row.get('target_key')}\n"
        f"model: {row.get('model')}\n"
        f"parse_status: {row.get('parse_status')}\n"
        f"parse_error: {row.get('parse_error')}\n"
        f"response_chars: {row.get('response_chars')}\n"
        f"elapsed_sec: {row.get('elapsed_sec')}\n"
        f"created_at: {row.get('created_at')}\n"
        f"\n===== SYSTEM PROMPT =====\n{row.get('prompt_system') or ''}\n"
        f"\n===== USER PROMPT =====\n{row.get('prompt_user') or ''}\n"
        f"\n===== RESPONSE RAW =====\n{row.get('response_raw') or ''}\n"
    )
    return PlainTextResponse(
        body,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/checkpoints")
def api_list_checkpoints(
    _admin: Optional[UserInDB] = Depends(require_role("admin")),
):
    """체크포인트 아카이브 목록 + 현재 진행 중인 체크포인트."""
    from analyzer.checkpoint import list_archives
    archives = list_archives()

    live: list[dict] = []
    cp_root = Path("_checkpoints")
    if cp_root.exists():
        for d in sorted(cp_root.iterdir(), reverse=True):
            if d.is_dir() and d.name != "archive":
                stages = sorted(f.stem for f in d.glob("*.json") if f.stem != "_meta")
                meta_path = d / "_meta.json"
                meta = None
                if meta_path.exists():
                    try:
                        import json as _json
                        meta = _json.loads(meta_path.read_text(encoding="utf-8"))
                    except Exception:
                        meta = None
                live.append({
                    "date": d.name,
                    "stages": stages,
                    "meta": meta,
                })
    return {"archives": archives, "live": live}


@router.get("/checkpoints/{date}")
def api_checkpoint_detail(
    date: str,
    stage: Optional[str] = Query(default=None),
    _admin: Optional[UserInDB] = Depends(require_role("admin")),
):
    """체크포인트 단건 조회 — 현재 진행 중인 체크포인트의 특정 stage."""
    # 경로 탈출 방지
    if not date.replace("-", "").isalnum() or len(date) > 20:
        raise HTTPException(status_code=400, detail="invalid date")

    cp_dir = Path("_checkpoints") / date
    if not cp_dir.exists():
        raise HTTPException(status_code=404, detail="checkpoint not found")

    if stage:
        # 경로 탈출 방지
        if not stage.replace("_", "").isalnum() or len(stage) > 30:
            raise HTTPException(status_code=400, detail="invalid stage")
        target = cp_dir / f"{stage}.json"
        if not target.exists():
            raise HTTPException(status_code=404, detail="stage not found")
        import json as _json
        try:
            data = _json.loads(target.read_text(encoding="utf-8"))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"parse error: {e}")
        return {"date": date, "stage": stage, "data": data}

    # 전체 stage 목록
    stages = sorted(f.stem for f in cp_dir.glob("*.json") if f.stem != "_meta")
    return {"date": date, "stages": stages}


@router.get("/checkpoints/archive/{date}/download")
def api_checkpoint_archive_download(
    date: str,
    _admin: Optional[UserInDB] = Depends(require_role("admin")),
):
    """체크포인트 아카이브 tar.gz 다운로드."""
    if not date.replace("-", "").isalnum() or len(date) > 20:
        raise HTTPException(status_code=400, detail="invalid date")
    target = Path("_checkpoints") / "archive" / f"{date}.tar.gz"
    if not target.exists():
        raise HTTPException(status_code=404, detail="archive not found")
    return FileResponse(
        path=str(target),
        media_type="application/gzip",
        filename=f"checkpoint_{date}.tar.gz",
    )


@router.get("/incidents")
def api_list_incidents(
    severity: Optional[str] = Query(default=None, description="info/warn/critical"),
    limit: int = Query(default=30, ge=1, le=200),
    _admin: Optional[UserInDB] = Depends(require_role("admin")),
    conn=Depends(get_db_conn),
):
    """사건 보고서 목록 (최근 run들의 incident 요약)."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        if severity:
            cur.execute(
                """SELECT ir.*, ar.run_type, ar.started_at, ar.status
                   FROM incident_reports ir
                   JOIN app_runs ar ON ar.id = ir.run_id
                   WHERE ir.severity = %s
                   ORDER BY ir.created_at DESC LIMIT %s""",
                (severity, limit),
            )
        else:
            cur.execute(
                """SELECT ir.*, ar.run_type, ar.started_at, ar.status
                   FROM incident_reports ir
                   JOIN app_runs ar ON ar.id = ir.run_id
                   ORDER BY ir.created_at DESC LIMIT %s""",
                (limit,),
            )
        rows = [dict(r) for r in cur.fetchall()]

    for r in rows:
        for k, v in list(r.items()):
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
    return {"incidents": rows, "total": len(rows)}
