"""The durable run store: SQLite in WAL mode (P04-2).

One file holds everything a restart needs:

- ``events``: the append-only log. A duplicate idempotency key is refused
  quietly (``append`` returns False: delivery is at-least-once), while a
  sequence that is not exactly the next one is ``stale_state``. Nothing ever
  updates or deletes an event.
- ``leases``: one claim per run with a TTL and a fencing token that only ever
  grows. Every write a worker makes on a run (append, heartbeat, checkpoint)
  names its lease, and a lease that has expired or been superseded is
  ``stale_state``, so a worker that lost its claim can never write over the
  one that took it.
- ``budgets``: token and attempt limits. Charging past a limit is
  ``budget_exhausted`` and records nothing. A retryable failure pushes the
  run's ``not_before`` out by full-jitter exponential backoff; a claim before
  then is refused. A non-retryable failure is never rescheduled.
- ``checkpoints``: the workflow position (opaque JSON to this adapter) and
  the last sequence, written under the current lease. Recovery is "claim,
  read the checkpoint, read events after it", never "trust memory".

Every write runs in ``BEGIN IMMEDIATE``, so two processes racing for the same
claim serialize on the database lock and exactly one wins.
"""

import json
import random
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hyper_agents.domain.errors import (
    RETRYABLE,
    BudgetExhausted,
    ErrorCategory,
    PolicyDenied,
    StaleState,
)
from hyper_agents.domain.events import EventEnvelope, read_envelope

_SCHEMA = """
create table if not exists events (
    run_id text not null,
    sequence integer not null,
    idempotency_key text not null unique,
    body text not null,
    primary key (run_id, sequence)
);
create table if not exists leases (
    run_id text primary key,
    holder text not null,
    token integer not null,
    expires_at real not null
);
create table if not exists budgets (
    run_id text primary key,
    tokens_limit integer not null,
    tokens_used integer not null default 0,
    attempts_limit integer not null,
    attempts_used integer not null default 0,
    not_before real not null default 0
);
create table if not exists checkpoints (
    run_id text primary key,
    sequence integer not null,
    position text not null,
    token integer not null
);
"""

Clock = Callable[[], float]


@dataclass(frozen=True)
class RunLease:
    run_id: str
    holder: str
    token: int
    expires_at: float


@dataclass(frozen=True)
class Checkpoint:
    run_id: str
    sequence: int
    position: dict[str, Any]


@dataclass(frozen=True)
class Budget:
    tokens_limit: int
    tokens_used: int
    attempts_limit: int
    attempts_used: int
    not_before: float


def full_jitter(attempt: int, *, base: float, cap: float, rng: random.Random) -> float:
    """AWS-style full jitter: uniform in [0, min(cap, base * 2**attempt)]."""
    return rng.uniform(0.0, min(cap, base * (2**attempt)))


