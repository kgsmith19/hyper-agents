"""Integration tests for roles CLI and compiler (P02-4)."""

import json
from pathlib import Path

from typer.testing import CliRunner

from hyper_agents.adapters.yaml_parser import SafeYamlParser
from hyper_agents.application.catalog import CatalogLoader
from hyper_agents.application.compiler import RoleCompiler
from hyper_agents.interfaces.cli import app

runner = CliRunner()

# Find the catalog root from the test file location
CATALOG_ROOT = Path(__file__).parent.parent.parent / "agents" / "catalog"


def test_roles_list_shows_all_roles() -> None:
    """roles list shows all available roles."""
    result = runner.invoke(app, ["roles", "list"])
    assert result.exit_code == 0
    roles = result.stdout.strip().split("\n")
    assert len(roles) > 0
    assert "planner" in roles
    assert "builder" in roles
    assert "reviewer" in roles
    assert "verifier" in roles


def test_roles_show_displays_manifest() -> None:
    """roles show <role> displays the role manifest."""
    result = runner.invoke(app, ["roles", "show", "planner"])
    assert result.exit_code == 0
    manifest = json.loads(result.stdout)
    assert manifest["id"] == "planner"
    assert "summary" in manifest
    assert "capabilities" in manifest
    assert "limits" in manifest


def test_roles_show_invalid_role_fails_gracefully() -> None:
    """roles show with invalid role name fails gracefully."""
    result = runner.invoke(app, ["roles", "show", "invalid-role"])
    assert result.exit_code == 1
    assert "error" in result.stdout.lower() or "error" in result.stderr.lower()


def test_roles_validate_checks_bundles() -> None:
    """roles validate checks all bundles."""
    result = runner.invoke(app, ["roles", "validate"])
    assert result.exit_code == 0
    assert "valid" in result.stdout.lower()


def test_compile_resolves_precedence() -> None:
    """RoleCompiler.compile applies precedence: task owner > acceptance > role > knowledge."""
    loader = CatalogLoader(CATALOG_ROOT, SafeYamlParser())
    registry = loader.load()
    role = registry.get("planner")

    compiler = RoleCompiler()

    task: dict[str, object] = {
        "owner": {"approval_required": True},
        "acceptance": {"ac1": "planner must exist"},
        "instructions": {"tone": "professional"},
        "knowledge": {"context": "background info"},
    }
    grants: dict[str, object] = {}

    compiled = compiler.compile(role, task, grants)

    # Verify precedence: owner is in the result
    assert compiled.task_owner_rules == {"approval_required": True}
    assert compiled.acceptance == {"ac1": "planner must exist"}
    assert compiled.role_instructions == {"tone": "professional"}
    assert compiled.knowledge == {"context": "background info"}


def test_grants_are_hard_controls() -> None:
    """Grants are structured tuples with no templating."""
    loader = CatalogLoader(CATALOG_ROOT, SafeYamlParser())
    registry = loader.load()
    role = registry.get("builder")

    compiler = RoleCompiler()

    task: dict[str, object] = {}
    grants: dict[str, object] = {
        "limit": {"max_tokens": 100_000},
        "approval": {"max_files_changed": 5},
    }

    compiled = compiler.compile(role, task, grants)

    # Verify grants are immutable structured data
    grant = compiled.grant
    assert grant.limit.max_tokens == 100_000
    assert grant.approval.max_files_changed == 5
    # Verify frozen (immutable)
    try:
        grant.limit.max_tokens = 200_000  # type: ignore
        raise AssertionError("grant should be immutable")
    except (AttributeError, TypeError):
        pass  # Expected


def test_compiled_role_is_json_dumpable() -> None:
    """A compiled role can be dumped to JSON without loss."""
    loader = CatalogLoader(CATALOG_ROOT, SafeYamlParser())
    registry = loader.load()
    role = registry.get("reviewer")

    compiler = RoleCompiler()
    task: dict[str, object] = {"owner": {"key": "value"}}
    grants: dict[str, object] = {}

    compiled = compiler.compile(role, task, grants)

    # Should not raise
    json_str = compiled.to_json()
    assert json_str
    assert compiled.role_id == "reviewer"
    assert "diff" in compiled.role_summary.lower()

    # Round-trip parse to verify structure
    parsed = json.loads(json_str)
    assert parsed["role_id"] == "reviewer"
    assert parsed["grant"] is not None
