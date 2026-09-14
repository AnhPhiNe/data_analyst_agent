"""Structured model boundary and provider adapters."""

from tabular_analytics_agent.model_gateway.base import (
    BaseModelGateway,
    ModelGateway,
    validation_error_detail,
)
from tabular_analytics_agent.model_gateway.errors import (
    ModelConfigurationError,
    ModelGatewayError,
    ModelOutputValidationError,
    ModelProviderError,
)
from tabular_analytics_agent.model_gateway.fake import FakeModelGateway
from tabular_analytics_agent.model_gateway.gemini import (
    DEFAULT_GEMINI_MODEL,
    GeminiModelGateway,
    GeminiSettings,
)
from tabular_analytics_agent.model_gateway.models import (
    ModelCallTrace,
    ModelTask,
    ModelUsage,
    StructuredModelRequest,
    StructuredModelResponse,
)
from tabular_analytics_agent.model_gateway.schemas import (
    ChartIntentDraft,
    GoalInterpretation,
    InsightAssertionDraft,
    InsightDraft,
    InsightDraftBatch,
    PlanDraft,
    PlanStepDraft,
    SemanticAnnotationDraft,
    SQLToolRequestDraft,
    StatisticalToolRequestDraft,
)

__all__ = [
    "DEFAULT_GEMINI_MODEL",
    "BaseModelGateway",
    "ChartIntentDraft",
    "FakeModelGateway",
    "GeminiModelGateway",
    "GeminiSettings",
    "GoalInterpretation",
    "InsightAssertionDraft",
    "InsightDraft",
    "InsightDraftBatch",
    "ModelCallTrace",
    "ModelConfigurationError",
    "ModelGateway",
    "ModelGatewayError",
    "ModelOutputValidationError",
    "ModelProviderError",
    "ModelTask",
    "ModelUsage",
    "PlanDraft",
    "PlanStepDraft",
    "SQLToolRequestDraft",
    "SemanticAnnotationDraft",
    "StatisticalToolRequestDraft",
    "StructuredModelRequest",
    "StructuredModelResponse",
    "validation_error_detail",
]