class SqliteRunStore:
    def __init__(
        self,
        path: Path,
        *,
        clock: Clock = time.time,
        rng: random.Random | None = None,
        backoff_base: float = 1.0,
        backoff_cap: float = 300.0,
    ) -> None:
        self._clock = clock
        self._rng = rng or random.Random()  # noqa: S311 -- retry jitter, not a secret
        self._base = backoff_base
        self._cap = backoff_cap
        self._db = sqlite3.connect(path, isolation_level=None, timeout=10.0)
        self._db.row_factory = sqlite3.Row
        mode = self._db.execute("pragma journal_mode=wal").fetchone()[0]
        if mode != "wal":
            raise PolicyDenied(f"run store needs WAL mode, got {mode!r}")
        self._db.execute("pragma synchronous=normal")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    # --- transactions ---------------------------------------------------

    def _write(self, work: Callable[[sqlite3.Connection], Any]) -> Any:
        self._db.execute("begin immediate")
        try:
            result = work(self._db)
        except BaseException:
            self._db.execute("rollback")
            raise
        self._db.execute("commit")
        return result

    def _current(self, db: sqlite3.Connection, lease: RunLease) -> None:
        row = db.execute(
            "select token, expires_at from leases where run_id = ?", (lease.run_id,)
        ).fetchone()
        if row is None or row["token"] != lease.token:
            raise StaleState(f"lease on {lease.run_id!r} was superseded")
        if row["expires_at"] <= self._clock():
            raise StaleState(f"lease on {lease.run_id!r} expired")

    # --- RunStorePort ---------------------------------------------------

    def append(self, event: EventEnvelope, lease: RunLease | None = None) -> bool:
        """Append the run's next event; False if this idempotency key is already stored.

        With a lease, the append is fenced: a lost or superseded lease is stale_state.
        """

        def work(db: sqlite3.Connection) -> bool:
            if lease is not None:
                if lease.run_id != event.run_id:
                    raise StaleState("lease and event name different runs")
                self._current(db, lease)
            if db.execute(
                "select 1 from events where idempotency_key = ?", (event.idempotency_key,)
            ).fetchone():
                return False
            last = db.execute(
                "select max(sequence) from events where run_id = ?", (event.run_id,)
            ).fetchone()[0]
            expected = 0 if last is None else last + 1
            if event.sequence != expected:
                raise StaleState(
                    f"run {event.run_id!r} expects sequence {expected}, got {event.sequence}"
                )
            db.execute(
                "insert into events (run_id, sequence, idempotency_key, body) values (?, ?, ?, ?)",
                (
                    event.run_id,
                    event.sequence,
                    event.idempotency_key,
                    event.model_dump_json(by_alias=True),
                ),
            )
            return True

        return bool(self._write(work))

    def events(self, run_id: str, after: int = -1) -> list[EventEnvelope]:
        rows = self._db.execute(
            "select body from events where run_id = ? and sequence > ? order by sequence",
            (run_id, after),
        ).fetchall()
        return [read_envelope(row["body"]) for row in rows]

    # --- leases ---------------------------------------------------------

    def claim(self, run_id: str, holder: str, ttl: float) -> RunLease | None:
        """Take the run if it is unclaimed or its lease expired, and it is not backing off."""

        def work(db: sqlite3.Connection) -> RunLease | None:
            now = self._clock()
            budget = db.execute(
                "select not_before from budgets where run_id = ?", (run_id,)
            ).fetchone()
            if budget is not None and budget["not_before"] > now:
                return None
            row = db.execute(
                "select token, expires_at from leases where run_id = ?", (run_id,)
            ).fetchone()
            if row is not None and row["expires_at"] > now:
                return None
            token = 1 if row is None else row["token"] + 1
            db.execute(
                "insert into leases (run_id, holder, token, expires_at) values (?, ?, ?, ?) "
                "on conflict (run_id) do update set holder = excluded.holder, "
                "token = excluded.token, expires_at = excluded.expires_at",
                (run_id, holder, token, now + ttl),
            )
            return RunLease(run_id, holder, token, now + ttl)

        result: RunLease | None = self._write(work)
        return result

    def heartbeat(self, lease: RunLease, ttl: float) -> RunLease:
        def work(db: sqlite3.Connection) -> RunLease:
            self._current(db, lease)
            expires = self._clock() + ttl
            db.execute(
                "update leases set expires_at = ? where run_id = ? and token = ?",
                (expires, lease.run_id, lease.token),
            )
            return RunLease(lease.run_id, lease.holder, lease.token, expires)

        result: RunLease = self._write(work)
        return result

    def release(self, lease: RunLease) -> None:
        """Give the claim back. The token is kept, so the next claim still fences this one."""

        def work(db: sqlite3.Connection) -> None:
            self._current(db, lease)
            db.execute(
                "update leases set expires_at = 0 where run_id = ? and token = ?",
                (lease.run_id, lease.token),
            )

        self._write(work)

    # --- budgets and retries --------------------------------------------

    def set_budget(self, run_id: str, *, tokens: int, attempts: int) -> None:
        if tokens <= 0 or attempts <= 0:
            raise ValueError("budget limits must be positive")
        self._write(
            lambda db: db.execute(
                "insert into budgets (run_id, tokens_limit, attempts_limit) values (?, ?, ?) "
                "on conflict (run_id) do update set tokens_limit = excluded.tokens_limit, "
                "attempts_limit = excluded.attempts_limit",
                (run_id, tokens, attempts),
            )
        )

    def budget(self, run_id: str) -> Budget:
        row = self._db.execute("select * from budgets where run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise StaleState(f"run {run_id!r} has no budget")
        return Budget(
            row["tokens_limit"],
            row["tokens_used"],
            row["attempts_limit"],
            row["attempts_used"],
            row["not_before"],
        )

    def charge(self, lease: RunLease, tokens: int) -> int:
        """Spend tokens under the current lease; return what is left."""
        if tokens < 0:
            raise ValueError("cannot charge negative tokens")

        def work(db: sqlite3.Connection) -> int:
            self._current(db, lease)
            budget = self.budget(lease.run_id)
            if budget.tokens_used + tokens > budget.tokens_limit:
                raise BudgetExhausted(
                    f"run {lease.run_id!r} would use {budget.tokens_used + tokens} "
                    f"of {budget.tokens_limit} tokens"
                )
            db.execute(
                "update budgets set tokens_used = tokens_used + ? where run_id = ?",
                (tokens, lease.run_id),
            )
            return budget.tokens_limit - budget.tokens_used - tokens

        result: int = self._write(work)
        return result

    def record_failure(self, lease: RunLease, category: ErrorCategory) -> float | None:
        """Count a failed attempt and give the claim back.

        Returns when the next attempt may start, or None when the run must not
        be retried: the category is not retryable (budget exhaustion, policy,
        stale state...) or the attempt budget is spent.
        """

        def work(db: sqlite3.Connection) -> float | None:
            self._current(db, lease)
            budget = self.budget(lease.run_id)
            attempts = budget.attempts_used + 1
            retry = category in RETRYABLE and attempts < budget.attempts_limit
            now = self._clock()
            not_before = (
                now + full_jitter(attempts, base=self._base, cap=self._cap, rng=self._rng)
                if retry
                else float("inf")
            )
            db.execute(
                "update budgets set attempts_used = ?, not_before = ? where run_id = ?",
                (attempts, not_before, lease.run_id),
            )
            db.execute(
                "update leases set expires_at = 0 where run_id = ? and token = ?",
                (lease.run_id, lease.token),
            )
            return not_before if retry else None

        result: float | None = self._write(work)
        return result

    # --- checkpoints ----------------------------------------------------

    def checkpoint(self, lease: RunLease, sequence: int, position: dict[str, Any]) -> None:
        def work(db: sqlite3.Connection) -> None:
            self._current(db, lease)
            stored = db.execute(
                "select 1 from events where run_id = ? and sequence = ?",
                (lease.run_id, sequence),
            ).fetchone()
            if stored is None:
                raise StaleState(f"checkpoint names unstored sequence {sequence}")
            db.execute(
                "insert into checkpoints (run_id, sequence, position, token) values (?, ?, ?, ?) "
                "on conflict (run_id) do update set sequence = excluded.sequence, "
                "position = excluded.position, token = excluded.token",
                (lease.run_id, sequence, json.dumps(position, sort_keys=True), lease.token),
            )

        self._write(work)

    def load_checkpoint(self, run_id: str) -> Checkpoint | None:
        row = self._db.execute(
            "select sequence, position from checkpoints where run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return Checkpoint(run_id, row["sequence"], json.loads(row["position"]))
