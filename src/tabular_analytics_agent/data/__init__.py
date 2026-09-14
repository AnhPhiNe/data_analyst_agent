"""Public interface for secure, deterministic tabular data operations."""

from tabular_analytics_agent.data.core import TabularDataCore
from tabular_analytics_agent.data.errors import (
    DataCoreError,
    DatasetIntegrityError,
    QueryExecutionError,
    QueryTimeoutError,
    SheetSelectionError,
    UnsafeFileError,
    UnsafeQueryError,
    UnsupportedFileError,
)
from tabular_analytics_agent.data.models import (
    DataCoreLimits,
    DatasetHandle,
    QueryColumn,
    QueryInspection,
    QueryResult,
    UploadInspection,
)
from tabular_analytics_agent.data.sql_policy import replace_column_references

__all__ = [
    "DataCoreError",
    "DataCoreLimits",
    "DatasetHandle",
    "DatasetIntegrityError",
    "QueryColumn",
    "QueryExecutionError",
    "QueryInspection",
    "QueryResult",
    "QueryTimeoutError",
    "SheetSelectionError",
    "TabularDataCore",
    "UnsafeFileError",
    "UnsafeQueryError",
    "UnsupportedFileError",
    "UploadInspection",
    "replace_column_references",
]
