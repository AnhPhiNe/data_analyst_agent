"""Public interface for validated Plotly-compatible chart rendering."""

from tabular_analytics_agent.visualization.errors import ChartValidationError
from tabular_analytics_agent.visualization.models import ChartRenderResult
from tabular_analytics_agent.visualization.renderer import render_chart, validate_chart_intent

__all__ = [
    "ChartRenderResult",
    "ChartValidationError",
    "render_chart",
    "validate_chart_intent",
]
