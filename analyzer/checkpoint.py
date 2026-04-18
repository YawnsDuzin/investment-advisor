"""분석 파이프라인 체크포인트 시스템

각 Stage 완료 시 결과를 JSON 파일로 저장하여,
파이프라인 중간 실패 시 마지막 성공 Stage부터 이어서 작업할 수 있다.

뉴스 지문(fingerprint)을 함께 저장하여, 뉴스가 달라졌으면
체크포인트를 무시하고 새로 시작한다.

사용 예:
    cp = CheckpointManager("2026-04-18", news_fingerprint="abc123")
    if cp.has("stage1a"):
        result = cp.load("stage1a")
    else:
        result = run_stage1a(...)
        cp.save("stage1a", result)
    cp.clear()  # DB 저장 성공 후 정리
"""
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from shared.logger import get_logger

# 체크포인트 Stage 순서
STAGES = ("stage1a", "stage1b", "momentum", "stage2", "final")


def compute_news_fingerprint(news_articles: list[dict]) -> str:
    """뉴스 기사 목록에서 지문(hash) 생성

    제목 목록을 정렬하여 SHA-256 해시 계산.
    같은 뉴스 세트면 같은 fingerprint → 체크포인트 재사용 가능.
    """
    titles = sorted(a.get("title", "") for a in news_articles)
    combined = "\n".join(titles)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]


class CheckpointManager:
    """분석 파이프라인 체크포인트 관리자

    Args:
        analysis_date: 분석 날짜 (예: "2026-04-18")
        news_fingerprint: 뉴스 세트 지문 (다르면 기존 체크포인트 무효화)
        base_dir: 체크포인트 저장 루트 디렉토리
        force_fresh: True면 기존 체크포인트 무시하고 새로 시작
    """

    def __init__(
        self,
        analysis_date: str,
        news_fingerprint: str = "",
        base_dir: str = "_checkpoints",
        force_fresh: bool = False,
    ):
        self.log = get_logger("체크포인트")
        self.date = analysis_date
        self.fingerprint = news_fingerprint
        self.dir = Path(base_dir) / analysis_date
        self._meta_path = self.dir / "_meta.json"

        if force_fresh and self.dir.exists():
            self.log.info("--fresh 모드: 기존 체크포인트 삭제")
            self.clear()
            return

        # 기존 체크포인트가 있으면 fingerprint 비교
        if self.dir.exists() and self._meta_path.exists():
            try:
                meta = json.loads(self._meta_path.read_text(encoding="utf-8"))
                old_fp = meta.get("news_fingerprint", "")
                if old_fp and old_fp != news_fingerprint:
                    self.log.info(
                        f"뉴스 세트 변경 감지 (기존: {old_fp[:8]}... → 현재: {news_fingerprint[:8]}...) "
                        "→ 기존 체크포인트 삭제"
                    )
                    self.clear()
                else:
                    saved = [s for s in STAGES if (self.dir / f"{s}.json").exists()]
                    if saved:
                        self.log.info(f"체크포인트 발견: {', '.join(saved)}")
            except Exception:
                self.clear()

    def save(self, stage: str, data: dict) -> None:
        """Stage 결과를 체크포인트 파일로 저장"""
        self.dir.mkdir(parents=True, exist_ok=True)

        # 메타 파일 저장/갱신
        meta = {
            "analysis_date": self.date,
            "news_fingerprint": self.fingerprint,
            "last_stage": stage,
            "last_saved_at": datetime.now().isoformat(),
        }
        self._meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # Stage 데이터를 임시 파일에 쓰고 원자적으로 교체
        target = self.dir / f"{stage}.json"
        tmp = self.dir / f"{stage}.json.tmp"
        try:
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8"
            )
            # Windows에서는 rename 전 기존 파일 삭제 필요
            if target.exists():
                target.unlink()
            tmp.rename(target)
            self.log.info(f"[{stage}] 체크포인트 저장 완료")
        except Exception as e:
            self.log.warning(f"[{stage}] 체크포인트 저장 실패: {e}")
            if tmp.exists():
                tmp.unlink()

    def load(self, stage: str) -> dict | None:
        """Stage 체크포인트 로드. 없으면 None."""
        target = self.dir / f"{stage}.json"
        if not target.exists():
            return None
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            self.log.info(f"[{stage}] 체크포인트에서 복원")
            return data
        except Exception as e:
            self.log.warning(f"[{stage}] 체크포인트 로드 실패: {e}")
            return None

    def has(self, stage: str) -> bool:
        """Stage 체크포인트 존재 여부"""
        return (self.dir / f"{stage}.json").exists()

    def clear(self) -> None:
        """모든 체크포인트 삭제 (DB 저장 성공 후 호출)"""
        if self.dir.exists():
            try:
                shutil.rmtree(self.dir)
                self.log.info("체크포인트 정리 완료")
            except Exception as e:
                self.log.warning(f"체크포인트 정리 실패: {e}")

    def last_completed_stage(self) -> str | None:
        """마지막으로 완료된 Stage 반환"""
        for stage in reversed(STAGES):
            if self.has(stage):
                return stage
        return None
