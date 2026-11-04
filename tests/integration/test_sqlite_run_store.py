"""The SQLite run store under faults (P04-2).

Real files, real connections: simultaneous claims race through separate
connections on one database, a crash is a store dropped without release, and
a restart is a new store on the same file. Time is a controllable clock, so
lease expiry and backoff are exact, never slept.
"""

# Seeded generators make retry jitter reproducible; nothing here is a secret.
# ruff: noqa: S311

import random
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hyper_agents.adapters.sqlite_run_store import SqliteRunStore, full_jitter
from hyper_agents.domain.errors import BudgetExhausted, ErrorCategory, StaleState
from hyper_agents.domain.events import EventEnvelope, envelope


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "runs.sqlite"


def store(db: Path, clock: Clock, seed: int = 7) -> SqliteRunStore:
    return SqliteRunStore(db, clock=clock, rng=random.Random(seed))


def event(run: str, sequence: int, key: str | None = None) -> EventEnvelope:
    return envelope(
        run_id=run,
        sequence=sequence,
        type="run.step",
        occurred_at=datetime(2026, 11, 4, tzinfo=UTC),
        idempotency_key=key or f"{run}-{sequence}",
        payload={"n": sequence},
    )


def test_the_store_runs_in_wal_mode(db: Path, clock: Clock) -> None:
    store(db, clock).close()
    with sqlite3.connect(db) as raw:
        assert raw.execute("pragma journal_mode").fetchone()[0] == "wal"


def test_events_are_append_only_in_sequence(db: Path, clock: Clock) -> None:
    s = store(db, clock)
    assert s.append(event("r1", 0)) and s.append(event("r1", 1))
    assert [e.sequence for e in s.events("r1")] == [0, 1]
    assert [e.sequence for e in s.events("r1", after=0)] == [1]
    with pytest.raises(StaleState, match="expects sequence 2, got 5"):
        s.append(event("r1", 5))  # a gap
    with pytest.raises(StaleState, match="expects sequence 2, got 1"):
        s.append(event("r1", 1, key="other"))  # reuse with a new key


def test_a_duplicate_delivery_is_refused_quietly(db: Path, clock: Clock) -> None:
    s = store(db, clock)
    assert s.append(event("r1", 0)) is True
    assert s.append(event("r1", 0)) is False  # same idempotency key: at-least-once delivery
    assert len(s.events("r1")) == 1


