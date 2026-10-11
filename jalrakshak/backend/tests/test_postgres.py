"""End-to-end tests of the service layer against a REAL PostgreSQL database and the REAL C++ scheduler.

Run from the backend/ folder (see tests/pgtest.py for the environment variables):

    SCHEDULER_BIN=../scheduler/build/jalrakshak-scheduler \
    JAL_TEST_DSN=postgresql://postgres:jal123@localhost:5432/jalrakshak_test \
    python -m unittest tests.test_postgres -v
"""
import os
import shutil
import threading
import unittest
from decimal import Decimal

from app import config, service
from app.errors import ConflictError, InvalidInput, NotFoundError
from tests import pgtest

BIN = os.environ.get("SCHEDULER_BIN") or shutil.which("jalrakshak-scheduler")
READY = pgtest.available() and BIN and os.path.exists(BIN)


@unittest.skipUnless(READY, "needs a test database (JAL_TEST_DSN or JAL_TEST_PSQL) and the compiled scheduler")
class PostgresServiceTests(unittest.TestCase):
    def setUp(self):
        config.SCHEDULER_BIN = BIN
        pgtest.reset_database()
        self.conn = pgtest.open_conn()
        self.addCleanup(self.conn.close)

    def scalar(self, sql, params=()):
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchone().values())[0]

    # ----- reads
    def test_database_is_reachable_and_seeded(self):
        self.assertEqual(self.scalar("SELECT 1 AS ok"), 1)
        self.assertEqual(len(service.list_reservoirs(self.conn)), 1)
        self.assertEqual(len(service.list_users(self.conn)), 8)
        pending = service.list_requests(self.conn, "pending")
        self.assertEqual(len(pending), 8)
        self.assertEqual(len(service.list_requests(self.conn)), 8)
        with self.assertRaises(InvalidInput):
            service.list_requests(self.conn, "bogus")

    def test_status_summary(self):
        st = service.get_status(self.conn, 1)
        self.assertEqual(st["available_litres"], 50000)
        self.assertEqual(st["pending_count"], 8)
        self.assertEqual(st["pending_demand_litres"], 80000)
        self.assertAlmostEqual(st["demand_to_supply_ratio"], 1.6)
        self.assertEqual(st["scarcity"], "severe")
        with self.assertRaises(NotFoundError):
            service.get_status(self.conn, 99)

    # ----- simulate
    def test_simulate_runs_all_four_algorithms_and_changes_nothing(self):
        out = service.simulate(self.conn, 1, config.ALL_ALGORITHMS)
        self.assertEqual(out["pending_count"], 8)
        self.assertEqual([r["algorithm"] for r in out["results"]], config.ALL_ALGORITHMS)
        by_alg = {r["algorithm"]: r for r in out["results"]}
        self.assertEqual(by_alg["fcfs"]["metrics"]["unserved_count"], 4)
        # Default batch window "all": nothing starts before the last sample request arrives (t=30),
        # so every served request waits 30 minutes longer than the hand-calculated 40.25.
        self.assertAlmostEqual(by_alg["fcfs"]["metrics"]["avg_wait_time"], 70.25)
        for r in out["results"]:
            self.assertLessEqual(sum(a["allocated_litres"] for a in r["allocations"]), 50000 + 1e-6)
        self.assertEqual(self.scalar("SELECT available_litres FROM reservoirs"), Decimal("50000.00"))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM water_requests WHERE status = 'pending'"), 8)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM scheduler_runs"), 0)

    # ----- commit
    def test_commit_fcfs_updates_everything_consistently(self):
        res = service.commit_allocation(self.conn, 1, "fcfs")
        self.assertEqual(res["remaining_litres"], 0)
        self.assertEqual(self.scalar("SELECT available_litres FROM reservoirs"), Decimal("0.00"))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM allocations WHERE run_id = %s", (res["run_id"],)), 8)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM water_requests WHERE status = 'pending'"), 0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM water_requests WHERE status = 'approved'"), 4)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM water_requests WHERE status = 'rejected'"), 4)

        runs = service.list_runs(self.conn)
        self.assertEqual(len(runs), 1)
        self.assertTrue(runs[0]["committed"])
        self.assertEqual(runs[0]["metrics"]["unserved_count"], 4)

        detail = service.get_run(self.conn, res["run_id"])
        self.assertEqual(len(detail["allocations"]), 8)
        self.assertEqual(detail["algorithm"], "fcfs")
        with self.assertRaises(NotFoundError):
            service.get_run(self.conn, 9999)

        with self.assertRaises(ConflictError):  # nothing pending any more: water cannot be given out twice
            service.commit_allocation(self.conn, 1, "fcfs")

    def test_commit_every_algorithm_never_over_allocates_in_the_database(self):
        for alg in config.ALL_ALGORITHMS:
            with self.subTest(algorithm=alg):
                pgtest.reset_database()
                conn = pgtest.open_conn()
                try:
                    res = service.commit_allocation(conn, 1, alg)
                    with conn.cursor() as cur:
                        cur.execute("SELECT COALESCE(SUM(allocated_litres), 0) AS used FROM allocations")
                        used = cur.fetchone()["used"]
                        cur.execute("SELECT available_litres FROM reservoirs")
                        left = cur.fetchone()["available_litres"]
                    self.assertEqual(used + left, Decimal("50000.00"))  # litres add up to the cent
                    self.assertGreaterEqual(left, 0)
                    self.assertEqual(len(res["allocations"]), 8)
                finally:
                    conn.close()

    def test_failed_commit_leaves_the_database_untouched(self):
        old = config.SCHEDULER_BIN
        config.SCHEDULER_BIN = "/no/such/scheduler"
        try:
            with self.assertRaises(Exception):
                service.commit_allocation(self.conn, 1, "fcfs")
        finally:
            config.SCHEDULER_BIN = old
        self.assertEqual(self.scalar("SELECT available_litres FROM reservoirs"), Decimal("50000.00"))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM scheduler_runs"), 0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM water_requests WHERE status = 'pending'"), 8)

    def test_fairness_history_is_read_from_committed_runs(self):
        service.commit_allocation(self.conn, 1, "fcfs")
        with self.conn.cursor() as cur:
            ratios = service._zone_ratios(cur)
        self.assertTrue(ratios)
        for ratio in ratios.values():
            self.assertTrue(0.0 <= ratio <= 1.0)

    # ----- concurrency: the point of SELECT ... FOR UPDATE
    def test_concurrent_commits_allocate_the_water_exactly_once(self):
        outcomes, lock = [], threading.Lock()
        barrier = threading.Barrier(4)

        def worker():
            conn = pgtest.open_conn()
            try:
                barrier.wait()
                try:
                    service.commit_allocation(conn, 1, "water_aware")
                    result = "committed"
                except ConflictError:
                    result = "conflict"
                except Exception as exc:  # anything else is a bug
                    result = f"error: {exc!r}"
            finally:
                conn.close()
            with lock:
                outcomes.append(result)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)

        self.assertEqual(sorted(outcomes), ["committed", "conflict", "conflict", "conflict"], outcomes)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM scheduler_runs WHERE committed"), 1)
        used = self.scalar("SELECT COALESCE(SUM(allocated_litres), 0) FROM allocations")
        left = self.scalar("SELECT available_litres FROM reservoirs")
        self.assertEqual(used + left, Decimal("50000.00"))

    # ----- writes
    def test_create_request_then_it_shows_up(self):
        row = service.create_request(self.conn, 1, 1, 5000, 4, 120)
        self.assertEqual(row["status"], "pending")
        self.assertEqual(len(service.list_requests(self.conn, "pending")), 9)
        self.assertEqual(service.simulate(self.conn, 1, ["fcfs"])["pending_count"], 9)

    def test_create_request_rejects_bad_input_with_clear_errors(self):
        with self.assertRaises(NotFoundError):  # user 999 does not exist
            service.create_request(self.conn, 999, 1, 5000, 3, 60)
        with self.assertRaises(InvalidInput):   # deadline before arrival violates a CHECK rule
            service.create_request(self.conn, 1, 1, 5000, 3, -10)
        with self.assertRaises(InvalidInput):   # zero litres violates a CHECK rule
            service.create_request(self.conn, 1, 1, 0, 3, 60)
        self.assertEqual(len(service.list_requests(self.conn)), 8)  # nothing was inserted

    def test_database_itself_refuses_a_negative_water_level(self):
        with self.assertRaises(Exception) as ctx:
            with self.conn:
                with self.conn.cursor() as cur:
                    cur.execute("UPDATE reservoirs SET available_litres = available_litres - 60000")
        self.assertEqual(getattr(ctx.exception, "pgcode", None), "23514")
        self.assertEqual(self.scalar("SELECT available_litres FROM reservoirs"), Decimal("50000.00"))

    # ----- demo reset
    def test_demo_reset_restores_the_starting_state(self):
        service.commit_allocation(self.conn, 1, "priority")
        st = service.reset_demo(self.conn, 1, 50000)
        self.assertEqual(st["available_litres"], 50000)
        self.assertEqual(st["pending_count"], 8)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM scheduler_runs"), 0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM allocations"), 0)
        service.commit_allocation(self.conn, 1, "water_aware")  # and it can be used again

    def test_demo_reset_removes_added_requests_and_restarts_the_clock(self):
        service.create_request(self.conn, 1, 1, 999, 5, 30)
        service.commit_allocation(self.conn, 1, "fcfs")
        st = service.reset_demo(self.conn, 1, 50000)
        self.assertEqual(st["pending_count"], 8)
        self.assertEqual(st["pending_demand_litres"], 80000)  # the hand-added 999 L request is gone
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM water_requests WHERE deadline < now()"), 0)

    def test_demo_reset_rejects_more_than_capacity_and_changes_nothing(self):
        service.commit_allocation(self.conn, 1, "fcfs")
        with self.assertRaises(InvalidInput):
            service.reset_demo(self.conn, 1, 300000)  # capacity is 200,000
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM scheduler_runs"), 1)  # rolled back
        with self.assertRaises(NotFoundError):
            service.reset_demo(self.conn, 99, 1000)


