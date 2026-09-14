"""Public interface for deterministic statistical analysis."""

from tabular_analytics_agent.statistics.engine import analyze, display_group_label
from tabular_analytics_agent.statistics.errors import (
    InsufficientSampleError,
    StatisticalAnalysisError,
)
from tabular_analytics_agent.statistics.models import (
    AlternativeHypothesis,
    AssumptionCheck,
    AssumptionStatus,
    ConfidenceInterval,
    StatisticalEstimate,
    StatisticalOperation,
    StatisticalRequest,
    StatisticalResult,
    parameter_guide,
)
from tabular_analytics_agent.statistics.service import StatisticalTool, StatisticalToolOutput

__all__ = [
    "AlternativeHypothesis",
    "AssumptionCheck",
    "AssumptionStatus",
    "ConfidenceInterval",
    "InsufficientSampleError",
    "StatisticalAnalysisError",
    "StatisticalEstimate",
    "StatisticalOperation",
    "StatisticalRequest",
    "StatisticalResult",
    "StatisticalTool",
    "StatisticalToolOutput",
    "analyze",
    "display_group_label",
    "parameter_guide",
]
