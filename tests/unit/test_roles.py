import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from hyper_agents.domain.roles import Capability, RoleManifest, resolve_inside

SCHEMA_SNAPSHOT = Path(__file__).resolve().parents[2] / "schemas" / "role.schema.json"


def valid(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "reviewer",
        "version": "1.0.0",
        "summary": "Reads a diff and reports findings. Writes nothing.",
        "capabilities": ["read_repo", "review_diff"],
        "tools": ["read_file"],
        "permitted_paths": ["src", "tests"],
        "limits": {"max_turns": 20, "timeout_seconds": 600, "max_tokens": 200_000},
        "approval_thresholds": {"max_files_changed": 0, "max_lines_changed": 0},
        "input_schema": "agents/catalog/reviewer/input.schema.json",
        "output_schema": "agents/catalog/reviewer/output.schema.json",
    }
    base.update(overrides)
    return base


def test_a_well_formed_manifest_is_accepted() -> None:
    manifest = RoleManifest.model_validate(valid())
    assert manifest.capabilities == (Capability.READ_REPO, Capability.REVIEW_DIFF)


def test_unknown_capability_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RoleManifest.model_validate(valid(capabilities=["read_repo", "deploy_to_prod"]))


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RoleManifest.model_validate(valid(inherit_from="builder"))


def test_version_drift_is_rejected() -> None:
    for bad in ("1.0", "v1.0.0", "1.0.0-beta"):
        with pytest.raises(ValidationError):
            RoleManifest.model_validate(valid(version=bad))


@pytest.mark.parametrize("elevated", ["git_push", "publish", "read_secret", "network_unrestricted"])
def test_an_elevated_tool_is_never_granted(elevated: str) -> None:
    with pytest.raises(ValidationError, match="elevated tools are never granted"):
        RoleManifest.model_validate(valid(tools=["read_file", elevated]))


@pytest.mark.parametrize(
    "bad", ["/etc/passwd", "../outside", "src/../../x", "src\\x", "~/keys", "", "src//x"]
)
def test_path_escapes_are_rejected_lexically(bad: str) -> None:
    with pytest.raises(ValidationError):
        RoleManifest.model_validate(valid(permitted_paths=[bad]))


def test_a_symlink_that_leaves_the_root_is_refused_on_disk(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    outside = tmp_path / "secrets"
    outside.mkdir()
    (root / "src" / "link").symlink_to(outside)
    with pytest.raises(PermissionError):
        resolve_inside(root, "src/link")


def test_an_inside_path_resolves_under_the_root(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    assert resolve_inside(tmp_path, "src") == (tmp_path / "src").resolve()


def test_the_exported_schema_matches_the_model() -> None:
    expected = RoleManifest.model_json_schema()
    committed = json.loads(SCHEMA_SNAPSHOT.read_text())
    assert committed == expected, (
        "regenerate with: uv run python -m hyper_agents.domain.roles > schemas/role.schema.json"
    )
