"""Errors exposed by the model gateway boundary."""


class ModelGatewayError(RuntimeError):
    """Base error for model configuration, transport, and output failures."""


class ModelConfigurationError(ModelGatewayError):
    """Raised when a model adapter cannot be configured safely."""


class ModelProviderError(ModelGatewayError):
    """Raised when a provider request fails after bounded retries."""


class ModelOutputValidationError(ModelGatewayError):
    """Raised when structured output remains invalid after one repair."""
