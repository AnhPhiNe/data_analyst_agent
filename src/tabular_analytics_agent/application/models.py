"""Validated application records shared with the Streamlit adapter."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from tabular_analytics_agent.data import DatasetHandle, UploadInspection
from tabular_analytics_agent.domain import AnalysisSession, DataProfile


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