@unittest.skipUnless(READY, "needs a test database (JAL_TEST_DSN or JAL_TEST_PSQL) and the compiled scheduler")
class SettingsTests(unittest.TestCase):
    """Editing the reservoir, zones and requesters (the dashboard's Settings tab)."""

    def setUp(self):
        config.SCHEDULER_BIN = BIN
        pgtest.reset_database()
        self.conn = pgtest.open_conn()
        self.addCleanup(self.conn.close)

    def scalar(self, sql, params=()):
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchone().values())[0]

    # ----- reservoir
    def test_raise_capacity_and_level(self):
        st = service.update_reservoir(self.conn, 1, capacity_litres=300000)
        self.assertEqual((st["capacity_litres"], st["available_litres"]), (300000, 50000))
        st = service.update_reservoir(self.conn, 1, name="Lake Jal", available_litres=120000)
        self.assertEqual((st["name"], st["available_litres"], st["scarcity"]), ("Lake Jal", 120000, "ok"))

    def test_capacity_below_water_is_refused_with_advice_unless_level_lowered_too(self):
        with self.assertRaises(InvalidInput) as ctx:
            service.update_reservoir(self.conn, 1, capacity_litres=40000)
        self.assertIn("Lower the water level", str(ctx.exception))
        self.assertEqual(self.scalar("SELECT capacity_litres FROM reservoirs"), Decimal("200000.00"))
        st = service.update_reservoir(self.conn, 1, capacity_litres=40000, available_litres=40000)
        self.assertEqual((st["capacity_litres"], st["available_litres"]), (40000, 40000))
        with self.assertRaises(InvalidInput):
            service.update_reservoir(self.conn, 1, available_litres=40001)
        with self.assertRaises(InvalidInput):
            service.update_reservoir(self.conn, 1, name="   ")
        with self.assertRaises(NotFoundError):
            service.update_reservoir(self.conn, 9, capacity_litres=1)

    def test_new_capacity_is_used_by_plans_and_reset(self):
        service.update_reservoir(self.conn, 1, capacity_litres=30000, available_litres=30000)
        sim = service.simulate(self.conn, 1, ["water_aware"])
        self.assertLessEqual(sum(a["allocated_litres"] for a in sim["results"][0]["allocations"]), 30000)
        self.assertEqual(service.reset_demo(self.conn, 1)["available_litres"], 30000)  # 50,000 > capacity
        with self.assertRaises(InvalidInput) as ctx:
            service.reset_demo(self.conn, 1, 35000)
        self.assertIn("30,000", str(ctx.exception))
        service.update_reservoir(self.conn, 1, capacity_litres=200000)
        self.assertEqual(service.reset_demo(self.conn, 1)["available_litres"], 50000)
        self.assertEqual(service.reset_demo(self.conn, 1, 90000)["available_litres"], 90000)

    # ----- zones
    def test_zone_create_update_delete(self):
        z = service.create_zone(self.conn, "  East Zone ", 52000)
        self.assertEqual(z["name"], "East Zone")
        self.assertEqual(service.update_zone(self.conn, z["zone_id"], population=55000)["population"], 55000)
        with self.assertRaises(ConflictError):
            service.create_zone(self.conn, "North Zone", 10)  # names are unique
        with self.assertRaises(ConflictError):
            service.update_zone(self.conn, z["zone_id"], name="North Zone")
        with self.assertRaises(InvalidInput):
            service.update_zone(self.conn, z["zone_id"], population=0)
        with self.assertRaises(ConflictError):
            service.delete_zone(self.conn, 1)  # has requesters
        self.assertEqual(service.delete_zone(self.conn, z["zone_id"]), {"deleted": z["zone_id"]})
        with self.assertRaises(NotFoundError):
            service.delete_zone(self.conn, z["zone_id"])
        self.assertEqual(len(service.list_zones(self.conn)), 3)

    # ----- requesters
    def test_user_create_update_delete(self):
        u = service.create_user(self.conn, "Sunrise School", "school", 4, 3)
        self.assertEqual((u["zone_name"], u["priority"]), ("South Zone", 4))
        u = service.update_user(self.conn, u["user_id"], priority=5, zone_id=1)
        self.assertEqual((u["priority"], u["zone_name"]), (5, "North Zone"))
        with self.assertRaises(InvalidInput):
            service.create_user(self.conn, "X", "spaceport", 3, 1)
        with self.assertRaises(InvalidInput):
            service.update_user(self.conn, u["user_id"], priority=9)
        with self.assertRaises(NotFoundError):
            service.create_user(self.conn, "X", "park", 1, 99)
        with self.assertRaises(ConflictError) as ctx:
            service.delete_user(self.conn, 1)  # the hospital has requests on record
        self.assertIn("rename", str(ctx.exception))
        self.assertEqual(service.delete_user(self.conn, u["user_id"]), {"deleted": u["user_id"]})
        with self.assertRaises(NotFoundError):
            service.update_user(self.conn, u["user_id"], name="gone")

    def test_priority_change_affects_the_next_plan(self):
        before = service.simulate(self.conn, 1, ["priority"])["results"][0]["allocations"]
        park = next(a for a in before if a["request_id"] == 6)  # Town Park, priority 1: no water
        self.assertEqual(park["allocated_litres"], 0)
        service.update_user(self.conn, 8, priority=5)  # make the park as important as the hospital
        after = service.simulate(self.conn, 1, ["priority"])["results"][0]["allocations"]
        self.assertEqual(next(a for a in after if a["request_id"] == 6)["allocated_litres"], 4000)

    def test_renamed_sample_requesters_still_reset(self):
        service.update_user(self.conn, 1, name="District Hospital")
        st = service.reset_demo(self.conn, 1)
        self.assertEqual((st["pending_count"], st["pending_demand_litres"]), (8, 80000))


if __name__ == "__main__":
    unittest.main()
