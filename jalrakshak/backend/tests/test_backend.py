"""Run from the backend/ folder:  python -m unittest discover -s tests -v
Needs the compiled scheduler: set SCHEDULER_BIN to its path (tests that need it are skipped otherwise)."""
import os
import shutil
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app import service
from app.errors import ConflictError, NotFoundError
from app.scheduler_client import (SchedulerError, SchedulerUnavailable, build_scheduler_input,
                                  run_scheduler)

BIN = os.environ.get("SCHEDULER_BIN") or shutil.which("jalrakshak-scheduler")
needs_bin = unittest.skipUnless(BIN and os.path.exists(BIN), "scheduler binary not available")

T0 = datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc)

# Same scenario as db/seed.sql: 8 requests, 80,000 L requested.
SEED = [(1, 5, "industry", 2, 3, 20000, 0), (2, 3, "domestic", 4, 1, 12000, 5),
        (3, 1, "hospital", 5, 2, 10000, 10), (4, 7, "commercial", 2, 2, 8000, 12),
        (5, 2, "school", 4, 1, 6000, 15), (6, 8, "park", 1, 2, 4000, 20),
        (7, 4, "domestic", 4, 2, 15000, 25), (8, 6, "industry", 2, 3, 5000, 30)]


def pending_rows():
    rows = []
    for rid, uid, utype, prio, zone, litres, arr in SEED:
        rows.append({"request_id": rid, "user_id": uid, "user_name": f"user{uid}", "user_type": utype,
                     "priority": prio, "zone_id": zone, "zone_population": 50000, "urgency": 3,
                     "requested_litres": Decimal(litres), "arrival_time": T0 + timedelta(minutes=arr),
                     "deadline": T0 + timedelta(minutes=arr + 240)})
    return rows


class FakeCursor:
    def __init__(self, conn):
        self.conn, self.rows = conn, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        self.conn.log.append((" ".join(sql.split()), params))
        self.rows = []
        for key, rows in self.conn.handlers:
            if key in sql:
                self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self, handlers):
        self.handlers, self.log, self.committed, self.rolled_back = handlers, [], 0, 0

    def cursor(self):
        return FakeCursor(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *a):
        if exc_type:
            self.rolled_back += 1
        else:
            self.committed += 1
        return False


def make_conn(available=50000, pending=None):
    return FakeConn([
        ("FROM reservoirs", [{"reservoir_id": 1, "available_litres": Decimal(available)}]),
        ("FROM water_requests r", pending_rows() if pending is None else pending),
        ("GROUP BY u.zone_id", []),
        ("INSERT INTO scheduler_runs", [{"run_id": 7}]),
    ])


class SchedulerClientTests(unittest.TestCase):
    def test_build_input_converts_times_and_types(self):
        p = build_scheduler_input("fcfs", Decimal("50000"), pending_rows(), {2: 0.4})
        self.assertEqual(p["available_litres"], 50000.0)
        self.assertEqual(len(p["requests"]), 8)
        r3 = p["requests"][2]
        self.assertEqual((r3["arrival_time"], r3["deadline"]), (10.0, 250.0))
        self.assertIsInstance(r3["requested_litres"], float)
        self.assertEqual(r3["zone_received_ratio"], 0.4)       # zone 2
        self.assertEqual(p["requests"][1]["zone_received_ratio"], 0.0)  # zone 1: no history
        self.assertEqual(p["requests"][0]["arrival_time"], 0.0)  # earliest arrival is t=0

    @needs_bin
    def test_real_scheduler_matches_hand_calculated_numbers(self):
        out = run_scheduler(build_scheduler_input("fcfs", 50000, pending_rows()), binary=BIN)
        self.assertEqual(out["metrics"]["unserved_count"], 4)
        self.assertAlmostEqual(out["metrics"]["avg_wait_time"], 40.25)
        self.assertEqual(out["remaining_litres"], 0)

    @needs_bin
    def test_unimplemented_algorithm_is_scheduler_error(self):
        with self.assertRaises(SchedulerError):
            run_scheduler(build_scheduler_input("priority", 50000, pending_rows()), binary=BIN)

    def test_missing_binary_is_unavailable(self):
        with self.assertRaises(SchedulerUnavailable):
            run_scheduler({"algorithm": "fcfs"}, binary="/no/such/binary")


@needs_bin
class ServiceTests(unittest.TestCase):
    def setUp(self):
        os.environ["SCHEDULER_BIN"] = BIN
        from app import config
        config.SCHEDULER_BIN = BIN

    def test_commit_locks_first_then_writes_everything(self):
        conn = make_conn()
        res = service.commit_allocation(conn, 1, "fcfs")
        sqls = [s for s, _ in conn.log]
        self.assertIn("FOR UPDATE", sqls[0])                       # reservoir locked first
        self.assertIn("FROM reservoirs", sqls[0])
        self.assertEqual(res["run_id"], 7)
        self.assertEqual(conn.committed, 1)
        self.assertEqual(conn.rolled_back, 0)
        allocs = [p for s, p in conn.log if s.startswith("INSERT INTO allocations")]
        self.assertEqual(len(allocs), 8)                           # every request gets a row
        statuses = {p[1]: p[0] for s, p in conn.log if s.startswith("UPDATE water_requests")}
        self.assertEqual(statuses[3], "approved")                  # hospital served
        self.assertEqual(statuses[5], "rejected")
        update = [p for s, p in conn.log if s.startswith("UPDATE reservoirs")][0]
        self.assertEqual(update, (50000.0, 1))                     # decremented by total allocated

    def test_commit_with_no_pending_conflicts(self):
        with self.assertRaises(ConflictError):
            service.commit_allocation(make_conn(pending=[]), 1, "fcfs")

    def test_commit_unknown_reservoir(self):
        conn = FakeConn([("FROM reservoirs", [])])
        with self.assertRaises(NotFoundError):
            service.commit_allocation(conn, 99, "fcfs")
        self.assertEqual(conn.rolled_back, 1)

    def test_failed_scheduler_rolls_back_without_writes(self):
        conn = make_conn()
        with self.assertRaises(SchedulerError):
            service.commit_allocation(conn, 1, "water_aware")      # not implemented yet
        self.assertEqual(conn.rolled_back, 1)
        self.assertFalse(any(s.startswith(("INSERT", "UPDATE")) for s, _ in conn.log))

    def test_simulate_does_not_write_and_reports_errors_per_algorithm(self):
        conn = make_conn()
        out = service.simulate(conn, 1, ["fcfs", "priority"])
        self.assertEqual(out["pending_count"], 8)
        self.assertEqual(out["results"][0]["algorithm"], "fcfs")
        self.assertIn("error", out["results"][1])
        self.assertFalse(any(s.startswith(("INSERT", "UPDATE")) for s, _ in conn.log))
        self.assertEqual(conn.committed + conn.rolled_back, 0)

    def test_list_requests_rejects_bad_status(self):
        with self.assertRaises(ValueError):
            service.list_requests(make_conn(), "bogus")


if __name__ == "__main__":
    unittest.main()
