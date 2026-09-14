"""Safe failures exposed by statistical Tool Actions."""


class StatisticalAnalysisError(ValueError):
    """The requested analysis is invalid or unsupported by the available data."""


class InsufficientSampleError(StatisticalAnalysisError):
    """The data has too few usable values for a reliable statistical result."""
