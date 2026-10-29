"""Error taxonomy (docs/design/v1.md, "Error taxonomy").

Every failure the controller can observe maps to exactly one category, and the
category alone decides whether a retry is allowed. Messages never carry secret
values or repository content.
"""

from enum import StrEnum


class ErrorCategory(StrEnum):
    INVALID_INPUT = "invalid_input"  # malformed task, contract or role manifest
    POLICY_DENIED = "policy_denied"  # a grant, path, network or approval rule refused
    BUDGET_EXHAUSTED = "budget_exhausted"  # token, cost, wall-clock or attempt limit hit
    STALE_STATE = "stale_state"  # head SHA moved, lease lost, version drift
    PROVIDER_UNAVAILABLE = "provider_unavailable"  # model or sandbox backend unreachable
    WORKER_FAILED = "worker_failed"  # the worker ran and returned an unusable result
    CANCELLED = "cancelled"  # an operator or a stop condition cancelled the run
    INTERNAL = "internal"  # a defect in hyper-agents itself


RETRYABLE: frozenset[ErrorCategory] = frozenset(
    {ErrorCategory.PROVIDER_UNAVAILABLE, ErrorCategory.WORKER_FAILED}
)


class HyperAgentsError(Exception):
    """Base error: always carries exactly one category."""

    category: ErrorCategory = ErrorCategory.INTERNAL

    def __init__(self, message: str, category: ErrorCategory | None = None) -> None:
        super().__init__(message)
        if category is not None:
            self.category = category

    @property
    def retryable(self) -> bool:
        return self.category in RETRYABLE


class InvalidInput(HyperAgentsError):
    category = ErrorCategory.INVALID_INPUT


class PolicyDenied(HyperAgentsError):
    category = ErrorCategory.POLICY_DENIED
