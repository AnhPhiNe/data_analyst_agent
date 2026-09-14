"""Resource limits read from environment variables."""

from __future__ import annotations

import os
from collections.abc import Mapping

from pydantic import BaseModel, ValidationError

from tabular_analytics_agent.application.errors import ApplicationError
from tabular_analytics_agent.data import DataCoreLimits
from tabular_analytics_agent.domain import ExecutionBudget

_PREFIX = "TABULAR_AGENT_"
# The per-call model timeout already has a documented variable shared with the Gemini adapter.
_VARIABLE_ALIASES = {"model_call_timeout_seconds": "TABULAR_AGENT_MODEL_TIMEOUT_SECONDS"}


def limit_variable(field_name: str) -> str:
    """Return the environment variable that overrides one limit field."""
    return _VARIABLE_ALIASES.get(field_name, f"{_PREFIX}{field_name.upper()}")


def limits_from_environment(
    environment: Mapping[str, str] | None = None,
) -> tuple[DataCoreLimits, ExecutionBudget]:
    """Build data and run limits; unset variables keep the model defaults."""
    values = environment if environment is not None else os.environ
    return (
        _limits_from_values(DataCoreLimits, values),
        _limits_from_values(ExecutionBudget, values),
    )


def _limits_from_values[LimitsT: BaseModel](
    model: type[LimitsT], values: Mapping[str, str]
) -> LimitsT:
    overrides = {
        field_name: raw.strip()
        for field_name in model.model_fields
        if (raw := values.get(limit_variable(field_name), "")).strip()
    }
    try:
        return model.model_validate(overrides)
    except ValidationError as exc:
        problems = "; ".join(
            f"{limit_variable(str(error['loc'][0]))}: {error['msg']}"
            for error in exc.errors()
            if error["loc"]
        )
        raise ApplicationError(f"Invalid resource limit in the environment: {problems}") from exc


__all__ = ["limit_variable", "limits_from_environment"]
