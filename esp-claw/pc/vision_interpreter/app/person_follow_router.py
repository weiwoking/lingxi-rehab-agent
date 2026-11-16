import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from .person_follow_response import PersonFollowError, build_error
from .person_follow_schemas import PersonFollowMetadata
from .person_follow_service import PersonFollowService


logger = logging.getLogger("vision-interpreter.person-follow")
MAX_METADATA_BYTES = 16_384


def _error_response(error: PersonFollowError) -> JSONResponse:
    return JSONResponse(status_code=error.http_status, content=build_error(error))


async def _metadata_text(part: Any) -> str:
    if isinstance(part, UploadFile):
        raw = await part.read(MAX_METADATA_BYTES + 1)
        if len(raw) > MAX_METADATA_BYTES:
            raise PersonFollowError("BAD_METADATA_JSON", "Metadata part is too large.", 400)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PersonFollowError("BAD_METADATA_JSON", "Metadata must be UTF-8 JSON.", 400) from exc
    if not isinstance(part, str):
        raise PersonFollowError("BAD_MULTIPART", "metadata must be a multipart field.", 400)
    if len(part.encode("utf-8")) > MAX_METADATA_BYTES:
        raise PersonFollowError("BAD_METADATA_JSON", "Metadata part is too large.", 400)
    return part


def create_person_follow_router(service: PersonFollowService) -> APIRouter:
    router = APIRouter()

    @router.post("/api/v1/person-follow/detect")
    async def detect(request: Request):
        try:
            content_type = request.headers.get("content-type", "")
            if not content_type.lower().startswith("multipart/form-data"):
                raise PersonFollowError("BAD_MULTIPART", "Content-Type must be multipart/form-data.", 400)
            try:
                form = await request.form()
            except Exception as exc:
                raise PersonFollowError("BAD_MULTIPART", f"Malformed multipart body: {exc}", 400) from exc

            images = form.getlist("image")
            metadata_parts = form.getlist("metadata")
            if len(images) != 1 or len(metadata_parts) != 1:
                raise PersonFollowError("BAD_MULTIPART", "Exactly one image and one metadata part are required.", 400)
            image = images[0]
            if not isinstance(image, UploadFile):
                raise PersonFollowError("BAD_MULTIPART", "image must be a file part.", 400)
            if image.content_type is None or image.content_type.lower() != "image/jpeg":
                raise PersonFollowError("UNSUPPORTED_MEDIA_TYPE", "image part must use Content-Type image/jpeg.", 415)

            metadata_text = await _metadata_text(metadata_parts[0])
            try:
                metadata_json = json.loads(metadata_text)
            except (json.JSONDecodeError, TypeError) as exc:
                raise PersonFollowError("BAD_METADATA_JSON", f"Invalid metadata JSON: {exc}", 400) from exc
            if isinstance(metadata_json, dict) and "target_class" not in metadata_json:
                metadata_json["target_class"] = str(service.config["target_class"])
            try:
                metadata = PersonFollowMetadata.model_validate(metadata_json)
            except ValidationError as exc:
                raise PersonFollowError("REQUEST_SCHEMA_ERROR", f"Invalid request metadata: {exc}", 422) from exc

            max_bytes = int(service.config["max_image_bytes"])
            jpeg = await image.read(max_bytes + 1)
            if len(jpeg) > max_bytes:
                raise PersonFollowError("IMAGE_TOO_LARGE", f"JPEG exceeds {max_bytes} bytes.", 413)
            if not jpeg:
                raise PersonFollowError("BAD_JPEG", "JPEG part is empty.", 400)
            return JSONResponse(status_code=200, content=service.detect(jpeg, metadata))
        except PersonFollowError as error:
            return _error_response(error)
        except Exception as exc:
            logger.exception("Unexpected person-follow error")
            return _error_response(PersonFollowError("INTERNAL_ERROR", "Unexpected server error.", 500))

    return router
