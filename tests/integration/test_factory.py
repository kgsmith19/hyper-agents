"""Integration tests for factory, mint/discharge, and idempotency (P03-1)."""

import pytest

from hyper_agents.adapters.yaml_parser import SafeYamlParser
from hyper_agents.application.catalog import CatalogLoader
from hyper_agents.application.factory import AgentFactory, MintRequest, Receipt
from hyper_agents.domain.errors import InvalidInput

CATALOG_ROOT = __import__("pathlib").Path(__file__).parent.parent.parent / "agents" / "catalog"


def test_mint_request_is_typed() -> None:
    """MintRequest fields are required and validated."""
    loader = CatalogLoader(CATALOG_ROOT, SafeYamlParser())
    registry = loader.load()
    role = registry.get("planner")

    # Valid request
    request = MintRequest(
        idempotency_key="test-1",
        role=role.manifest,
        task={"owner": {}},
        grants={},
        budget_tokens=10_000,
    )
    assert request.idempotency_key == "test-1"
    assert request.role.id == "planner"
    assert request.budget_tokens == 10_000


def test_mint_request_rejects_empty_idempotency_key() -> None:
    """MintRequest rejects empty idempotency_key."""
    loader = CatalogLoader(CATALOG_ROOT, SafeYamlParser())
    registry = loader.load()
    role = registry.get("planner")

    with pytest.raises(ValueError, match="idempotency_key"):
        MintRequest(
            idempotency_key="",
            role=role.manifest,
            task={},
            grants={},
            budget_tokens=10_000,
        )


def test_mint_request_rejects_zero_budget() -> None:
    """MintRequest rejects zero or negative budget."""
    loader = CatalogLoader(CATALOG_ROOT, SafeYamlParser())
    registry = loader.load()
    role = registry.get("planner")

    with pytest.raises(ValueError, match="budget_tokens"):
        MintRequest(
            idempotency_key="test-1",
            role=role.manifest,
            task={},
            grants={},
            budget_tokens=0,
        )


def test_mint_is_idempotent() -> None:
    """A second mint with the same idempotency key returns the same lease."""
    loader = CatalogLoader(CATALOG_ROOT, SafeYamlParser())
    registry = loader.load()
    role = registry.get("builder")

    factory = AgentFactory()
    request = MintRequest(
        idempotency_key="idem-1",
        role=role.manifest,
        task={"owner": {}},
        grants={},
        budget_tokens=50_000,
    )

    lease1 = factory.mint(request)
    lease2 = factory.mint(request)

    assert lease1.lease_id == lease2.lease_id
    assert lease1.idempotency_key == lease2.idempotency_key


def test_discharge_yields_receipt() -> None:
    """Discharge always yields a receipt."""
    loader = CatalogLoader(CATALOG_ROOT, SafeYamlParser())
    registry = loader.load()
    role = registry.get("reviewer")

    factory = AgentFactory()
    request = MintRequest(
        idempotency_key="test-2",
        role=role.manifest,
        task={},
        grants={},
        budget_tokens=20_000,
    )
    lease = factory.mint(request)

    receipt = factory.discharge(lease.lease_id, status="completed", tokens_used=15_000)

    assert receipt.lease_id == lease.lease_id
    assert receipt.status == "completed"
    assert receipt.tokens_used == 15_000


def test_discharge_on_cancel_yields_receipt() -> None:
    """Discharge on cancel yields a receipt."""
    loader = CatalogLoader(CATALOG_ROOT, SafeYamlParser())
    registry = loader.load()
    role = registry.get("verifier")

    factory = AgentFactory()
    request = MintRequest(
        idempotency_key="test-3",
        role=role.manifest,
        task={},
        grants={},
        budget_tokens=30_000,
    )
    lease = factory.mint(request)

    receipt = factory.discharge(lease.lease_id, status="cancelled")

    assert receipt.status == "cancelled"
    assert isinstance(receipt, Receipt)


def test_discharge_on_failure_includes_error() -> None:
    """Discharge on failure includes the error message."""
    loader = CatalogLoader(CATALOG_ROOT, SafeYamlParser())
    registry = loader.load()
    role = registry.get("planner")

    factory = AgentFactory()
    request = MintRequest(
        idempotency_key="test-4",
        role=role.manifest,
        task={},
        grants={},
        budget_tokens=25_000,
    )
    lease = factory.mint(request)

    receipt = factory.discharge(
        lease.lease_id,
        status="failed",
        tokens_used=10_000,
        error="role refused the task",
    )

    assert receipt.status == "failed"
    assert receipt.error == "role refused the task"


def test_discharge_on_nonexistent_lease_fails() -> None:
    """Discharge on a nonexistent lease raises InvalidInput."""
    factory = AgentFactory()

    with pytest.raises(InvalidInput, match="lease not found"):
        factory.discharge("nonexistent-lease-id")


def test_discharge_twice_fails() -> None:
    """Discharging the same lease twice raises InvalidInput."""
    loader = CatalogLoader(CATALOG_ROOT, SafeYamlParser())
    registry = loader.load()
    role = registry.get("builder")

    factory = AgentFactory()
    request = MintRequest(
        idempotency_key="test-5",
        role=role.manifest,
        task={},
        grants={},
        budget_tokens=40_000,
    )
    lease = factory.mint(request)

    factory.discharge(lease.lease_id)

    with pytest.raises(InvalidInput, match="already discharged"):
        factory.discharge(lease.lease_id)
