from dataclasses import dataclass
from typing import Optional


@dataclass
class VisionError(Exception):
    code: str
    message: str
    http_status: int = 400
    retryable: bool = False
    capture_id: Optional[str] = None
