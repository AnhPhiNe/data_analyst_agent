"""Deterministic model adapter for tests and local demos."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel

from tabular_analytics_agent.model_gateway.base import BaseModelGateway
from tabular_analytics_agent.model_gateway.errors import ModelProviderError
from tabular_analytics_agent.model_gateway.models import RawModelResponse, StructuredModelRequest


class FakeModelGateway(BaseModelGateway):
    """Consume predefined outputs in order and record every request."""

    def __init__(self, outputs: Iterable[object], *, model_id: str = "fake-model") -> None:
        super().__init__()
        self._outputs = deque(outputs)
        self._model_id = model_id
        self.requests: list[StructuredModelRequest[Any]] = []

    def _generate_raw[ResponseT: BaseModel](
        self, request: StructuredModelRequest[ResponseT]
    ) -> RawModelResponse:
        self.requests.append(request)
        if not self._outputs:
            raise ModelProviderError("FakeModelGateway has no queued output")
        output = self._outputs.popleft()
        if isinstance(output, Exception):
            raise output
        return RawModelResponse(payload=output, model_id=self._model_id)
