"""In-memory ``ForgePort`` for tests and offline runs. Records every write."""

from dataclasses import dataclass, field, replace
from datetime import datetime

from hyper_agents.domain.forge import Access, Change, Item


@dataclass
class FakeForge:
    merged: list[Change] = field(default_factory=lambda: [])
    open: list[Change] = field(default_factory=lambda: [])
    items: dict[int, Item] = field(default_factory=lambda: {})
    reopened: dict[int, datetime] = field(default_factory=lambda: {})
    live_settings: dict[str, object] = field(default_factory=lambda: {})
    files: dict[str, str] = field(default_factory=lambda: {})
    writes: list[tuple[str, object]] = field(default_factory=lambda: [])
    access_by_repo: dict[str, Access] = field(default_factory=lambda: {})
    tracked: dict[str, tuple[int, str, str, bool]] = field(default_factory=lambda: {})
    notes: dict[int, list[str]] = field(default_factory=lambda: {})

    def merged_changes_since(self, repo: str, since: datetime) -> list[Change]:
        return [c for c in self.merged if c.merged_at is not None and c.merged_at >= since]

    def open_changes(self, repo: str) -> list[Change]:
        return list(self.open)

    def item(self, repo: str, number: int) -> Item | None:
        return self.items.get(number)

    def reopened_since(self, repo: str, number: int, since: datetime) -> bool:
        when = self.reopened.get(number)
        return when is not None and when > since

    def close_item(self, repo: str, number: int, note: str) -> None:
        self.items[number] = Item(number=number, open=False)
        self.writes.append(("close", number))

    def set_label(self, repo: str, number: int, label: str, present: bool) -> None:
        self.open = [
            replace(
                c,
                labels=tuple(sorted({*c.labels, label} if present else set(c.labels) - {label})),
            )
            if c.number == number
            else c
            for c in self.open
        ]
        self.writes.append(("label" if present else "unlabel", number))

    def upsert_note(self, repo: str, number: int, marker: str, text: str) -> None:
        held = self.notes.setdefault(number, [])
        if marker not in held:
            held.append(marker)
        self.writes.append(("note", number))

    def update_change_branch(self, repo: str, number: int) -> None:
        self.writes.append(("update_branch", number))

    def settings(self, repo: str) -> dict[str, object]:
        return dict(self.live_settings)

    def set_settings(self, repo: str, changes: dict[str, object]) -> None:
        self.live_settings.update(changes)
        self.writes.append(("settings", dict(changes)))

    def read_file(self, repo: str, ref: str, path: str) -> str:
        return self.files[f"{repo}@{ref}:{path}"]

    def access(self, repo: str) -> Access:
        return self.access_by_repo.get(repo, Access(False, False))

    def ensure_item(self, repo: str, marker: str, title: str, body: str, open: bool) -> int:
        number = self.tracked.get(marker, (900 + len(self.tracked), "", "", True))[0]
        self.tracked[marker] = (number, title, body, open)
        self.writes.append(("ensure_item", number))
        return number

    def note_exists(self, repo: str, number: int, marker: str) -> bool:
        return marker in self.notes.get(number, [])

    def dispatch(self, repo: str, workflow: str, ref: str, inputs: dict[str, str]) -> None:
        self.writes.append(("dispatch", dict(inputs)))
