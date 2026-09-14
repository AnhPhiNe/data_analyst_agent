"""Errors exposed by the model gateway boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tabular_analytics_agent.model_gateway.models import ModelCallTrace


class ModelGatewayError(RuntimeError):
    """Base error for model configuration, transport, and output failures."""


class ModelConfigurationError(ModelGatewayError):
    """Raised when a model adapter cannot be configured safely."""


class ModelProviderError(ModelGatewayError):
    """Raised when a provider request fails after bounded retries."""


class ModelOutputValidationError(ModelGatewayError):
    """Raised when structured output remains invalid after one repair."""

    def __init__(self, message: str, *, trace: ModelCallTrace | None = None) -> None:
        super().__init__(message)
        self.trace = trace
