"""ASCII field ids and canonical field-name resolution for model output."""

from __future__ import annotations

import unicodedata
from typing import Any

from tabular_analytics_agent.data import (
    QueryResult,
)
from tabular_analytics_agent.domain import (
    DataProfile,
)


class PlanRepairError(ValueError):
    """A plan defect that replanning with the exact error can fix."""


class UnknownFieldError(PlanRepairError):
    """A model referenced a field name that is not in the Data Profile."""


def field_key(name: str) -> str:
    # Vietnamese headers may arrive precomposed or decomposed; compare them in one form.
    return unicodedata.normalize("NFC", name).casefold()


def field_ids(profile: DataProfile) -> dict[str, str]:
    """Stable ASCII ids (c1, c2, ...) that let the model avoid copying non-ASCII field names.

    An id that equals a real field name is not assigned, so a real name always wins.
    """
    real_names = {field_key(field.name) for field in profile.fields}
    return {
        f"c{index}": field.name
        for index, field in enumerate(profile.fields, start=1)
        if f"c{index}" not in real_names
    }


def field_lookup(profile: DataProfile) -> dict[str, str]:
    lookup = {field_key(field.name): field.name for field in profile.fields}
    lookup.update({field_key(field_id): name for field_id, name in field_ids(profile).items()})
    return lookup


def result_column_lookup(result: QueryResult) -> dict[str, str]:
    """Map exact result column names and their ids (r1, r2, ...) to result column names."""
    lookup = {field_key(column.name): column.name for column in result.columns}
    for index, column in enumerate(result.columns, start=1):
        lookup.setdefault(field_key(f"r{index}"), column.name)
    return lookup


def resolve_column(lookup: dict[str, str], name: str | None) -> str | None:
    return None if name is None else lookup.get(field_key(name), name)


def canonicalize_profile_fields(profile: DataProfile, fields: tuple[str, ...]) -> tuple[str, ...]:
    canonical_names = field_lookup(profile)
    canonical: list[str] = []
    unknown: list[str] = []
    for field in fields:
        canonical_name = canonical_names.get(field_key(field))
        if canonical_name is None:
            unknown.append(field)
        else:
            canonical.append(canonical_name)
    if unknown:
        names = ", ".join(sorted(set(unknown)))
        raise UnknownFieldError(f"Unknown fields requested: {names}")
    return tuple(canonical)


def canonicalize_statistical_fields(
    request: dict[str, Any], profile: DataProfile
) -> dict[str, Any]:
    canonical_fields = field_lookup(profile)
    field_names = ("value_fields", "value_field", "group_field", "x_field", "y_field")
    for name in field_names:
        value = request[name]
        if isinstance(value, list):
            request[name] = [
                canonical_fields.get(field_key(field), field) if isinstance(field, str) else field
                for field in value
            ]
        elif isinstance(value, str):
            request[name] = canonical_fields.get(field_key(value), value)
    return request
