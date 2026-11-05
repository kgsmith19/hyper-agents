"""The managed local hook installer is explicit, reversible, and non-destructive (P06-6)."""

import stat
from pathlib import Path

import pytest

from hyper_agents.interfaces.hook_installer import HookRefused, install, uninstall


def test_install_is_idempotent_and_writes_an_executable_managed_hook(tmp_path: Path) -> None:
    # Mutation caught: a changed marker or command breaks the managed-hook boundary.
    git_dir = tmp_path / ".git"
    (git_dir / "hooks").mkdir(parents=True)

    first = install(git_dir)
    second = install(git_dir)

    assert first.changed is True
    assert second.changed is False
    assert "hyper-agents-managed-hook:v1" in first.path.read_text()
    assert "uv run hyper-agents roles validate" in first.path.read_text()
    assert first.path.stat().st_mode & stat.S_IXUSR


def test_uninstall_removes_only_the_managed_hook(tmp_path: Path) -> None:
    git_dir = tmp_path / ".git"
    (git_dir / "hooks").mkdir(parents=True)
    hook = install(git_dir).path

    removed = uninstall(git_dir)
    repeated = uninstall(git_dir)

    assert removed.changed is True
    assert repeated.changed is False
    assert not hook.exists()


@pytest.mark.parametrize("action", [install, uninstall])
def test_unmanaged_hook_is_never_overwritten_or_deleted(tmp_path: Path, action: object) -> None:
    # Negative control: a user-owned hook must survive both installer operations unchanged.
    git_dir = tmp_path / ".git"
    hooks = git_dir / "hooks"
    hooks.mkdir(parents=True)
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\necho user-hook\n")

    with pytest.raises(HookRefused):
        action(git_dir)  # type: ignore[operator]

    assert hook.read_text() == "#!/bin/sh\necho user-hook\n"
