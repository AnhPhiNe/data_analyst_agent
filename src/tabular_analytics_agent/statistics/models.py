"""Typed contracts for deterministic statistical Tool Actions."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonEmptyText = Annotated[str, Field(min_length=1)]
Probability = Annotated[float, Field(ge=0.0, le=1.0)]
PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]


class StatisticsModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )


class StatisticalOperation(StrEnum):
    DESCRIPTIVE = "descriptive"
    CORRELATION = "correlation"
    CONFIDENCE_INTERVAL = "confidence_interval"
    T_TEST = "t_test"
    MANN_WHITNEY = "mann_whitney"
    CHI_SQUARE = "chi_square"
    ANOVA = "anova"
    KRUSKAL_WALLIS = "kruskal_wallis"
    LINEAR_REGRESSION = "linear_regression"
    LOGISTIC_REGRESSION = "logistic_regression"


class AlternativeHypothesis(StrEnum):
    TWO_SIDED = "two-sided"
    LESS = "less"
    GREATER = "greater"


class AssumptionStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_CHECKED = "not_checked"


class AssumptionCheck(StatisticsModel):
    name: NonEmptyText
    status: AssumptionStatus
    message: NonEmptyText


class StatisticalEstimate(StatisticsModel):
    metric: NonEmptyText
    value: float
    unit: str | None = None


class ConfidenceInterval(StatisticsModel):
    confidence_level: Probability
    lower: float
    upper: float

    @model_validator(mode="after")
    def bounds_are_ordered(self) -> Self:
        if self.lower > self.upper:
            raise ValueError("confidence interval lower bound cannot exceed upper bound")
        return self


class StatisticalRequest(StatisticsModel):
    operation: StatisticalOperation
    value_fields: tuple[str, ...] = ()
    value_field: str | None = None
    group_field: str | None = None
    x_field: str | None = None
    y_field: str | None = None
    group_order: tuple[str | int | float | bool, ...] = ()
    confidence_level: Annotated[float, Field(gt=0.0, lt=1.0)] = 0.95
    alpha: Annotated[float, Field(gt=0.0, lt=1.0)] = 0.05
    alternative: AlternativeHypothesis = AlternativeHypothesis.TWO_SIDED
    multiple_testing_count: PositiveInt = 1
    positive_class: str | int | float | bool | None = None
    random_seed: NonNegativeInt = 42

    @model_validator(mode="after")
    def fields_match_operation(self) -> Self:
        required: dict[StatisticalOperation, tuple[str, ...]] = {
            StatisticalOperation.DESCRIPTIVE: ("value_fields",),
            StatisticalOperation.CONFIDENCE_INTERVAL: ("value_field",),
            StatisticalOperation.T_TEST: ("value_field", "group_field"),
            StatisticalOperation.MANN_WHITNEY: ("value_field", "group_field"),
            StatisticalOperation.ANOVA: ("value_field", "group_field"),
            StatisticalOperation.KRUSKAL_WALLIS: ("value_field", "group_field"),
            StatisticalOperation.CORRELATION: ("x_field", "y_field"),
            StatisticalOperation.CHI_SQUARE: ("x_field", "y_field"),
            StatisticalOperation.LINEAR_REGRESSION: ("x_field", "y_field"),
            StatisticalOperation.LOGISTIC_REGRESSION: ("x_field", "y_field"),
        }
        missing = [name for name in required[self.operation] if not getattr(self, name)]
        if missing:
            raise ValueError(f"{self.operation.value} requires: {', '.join(missing)}")
        if self.operation is StatisticalOperation.DESCRIPTIVE and len(
            set(self.value_fields)
        ) != len(self.value_fields):
            raise ValueError("descriptive fields must be unique")
        if (
            self.operation is StatisticalOperation.LOGISTIC_REGRESSION
            and self.positive_class is None
        ):
            raise ValueError("logistic_regression requires positive_class")
        two_group_operations = {
            StatisticalOperation.T_TEST,
            StatisticalOperation.MANN_WHITNEY,
        }
        if self.operation in two_group_operations:
            if len(self.group_order) != 2:
                raise ValueError(f"{self.operation.value} requires two ordered group identities")
            left, right = self.group_order
            if type(left) is type(right) and left == right:
                raise ValueError("ordered group identities must be distinct")
        elif self.group_order:
            raise ValueError("group_order applies only to two-group tests")
        one_sided_operations = {
            StatisticalOperation.CORRELATION,
            StatisticalOperation.T_TEST,
            StatisticalOperation.MANN_WHITNEY,
            StatisticalOperation.LINEAR_REGRESSION,
        }
        if (
            self.alternative is not AlternativeHypothesis.TWO_SIDED
            and self.operation not in one_sided_operations
        ):
            raise ValueError(f"{self.operation.value} supports only a two-sided alternative")
        return self

    @property
    def source_fields(self) -> tuple[str, ...]:
        candidates = (
            *self.value_fields,
            self.value_field,
            self.group_field,
            self.x_field,
            self.y_field,
        )
        return tuple(dict.fromkeys(field for field in candidates if field))

    def reproducible_parameters(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"source_fields"})


class StatisticalResult(StatisticsModel):
    result_id: UUID
    operation: StatisticalOperation
    source_fields: tuple[NonEmptyText, ...]
    sample_size: NonNegativeInt
    missing_row_count: NonNegativeInt
    missing_data_handling: NonEmptyText
    group_sizes: dict[str, NonNegativeInt] = Field(default_factory=dict)
    estimates: tuple[StatisticalEstimate, ...]
    statistic_name: str | None = None
    statistic: float | None = None
    p_value: Probability | None = None
    adjusted_alpha: Annotated[float, Field(gt=0.0, lt=1.0)] | None = None
    statistically_significant: bool | None = None
    practically_significant: bool | None = None
    confidence_interval: ConfidenceInterval | None = None
    effect_size: StatisticalEstimate | None = None
    assumptions: tuple[AssumptionCheck, ...] = ()
    warnings: tuple[str, ...] = ()
    parameters: dict[str, Any]

    @model_validator(mode="after")
    def significance_is_reproducible(self) -> Self:
        if self.p_value is not None:
            if self.adjusted_alpha is None or self.statistically_significant is None:
                raise ValueError("p-values require adjusted alpha and significance status")
            if self.statistically_significant is not (self.p_value < self.adjusted_alpha):
                raise ValueError("significance status must match p-value and adjusted alpha")
        elif self.adjusted_alpha is not None or self.statistically_significant is not None:
            raise ValueError("adjusted alpha and significance status require a p-value")
        if not self.estimates:
            raise ValueError("statistical results require computed estimates")
        return self
