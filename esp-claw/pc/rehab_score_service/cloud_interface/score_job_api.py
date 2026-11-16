"""FastAPI routes for persistent score jobs."""

from typing import AsyncIterable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cloud_interface.score_job_store import JobError, ScoreJobStore, TASK_MAX_BYTES
from cloud_interface.score_job_worker import ScoreJobWorker


def _error(exc: JobError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error_code": exc.error_code, "message": exc.message},
    )


def _content_length(request: Request, maximum: int = 0) -> int:
    raw = request.headers.get("content-length")
    if raw is None:
        raise JobError(422, "content_length_required", "Content-Length is required")
    try:
        value = int(raw)
    except ValueError as exc:
        raise JobError(422, "invalid_content_length", "Content-Length must be a decimal integer") from exc
    if value < 0:
        raise JobError(422, "invalid_content_length", "Content-Length must not be negative")
    if maximum and value > maximum:
        raise JobError(413, "task_too_large", "task.json exceeds 16 KiB")
    return value


async def _read_small_stream(request: Request, expected: int) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > TASK_MAX_BYTES:
            raise JobError(413, "task_too_large", "task.json exceeds 16 KiB")
    if len(body) != expected:
        raise JobError(422, "content_length_mismatch", "task body does not match Content-Length")
    return bytes(body)


def create_job_router(store: ScoreJobStore, worker: ScoreJobWorker) -> APIRouter:
    router = APIRouter(prefix="/api/v1/score/jobs")

    @router.post("")
    async def create_job(request: Request):
        try:
            length = _content_length(request, TASK_MAX_BYTES)
            if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise JobError(422, "invalid_content_type", "job creation requires application/json")
            session_id = request.headers.get("x-session-id", "")
            raw_size = request.headers.get("x-csv-size", "")
            csv_sha256 = request.headers.get("x-csv-sha256", "")
            try:
                csv_size = int(raw_size)
            except ValueError as exc:
                raise JobError(422, "invalid_csv_size", "X-CSV-Size must be a decimal integer") from exc
            task_bytes = await _read_small_stream(request, length)
            status, created = store.create_or_reuse(session_id, csv_size, csv_sha256, task_bytes)
            upload_required = (store._job_dir(session_id) or store.sessions_root).parent == store.staging_root
            if not upload_required and status.get("state") == "accepted":
                if not worker.enqueue(session_id):
                    return _error(JobError(503, "score_queue_full", "score queue is temporarily full"))
            return JSONResponse(
                status_code=201 if created else 200,
                content=store.job_response(status, upload_required),
            )
        except JobError as exc:
            return _error(exc)

    @router.put("/{job_id}/csv")
    async def upload_csv(job_id: str, request: Request):
        try:
            length = _content_length(request)
            content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type != "text/csv":
                raise JobError(422, "invalid_content_type", "CSV upload requires text/csv")
            status, promoted = await store.receive_csv(job_id, length, request.stream())
            if status["state"] == "accepted" and not worker.enqueue(job_id):
                return _error(JobError(503, "score_queue_full", "score queue is temporarily full"))
            return JSONResponse(
                status_code=202 if promoted else 200,
                content=store.public_status(status),
            )
        except JobError as exc:
            return _error(exc)

    @router.get("/{job_id}")
    async def get_job(job_id: str):
        try:
            return store.public_status(store.read_status(job_id))
        except JobError as exc:
            return _error(exc)

    @router.get("/{job_id}/result")
    async def get_result(job_id: str):
        try:
            return worker.compact_result(job_id)
        except JobError as exc:
            return _error(exc)

    @router.post("/{job_id}/retry")
    async def retry_job(job_id: str):
        try:
            status = store.retry(job_id)
            if not worker.enqueue(job_id):
                store.update_state(job_id, "failed", "score_queue_full", "score queue is temporarily full")
                raise JobError(503, "score_queue_full", "score queue is temporarily full")
            return JSONResponse(status_code=202, content=store.public_status(status))
        except JobError as exc:
            return _error(exc)

    return router
