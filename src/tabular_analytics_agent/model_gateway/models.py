"""Provider-neutral contracts for structured model generation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(gt=0)]


class ModelTask(StrEnum):
    SEMANTIC_INTERPRETATION = "semantic_interpretation"
    PLAN = "plan"
    TOOL_REQUEST = "tool_request"
    CHART_INTENT = "chart_intent"
    INSIGHT_DRAFT = "insight_draft"


class GatewayModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ModelUsage(GatewayModel):
    prompt_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0
    total_tokens: NonNegativeInt = 0


class ModelCallTrace(GatewayModel):
    model_id: str = Field(min_length=1)
    task: ModelTask
    prompt_template_version: str = Field(min_length=1)
    temperature: Annotated[float, Field(ge=0.0, le=2.0)]
    max_output_tokens: PositiveInt
    timeout_seconds: Annotated[float, Field(gt=0.0)]
    latency_ms: NonNegativeInt
    provider_retry_count: NonNegativeInt = 0
    validation_repair_count: NonNegativeInt = 0
    usage: ModelUsage = Field(default_factory=ModelUsage)
    estimated_cost_usd: Annotated[float, Field(ge=0.0)] | None = None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class StructuredModelRequest[ResponseT: BaseModel]:
    """One validated generation request with a concrete response schema."""

    task: ModelTask
    prompt: str
    response_schema: type[ResponseT]
    system_instruction: str
    prompt_template_version: str = "1"
    temperature: float = 1.0
    max_output_tokens: int = 4096
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.prompt.strip():
            raise ValueError("prompt cannot be empty")
        if not self.system_instruction.strip():
            raise ValueError("system_instruction cannot be empty")
        if not self.prompt_template_version.strip():
            raise ValueError("prompt_template_version cannot be empty")
        if not 0.0 <= self.temperature <= 2.0:
            raise ValueError("temperature must be between 0 and 2")
        if self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


@dataclass(frozen=True, slots=True)
class RawModelResponse:
    payload: object
    model_id: str
    prompt_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    provider_retry_count: int = 0


class StructuredModelResponse[ResponseT: BaseModel](GatewayModel):
    output: ResponseT
    trace: ModelCallTrace