def test_simultaneous_claims_have_exactly_one_winner(db: Path, clock: Clock) -> None:
    store(db, clock).close()  # create the schema once
    barrier = threading.Barrier(8)
    won: list[str] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def contend(name: str) -> None:
        mine = store(db, clock)
        barrier.wait()
        try:
            lease = mine.claim("r1", name, ttl=30)
        except BaseException as error:  # a thread's exception would not fail the test
            with lock:
                errors.append(error)
            return
        finally:
            mine.close()
        if lease is not None:
            with lock:
                won.append(name)

    threads = [threading.Thread(target=contend, args=(f"w{n}",)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []  # every loser got a clean "no", never a lock error
    assert len(won) == 1


def test_a_lost_lease_is_fenced_out(db: Path, clock: Clock) -> None:
    s = store(db, clock)
    old = s.claim("r1", "a", ttl=10)
    assert old is not None
    assert s.claim("r1", "b", ttl=10) is None  # still held
    clock.now += 11  # a's heartbeat stopped
    new = s.claim("r1", "b", ttl=10)
    assert new is not None and new.token > old.token
    with pytest.raises(StaleState):
        s.heartbeat(old, ttl=10)
    with pytest.raises(StaleState):
        s.append(event("r1", 0), lease=old)
    assert s.append(event("r1", 0), lease=new)
    with pytest.raises(StaleState):
        s.checkpoint(old, 0, {"state": "building"})


def test_an_expired_lease_cannot_write_even_before_anyone_reclaims(db: Path, clock: Clock) -> None:
    s = store(db, clock)
    lease = s.claim("r1", "a", ttl=5)
    assert lease is not None
    clock.now += 6
    with pytest.raises(StaleState, match="expired"):
        s.append(event("r1", 0), lease=lease)


def test_heartbeat_extends_and_release_keeps_fencing(db: Path, clock: Clock) -> None:
    s = store(db, clock)
    lease = s.claim("r1", "a", ttl=5)
    assert lease is not None
    clock.now += 4
    lease = s.heartbeat(lease, ttl=5)
    clock.now += 4  # past the first TTL, inside the extended one
    assert s.claim("r1", "b", ttl=5) is None
    s.release(lease)
    again = s.claim("r1", "b", ttl=5)
    assert again is not None and again.token == lease.token + 1


def test_crash_during_dispatch_recovers_from_checkpoint(db: Path, clock: Clock) -> None:
    first = store(db, clock)
    first.set_budget("r1", tokens=1000, attempts=3)
    lease = first.claim("r1", "worker-1", ttl=30)
    assert lease is not None
    for n in range(3):
        first.append(event("r1", n), lease=lease)
    first.checkpoint(lease, 1, {"state": "testing", "visits": {"testing": 1}})
    first.close()  # crash: no release, no further writes

    restarted = store(db, clock)
    assert restarted.claim("r1", "worker-2", ttl=30) is None  # the dead lease still holds
    clock.now += 31
    lease2 = restarted.claim("r1", "worker-2", ttl=30)
    assert lease2 is not None
    checkpoint = restarted.load_checkpoint("r1")
    assert checkpoint is not None and checkpoint.position["state"] == "testing"
    replay = restarted.events("r1", after=checkpoint.sequence)
    assert [e.sequence for e in replay] == [2]  # recover reads state before acting
    assert restarted.append(event("r1", 2)) is False  # a re-dispatched step is deduplicated
    assert restarted.append(event("r1", 3), lease=lease2)


def test_a_checkpoint_must_name_a_stored_event(db: Path, clock: Clock) -> None:
    s = store(db, clock)
    lease = s.claim("r1", "a", ttl=30)
    assert lease is not None
    with pytest.raises(StaleState, match="unstored sequence"):
        s.checkpoint(lease, 0, {"state": "planning"})


def test_charging_past_the_token_budget_records_nothing(db: Path, clock: Clock) -> None:
    s = store(db, clock)
    s.set_budget("r1", tokens=100, attempts=3)
    lease = s.claim("r1", "a", ttl=30)
    assert lease is not None
    assert s.charge(lease, 60) == 40
    with pytest.raises(BudgetExhausted) as caught:
        s.charge(lease, 41)
    assert caught.value.retryable is False
    assert s.budget("r1").tokens_used == 60


def test_a_retryable_failure_backs_off_and_a_claim_before_then_is_refused(
    db: Path, clock: Clock
) -> None:
    s = store(db, clock)
    s.set_budget("r1", tokens=100, attempts=3)
    lease = s.claim("r1", "a", ttl=30)
    assert lease is not None
    retry_at = s.record_failure(lease, ErrorCategory.PROVIDER_UNAVAILABLE)
    assert retry_at is not None and retry_at >= clock.now
    expected = clock.now + full_jitter(1, base=1.0, cap=300.0, rng=random.Random(7))
    assert abs(retry_at - expected) < 1e-9
    if retry_at > clock.now:
        assert s.claim("r1", "b", ttl=30) is None
    clock.now = retry_at + 0.001
    assert s.claim("r1", "b", ttl=30) is not None


def test_attempts_run_out_and_are_never_rescheduled(db: Path, clock: Clock) -> None:
    s = store(db, clock)
    s.set_budget("r1", tokens=100, attempts=2)
    lease = s.claim("r1", "a", ttl=30)
    assert lease is not None
    retry_at = s.record_failure(lease, ErrorCategory.WORKER_FAILED)
    assert retry_at is not None
    clock.now = retry_at + 1
    lease = s.claim("r1", "a", ttl=30)
    assert lease is not None
    assert s.record_failure(lease, ErrorCategory.WORKER_FAILED) is None
    clock.now += 10_000
    assert s.claim("r1", "a", ttl=30) is None


@pytest.mark.parametrize(
    "category",
    [ErrorCategory.BUDGET_EXHAUSTED, ErrorCategory.POLICY_DENIED, ErrorCategory.STALE_STATE],
)
def test_a_non_retryable_failure_is_never_rescheduled(
    db: Path, clock: Clock, category: ErrorCategory
) -> None:
    s = store(db, clock)
    s.set_budget("r1", tokens=100, attempts=5)
    lease = s.claim("r1", "a", ttl=30)
    assert lease is not None
    assert s.record_failure(lease, category) is None
    clock.now += 1_000_000
    assert s.claim("r1", "a", ttl=30) is None


def test_full_jitter_is_bounded_and_capped() -> None:
    rng = random.Random(1)
    for attempt in range(12):
        delay = full_jitter(attempt, base=1.0, cap=60.0, rng=rng)
        assert 0.0 <= delay <= min(60.0, 2.0**attempt)
