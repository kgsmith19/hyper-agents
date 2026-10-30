"""Agent factory: mint and discharge leases with idempotency (P03-1).

A lease is a contract to run a role on a task with grants and a budget.
Mint is idempotent: the same idempotency key yields the same lease.
Discharge always returns a receipt, including on cancel.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from hyper_agents.domain.errors import InvalidInput
from hyper_agents.domain.roles import RoleManifest


@dataclass(frozen=True)
class MintRequest:
    """Request to lease an agent to run a role."""

    idempotency_key: str
    role: RoleManifest
    task: dict[str, object]
    grants: dict[str, object]
    budget_tokens: int

    def __post_init__(self) -> None:
        """Validate the request."""
        if not self.idempotency_key or len(self.idempotency_key) > 128:
            raise ValueError("idempotency_key must be 1-128 chars")
        if self.budget_tokens <= 0:
            raise ValueError("budget_tokens must be > 0")
        if not self.role.id or not self.role.version:
            raise ValueError("role must have id and version")


@dataclass(frozen=True)
class Lease:
    """A live agent running a role on a task."""

    lease_id: str
    idempotency_key: str
    role_id: str
    budget_tokens: int
    started_at_iso: str

    def __post_init__(self) -> None:
        """Validate the lease."""
        if not self.lease_id or not self.idempotency_key or not self.role_id:
            raise ValueError("lease requires lease_id, idempotency_key, and role_id")


@dataclass(frozen=True)
class Receipt:
    """Result of a lease: completion, cancellation, or failure."""

    lease_id: str
    status: str  # "completed", "cancelled", "failed"
    tokens_used: int
    completed_at_iso: str
    error: str | None = None

    def __post_init__(self) -> None:
        """Validate the receipt."""
        if self.status not in ("completed", "cancelled", "failed"):
            raise ValueError(f"status must be completed/cancelled/failed, got {self.status}")
        if self.tokens_used < 0 or self.tokens_used > 2_000_000:
            raise ValueError("tokens_used must be 0-2M")


class AgentFactory:
    """Mint and discharge agent leases with idempotency."""

    def __init__(self) -> None:
        self._leases: dict[str, Lease] = {}
        self._receipts: dict[str, Receipt] = {}

    def mint(self, request: MintRequest) -> Lease:
        """Lease an agent; idempotent by idempotency_key.

        Args:
            request: MintRequest with role, task, grants, and budget.

        Returns:
            A Lease with a lease_id, started time, and budget.

        Raises:
            InvalidInput if the request is invalid.
        """
        # Check for existing lease
        for lease in self._leases.values():
            if lease.idempotency_key == request.idempotency_key:
                return lease

        # Validate
        try:
            request_validated = MintRequest(
                idempotency_key=request.idempotency_key,
                role=request.role,
                task=request.task,
                grants=request.grants,
                budget_tokens=request.budget_tokens,
            )
        except ValueError as e:
            raise InvalidInput(f"mint request invalid: {e}") from None

        # Create new lease
        lease = Lease(
            lease_id=str(uuid4()),
            idempotency_key=request_validated.idempotency_key,
            role_id=request_validated.role.id,
            budget_tokens=request_validated.budget_tokens,
            started_at_iso=datetime.now(UTC).isoformat(),
        )
        self._leases[lease.lease_id] = lease
        return lease

    def discharge(
        self,
        lease_id: str,
        status: str = "completed",
        tokens_used: int = 0,
        error: str | None = None,
    ) -> Receipt:
        """Discharge a lease and return a receipt.

        Args:
            lease_id: The lease to discharge.
            status: "completed", "cancelled", or "failed".
            tokens_used: Tokens consumed.
            error: Error message if status is "failed".

        Returns:
            A Receipt with the final status.

        Raises:
            InvalidInput if the lease is not found or already discharged.
        """
        if lease_id not in self._leases:
            raise InvalidInput(f"lease not found: {lease_id}")

        if lease_id in self._receipts:
            raise InvalidInput(f"lease already discharged: {lease_id}")

        receipt = Receipt(
            lease_id=lease_id,
            status=status,
            tokens_used=tokens_used,
            completed_at_iso=datetime.now(UTC).isoformat(),
            error=error,
        )
        self._receipts[lease_id] = receipt
        return receipt
