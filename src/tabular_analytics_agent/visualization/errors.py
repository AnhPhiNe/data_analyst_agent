"""Errors raised by deterministic chart validation and rendering."""


class ChartValidationError(ValueError):
    """Raised when a Chart Intent cannot safely represent its source result."""


class ArtifactStoreError(RuntimeError):
    """Raised when durable artifact metadata or render specs cannot be stored."""


class ArtifactNotFoundError(ArtifactStoreError):
    """Raised when an artifact does not exist in the requested session."""


class ArtifactTransitionError(ArtifactStoreError):
    """Raised when an artifact lifecycle transition is not allowed."""
