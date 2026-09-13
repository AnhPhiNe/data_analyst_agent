"""Public interface for validated Plotly-compatible chart rendering."""

from tabular_analytics_agent.visualization.artifacts import ArtifactStore
from tabular_analytics_agent.visualization.errors import (
    ArtifactNotFoundError,
    ArtifactStoreError,
    ArtifactTransitionError,
    ChartValidationError,
)
from tabular_analytics_agent.visualization.models import ChartRenderResult
from tabular_analytics_agent.visualization.renderer import (
    make_query_result_reference,
    render_chart,
    validate_chart_intent,
)

__all__ = [
    "ArtifactNotFoundError",
    "ArtifactStore",
    "ArtifactStoreError",
    "ArtifactTransitionError",
    "ChartRenderResult",
    "ChartValidationError",
    "make_query_result_reference",
    "render_chart",
    "validate_chart_intent",
]
