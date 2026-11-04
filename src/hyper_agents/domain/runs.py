"""Run-store value types shared by the controller and its store adapter (P04-3).

Plain frozen records: a lease names its fencing token, a checkpoint holds the
workflow position as JSON, and a budget is what is left to spend.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RunLease:
    run_id: str
    holder: str
    token: int
    expires_at: float


@dataclass(frozen=True)
class Checkpoint:
    run_id: str
    sequence: int
    position: dict[str, Any]


@dataclass(frozen=True)
class Budget:
    tokens_limit: int
    tokens_used: int
    attempts_limit: int
    attempts_used: int
    not_before: float
