"""Read-only SQL validation kept private to the tabular data implementation."""

from __future__ import annotations

import sqlglot
from sqlglot import expressions as exp

from tabular_analytics_agent.data.errors import UnsafeQueryError

_DENIED_FUNCTIONS = {
    "csv_scan",
    "glob",
    "httpfs",
    "parquet_scan",
    "postgres_scan",
    "query",
    "query_table",
    "read_blob",
    "read_csv",
    "read_csv_auto",
    "read_json",
    "read_json_auto",
    "read_ndjson",
    "read_parquet",
    "sqlite_scan",
}


def validate_read_only_sql(sql: str, *, allowed_table: str) -> str:
    """Return normalized SQL when it is one read-only query over the allowed table."""
    candidate = sql.strip()
    if not candidate:
        raise UnsafeQueryError("SQL cannot be empty")

    try:
        statements = sqlglot.parse(candidate, read="duckdb")
    except sqlglot.errors.ParseError as exc:
        raise UnsafeQueryError(f"SQL could not be parsed: {exc}") from exc

    if len(statements) != 1 or statements[0] is None:
        raise UnsafeQueryError("Exactly one SQL statement is allowed")

    statement = statements[0]
    if not isinstance(statement, exp.Query):
        raise UnsafeQueryError("Only SELECT or WITH ... SELECT queries are allowed")

    forbidden_nodes = (
        exp.Alter,
        exp.Attach,
        exp.Command,
        exp.Copy,
        exp.Create,
        exp.Delete,
        exp.Drop,
        exp.Insert,
        exp.Merge,
        exp.Set,
        exp.Transaction,
        exp.Update,
        exp.Use,
    )
    if any(statement.find(node_type) for node_type in forbidden_nodes):
        raise UnsafeQueryError("SQL contains a forbidden operation")

    cte_names = {cte.alias_or_name.casefold() for cte in statement.find_all(exp.CTE)}
    allowed_names = {allowed_table.casefold(), *cte_names}
    referenced_tables = {
        table.name.casefold() for table in statement.find_all(exp.Table) if table.name
    }
    disallowed_tables = referenced_tables - allowed_names
    if disallowed_tables:
        names = ", ".join(sorted(disallowed_tables))
        raise UnsafeQueryError(f"Query references tables outside this session: {names}")

    for function in statement.find_all(exp.Func):
        name = str(function.name or function.key).casefold()
        if name.startswith("read_") or name in _DENIED_FUNCTIONS:
            raise UnsafeQueryError(f"External-access function is forbidden: {name}")

    return statement.sql(dialect="duckdb")
