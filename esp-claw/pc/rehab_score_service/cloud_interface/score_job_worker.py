"""Single-worker scoring queue with atomic result publication."""

import json
import shutil
import threading
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Literal, Optional

from cloud_interface.compact_score_result import build_compact_score_result
from cloud_interface.dedicated_job_score_adapter import attach_compact_actions_from_dedicated_result
from cloud_interface.score_job_store import JobError, ScoreJobStore, sha256_file, utc_now


QUEUE_MAX_JOBS = 8
QueueSource = Literal["live", "recovery"]


class ScoreJobWorker:
    def __init__(self, store: ScoreJobStore, scorer: Callable[..., Dict[str, Any]]):
        self.store = store
        self.scorer = scorer
        self._live_queue: Deque[str] = deque()
        self._recovery_queue: Deque[str] = deque()
        self._queued: Dict[str, QueueSource] = {}
        self._active_job_id: Optional[str] = None
        self._condition = threading.Condition()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="rehab-score-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._condition:
            self._condition.notify_all()
        if self._thread:
            self._thread.join(timeout=10)

    def enqueue(self, job_id: str, source: QueueSource = "live") -> bool:
        if source not in {"live", "recovery"}:
            raise ValueError("score job queue source must be live or recovery")
        with self._condition:
            if job_id == self._active_job_id:
                return True
            queued_source = self._queued.get(job_id)
            if queued_source == "live" or queued_source == source:
                return True
            if queued_source == "recovery" and source == "live":
                try:
                    self._recovery_queue.remove(job_id)
                except ValueError:
                    pass
                self._live_queue.append(job_id)
                self._queued[job_id] = "live"
                self._condition.notify()
                return True
            if source == "live":
                if len(self._live_queue) >= QUEUE_MAX_JOBS:
                    return False
                self._live_queue.append(job_id)
            else:
                self._recovery_queue.append(job_id)
            self._queued[job_id] = source
            self._condition.notify()
            return True

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._stop.is_set() and not self._live_queue and not self._recovery_queue:
                    self._condition.wait()
                if self._stop.is_set():
                    break
                queue_ref = self._live_queue if self._live_queue else self._recovery_queue
                job_id = queue_ref.popleft()
                self._queued.pop(job_id, None)
                self._active_job_id = job_id
            try:
                self.process_one(job_id)
            finally:
                with self._condition:
                    if self._active_job_id == job_id:
                        self._active_job_id = None

    def snapshot(self) -> Dict[str, Any]:
        with self._condition:
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "active_job_id": self._active_job_id,
                "live_queue_depth": len(self._live_queue),
                "recovery_backlog": len(self._recovery_queue),
                "queued_total": len(self._live_queue) + len(self._recovery_queue),
            }

    def process_one(self, job_id: str) -> None:
        try:
            self._process_one(job_id)
        except JobError:
            # A manually deleted or damaged session is isolated from the worker;
            # subsequent sessions must continue to run.
            return

    def _process_one(self, job_id: str) -> None:
        status = self.store.read_status(job_id)
        if status["state"] != "accepted":
            return
        input_dir = self.store.validated_session_dir(job_id)
        task_path = input_dir / "task.json"
        csv_path = input_dir / status["csv_file"]
        if sha256_file(task_path) != status["task_sha256"] or sha256_file(csv_path) != status["csv_sha256"]:
            self.store.update_state(job_id, "failed", "input_integrity_mismatch", "input bytes changed before scoring")
            return
        scoring_stage = self.store.path_resolver.scoring_staging_session_path(job_id)
        generated_dir = scoring_stage / "generated"
        publish_dir = scoring_stage / "publish"
        scoring_dir = self.store.path_resolver.session_scoring_path(job_id)
        if scoring_dir.exists():
            if (scoring_dir / "full_result.json").is_file():
                self.store.update_state(job_id, "succeeded", completed_at=utc_now())
            else:
                self.store.update_state(job_id, "failed", "result_already_exists", "formal result is incomplete")
            return
        shutil.rmtree(scoring_stage, ignore_errors=True)
        scoring_stage.mkdir(parents=True, exist_ok=False)
        self.store.update_state(job_id, "scoring")
        try:
            full = self.scorer(
                task_directory=str(input_dir),
                output_dir=str(generated_dir),
                patient_id="S001",
                session_id=job_id,
            )
            result_json = generated_dir / "score_result.json"
            if not result_json.is_file():
                raise ValueError("scorer did not create score_result.json")
            persisted = json.loads(result_json.read_text(encoding="utf-8"))
            compact = build_compact_score_result(persisted, job_id, completed_at=utc_now())
            if compact["session_id"] != job_id:
                raise ValueError("scorer result session_id does not match the score job")
            if compact["task_name"] != status["task_name"]:
                raise ValueError("scorer result task_name does not match task.json")
            if sha256_file(task_path) != status["task_sha256"] or sha256_file(csv_path) != status["csv_sha256"]:
                raise ValueError("input bytes changed during scoring")
            if not isinstance(full, dict):
                raise ValueError("scorer did not return a result object")
            publish_dir.mkdir()
            result_json.replace(publish_dir / "full_result.json")
            intermediate_dir = publish_dir / "intermediate"
            intermediate_dir.mkdir()
            dedicated_dir = generated_dir / "dedicated_scoring"
            if dedicated_dir.is_dir():
                dedicated_dir.replace(intermediate_dir / "dedicated_scoring")
            for generated_artifact in generated_dir.iterdir():
                generated_artifact.replace(intermediate_dir / generated_artifact.name)
            generated_dir.rmdir()
            scoring_dir.parent.mkdir(parents=True, exist_ok=True)
            publish_dir.replace(scoring_dir)
            scoring_stage.rmdir()
            self.store.update_state(job_id, "succeeded", completed_at=utc_now())
        except Exception as exc:
            shutil.rmtree(scoring_stage, ignore_errors=True)
            self.store.update_state(job_id, "failed", "scoring_failed", str(exc))

    def compact_result(self, job_id: str) -> Dict[str, Any]:
        status = self.store.read_status(job_id)
        if status["state"] == "failed":
            raise JobError(409, status.get("error_code") or "scoring_failed", status.get("message") or "score job failed")
        if status["state"] != "succeeded":
            raise JobError(409, "result_not_ready", "score result is not ready")
        result_path = (
            self.store.path_resolver.session_scoring_path(job_id)
            / "full_result.json"
        )
        try:
            full = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise JobError(500, "result_unreadable", "full score result is unreadable") from exc
        full = attach_compact_actions_from_dedicated_result(
            full,
            result_path.parent / "intermediate",
        )
        try:
            return build_compact_score_result(full, job_id, status.get("completed_at"))
        except ValueError as exc:
            raise JobError(500, "compact_result_invalid", str(exc)) from exc
