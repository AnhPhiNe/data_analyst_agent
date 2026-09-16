"""Read-only SQL validation kept private to the tabular data implementation."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast

import sqlglot
from sqlglot import expressions as exp
from sqlglot.optimizer.scope import Scope, traverse_scope

from tabular_analytics_agent.data.errors import UnsafeQueryError
from tabular_analytics_agent.data.models import DATASET_TABLE
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
# Functions whose value can change between executions of the same SQL text.  The SQL policy is
# deliberately conservative here: deterministic analytical functions remain allowed, while a
# volatile expression cannot be used as reproducible evidence.
_VOLATILE_FUNCTIONS = {
    "clock_timestamp",
    "current_date",
    "current_time",
    "current_timestamp",
    "currentdate",
    "currenttime",
    "currenttimestamp",
    "gen_random_uuid",
    "localtime",
    "localtimestamp",
    "now",
    "random",
    "rand",
    "today",
    "uuid",
    "uuidv4",
    "uuidv7",
}
_OUTPUT_ALIAS = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# This dependency is a source relation marker, not a user field.  It lets verification
# distinguish COUNT(*) from a literal output with no source columns.
ROW_COUNT_DEPENDENCY = "__row_count__"


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
    # ``None`` is reserved for legacy/directly-created records.  The policy always emits v1.
    provenance_version: Literal["v1"] | None = None
    base_relations: tuple[str, ...] = ()
    output_dependencies: tuple[tuple[str, tuple[str, ...]], ...] = ()


def replace_column_references(sql: str, replacements: Mapping[str, str]) -> str:
    """Rewrite field ids that resolve to the session table, preserving SQL aliases.

    Output aliases keep their meaning, and SQL that cannot be parsed is returned unchanged so
    the read-only policy reports the parse error itself. Qualified ids are rewritten only when
    their qualifier binds to the base ``dataset`` table in that SELECT scope; CTE and derived
    table columns are already named outputs and are left alone.
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
    try:
        scopes = traverse_scope(statement)
    except sqlglot.errors.SqlglotError:
        return sql
    scopes_by_select = {
        id(scope.expression): scope for scope in scopes if isinstance(scope.expression, exp.Select)
    }
    try:
        selected_sources_by_scope = {
            id(scope): cast(
                Mapping[str, tuple[exp.Expression, exp.Expression | Scope]],
                scope.selected_sources,
            )
            for scope in scopes
        }
    except sqlglot.errors.SqlglotError:
        return sql

    def aliases_for_scope(scope: Scope) -> set[str]:
        aliases = {
            projection.alias.casefold()
            for projection in scope.expression.expressions
            if isinstance(projection, exp.Alias) and projection.alias
        }
        selected_sources = selected_sources_by_scope[id(scope)]
        for _, source in selected_sources.values():
            if isinstance(source, Scope):
                aliases.update(name.casefold() for name in source.outer_columns)
                aliases.update(
                    name.casefold()
                    for name in getattr(source.expression, "named_selects", ())
                    if name
                )
        return aliases

    def source_for_qualifier(scope: Scope, qualifier: str) -> exp.Expression | Scope | None:
        normalized_qualifier = qualifier.casefold()
        current: Scope | None = scope
        while current is not None:
            selected_sources = selected_sources_by_scope[id(current)]
            for name, (_, source) in selected_sources.items():
                if name.casefold() == normalized_qualifier:
                    return source
            current = current.parent
        return None

    changed = False
    for column in statement.find_all(exp.Column):
        name = column.name
        target = lookup.get(name.casefold()) if name else None
        if target is None:
            continue

        select = column.find_ancestor(exp.Select)
        scope = scopes_by_select.get(id(select)) if select is not None else None
        if column.table:
            if column.db or column.catalog or scope is None:
                continue
            source = source_for_qualifier(scope, column.table)
            if not isinstance(source, exp.Table) or source.name.casefold() != DATASET_TABLE:
                continue
        elif scope is not None and name.casefold() in aliases_for_scope(scope):
            continue

        column.set("this", exp.to_identifier(target, quoted=True))
        changed = True
    return statement.sql(dialect="duckdb") if changed else sql


