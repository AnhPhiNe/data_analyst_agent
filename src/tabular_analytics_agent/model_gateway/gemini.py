"""LangChain Gemini implementation of the structured model gateway."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from queue import Empty, Queue
from threading import Thread
from typing import Protocol, cast

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from tabular_analytics_agent.model_gateway.base import BaseModelGateway
from tabular_analytics_agent.model_gateway.errors import (
    ModelConfigurationError,
    ModelProviderError,
)
from tabular_analytics_agent.model_gateway.models import RawModelResponse, StructuredModelRequest

_MIN_GEMINI_TRANSPORT_TIMEOUT_SECONDS = 21.0


class GeminiSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    api_key: SecretStr
    model_id: str = Field(default="gemini-3.5-flash", min_length=1)
    max_api_retries: int = Field(default=2, ge=0, le=5)
    retry_base_delay_seconds: float = Field(default=0.25, ge=0.0, le=10.0)
    model_call_timeout_seconds: float = Field(
        default=30.0,
        ge=_MIN_GEMINI_TRANSPORT_TIMEOUT_SECONDS,
        le=600.0,
    )

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> GeminiSettings:
        values = environment if environment is not None else os.environ
        api_key = values.get("GOOGLE_API_KEY") or values.get("GEMINI_API_KEY")
        if not api_key:
            raise ModelConfigurationError(
                "Set GOOGLE_API_KEY (or GEMINI_API_KEY) before using the Gemini adapter"
            )
        try:
            return cls(
                api_key=SecretStr(api_key),
                model_id=values.get("TABULAR_AGENT_MODEL", "gemini-3.5-flash"),
                model_call_timeout_seconds=float(
                    values.get("TABULAR_AGENT_MODEL_TIMEOUT_SECONDS", "30")
                ),
            )
        except ValueError as exc:
            raise ModelConfigurationError(
                "TABULAR_AGENT_MODEL_TIMEOUT_SECONDS must be between 21 and 600"
            ) from exc


class _StructuredRunnable(Protocol):
    def invoke(self, input: object) -> object: ...


class _ChatModel(Protocol):
    def with_structured_output(
        self,
        schema: object,
        *,
        method: str,
        include_raw: bool,
    ) -> _StructuredRunnable: ...


class _InvocationTimeout(TimeoutError):
    pass


class GeminiModelGateway(BaseModelGateway):
    """Call Gemini via LangChain native JSON schema with bounded transient retries."""

    def __init__(
        self,
        settings: GeminiSettings,
        *,
        client: _ChatModel | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        self._settings = settings
        self._client = client
        self._sleep = sleep
        self._monotonic = monotonic

    def close(self) -> None:
        if self._client is None:
            return
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    def _generate_raw[ResponseT: BaseModel](
        self, request: StructuredModelRequest[ResponseT]
    ) -> RawModelResponse:
        call_timeout = min(request.timeout_seconds, self._settings.model_call_timeout_seconds)
        if self._client is None and call_timeout < _MIN_GEMINI_TRANSPORT_TIMEOUT_SECONDS:
            raise ModelProviderError(
                "Gemini requires at least 21 seconds of remaining model-call budget"
            )
        deadline = self._monotonic() + call_timeout
        retries = 0
        messages = [
            SystemMessage(content=request.system_instruction),
            HumanMessage(content=request.prompt),
        ]
        while True:
            remaining_seconds = deadline - self._monotonic()
            if remaining_seconds <= 0:
                raise _model_timeout_error(call_timeout)
            client = self._client or _build_chat_model(
                self._settings,
                request,
                timeout_seconds=remaining_seconds,
            )
            structured = client.with_structured_output(
                request.response_schema,
                method="json_schema",
                include_raw=True,
            )
            try:
                response = _invoke_with_timeout(structured, messages, remaining_seconds)
                return _normalize_response(response, self._settings.model_id, retries)
            except Exception as exc:
                if isinstance(exc, _InvocationTimeout):
                    raise _model_timeout_error(call_timeout) from exc
                if not _is_retryable(exc) or retries >= self._settings.max_api_retries:
                    raise ModelProviderError(_safe_provider_error(exc)) from exc
                delay = self._settings.retry_base_delay_seconds * (2**retries)
                if delay >= deadline - self._monotonic():
                    raise _model_timeout_error(call_timeout) from exc
                retries += 1
                self._sleep(delay)


def _build_chat_model[ResponseT: BaseModel](
    settings: GeminiSettings,
    request: StructuredModelRequest[ResponseT],
    *,
    timeout_seconds: float,
) -> _ChatModel:
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
    except ImportError as exc:
        raise ModelConfigurationError(
            "Install project dependencies to enable the LangChain Gemini adapter"
        ) from exc
    return cast(
        _ChatModel,
        ChatGoogleGenerativeAI(
            model=settings.model_id,
            api_key=settings.api_key,
            temperature=request.temperature,
            max_tokens=request.max_output_tokens,
            max_retries=0,
            timeout=timeout_seconds,
            vertexai=False,
        ),
    )


def _invoke_with_timeout(
    runnable: _StructuredRunnable,
    input: object,
    timeout_seconds: float,
) -> object:
    outcomes: Queue[tuple[bool, object]] = Queue(maxsize=1)

    def invoke() -> None:
        try:
            outcomes.put_nowait((True, runnable.invoke(input)))
        except Exception as exc:
            outcomes.put_nowait((False, exc))

    Thread(target=invoke, name="gemini-model-call", daemon=True).start()
    try:
        succeeded, value = outcomes.get(timeout=timeout_seconds)
    except Empty as exc:
        raise _InvocationTimeout("Gemini model call timed out") from exc
    if succeeded:
        return value
    if isinstance(value, Exception):
        raise value
    raise ModelProviderError("Gemini request failed (invalid adapter outcome)")


def _model_timeout_error(timeout_seconds: float) -> ModelProviderError:
    return ModelProviderError(
        f"Gemini request exceeded its configured {timeout_seconds:g}-second timeout"
    )


def _normalize_response(
    response: object, configured_model_id: str, retries: int
) -> RawModelResponse:
    response_map = response if isinstance(response, Mapping) else {}
    raw = response_map.get("raw")
    payload = response_map.get("parsed")
    if payload is None:
        payload = getattr(raw, "text", None) or getattr(raw, "content", "")
    usage = getattr(raw, "usage_metadata", None)
    usage_map = usage if isinstance(usage, Mapping) else {}
    metadata = getattr(raw, "response_metadata", None)
    metadata_map = metadata if isinstance(metadata, Mapping) else {}
    return RawModelResponse(
        payload=payload,
        model_id=str(metadata_map.get("model_name") or configured_model_id),
        prompt_tokens=_mapping_int(usage_map, "input_tokens"),
        output_tokens=_mapping_int(usage_map, "output_tokens"),
        total_tokens=max(
            _mapping_int(usage_map, "total_tokens"),
            _mapping_int(usage_map, "input_tokens") + _mapping_int(usage_map, "output_tokens"),
        ),
        provider_retry_count=retries,
    )


def _mapping_int(values: Mapping[object, object], key: str) -> int:
    value = values.get(key, 0)
    if not isinstance(value, (int, float, str)):
        return 0
    try:
        return max(0, int(value))
    except ValueError:
        return 0


def _is_retryable(error: Exception) -> bool:
    if isinstance(error, (ConnectionError, TimeoutError)):
        return True
    status: object = getattr(error, "status_code", None) or getattr(error, "code", None)
    if not isinstance(status, (int, float, str)):
        return False
    try:
        status_code = int(status)
    except (TypeError, ValueError):
        return False
    return status_code == 429 or status_code >= 500


def _safe_provider_error(error: Exception) -> str:
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    suffix = f", status={status}" if isinstance(status, int) else ""
    return f"Gemini request failed ({type(error).__name__}{suffix})"
