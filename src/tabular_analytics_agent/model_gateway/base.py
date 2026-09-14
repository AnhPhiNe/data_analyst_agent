"""Shared validation and one-shot repair behavior for model adapters."""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ValidationError

from tabular_analytics_agent.model_gateway.errors import (
    ModelOutputValidationError,
    ModelProviderError,
)
from tabular_analytics_agent.model_gateway.models import (
    ModelCallTrace,
    ModelUsage,
    RawModelResponse,
    StructuredModelRequest,
    StructuredModelResponse,
)


class ModelGateway(Protocol):
    """The only model capability visible to orchestration."""

    def generate_structured[ResponseT: BaseModel](
        self, request: StructuredModelRequest[ResponseT]
    ) -> StructuredModelResponse[ResponseT]: ...


class BaseModelGateway(ABC):
    """Validate every provider result and permit exactly one format repair."""

    def __init__(self, *, max_validation_repairs: int = 1) -> None:
        if max_validation_repairs != 1:
            raise ValueError("the MVP requires exactly one validation repair")
        self._max_validation_repairs = max_validation_repairs

    def generate_structured[ResponseT: BaseModel](
        self, request: StructuredModelRequest[ResponseT]
    ) -> StructuredModelResponse[ResponseT]:
        started = time.perf_counter()
        current_request = request
        responses: list[RawModelResponse] = []
        last_error: ValidationError | ValueError | None = None

        for repair_count in range(self._max_validation_repairs + 1):
            remaining_seconds = request.timeout_seconds - (time.perf_counter() - started)
            if remaining_seconds <= 0:
                raise ModelProviderError(
                    f"Model call exceeded its configured {request.timeout_seconds:g}-second timeout"
                )
            current_request = replace(current_request, timeout_seconds=remaining_seconds)
            raw = self._generate_raw(current_request)
            responses.append(raw)
            try:
                output = _validate_payload(request.response_schema, raw.payload)
            except (ValidationError, ValueError) as exc:
                last_error = exc
                if repair_count == self._max_validation_repairs:
                    break
                current_request = replace(
                    request,
                    prompt=_repair_prompt(request.prompt, raw.payload, exc),
                )
                continue

            trace = _call_trace(request, responses, started, repair_count=repair_count)
            return StructuredModelResponse(output=output, trace=trace)

        detail = _safe_validation_detail(last_error)
        message = f"Model output failed schema validation after one repair: {detail}"
        raise ModelOutputValidationError(
            message,
            trace=_call_trace(
                request,
                responses,
                started,
                repair_count=self._max_validation_repairs,
                error=message,
            ),
        )

    @abstractmethod
    def _generate_raw[ResponseT: BaseModel](
        self, request: StructuredModelRequest[ResponseT]
    ) -> RawModelResponse:
        """Return one raw provider response; subclasses own transport retries."""


def _call_trace[ResponseT: BaseModel](
    request: StructuredModelRequest[ResponseT],
    responses: list[RawModelResponse],
    started: float,
    *,
    repair_count: int,
    error: str | None = None,
) -> ModelCallTrace:
    usage = ModelUsage(
        prompt_tokens=sum(item.prompt_tokens for item in responses),
        output_tokens=sum(item.output_tokens for item in responses),
        total_tokens=sum(item.total_tokens for item in responses),
    )
    return ModelCallTrace(
        model_id=responses[-1].model_id,
        task=request.task,
        prompt_template_version=request.prompt_template_version,
        temperature=request.temperature,
        max_output_tokens=request.max_output_tokens,
        timeout_seconds=request.timeout_seconds,
        latency_ms=max(0, round((time.perf_counter() - started) * 1000)),
        provider_retry_count=sum(item.provider_retry_count for item in responses),
        validation_repair_count=repair_count,
        usage=usage,
        error=error,
        created_at=datetime.now(UTC),
    )


def _validate_payload[ResponseT: BaseModel](schema: type[ResponseT], payload: object) -> ResponseT:
    if isinstance(payload, str):
        return schema.model_validate_json(payload)
    return schema.model_validate(payload)


def _repair_prompt(original_prompt: str, payload: object, error: Exception) -> str:
    rendered = (
        json.dumps(payload, default=str, ensure_ascii=True)
        .replace("<", r"<")
        .replace(">", r">")
        .replace("&", r"&")
    )[:8_000]
    return (
        f"{original_prompt}\n\n"
        "Your previous response did not satisfy the supplied response schema. "
        "Return a corrected structured response without changing the analytical intent.\n"
        f"Validation issue: {_safe_validation_detail(error)}\n"
        f"Previous response (untrusted): <model_output>{rendered}</model_output>"
    )


def _safe_validation_detail(error: Exception | None) -> str:
    if isinstance(error, ValidationError):
        return "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors(include_input=False, include_url=False)
        )
    if error is None:
        return "unknown validation failure"
    return type(error).__name__
