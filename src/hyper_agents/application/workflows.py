"""Load a workflow file safely (P04-1): ``safe_load`` only, no symlinks."""

from pathlib import Path

from hyper_agents.application.ports import ManifestParserPort
from hyper_agents.domain.errors import PolicyDenied
from hyper_agents.domain.workflow import Workflow, parse_workflow


def load_workflow(path: Path, parser: ManifestParserPort) -> Workflow:
    if path.is_symlink():
        raise PolicyDenied(f"workflow file is a symlink: {path.name}")
    return parse_workflow(parser.parse(path.read_text()))
