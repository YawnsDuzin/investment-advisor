"""관리자 API — 분석 실행 + SSE 실시간 로그 스트리밍"""
import os
import sys
import subprocess
import threading
import queue
import time
from typing import Optional
from fastapi import APIRouter, Request, Query, Depends
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from shared.config import DatabaseConfig, AnalyzerConfig, AuthConfig
from shared.db import get_untranslated_news, update_news_title_ko, update_news_translation, get_connection
from api.auth.dependencies import require_role, get_current_user, _get_auth_cfg
from api.auth.models import UserInDB

router = APIRouter(prefix="/admin", tags=["관리자"])

templates = Jinja2Templates(directory="api/templates")

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
    cfg = DatabaseConfig()
    articles = get_untranslated_news(cfg)
    return {"untranslated_count": len(articles), "translating": _translating}


@router.post("/translate-news")
def translate_existing_news(_admin: Optional[UserInDB] = Depends(require_role("admin"))):
    """기존 미번역 뉴스를 한글 번역 (SSE 스트리밍)"""
    global _translating

    if _translating:
        def already():
            yield "data: [경고] 번역이 이미 실행 중입니다.\n\n"
            yield "event: done\ndata: already\n\n"
        return StreamingResponse(already(), media_type="text/event-stream")

    cfg = DatabaseConfig()
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
def reset_all_data(_admin: Optional[UserInDB] = Depends(require_role("admin"))):
    """분석 데이터 전체 삭제 (CASCADE로 하위 테이블 포함)"""
    cfg = DatabaseConfig()
    conn = get_connection(cfg)
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
    finally:
        conn.close()
