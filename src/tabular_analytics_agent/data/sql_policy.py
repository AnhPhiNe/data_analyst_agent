"""Read-only SQL validation kept private to the tabular data implementation."""

from __future__ import annotations

from dataclasses import dataclass

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


@dataclass(frozen=True, slots=True)
class SQLAnalysis:
    normalized_sql: str
    referenced_columns: tuple[str, ...]
    has_wildcard: bool
    group_by_columns: tuple[str, ...] = ()


def validate_read_only_sql(sql: str, *, allowed_table: str) -> str:
    """Return normalized SQL when it is one read-only query over the allowed table."""
    return analyze_read_only_sql(sql, allowed_table=allowed_table).normalized_sql


def analyze_read_only_sql(sql: str, *, allowed_table: str) -> SQLAnalysis:
    """Validate SQL and report canonical text plus referenced column names."""
    candidate = sql.strip()
    if not candidate:
        raise UnsafeQueryError("SQL cannot be empty")

    try:
        statements = sqlglot.parse(candidate, read="duckdb")
    except sqlglot.errors.ParseError as exc:
        # sqlglot's full message embeds terminal highlighting; its structured errors do not.
        detail = "; ".join(
            f"{error.get('description')} (line {error.get('line')}, column {error.get('col')})"
            for error in exc.errors
        )
        raise UnsafeQueryError(
            f"SQL could not be parsed: {detail or type(exc).__name__}. Quote column names that "
            'contain spaces or non-ASCII letters with double quotes, for example "Unit Price".'
        ) from exc

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

    if statement.find(exp.Columns):
        raise UnsafeQueryError("Dynamic COLUMNS selectors are forbidden")

    cte_names = {cte.alias_or_name.casefold() for cte in statement.find_all(exp.CTE)}
    allowed_names = {allowed_table.casefold(), *cte_names}
    referenced_tables = {
        table.name.casefold() for table in statement.find_all(exp.Table) if table.name
    }
    disallowed_tables = referenced_tables - allowed_names
    if disallowed_tables:
        names = ", ".join(sorted(disallowed_tables))
        raise UnsafeQueryError(
            f"Query references tables outside this session: {names}. "
            f"The only available table is named {allowed_table!r}."
        )

    relation_identifiers = {
        table.alias_or_name.casefold()
        for table in statement.find_all(exp.Table)
        if table.alias_or_name
    }
    if any(
        not column.table and column.name.casefold() in relation_identifiers
        for column in statement.find_all(exp.Column)
        if column.name
    ):
        raise UnsafeQueryError("Table values cannot be used as scalar row structs")

    for function in statement.find_all(exp.Func):
        name = str(function.name or function.key).casefold()
        if name.startswith("read_") or name in _DENIED_FUNCTIONS:
            raise UnsafeQueryError(f"External-access function is forbidden: {name}")

    output_alias_references = _output_alias_references(statement)
    referenced_columns = tuple(
        sorted(
            {
                column.name
                for column in statement.find_all(exp.Column)
                if column.name and id(column) not in output_alias_references
            }
        )
    )
    return SQLAnalysis(
        normalized_sql=statement.sql(dialect="duckdb"),
        referenced_columns=referenced_columns,
        has_wildcard=any(
            isinstance(star.parent, (exp.Select, exp.Column))
            for star in statement.find_all(exp.Star)
        ),
        group_by_columns=_group_by_output_columns(statement),
    )


def _output_alias_references(statement: exp.Query) -> set[int]:
    """Return ORDER BY columns that DuckDB resolves to SELECT output aliases."""
    references: set[int] = set()
    for select in statement.find_all(exp.Select):
        output_aliases = {
            projection.alias_or_name.casefold()
            for projection in select.expressions
            if isinstance(projection, exp.Alias) and projection.alias_or_name
        }
        order = select.args.get("order")
        if not output_aliases or not isinstance(order, exp.Order):
            continue
        references.update(
            id(column)
            for column in order.find_all(exp.Column)
            if column.find_ancestor(exp.Select) is select
            and not column.table
            and column.name
            and column.name.casefold() in output_aliases
        )
    return references


def _group_by_output_columns(statement: exp.Query) -> tuple[str, ...]:
    """Return the output column names that are GROUP BY keys of the outer SELECT.

    Handles grouping by column, expression, output alias, ordinal position, and GROUP BY ALL.
    """
    group = statement.args.get("group")
    if not isinstance(statement, exp.Select) or not isinstance(group, exp.Group):
        return ()
    projections = statement.expressions
    keys: list[exp.Expression] = []
    if group.args.get("all"):
        keys = [projection for projection in projections if not projection.find(exp.AggFunc)]
    for key in group.expressions:
        if isinstance(key, exp.Literal) and key.is_int:
            position = int(key.name) - 1
            keys.extend(projections[position : position + 1] if position >= 0 else ())
            continue
        keys.extend(
            projection
            for projection in projections
            if projection.unalias() == key
            or (
                isinstance(key, exp.Column)
                and not key.table
                and key.name.casefold() == projection.alias_or_name.casefold()
            )
        )
    return tuple(dict.fromkeys(item.alias_or_name for item in keys if item.alias_or_name))
