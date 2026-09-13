"""Stable error modes exposed by the tabular data module."""


class DataCoreError(Exception):
    """Base error for deterministic dataset operations."""


class UnsupportedFileError(DataCoreError):
    """The upload format is outside the Supported Dataset contract."""


class UnsafeFileError(DataCoreError):
    """The upload violates a file safety or structural constraint."""


class SheetSelectionError(DataCoreError):
    """An XLSX sheet selection is missing or invalid."""


class DatasetIntegrityError(DataCoreError):
    """A persisted Source or Working Dataset no longer matches its handle."""


class UnsafeQueryError(DataCoreError):
    """A query violates the read-only analytical SQL policy."""


class QueryExecutionError(DataCoreError):
    """A validated query could not be executed."""


class QueryTimeoutError(QueryExecutionError):
    """A query exceeded its configured execution timeout."""
