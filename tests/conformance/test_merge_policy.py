"""Agents merge green, reviewed PRs (#61): no instruction file may forbid it again."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTRUCTION_FILES = ("AGENTS.md", "README.md", "docs/design/v1.md")
FORBIDS_AGENT_MERGE = re.compile(
    r"(owner merges|agents? (never|must not|may not|cannot) merges?)", re.IGNORECASE
)


def forbidden_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if FORBIDS_AGENT_MERGE.search(line)]


def test_no_instruction_file_forbids_agents_from_merging() -> None:
    for name in INSTRUCTION_FILES:
        path = ROOT / name
        if path.exists():
            assert forbidden_lines(path.read_text(encoding="utf-8")) == [], name


def test_agents_md_states_that_agents_merge_on_a_green_gate() -> None:
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "agent merges its own PR" in text and "PR Gate" in text


def test_the_rule_catches_the_old_wording() -> None:  # negative control
    old = "The owner merges; an agent never merges and never bypasses the gate."
    assert forbidden_lines(old)
