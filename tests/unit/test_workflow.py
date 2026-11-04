"""The workflow state machine is strict at load and refuses illegal moves (P04-1)."""

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from hyper_agents.adapters.yaml_parser import SafeYamlParser
from hyper_agents.application.workflows import load_workflow
from hyper_agents.domain.errors import (
    ErrorCategory,
    HyperAgentsError,
    InvalidInput,
    PolicyDenied,
)
from hyper_agents.domain.workflow import Slot, advance, parse_workflow, start

DEFAULT = Path(__file__).resolve().parents[2] / "workflows" / "default.yaml"


def raw() -> dict[str, Any]:
    return yaml.safe_load(DEFAULT.read_text())


def broken(mutate: Callable[[dict[str, Any]], object]) -> dict[str, Any]:
    data = copy.deepcopy(raw())
    mutate(data)
    return data


def test_the_default_workflow_loads_and_names_every_slot() -> None:
    workflow = load_workflow(DEFAULT, SafeYamlParser())
    assert workflow.initial == "planning"
    assert workflow.slots() == frozenset(Slot)


def test_the_happy_path_reaches_done() -> None:
    workflow = parse_workflow(raw())
    position = start(workflow)
    for event in (
        "plan.accepted",
        "build.completed",
        "tests.passed",
        "review.approved",
        "verify.passed",
        "approval.granted",
    ):
        position = advance(workflow, position, event)
    assert position.state == "done"


def test_every_working_state_can_cancel_and_fail() -> None:
    workflow = parse_workflow(raw())
    working = [s.name for s in workflow.states if not s.terminal]
    edges = {(t.from_, t.event, t.to) for t in workflow.transitions}
    for name in working:
        assert (name, "run.cancel", "cancelled") in edges
        assert (name, "run.fail", "failed") in edges


def test_an_illegal_transition_is_denied_and_changes_nothing() -> None:
    workflow = parse_workflow(raw())
    position = start(workflow)
    with pytest.raises(PolicyDenied, match="no transition from 'planning'"):
        advance(workflow, position, "approval.granted")
    assert position.state == "planning" and position.visits == {"planning": 1}


def test_a_terminal_state_accepts_nothing() -> None:
    workflow = parse_workflow(raw())
    done = advance(workflow, start(workflow), "run.cancel")
    with pytest.raises(PolicyDenied):
        advance(workflow, done, "plan.accepted")


def test_a_review_loop_is_bounded_by_max_visits() -> None:
    workflow = parse_workflow(raw())
    position = advance(workflow, start(workflow), "plan.accepted")  # building: 1
    limit = workflow.state("building").max_visits
    assert limit is not None
    with pytest.raises(HyperAgentsError) as caught:
        for _ in range(limit + 1):
            position = advance(workflow, position, "build.completed")
            position = advance(workflow, position, "tests.failed")
    assert caught.value.category is ErrorCategory.BUDGET_EXHAUSTED
    assert caught.value.retryable is False


Mutation = Callable[[dict[str, Any]], object]
MALFORMED: list[tuple[str, Mutation, str]] = [
    ("unknown field", lambda d: d.update(extra=1), "Extra inputs"),
    (
        "unknown state",
        lambda d: d["transitions"].append(
            {"from": "planning", "event": "plan.skip", "to": "nowhere"}
        ),
        "unknown state",
    ),
    (
        "duplicate edge",
        lambda d: d["transitions"].append(
            {"from": "planning", "event": "plan.accepted", "to": "failed"}
        ),
        "duplicate transition",
    ),
    (
        "edge out of a terminal",
        lambda d: d["transitions"].append(
            {"from": "done", "event": "run.reopen", "to": "planning"}
        ),
        "terminal state",
    ),
    (
        "unreachable state",
        lambda d: d["states"].append({"name": "orphan", "slot": "build", "max_visits": 1}),
        "unreachable",
    ),
    ("unbounded state", lambda d: d["states"][1].pop("max_visits"), "max_visits"),
    ("bad event name", lambda d: d["transitions"][0].update(event="Accept"), "pattern"),
    ("unknown slot", lambda d: d["states"][0].update(slot="deploy"), "Input should be"),
    ("terminal initial", lambda d: d.update(initial="done"), "initial state"),
]


@pytest.mark.parametrize(("label", "mutate", "message"), MALFORMED)
def test_a_malformed_workflow_is_invalid_input(label: str, mutate: Mutation, message: str) -> None:
    with pytest.raises(InvalidInput, match=message):
        parse_workflow(broken(mutate))


def test_a_state_that_can_never_finish_is_refused() -> None:
    def trap(d: dict[str, Any]) -> None:
        d["states"].append({"name": "trap", "slot": "build", "max_visits": 1})
        d["transitions"].append({"from": "planning", "event": "plan.trap", "to": "trap"})

    with pytest.raises(InvalidInput, match="can never finish"):
        parse_workflow(broken(trap))


def test_a_tagged_yaml_workflow_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "evil.yaml"
    path.write_text("id: !!python/object/apply:os.system ['echo hi']\n")
    with pytest.raises(PolicyDenied):
        load_workflow(path, SafeYamlParser())


def test_a_symlinked_workflow_is_refused(tmp_path: Path) -> None:
    link = tmp_path / "default.yaml"
    link.symlink_to(DEFAULT)
    with pytest.raises(PolicyDenied, match="symlink"):
        load_workflow(link, SafeYamlParser())
