"""The P05 security gate: review and verification cannot be bent (P05).

Real SQLite store, real catalog and workflow, and the real controller. Only the
reviewer and the base worker are scripted, and time is a controllable clock so
a lease can be lost deliberately.
"""

# Seeded generators make retry jitter reproducible; nothing here is a secret.
# ruff: noqa: S311

import random
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

import pytest

from hyper_agents.adapters.sqlite_run_store import SqliteRunStore
from hyper_agents.adapters.yaml_parser import SafeYamlParser
from hyper_agents.application.catalog import CatalogLoader
from hyper_agents.application.controller import Controller, StepOutcome, StepRequest
from hyper_agents.application.review import FindingsLedger, ReadOnlyWorkspace, ReviewService
from hyper_agents.application.slot_worker import ReviewVerifyWorker
from hyper_agents.application.team import Risk, TeamSelector
from hyper_agents.application.verify import CheckResult, VerificationService
from hyper_agents.application.workflows import load_workflow
from hyper_agents.domain.errors import StaleState
from hyper_agents.domain.workflow import Slot

ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 40
OTHER = "b" * 40
CONTRACT = {"criteria": [{"id": "AC1", "claim": "it works", "check": "pytest -q"}]}
HAPPY = {
    "planning": "plan.accepted",
    "building": "build.completed",
    "testing": "tests.passed",
    "awaiting_approval": "approval.granted",
}


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class Base:
    """The non-review slots: plan, build, test and the owner stand-in approve."""

    def perform(self, request: StepRequest) -> object:
        return StepOutcome(HAPPY[request.state], 10, request.head_sha)


class CleanReviewer:
    def review(self, workspace: ReadOnlyWorkspace, head_sha: str) -> tuple[object, int]:
        workspace.read("src/a.py")
        return {"verdict": "approve", "findings": []}, 5


def passing(sha: str) -> list[CheckResult]:
    return [CheckResult("AC1", sha, passed=True, output_digest="d")]


def build(
    tmp_path: Path,
    clock: Clock,
    reviewer: object,
    *,
    source: dict[str, str] | None = None,
    verify_sha: str | None = None,
) -> tuple[Controller, SqliteRunStore]:
    store = SqliteRunStore(tmp_path / "runs.sqlite", clock=clock, rng=random.Random(3))
    registry = CatalogLoader(ROOT / "agents" / "catalog", SafeYamlParser()).load()
    workflow = load_workflow(ROOT / "workflows" / "default.yaml", SafeYamlParser())
    files = source if source is not None else {"src/a.py": "x = 1"}
    worker = ReviewVerifyWorker(
        Base(),
        reviewer=reviewer,  # type: ignore[arg-type]
        workspace=ReadOnlyWorkspace(MappingProxyType(files)),
        review=ReviewService(FindingsLedger()),
        verify=VerificationService(),
        results=lambda sha: passing(verify_sha or sha),
        criteria=("AC1",),
    )
    controller = Controller(
        store,
        workflow,
        TeamSelector(registry, workflow),
        worker,
        lease_ttl=30,
        now=lambda: datetime(2026, 11, 5, tzinfo=UTC),
    )
    return controller, store


def admit(controller: Controller, run: str = "r1") -> None:
    controller.admit(
        run,
        idempotency_key=f"delivery-{run}",
        contract=CONTRACT,
        risk=Risk.R1,
        paths=["src/a.py"],
        head_sha=SHA,
        tokens=1000,
        attempts=1,
    )


def types(store: SqliteRunStore, run: str = "r1") -> list[str]:
    return [event.type for event in store.events(run)]


def run_to_stop(controller: Controller, run: str = "r1") -> str:
    for _ in range(20):
        result = controller.step(run, "w1")
        if result.status != "advanced":
            return f"{result.status}:{result.detail}" if result.detail else result.status
    raise AssertionError("run did not stop")


