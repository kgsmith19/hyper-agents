"""Explicit, reversible installer for the repository's owned pre-commit hook (P06-6)."""

import argparse
import json
import os
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

_MARKER = "# hyper-agents-managed-hook:v1"
_HOOK_NAME = "pre-commit"
_CONTENT = f"#!/bin/sh\n{_MARKER}\nexec uv run hyper-agents roles validate\n"


class HookRefused(RuntimeError):
    """The requested operation would touch a hook not owned by hyper-agents."""


@dataclass(frozen=True)
class HookResult:
    path: Path
    changed: bool


def install(git_dir: Path) -> HookResult:
    """Install the managed hook, refusing to overwrite any unowned existing hook."""
    hooks = _hooks_dir(git_dir, create=True)
    path = hooks / _HOOK_NAME
    if path.exists():
        _require_managed(path)
        return HookResult(path, changed=False)
    _atomic_write(path, _CONTENT)
    return HookResult(path, changed=True)


def uninstall(git_dir: Path) -> HookResult:
    """Remove only the exact hook that this installer owns."""
    hooks = _hooks_dir(git_dir, create=False)
    path = hooks / _HOOK_NAME
    if not path.exists():
        return HookResult(path, changed=False)
    _require_managed(path)
    path.unlink()
    return HookResult(path, changed=True)


def _hooks_dir(git_dir: Path, *, create: bool) -> Path:
    if not git_dir.is_dir():
        raise HookRefused(f"git directory not found: {git_dir}")
    hooks = git_dir / "hooks"
    if create:
        hooks.mkdir(exist_ok=True)
    return hooks


def _require_managed(path: Path) -> None:
    if not path.is_file() or _MARKER not in path.read_text(encoding="utf-8"):
        raise HookRefused(f"refusing to modify unmanaged hook: {path}")


def _atomic_write(path: Path, content: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{_HOOK_NAME}.", dir=path.parent, text=True)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        temporary_path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        temporary_path.replace(path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    """Install or remove the managed hook only at an explicitly named git directory."""
    parser = argparse.ArgumentParser(prog="hyper-agents-hook")
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument("--git-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = install(args.git_dir) if args.action == "install" else uninstall(args.git_dir)
    except HookRefused as error:
        print(json.dumps({"status": "refused", "error": str(error)}))
        return 2
    print(json.dumps({"status": "ok", "action": args.action, "changed": result.changed}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
