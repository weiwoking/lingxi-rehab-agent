from __future__ import annotations


MAX_TEXT_CODEPOINTS = 300


class TextValidationError(ValueError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


def validate_text(text: str) -> int:
    """Validate without modifying the text and return its Unicode codepoint count."""
    codepoints = len(text)
    if not text or not text.strip():
        raise TextValidationError("empty_text", "text must not be empty")
    if codepoints > MAX_TEXT_CODEPOINTS:
        raise TextValidationError(
            "text_too_long",
            f"text exceeds {MAX_TEXT_CODEPOINTS} Unicode code points",
        )
    return codepoints
