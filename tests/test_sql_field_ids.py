from __future__ import annotations

from pathlib import Path

import pytest

from tabular_analytics_agent.data import (
    DatasetHandle,
    QueryExecutionError,
    TabularDataCore,
    UnsafeQueryError,
    replace_column_references,
)
from tabular_analytics_agent.orchestration.field_ids import field_ids


def _sales_dataset(tmp_path: Path) -> tuple[TabularDataCore, DatasetHandle]:
    upload = tmp_path / "sales.csv"
    upload.write_text(
        "Khu vực,Doanh thu,c1\nBắc,100,real-a\nNam,150,real-b\n",
        encoding="utf-8",
        newline="",
    )
    core = TabularDataCore(tmp_path / "session")
    return core, core.ingest(upload)


def test_qualified_dataset_and_table_alias_ids_rewrite_and_execute(tmp_path: Path) -> None:
    core, handle = _sales_dataset(tmp_path)
    replacements = {"c1": "Khu vực", "c2": "Doanh thu"}

    dataset_sql = replace_column_references(
        "SELECT dataset.c1 FROM dataset WHERE dataset.c2 > 120",
        replacements,
    )
    alias_sql = replace_column_references(
        "SELECT d.c1 FROM dataset AS d WHERE d.c2 = 100",
        replacements,
    )

    assert 'dataset."Khu vực"' in dataset_sql
    assert 'dataset."Doanh thu"' in dataset_sql
    assert 'd."Khu vực"' in alias_sql
    assert 'd."Doanh thu"' in alias_sql
    assert core.query(handle, dataset_sql).rows == (("Nam",),)
    assert core.query(handle, alias_sql).rows == (("Bắc",),)


def test_nested_shadowed_alias_rewrites_only_the_dataset_scope(tmp_path: Path) -> None:
    core, handle = _sales_dataset(tmp_path)
    sql = (
        "WITH source AS (SELECT d.c1 AS c1 FROM dataset AS d) "
        "SELECT d.c1 FROM dataset AS d "
        "WHERE EXISTS (SELECT 1 FROM source AS d WHERE d.c1 = 'Bắc') "
        "ORDER BY d.c1"
    )

    rewritten = replace_column_references(sql, {"c1": "Khu vực"})

    assert rewritten.count('d."Khu vực"') == 3
    assert "FROM source AS d WHERE d.c1 = 'Bắc'" in rewritten
    assert core.query(handle, rewritten).rows == (("Bắc",), ("Nam",))


def test_cte_output_alias_is_preserved_while_its_source_id_is_rewritten() -> None:
    sql = "WITH source AS (SELECT d.c2 AS c1 FROM dataset AS d) SELECT source.c1 FROM source"

    rewritten = replace_column_references(
        sql,
        {"c1": "Khu vực", "c2": "Doanh thu"},
    )

    assert 'd."Doanh thu" AS c1' in rewritten
    assert "SELECT source.c1 FROM source" in rewritten
    assert 'source."Khu vực"' not in rewritten


def test_output_aliases_do_not_shadow_field_ids_in_other_select_scopes() -> None:
    sql = "WITH source AS (SELECT c2 AS c1 FROM dataset) SELECT c1 FROM dataset"

    rewritten = replace_column_references(
        sql,
        {"c1": "Khu vực", "c2": "Doanh thu"},
    )

    assert 'SELECT "Doanh thu" AS c1 FROM dataset' in rewritten
    assert rewritten.endswith('SELECT "Khu vực" FROM dataset')


def test_real_c1_field_name_is_not_rewritten(tmp_path: Path) -> None:
    core, handle = _sales_dataset(tmp_path)
    replacements = field_ids(core.profile(handle))

    assert "c1" not in replacements
    rewritten = replace_column_references(
        "SELECT d.c1, d.c2 FROM dataset AS d ORDER BY d.c1",
        replacements,
    )

    assert "d.c1" in rewritten
    assert 'd."Doanh thu"' in rewritten
    assert core.query(handle, rewritten).rows == (("real-a", 100), ("real-b", 150))


def test_non_allowlisted_table_qualifier_is_not_rewritten(tmp_path: Path) -> None:
    core, handle = _sales_dataset(tmp_path)
    sql = "SELECT other_table.c1 FROM other_table"

    rewritten = replace_column_references(sql, {"c1": "Khu vực"})

    assert rewritten == sql
    with pytest.raises(UnsafeQueryError, match="outside this session"):
        core.inspect_query(handle, rewritten)


def test_duplicate_table_alias_scope_errors_fall_through_to_query_policy(
    tmp_path: Path,
) -> None:
    core, handle = _sales_dataset(tmp_path)
    sql = "SELECT d.c1 FROM dataset AS d JOIN dataset AS d ON TRUE"

    assert replace_column_references(sql, {"c1": "Khu vực"}) == sql
    with pytest.raises(QueryExecutionError):
        core.inspect_query(handle, sql)


def test_unrelated_invalid_cte_scope_falls_back_to_original_sql() -> None:
    sql = (
        "WITH broken AS (SELECT 1 FROM dataset AS d JOIN dataset AS d ON TRUE) "
        "SELECT c1 FROM dataset"
    )

    assert replace_column_references(sql, {"c1": "area"}) == sql
