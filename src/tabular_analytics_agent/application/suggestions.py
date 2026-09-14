"""Deterministic Analytical Goal suggestions derived from a Data Profile."""

from __future__ import annotations

from tabular_analytics_agent.domain import DataProfile, FieldKind

_MAX_SUGGESTIONS = 5
_MAX_GROUP_CATEGORIES = 20
_IDENTIFIER_WARNING = "identifier-like unique field"


def suggest_goals(profile: DataProfile) -> tuple[str, ...]:
    """Return three to five goals built only from field kinds and profile facts.

    No model call is made, so suggestions cost no quota and never name a field that is not in
    the profile. Possible PII and identifier-like fields are never suggested.
    """
    excluded = set(profile.pii_candidates)
    usable = [
        field
        for field in profile.fields
        if field.name not in excluded and _IDENTIFIER_WARNING not in field.warnings
    ]
    measures = [
        field.name for field in usable if field.kind is FieldKind.NUMERIC and field.unique_count > 1
    ]
    groups = [
        field
        for field in usable
        if field.kind in {FieldKind.CATEGORICAL, FieldKind.BOOLEAN}
        and 2 <= field.unique_count <= _MAX_GROUP_CATEGORIES
    ]
    dates = [field.name for field in usable if field.kind is FieldKind.DATETIME]
    binary_groups = [field.name for field in groups if field.unique_count == 2]

    suggestions: list[str] = []
    if measures and groups:
        suggestions.append(f"What is the average {measures[0]} for each {groups[0].name}?")
    if len(measures) >= 2:
        suggestions.append(f"Is {measures[0]} correlated with {measures[1]}?")
    if measures and dates:
        suggestions.append(f"How does the total {measures[0]} change over {dates[0]}?")
    if measures and binary_groups:
        suggestions.append(
            f"Is the average {measures[0]} significantly different between the "
            f"{binary_groups[0]} groups?"
        )
    if profile.duplicate_row_count or any(field.missing_count for field in profile.fields):
        suggestions.append(
            "Which columns have missing values, and how many duplicate rows are there?"
        )
    if measures:
        suggestions.append(f"What are the minimum, average, and maximum of {measures[0]}?")
    if groups:
        suggestions.append(f"How many rows are there for each {groups[0].name}?")
    suggestions.append("Which columns does this dataset have, and what kind of data is in each?")
    suggestions.append("How many rows and columns does this dataset have?")
    return tuple(dict.fromkeys(suggestions))[:_MAX_SUGGESTIONS]


__all__ = ["suggest_goals"]