def test_a_clean_reviewed_and_verified_run_reaches_done(tmp_path: Path) -> None:
    # Negative control for every test below: the wiring itself is correct.
    controller, store = build(tmp_path, Clock(), CleanReviewer())
    admit(controller)
    assert run_to_stop(controller) == "finished"
    assert controller.position("r1").state == "done"
    assert "review.approved" in types(store) and "verify.passed" in types(store)


class MutatingReviewer:
    """Holds the same dict the workspace wraps, and writes through it."""

    def __init__(self, source: dict[str, str]) -> None:
        self.source = source

    def review(self, workspace: ReadOnlyWorkspace, head_sha: str) -> tuple[object, int]:
        self.source["src/a.py"] = "x = 2"
        return {"verdict": "approve", "findings": []}, 1


def test_a_read_only_reviewer_that_mutates_fails_the_run(tmp_path: Path) -> None:
    source = {"src/a.py": "x = 1"}
    controller, store = build(tmp_path, Clock(), MutatingReviewer(source), source=source)
    admit(controller)
    assert run_to_stop(controller) == "failed:policy_denied"
    assert "review.approved" not in types(store)


class ApprovingOverBlocking:
    def review(self, workspace: ReadOnlyWorkspace, head_sha: str) -> tuple[object, int]:
        # A prompt that tells the reviewer to approve despite a blocking finding.
        finding = {"severity": "blocking", "claim": "the lease is never checked"}
        return {"verdict": "approve", "findings": [finding]}, 1


def test_a_prompt_cannot_relax_policy_into_an_approval(tmp_path: Path) -> None:
    controller, store = build(tmp_path, Clock(), ApprovingOverBlocking())
    admit(controller)
    assert run_to_stop(controller) == "failed:policy_denied"
    assert "review.approved" not in types(store)


class LeaseThief:
    """Loses its lease mid-step: the TTL lapses and another holder claims the run."""

    def __init__(self) -> None:
        self.clock: Clock | None = None
        self.store: SqliteRunStore | None = None

    def review(self, workspace: ReadOnlyWorkspace, head_sha: str) -> tuple[object, int]:
        assert self.clock is not None and self.store is not None
        self.clock.now += 31  # past the 30 second TTL
        assert self.store.claim("r1", "intruder", 30) is not None
        return {"verdict": "approve", "findings": []}, 1


def test_a_revoked_lease_cannot_publish_a_review(tmp_path: Path) -> None:
    clock = Clock()
    thief = LeaseThief()
    controller, store = build(tmp_path, clock, thief)
    thief.clock, thief.store = clock, store
    admit(controller)
    for _ in range(3):  # planning, building, testing: the run now waits in reviewing
        assert controller.step("r1", "w1").status == "advanced"
    assert controller.position("r1").state == "reviewing"
    with pytest.raises(StaleState, match=r"superseded|expired"):
        controller.step("r1", "w1")  # the reviewer lost its lease mid-step
    assert "review.approved" not in types(store)
    assert "review.changes_requested" not in types(store)


def test_a_stale_sha_verification_is_never_green(tmp_path: Path) -> None:
    # The checks ran at OTHER while the run is at SHA: the receipt must refuse it.
    controller, store = build(tmp_path, Clock(), CleanReviewer(), verify_sha=OTHER)
    admit(controller)
    assert run_to_stop(controller) == "failed:stale_state"
    assert "verify.passed" not in types(store)
    assert controller.position("r1").state == "failed"
    assert store.events("r1")[-1].payload["category"] == "stale_state"


def test_the_approval_slot_stays_with_the_owner_stand_in(tmp_path: Path) -> None:
    # Negative control on the staffing: a reviewer is never handed the approve slot.
    controller, store = build(tmp_path, Clock(), CleanReviewer())
    admit(controller)
    staff = store.events("r1")[0].payload["staff"]
    assert staff[Slot.REVIEW.value] == "reviewer"
    assert staff[Slot.APPROVE.value] == "owner-standin"
