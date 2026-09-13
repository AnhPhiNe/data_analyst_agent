"""Errors raised by deterministic chart validation and rendering."""


class ChartValidationError(ValueError):
    """Raised when a Chart Intent cannot safely represent its source result."""
