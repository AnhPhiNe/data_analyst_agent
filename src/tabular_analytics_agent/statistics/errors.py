"""Safe failures exposed by statistical Tool Actions."""


class StatisticalAnalysisError(ValueError):
    """The requested analysis is invalid or unsupported by the available data."""


class StatisticalTimeoutError(StatisticalAnalysisError):
    """A full-data statistical execution exceeded its deadline and was terminated."""


class InsufficientSampleError(StatisticalAnalysisError):
    """The data has too few usable values for a reliable statistical result."""


class StatisticalParameterError(StatisticalAnalysisError):
    """A request parameter does not match the data, so a corrected request can succeed."""
