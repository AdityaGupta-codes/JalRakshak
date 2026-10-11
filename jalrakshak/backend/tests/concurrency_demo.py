"""Concurrency demo: what SELECT ... FOR UPDATE protects against.

Four clients try to commit an allocation at the same moment, with plenty of water (200,000 L for
80,000 L of requests). It is run twice: with the real code, and with the row locks removed.

Expected result:
  WITH locks    -> exactly one commit wins, the others get a clean "no pending requests" conflict
  WITHOUT locks -> several commits win and the same requests are allocated more than once
                   (so more litres are handed out than were requested)

Run from the backend/ folder (needs the compiled scheduler and a TEST database, see tests/pgtest.py):

    SCHEDULER_BIN=../scheduler/build/jalrakshak-scheduler \
    JAL_TEST_DSN=postgresql://postgres:jal123@localhost:5432/jalrakshak_test \
    python -m tests.concurrency_demo
"""
import os
import sys
import threading
import types

sys.path.insert(0, ".")
from app import config, service  # noqa: E402
from app.errors import ConflictError  # noqa: E402
from tests import pgtest  # noqa: E402

CLIENTS = 4


def without_locks():
    """A copy of app.service whose commit_allocation does not lock anything."""
    src = open(os.path.join(os.path.dirname(service.__file__), "service.py")).read()
    src = src.replace(" FOR UPDATE OF r", "")
    src = src.replace('WHERE reservoir_id = %s FOR UPDATE", (reservoir_id,))', 'WHERE reservoir_id = %s", (reservoir_id,))')
    module = types.ModuleType("app.service_without_locks")
    module.__package__ = "app"
    exec(compile(src, "service_without_locks", "exec"), module.__dict__)
    return module


def trial(label, commit):
    pgtest.reset_database()
    setup = pgtest.open_conn()
    with setup:
        with setup.cursor() as cur:
            cur.execute("UPDATE reservoirs SET available_litres = 200000")
    setup.close()

    results, guard, barrier = [], threading.Lock(), threading.Barrier(CLIENTS)

    def client():
        conn = pgtest.open_conn()
        try:
            barrier.wait()
            try:
                commit(conn, 1, "water_aware")
                outcome = "committed"
            except ConflictError:
                outcome = "conflict (no pending requests)"
            except Exception as exc:
                outcome = "database error " + str(getattr(exc, "pgcode", type(exc).__name__))
        finally:
            conn.close()
        with guard:
            results.append(outcome)

    threads = [threading.Thread(target=client) for _ in range(CLIENTS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)

    check = pgtest.open_conn()
    with check.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM scheduler_runs")
        runs = cur.fetchone()["n"]
        cur.execute("SELECT COALESCE(SUM(allocated_litres), 0) AS used FROM allocations")
        used = cur.fetchone()["used"]
    check.close()
    print(f"\n{label}")
    for r in sorted(results):
        print("   ", r)
    print(f"    runs stored: {runs}   litres allocated: {used}   (requests total 80,000 L)")


if __name__ == "__main__":
    if not pgtest.available():
        sys.exit("set JAL_TEST_DSN (and install psycopg2) or JAL_TEST_PSQL - see tests/pgtest.py")
    config.SCHEDULER_BIN = os.environ.get("SCHEDULER_BIN", config.SCHEDULER_BIN)
    trial("WITH row locks (the real code)", service.commit_allocation)
    trial("WITHOUT row locks (for comparison)", without_locks().commit_allocation)
