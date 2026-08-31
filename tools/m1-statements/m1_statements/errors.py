from __future__ import annotations


class SafeToolError(Exception):
    """An operator-safe error whose text never contains extracted private data."""

    def __init__(self, code: str, message: str, *, exit_code: int = 2) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.exit_code = exit_code


UNEXPECTED_ERROR_MESSAGE = (
    "The operation failed before a safe import bundle could be produced. "
    "No extracted statement values were written to the console."
)