def quote_identifier(value: str) -> str:
    """Quote a field or table name for DuckDB SQL."""
    return '"' + value.replace('"', '""') + '"'


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
    if allowed_table.casefold() in cte_names:
        raise UnsafeQueryError(
            f"CTE name {allowed_table!r} is reserved for the session dataset and cannot be shadowed"
        )
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

    base_relations = tuple(
        dict.fromkeys(
            table.name
            for table in statement.find_all(exp.Table)
            if table.name and table.name.casefold() == allowed_table.casefold()
        )
    )
    if not base_relations:
        raise UnsafeQueryError(
            f"Query must reference the session dataset table {allowed_table!r}; "
            "a CTE or literal-only query is not a reproducible dataset analysis"
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
        name = _function_name(function)
        if name.startswith("read_") or name in _DENIED_FUNCTIONS:
            raise UnsafeQueryError(f"External-access function is forbidden: {name}")
        if name in _VOLATILE_FUNCTIONS:
            raise UnsafeQueryError(
                f"Volatile function is forbidden for reproducible evidence: {name}"
            )

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
        provenance_version="v1",
        base_relations=base_relations,
        output_dependencies=_output_dependencies(statement, allowed_table=allowed_table),
    )


def _function_name(function: exp.Func) -> str:
    """Return a stable lower-case function name for named and anonymous SQL functions."""
    if isinstance(function, exp.Anonymous):
        return str(function.this or "").casefold()
    return str(function.name or function.key).casefold()


