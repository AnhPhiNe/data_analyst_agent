"""LangChain Gemini implementation of the structured model gateway."""

from __future__ import annotations

import math
import os
import re
import time
from collections import deque
from collections.abc import Callable, Mapping
from queue import Empty, Queue
from threading import Lock, Thread
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
_RATE_WINDOW_SECONDS = 60.0
_SINGLE_KEY_NAMES = ("GOOGLE_API_KEY", "GEMINI_API_KEY")
_NUMBERED_KEY_NAME = re.compile(r"(?:GOOGLE|GEMINI)_API_KEY_(\d+)")
_KEY_LIST_NAMES = ("GEMINI_API_KEYS", "GOOGLE_API_KEYS")
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"


class GeminiSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    api_key: SecretStr
    additional_api_keys: tuple[SecretStr, ...] = ()
    model_id: str = Field(default=DEFAULT_GEMINI_MODEL, min_length=1)
    max_api_retries: int = Field(default=2, ge=0, le=5)
    retry_base_delay_seconds: float = Field(default=0.25, ge=0.0, le=10.0)
    requests_per_minute_per_key: int = Field(default=15, ge=1, le=10_000)
    rate_limit_cooldown_seconds: float = Field(default=65.0, ge=1.0, le=3600.0)
    model_call_timeout_seconds: float = Field(
        default=30.0,
        ge=_MIN_GEMINI_TRANSPORT_TIMEOUT_SECONDS,
        le=600.0,
    )

    @property
    def api_keys(self) -> tuple[SecretStr, ...]:
        """Every distinct key, in rotation order."""
        unique: dict[str, SecretStr] = {}
        for key in (self.api_key, *self.additional_api_keys):
            unique.setdefault(key.get_secret_value(), key)
        return tuple(unique.values())

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> GeminiSettings:
        values = environment if environment is not None else os.environ
        api_keys = _environment_api_keys(values)
        if not api_keys:
            raise ModelConfigurationError(
                "Set GOOGLE_API_KEY (or GEMINI_API_KEY, or comma-separated GEMINI_API_KEYS) "
                "before using the Gemini adapter"
            )
        try:
            return cls(
                api_key=SecretStr(api_keys[0]),
                additional_api_keys=tuple(SecretStr(key) for key in api_keys[1:]),
                model_id=values.get("TABULAR_AGENT_MODEL", DEFAULT_GEMINI_MODEL),
                model_call_timeout_seconds=float(
                    values.get("TABULAR_AGENT_MODEL_TIMEOUT_SECONDS", "30")
                ),
            )
        except ValueError as exc:
            raise ModelConfigurationError(
                "TABULAR_AGENT_MODEL_TIMEOUT_SECONDS must be between 21 and 600"
            ) from exc


def _environment_api_keys(values: Mapping[str, str]) -> list[str]:
    """Collect keys from single, numbered (GEMINI_API_KEY_2), and list variables.

    Any of these variables may hold several comma-separated keys; API keys never contain commas.
    """
    numbered = sorted(
        (int(match.group(1)), name)
        for name in values
        if (match := _NUMBERED_KEY_NAME.fullmatch(name))
    )
    names = [*_SINGLE_KEY_NAMES, *(name for _, name in numbered), *_KEY_LIST_NAMES]
    keys = (part.strip() for name in names for part in values.get(name, "").split(","))
    return list(dict.fromkeys(key for key in keys if key))


class _ApiKeyPool:
    """Use one key until its per-minute budget or a rate limit, then rotate to the next.

    An exhausted or rate-limited key is flagged for a cooldown that expires on its own; after
    the last key, rotation wraps to the first.
    """

    def __init__(self, key_count: int, *, requests_per_minute: int, cooldown_seconds: float):
        self._requests: list[deque[float]] = [deque() for _ in range(key_count)]
        self._limited_until = [0.0] * key_count
        self._current = 0
        self._requests_per_minute = requests_per_minute
        self._cooldown_seconds = cooldown_seconds
        self._lock = Lock()

    def acquire(self, now: float) -> int | None:
        """Reserve one request on the first usable key, or return None when all are resting."""
        with self._lock:
            count = len(self._requests)
            start = self._current
            for offset in range(count):
                index = (start + offset) % count
                if self._limited_until[index] > now:
                    continue
                window = self._requests[index]
                while window and window[0] <= now - _RATE_WINDOW_SECONDS:
                    window.popleft()
                if len(window) >= self._requests_per_minute:
                    self._limited_until[index] = now + self._cooldown_seconds
                    continue
                self._current = index
                window.append(now)
                return index
            # Resume with the key that becomes usable first (the lowest index on a tie).
            self._current = min(range(count), key=self._limited_until.__getitem__)
            return None

    def mark_rate_limited(self, index: int, now: float) -> None:
        with self._lock:
            self._limited_until[index] = now + self._cooldown_seconds
            self._current = (index + 1) % len(self._requests)

    def disable(self, index: int) -> None:
        """Stop using a key the provider rejected; it cannot recover during this session."""
        with self._lock:
            self._limited_until[index] = math.inf
            self._current = (index + 1) % len(self._requests)

    def seconds_until_available(self, now: float) -> float:
        """Return the wait for the next usable key, or infinity when every key was rejected."""
        with self._lock:
            return max(0.0, min(self._limited_until) - now)


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


