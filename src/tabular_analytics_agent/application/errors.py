"""Errors raised by the local interactive application workflow."""


class ApplicationError(RuntimeError):
    """Raised when local session state cannot be staged or restored safely."""
