from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from fastapi import HTTPException

from .config import PATIENTS_FILE


def _is_safe_id(value: str) -> bool:
    return value.replace("-", "").replace("_", "").isalnum()


@lru_cache(maxsize=1)
def list_patients() -> list[dict[str, Any]]:
    return json.loads(PATIENTS_FILE.read_text(encoding="utf-8"))


def get_patient(patient_id: str) -> dict[str, Any]:
    if not _is_safe_id(patient_id):
        raise HTTPException(status_code=400, detail="Invalid patient_id.")
    for patient in list_patients():
        if patient["patient_id"] == patient_id:
            return patient
    raise HTTPException(status_code=404, detail="Patient not found.")


def validate_patient(patient_id: str) -> None:
    get_patient(patient_id)

