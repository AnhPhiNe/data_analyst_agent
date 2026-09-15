"""Validated application records shared with the Streamlit adapter."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from tabular_analytics_agent.data import DatasetHandle, UploadInspection
from tabular_analytics_agent.domain import AnalysisSession, DataProfile, SessionStatus


class ApplicationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class StagedUpload(ApplicationModel):
    session_id: UUID
    upload_path: Path
    inspection: UploadInspection


class AnalysisWorkspace(ApplicationModel):
    session: AnalysisSession
    dataset_handle: DatasetHandle
    data_profile: DataProfile


class SessionSummary(ApplicationModel):
    """Small validated record used to render and select a persisted session."""

    session: AnalysisSession
    original_filename: str = Field(min_length=1)

    @property
    def session_id(self) -> UUID:
        """Expose the canonical identity without duplicating session metadata."""
        return self.session.session_id

    @property
    def status(self) -> SessionStatus:
        """Expose the session lifecycle state for presentation adapters."""
        return self.session.status

    @property
    def created_at(self) -> datetime:
        """Expose the durable creation timestamp for lightweight callers."""
        return self.session.created_at

    @property
    def updated_at(self) -> datetime:
        """Expose the durable update timestamp for stable ordering and display."""
        return self.session.updated_at


class ToolActionRerun(ApplicationModel):
    """Outcome of re-executing a saved Tool Action without a model call."""

    action_id: str = Field(min_length=1)
    reproduced: bool
    detail: str
