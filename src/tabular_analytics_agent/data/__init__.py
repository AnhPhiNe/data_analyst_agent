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
    DATASET_TABLE,
    DataCoreLimits,
    DatasetHandle,
    QueryColumn,
    QueryInspection,
    QueryResult,
    UploadInspection,
)
from tabular_analytics_agent.data.sql_policy import quote_identifier, replace_column_references

__all__ = [
    "DATASET_TABLE",
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
    "quote_identifier",
    "replace_column_references",
]