class _ClientFactory(Protocol):
    def __call__(
        self,
        settings: GeminiSettings,
        request: StructuredModelRequest[BaseModel],
        *,
        timeout_seconds: float,
        api_key: SecretStr,
    ) -> _ChatModel: ...


class _InvocationTimeout(TimeoutError):
    pass


class GeminiModelGateway(BaseModelGateway):
    """Call Gemini via LangChain native JSON schema with key rotation and bounded retries."""

    def __init__(
        self,
        settings: GeminiSettings,
        *,
        client: _ChatModel | None = None,
        client_factory: _ClientFactory | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        self._settings = settings
        self._api_keys = settings.api_keys
        self._client = client
        self._client_factory = client_factory or _build_chat_model
        self._sleep = sleep
        self._monotonic = monotonic
        self._key_pool = _ApiKeyPool(
            len(self._api_keys),
            requests_per_minute=settings.requests_per_minute_per_key,
            cooldown_seconds=settings.rate_limit_cooldown_seconds,
        )

    @property
    def api_key_count(self) -> int:
        return len(self._api_keys)

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
        key_index = self._acquire_key()
        messages = [
            SystemMessage(content=request.system_instruction),
            HumanMessage(content=request.prompt),
        ]
        while True:
            remaining_seconds = deadline - self._monotonic()
            if remaining_seconds <= 0:
                raise _model_timeout_error(call_timeout)
            client = self._client or self._client_factory(
                self._settings,
                cast(StructuredModelRequest[BaseModel], request),
                timeout_seconds=remaining_seconds,
                api_key=self._api_keys[key_index],
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
                if _is_rejected_key(exc):
                    self._key_pool.disable(key_index)
                    retries += 1
                    key_index = self._acquire_key(exc)
                    continue
                if _status_code(exc) == 429:
                    # Rest this key and move on immediately; waiting is left to the cooldown.
                    self._key_pool.mark_rate_limited(key_index, self._monotonic())
                    retries += 1
                    key_index = self._acquire_key(exc)
                    continue
                if not _is_retryable(exc) or retries >= self._settings.max_api_retries:
                    raise ModelProviderError(_safe_provider_error(exc)) from exc
                delay = self._settings.retry_base_delay_seconds * (2**retries)
                if delay >= deadline - self._monotonic():
                    raise _model_timeout_error(call_timeout) from exc
                retries += 1
                self._sleep(delay)

    def _acquire_key(self, error: Exception | None = None) -> int:
        now = self._monotonic()
        index = self._key_pool.acquire(now)
        if index is not None:
            return index
        wait = self._key_pool.seconds_until_available(now)
        if math.isinf(wait):
            message = f"All {len(self._api_keys)} Gemini API keys were rejected by the provider"
        else:
            message = (
                f"All {len(self._api_keys)} Gemini API keys are rate limited; the next key is "
                f"available in {math.ceil(wait)} seconds"
            )
        if error is None:
            raise ModelProviderError(message)
        raise ModelProviderError(f"{message} ({_safe_provider_error(error)})") from error


def _build_chat_model(
    settings: GeminiSettings,
    request: StructuredModelRequest[BaseModel],
    *,
    timeout_seconds: float,
    api_key: SecretStr,
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
            api_key=api_key,
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


def _status_code(error: Exception) -> int | None:
    status: object = getattr(error, "status_code", None) or getattr(error, "code", None)
    if not isinstance(status, (int, float, str)):
        return None
    try:
        return int(status)
    except (TypeError, ValueError):
        return None


def _is_rejected_key(error: Exception) -> bool:
    """Recognize an invalid, revoked, or unauthorized key; the message itself is never shown."""
    message = str(error).casefold()
    return (
        _status_code(error) in {401, 403}
        or "api key not valid" in message
        or ("api_key_invalid" in message)
    )


def _is_retryable(error: Exception) -> bool:
    if isinstance(error, (ConnectionError, TimeoutError)):
        return True
    status_code = _status_code(error)
    return status_code is not None and (status_code == 429 or status_code >= 500)


def _safe_provider_error(error: Exception) -> str:
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    suffix = f", status={status}" if isinstance(status, int) else ""
    return f"Gemini request failed ({type(error).__name__}{suffix})"
