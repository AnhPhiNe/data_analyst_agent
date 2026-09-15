"""Read-only SQL validation kept private to the tabular data implementation."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

import sqlglot
from sqlglot import expressions as exp

from tabular_analytics_agent.data.errors import UnsafeQueryError
from tabular_analytics_agent.domain import FilterScope

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
_OUTPUT_ALIAS = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True, slots=True)
class SQLAnalysis:
    normalized_sql: str
    referenced_columns: tuple[str, ...]
    has_wildcard: bool
    group_by_columns: tuple[str, ...] = ()
    unaliased_outputs: tuple[str, ...] = ()
    filters: tuple[str, ...] = ()
    filter_scopes: tuple[FilterScope, ...] = ()
    dataset_count_scope: Literal["whole_dataset"] | None = None


def replace_column_references(sql: str, replacements: Mapping[str, str]) -> str:
    """Rewrite unqualified column references, matched case-insensitively, to exact names.

    Output aliases keep their meaning, and SQL that cannot be parsed is returned unchanged so
    the read-only policy reports the parse error itself.
    """
    lookup = {key.casefold(): value for key, value in replacements.items()}
    if not lookup:
        return sql
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except sqlglot.errors.ParseError:
        return sql
    if len(statements) != 1 or statements[0] is None:
        return sql
    statement = statements[0]
    aliases = {alias.alias.casefold() for alias in statement.find_all(exp.Alias) if alias.alias}
    changed = False
    for column in statement.find_all(exp.Column):
        name = column.name
        target = lookup.get(name.casefold()) if name else None
        if target is None or column.table or name.casefold() in aliases:
            continue
        column.set("this", exp.to_identifier(target, quoted=True))
        changed = True
    return statement.sql(dialect="duckdb") if changed else sql


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

    _alias_calculated_outputs(statement)
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
        unaliased_outputs=_unaliased_outputs(statement),
        filters=_filters(statement),
        filter_scopes=_filter_scopes(statement),
        dataset_count_scope=(
            "whole_dataset"
            if is_whole_dataset_count(statement, allowed_table=allowed_table)
            else None
        ),
    )


def _alias_calculated_outputs(statement: exp.Query) -> None:
    """Give each unaliased calculated outer output a deterministic ASCII alias such as sum_1.

    Nothing can reference an unaliased expression by name, so adding the alias is safe, and
    evidence identifiers no longer depend on the model copying a long expression exactly.
    """
    if not isinstance(statement, exp.Select):
        return
    used = {projection.alias_or_name.casefold() for projection in statement.expressions}
    for projection in list(statement.expressions):
        if isinstance(projection, (exp.Alias, exp.Column, exp.Star)):
            continue
        base = projection.key.lower() if isinstance(projection, exp.AggFunc) else "value"
        index = 1
        while f"{base}_{index}" in used:
            index += 1
        alias = f"{base}_{index}"
        used.add(alias)
        projection.replace(exp.alias_(projection.copy(), alias))


def _unaliased_outputs(statement: exp.Query) -> tuple[str, ...]:
    """Return calculated outer projections whose explicit alias is not a short ASCII identifier.

    Plain source columns keep their names. Models copy non-ASCII output names unreliably, so a
    calculated output needs a simple alias to be referenced as evidence.
    """
    if not isinstance(statement, exp.Select):
        return ()
    return tuple(
        projection.sql(dialect="duckdb")
        for projection in statement.expressions
        if not isinstance(projection.unalias(), (exp.Column, exp.Star))
        and not (isinstance(projection, exp.Alias) and _OUTPUT_ALIAS.fullmatch(projection.alias))
    )


def _filters(statement: exp.Query) -> tuple[str, ...]:
    """Return the outer query's WHERE and HAVING conditions as DuckDB SQL."""
    if not isinstance(statement, exp.Select):
        return ()
    return tuple(
        clause.this.sql(dialect="duckdb")
        for clause in (statement.args.get("where"), statement.args.get("having"))
        if isinstance(clause, (exp.Where, exp.Having))
    )


def is_whole_dataset_count(
    query: str | exp.Query,
    *,
    allowed_table: str,
) -> bool:
    """Check whether SQL is exactly one unfiltered ``COUNT(*)`` over the session table."""
    if isinstance(query, str):
        try:
            statements = sqlglot.parse(query, read="duckdb")
        except sqlglot.errors.ParseError:
            return False
        if len(statements) != 1 or statements[0] is None:
            return False
        statement = statements[0]
    else:
        statement = query

    if not isinstance(statement, exp.Select):
        return False
    if any(
        value is not None
        for key, value in statement.args.items()
        if key not in {"expressions", "from_"}
    ):
        return False
    from_clause = statement.args.get("from_")
    if not isinstance(from_clause, exp.From) or not isinstance(from_clause.this, exp.Table):
        return False
    if any(value is not None for key, value in from_clause.args.items() if key != "this"):
        return False
    table = from_clause.this
    if (
        table.db
        or table.catalog
        or table.name.casefold() != allowed_table.casefold()
        or any(
            value is not None for key, value in table.args.items() if key not in {"this", "alias"}
        )
        or len(tuple(statement.find_all(exp.Table))) != 1
    ):
        return False
    if len(statement.expressions) != 1:
        return False
    projection = statement.expressions[0]
    count_expression = projection.this if isinstance(projection, exp.Alias) else projection
    return (
        isinstance(count_expression, exp.Count)
        and count_expression.sql(dialect="duckdb").casefold() == "count(*)"
    )


def _filter_scopes(statement: exp.Query) -> tuple[FilterScope, ...]:
    """Return WHERE/HAVING predicates with stable SELECT scope labels.

    ``filters`` intentionally remains the legacy outer-query tuple.  This richer view keeps
    predicates in their owning SELECT so a CTE, EXISTS, or nested subquery is never presented as
    one flattened conjunction with the outer query.
    """
    cte_scopes: dict[int, str] = {}
    cte_occurrences: dict[str, int] = {}
    for cte in statement.find_all(exp.CTE):
        if not cte.alias_or_name or not isinstance(cte.this, exp.Select):
            continue
        alias = cte.alias_or_name
        occurrence_key = alias.casefold()
        occurrence = cte_occurrences.get(occurrence_key, 0) + 1
        cte_occurrences[occurrence_key] = occurrence
        scope = f"cte:{alias}" if occurrence == 1 else f"cte:{alias}:{occurrence}"
        cte_scopes[id(cte.this)] = scope
    all_selects = tuple(statement.find_all(exp.Select))
    scoped_selects: list[tuple[str, exp.Select]] = []
    if isinstance(statement, exp.Select):
        scoped_selects.append(("outer", statement))
    cte_select_ids = set(cte_scopes)
    for select in all_selects:
        cte_scope = cte_scopes.get(id(select))
        if cte_scope is not None:
            scoped_selects.append((cte_scope, select))
    subquery_index = 0
    for select in all_selects:
        if select is statement or id(select) in cte_select_ids:
            continue
        subquery_index += 1
        scoped_selects.append((f"subquery:{subquery_index}", select))

    scopes: list[FilterScope] = []
    for scope, select in scoped_selects:
        for key in ("where", "having"):
            clause_name: Literal["where", "having"] = "where" if key == "where" else "having"
            clause = select.args.get(key)
            if not isinstance(clause, (exp.Where, exp.Having)):
                continue
            scopes.append(
                FilterScope(
                    scope=scope,
                    clause=clause_name,
                    expression=clause.this.sql(dialect="duckdb"),
                )
            )
    return tuple(scopes)


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
