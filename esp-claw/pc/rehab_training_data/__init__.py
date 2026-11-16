"""Unified PC training-data authority and offline migration foundations.

Path resolution has no import-time filesystem side effects.  R3-2 consumers use
the unified authority; explicit legacy resolver methods are read-only rollback
references and never participate in active writes.
"""

from .paths import (
    DEFAULT_TRAINING_ROOT,
    get_doctor_path,
    get_input_path,
    get_score_path,
    get_session_root,
    get_training_root,
)
from .resolver import TrainingDataResolver, get_doctor_root, get_score_root

__all__ = [
    "DEFAULT_TRAINING_ROOT",
    "get_training_root",
    "get_session_root",
    "get_input_path",
    "get_score_path",
    "get_doctor_path",
    "TrainingDataResolver",
    "get_score_root",
    "get_doctor_root",
]
