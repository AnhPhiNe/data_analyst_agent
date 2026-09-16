"""Contracts at the tabular data module interface."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tabular_analytics_agent.domain import DatasetIdentity, FilterScope

PositiveInt = Annotated[int, Field(gt=0)]
NonEmptyText = Annotated[str, Field(min_length=1)]


class DataModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class DataCoreLimits(DataModel):
    max_file_bytes: PositiveInt = 100 * 1024 * 1024
    max_xlsx_uncompressed_bytes: PositiveInt = 500 * 1024 * 1024
    max_xlsx_compression_ratio: Annotated[float, Field(gt=1.0)] = 100.0
    max_query_rows: PositiveInt = 10_000
    query_timeout_seconds: PositiveInt = 30
    duckdb_memory_limit_mb: PositiveInt = 512
    profile_top_values: PositiveInt = 5


class UploadInspection(DataModel):
    original_filename: NonEmptyText
    file_format: NonEmptyText
    size_bytes: PositiveInt
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    sheets: tuple[str, ...] = ()
    encoding: str | None = None
    delimiter: str | None = None
    warnings: tuple[str, ...] = ()


# The single table every session exposes to SQL; the integrity check rejects any other name.
DATASET_TABLE = "dataset"
# Row listings may number uploaded rows under this output name (rowid + 1); it is a position,
# not a measured value.
ROW_NUMBER_COLUMN = "row_number"


class DatasetHandle(DataModel):
    dataset: DatasetIdentity
    source_path: Path
    working_database_path: Path
    working_dataset_version: PositiveInt = 1
    table_name: NonEmptyText = DATASET_TABLE
    selected_sheet: str | None = None


class QueryColumn(DataModel):
    name: NonEmptyText
    data_type: NonEmptyText


class QueryInspection(DataModel):
    normalized_sql: NonEmptyText
    referenced_columns: tuple[str, ...]
    has_wildcard: bool = False
    group_by_columns: tuple[str, ...] = ()
    unaliased_outputs: tuple[str, ...] = ()
    filters: tuple[str, ...] = ()
    filter_scopes: tuple[FilterScope, ...] = ()
    dataset_count_scope: Literal["whole_dataset"] | None = None
    # ``None`` means the record predates provenance v1; do not infer full-data provenance from it.
    provenance_version: Literal["v1"] | None = None
    base_relations: tuple[str, ...] = ()
    output_dependencies: tuple[tuple[str, tuple[str, ...]], ...] = ()


class QueryResult(DataModel):
    query_id: UUID
    dataset_id: UUID
    working_dataset_version: PositiveInt
    sql: NonEmptyText
    columns: tuple[QueryColumn, ...]
    rows: tuple[tuple[str | int | float | bool | None, ...], ...]
    row_count: int = Field(ge=0)
    truncated: bool
    duration_ms: int = Field(ge=0)
    group_by_columns: tuple[str, ...] = ()
    filters: tuple[str, ...] = ()
    filter_scopes: tuple[FilterScope, ...] = ()
    dataset_count_scope: Literal["whole_dataset"] | None = None
    # The inspection is optional for legacy persisted results and direct test fixtures.  New
    # successful executions attach the exact policy analysis used to normalize and execute SQL.
    inspection: QueryInspection | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> QueryResult:
        if self.row_count != len(self.rows):
            raise ValueError("row_count must match the returned rows")
        names = [column.name for column in self.columns]
        if not names or len(names) != len(set(names)):
            raise ValueError("query result columns must be present and unique")
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("every result row must match the column schema")
        return self