def _output_dependencies(
    statement: exp.Query,
    *,
    allowed_table: str,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Resolve outer output columns to source fields through SQLGlot scopes.

    An empty dependency tuple means the output has no field dependency (for example a literal).
    ``ROW_COUNT_DEPENDENCY`` marks COUNT(*) so it is not confused with a literal.  This is
    intentionally a conservative lineage record: it identifies syntactic source columns and
    does not claim that a business meaning or unit has been proved.
    """
    if not isinstance(statement, exp.Select):
        return ()
    try:
        scopes = tuple(traverse_scope(statement))
    except sqlglot.errors.SqlglotError:
        return ()
    scopes_by_expression = {id(scope.expression): scope for scope in scopes}
    memo: dict[int, dict[str, tuple[str, ...]]] = {}

    def source_map(scope: Scope) -> dict[str, dict[str, tuple[str, ...]]]:
        result: dict[str, dict[str, tuple[str, ...]]] = {}
        for alias, (_, source) in scope.selected_sources.items():
            if isinstance(source, Scope):
                result[alias.casefold()] = scope_outputs(source)
            elif isinstance(source, exp.Table):
                # SQL identifiers are case-insensitive.  SQLGlot may materialize a CTE
                # reference with a Table node when the reference casing differs from the
                # declaration (for example ``Totals`` versus ``TOTALS``).  Recover the
                # corresponding scope before treating it as an external table.
                cte_source = next(
                    (
                        candidate
                        for name, candidate in scope.sources.items()
                        if name.casefold() == alias.casefold() and isinstance(candidate, Scope)
                    ),
                    None,
                )
                if cte_source is not None:
                    result[alias.casefold()] = scope_outputs(cte_source)
                elif source.name.casefold() == allowed_table.casefold():
                    # A base relation has no schema available to the SQL policy.  Any
                    # resolved column is therefore represented by its own source field name.
                    result[alias.casefold()] = {}
        return result

    def column_dependencies(
        column: exp.Column,
        scope: Scope,
        sources: dict[str, dict[str, tuple[str, ...]]],
    ) -> tuple[str, ...]:
        def source_dependency(
            relation: dict[str, tuple[str, ...]],
            source_name: str,
        ) -> tuple[str, ...]:
            """Resolve a projected name using SQL's case-insensitive identifier rules."""
            if not relation:
                return (source_name,)
            if source_name in relation:
                return relation[source_name]
            folded_name = source_name.casefold()
            for name, dependencies in relation.items():
                if name.casefold() == folded_name:
                    return dependencies
            return (source_name,)

        name = column.name
        if not name:
            return ()
        if column.table:
            relation = sources.get(column.table.casefold())
            if relation is None:
                return ()
            return source_dependency(relation, name)

        matches: list[tuple[str, ...]] = []
        for relation in sources.values():
            matches.append(source_dependency(relation, name))
        if matches:
            return tuple(dict.fromkeys(item for match in matches for item in match))
        # Correlated columns can be resolved in an outer scope.  The column name remains useful
        # provenance even when SQLGlot does not expose the outer relation through this scope.
        return (name,)

    def expression_dependencies(
        expression: exp.Expression,
        scope: Scope,
        sources: dict[str, dict[str, tuple[str, ...]]],
    ) -> tuple[str, ...]:
        if _is_count_star(expression):
            return (ROW_COUNT_DEPENDENCY,)
        dependencies: list[str] = []
        if any(_count_uses_star(count) for count in expression.find_all(exp.Count)):
            dependencies.append(ROW_COUNT_DEPENDENCY)
        for column in expression.find_all(exp.Column):
            dependencies.extend(column_dependencies(column, scope, sources))
        return tuple(dict.fromkeys(dependencies))

    def scope_outputs(scope: Scope) -> dict[str, tuple[str, ...]]:
        key = id(scope)
        if key in memo:
            return memo[key]
        sources = source_map(scope)
        output: dict[str, tuple[str, ...]] = {}
        # Store before descending so a malformed recursive CTE cannot recurse forever.
        memo[key] = output
        for projection in scope.expression.expressions:
            name = projection.alias_or_name or projection.sql(dialect="duckdb")
            output[name] = expression_dependencies(projection, scope, sources)
        return output

    outer_scope = scopes_by_expression.get(id(statement))
    if outer_scope is None:
        return ()
    try:
        output = scope_outputs(outer_scope)
    except sqlglot.errors.SqlglotError:
        # SQLGlot can reject malformed scope aliases (for example a duplicate JOIN alias) while
        # lazily materializing ``selected_sources``.  Leave execution to DuckDB's existing
        # schema/query error boundary instead of leaking an optimizer exception from inspection.
        return ()
    return tuple(
        (
            projection.alias_or_name or projection.sql(dialect="duckdb"),
            output.get(projection.alias_or_name or projection.sql(dialect="duckdb"), ()),
        )
        for projection in statement.expressions
    )


def _is_count_star(expression: exp.Expression) -> bool:
    """Identify COUNT(*) projections without treating COUNT(field) as a row-source marker."""
    count = expression.this if isinstance(expression, exp.Alias) else expression
    return isinstance(count, exp.Count) and _count_uses_star(count)


def _count_uses_star(count: exp.Count) -> bool:
    """Return whether a COUNT node counts rows rather than a source field."""
    if not isinstance(count, exp.Count):
        return False
    return isinstance(count.this, exp.Star) or any(
        isinstance(argument, exp.Star) for argument in count.args.get("expressions", ())
    )


_GENERATED_ALIAS = re.compile(r"([a-z]+)_([1-9][0-9]*)")


def _generated_alias(function: str, index: int) -> str:
    return f"{function}_{index}"


def generated_alias_function(name: str) -> str | None:
    """Return the function an alias of the generated form names, such as sum for sum_1."""
    match = _GENERATED_ALIAS.fullmatch(name)
    return match.group(1) if match else None


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
        while _generated_alias(base, index) in used:
            index += 1
        alias = _generated_alias(base, index)
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
