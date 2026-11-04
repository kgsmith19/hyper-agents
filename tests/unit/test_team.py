"""The TeamSelector raises risk from impact and staffs every slot (P04-1)."""

from pathlib import Path

import pytest

from hyper_agents.adapters.yaml_parser import SafeYamlParser
from hyper_agents.application.catalog import CatalogLoader, RoleRegistry
from hyper_agents.application.team import OWNER, Risk, TeamSelector, effective_risk
from hyper_agents.application.workflows import load_workflow
from hyper_agents.domain.errors import InvalidInput
from hyper_agents.domain.workflow import Slot

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def registry() -> RoleRegistry:
    return CatalogLoader(ROOT / "agents" / "catalog", SafeYamlParser()).load()


@pytest.fixture(scope="module")
def selector(registry: RoleRegistry) -> TeamSelector:
    return TeamSelector(
        registry, load_workflow(ROOT / "workflows" / "default.yaml", SafeYamlParser())
    )


def test_docs_only_stays_at_r0_with_the_stand_in_approving(selector: TeamSelector) -> None:
    team = selector.select(Risk.R0, ["docs/guide.md", "tests/unit/test_x.py"])
    assert team.risk is Risk.R0
    assert team.staff[Slot.BUILD] == "dev"
    assert team.staff[Slot.APPROVE] == "owner-standin"
    assert not team.independent_review and not team.owner_approval


def test_a_source_path_raises_r0_to_r1(selector: TeamSelector) -> None:
    assert selector.select(Risk.R0, ["src/hyper_agents/x.py"]).risk is Risk.R1


def test_a_schema_change_is_at_least_r2_with_independent_review(selector: TeamSelector) -> None:
    team = selector.select(Risk.R1, ["src/x.py"], schema_change=True)
    assert team.risk is Risk.R2
    assert team.independent_review
    assert team.staff[Slot.BUILD] == "senior-dev"
    assert team.staff[Slot.PLAN] == "business-analyst"


@pytest.mark.parametrize(
    "path", [".github/workflows/ci.yml", "agents/catalog/dev/role.yaml", "ops/registry.yaml"]
)
def test_a_control_plane_path_is_r3_and_needs_the_owner(selector: TeamSelector, path: str) -> None:
    team = selector.select(Risk.R0, [path])
    assert team.risk is Risk.R3
    assert team.owner_approval and team.staff[Slot.APPROVE] == OWNER
    assert any(path in reason for reason in team.reasons)


def test_an_authorization_change_is_r3(selector: TeamSelector) -> None:
    assert selector.select(Risk.R0, ["docs/a.md"], auth_change=True).risk is Risk.R3


def test_a_lookalike_path_is_not_the_control_plane(selector: TeamSelector) -> None:
    # Negative control: prefix matching is by path segment, not by string.
    assert selector.select(Risk.R0, ["agentsmith.md", "opsnotes/x.md"]).risk is Risk.R0


def test_impact_never_lowers_a_declared_risk(selector: TeamSelector) -> None:
    assert selector.select(Risk.R3, ["docs/a.md"]).risk is Risk.R3
    for declared in Risk:
        final, _ = effective_risk(declared, [], schema_change=False, auth_change=False)
        assert final is declared


def test_every_slot_is_staffed_at_every_risk(selector: TeamSelector) -> None:
    for risk in Risk:
        assert set(selector.select(risk, []).staff) == set(Slot)


@pytest.mark.parametrize("path", ["../etc/passwd", "/abs/path", "src/../.github/x"])
def test_an_escaping_path_is_refused(selector: TeamSelector, path: str) -> None:
    with pytest.raises(ValueError, match="path must"):
        selector.select(Risk.R0, [path])


def test_a_missing_role_fails_closed(registry: RoleRegistry) -> None:
    workflow = load_workflow(ROOT / "workflows" / "default.yaml", SafeYamlParser())
    without_tester = RoleRegistry({r: registry.get(r) for r in registry.list() if r != "tester"})
    with pytest.raises(InvalidInput, match="unknown role 'tester'"):
        TeamSelector(without_tester, workflow).select(Risk.R1, [])
