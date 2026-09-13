"""Public interface for deterministic statistical analysis."""

from tabular_analytics_agent.statistics.engine import analyze
from tabular_analytics_agent.statistics.errors import StatisticalAnalysisError
from tabular_analytics_agent.statistics.models import (
    AlternativeHypothesis,
    AssumptionCheck,
    AssumptionStatus,
    ConfidenceInterval,
    StatisticalEstimate,
    StatisticalOperation,
    StatisticalRequest,
    StatisticalResult,
)
from tabular_analytics_agent.statistics.service import StatisticalTool, StatisticalToolOutput

__all__ = [
    "AlternativeHypothesis",
    "AssumptionCheck",
    "AssumptionStatus",
    "ConfidenceInterval",
    "StatisticalAnalysisError",
    "StatisticalEstimate",
    "StatisticalOperation",
    "StatisticalRequest",
    "StatisticalResult",
    "StatisticalTool",
    "StatisticalToolOutput",
    "analyze",
]
