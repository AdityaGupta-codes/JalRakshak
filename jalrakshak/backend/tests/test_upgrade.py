"""A database created by an older version (v0.2) must be upgraded automatically and then work fully.

This is the situation after updating the code while Docker keeps the old database volume: Docker never
re-runs schema.sql on an existing volume, so the backend runs db/migrate.sql at start-up instead.
"""
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from app import config, migrations, service
from tests import pgtest

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BIN = os.environ.get("SCHEDULER_BIN") or shutil.which("jalrakshak-scheduler")
READY = pgtest.available() and BIN and os.path.exists(BIN)


def load_old_database():
    """Wipe the test database and build it exactly as v0.2 did (old schema + old seed)."""
    pgtest.reset_database()  # drops everything the new schema knows about
    conn = pgtest.open_conn()
    with conn:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS schema_info, model_evaluations CASCADE")
            cur.execute("DROP FUNCTION IF EXISTS load_sample_requests(INTEGER)")
    conn.close()
    if pgtest.DSN and pgtest.psycopg2:
        c = pgtest.psycopg2.connect(pgtest.DSN)
        c.autocommit = True
        with c.cursor() as cur:
            cur.execute((FIXTURES / "schema_v0_2.sql").read_text())
            cur.execute((FIXTURES / "seed_v0_2.sql").read_text())
        c.close()
    else:
        for f in ("schema_v0_2.sql", "seed_v0_2.sql"):
            subprocess.run(["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-f", str(FIXTURES / f), pgtest.PSQL_INFO],
                           check=True, capture_output=True, text=True)


@unittest.skipUnless(READY, "needs a test database and the compiled scheduler")
class UpgradeTests(unittest.TestCase):
    def setUp(self):
        config.SCHEDULER_BIN = BIN
        load_old_database()
        self.conn = pgtest.open_conn()
        self.addCleanup(self.conn.close)

    def test_old_database_is_detected_then_upgraded_without_losing_data(self):
        missing = migrations.check(self.conn)
        self.assertIn("table model_evaluations", missing)
        self.assertIn("function load_sample_requests()", missing)
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM water_requests")
            before = cur.fetchone()["n"]
        self.assertTrue(migrations.apply(self.conn))
        self.assertEqual(migrations.check(self.conn), [])
        self.assertTrue(migrations.apply(self.conn))  # safe to run again
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM water_requests")
            self.assertEqual(cur.fetchone()["n"], before)  # nothing deleted
            cur.execute("SELECT version FROM schema_info")
            self.assertEqual(cur.fetchone()["version"], "0.5")

    def test_everything_works_after_the_upgrade(self):
        migrations.apply(self.conn)
        sim = service.simulate(self.conn, 1, config.ALL_ALGORITHMS)
        self.assertEqual(sim["pending_count"], 8)
        service.commit_allocation(self.conn, 1, "water_aware")
        st = service.reset_demo(self.conn, 1, 50000)  # needs the new SQL function
        self.assertEqual((st["pending_count"], st["pending_demand_litres"]), (8, 80000))
        self.assertEqual(service.get_forecast(self.conn)["available"], False)
        self.assertTrue(any(a["alert_type"] == "low_water" for a in service.list_alerts(self.conn)))
        service.update_reservoir(self.conn, 1, capacity_litres=250000)
        service.create_zone(self.conn, "East Zone", 1000)

    def test_ml_pipeline_upgrades_an_old_database_by_itself(self):
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ml"))
            from jalml import pipeline
        except ImportError:
            self.skipTest("ml requirements not installed")
        pipeline.run(self.conn, out=None, days=200, log=lambda *_: None)
        self.assertEqual(migrations.check(self.conn), [])
        self.assertTrue(service.get_forecast(self.conn)["available"])

    def test_api_upgrades_on_first_request_and_health_explains_problems(self):
        try:
            from fastapi.testclient import TestClient
            from tests.test_api import _import_app
        except ImportError:
            self.skipTest("FastAPI not installed")
        cfg, db, main = _import_app()
        cfg.SCHEDULER_BIN = BIN

        def old_conn_without_upgrade():
            yield self.conn

        main.app.dependency_overrides[db.get_conn] = old_conn_without_upgrade
        try:
            client = TestClient(main.app)
            r = client.get("/health")  # old database, not upgraded: clear 503 with the fix
            self.assertEqual(r.status_code, 503)
            self.assertIn("model_evaluations", r.json()["detail"])
            self.assertIn("docker compose down -v", r.json()["detail"])

            def real_dependency():  # what get_conn does: upgrade once, then serve
                db._schema_ready = False
                db.ensure_schema(self.conn)
                yield self.conn

            main.app.dependency_overrides[db.get_conn] = real_dependency
            self.assertEqual(client.get("/health").json()["schema"], "up to date")
            self.assertEqual(client.post("/demo/reset", json={}).status_code, 200)
            self.assertEqual(client.post("/allocate/commit", json={"algorithm": "water_aware"}).status_code, 200)
        finally:
            main.app.dependency_overrides.clear()

    def test_database_without_any_tables_gives_clear_advice(self):
        with self.conn:
            with self.conn.cursor() as cur:
                cur.execute("DROP TABLE IF EXISTS alerts, predictions, consumption, allocations, scheduler_runs, "
                            "water_requests, reservoirs, users, zones CASCADE")
        with self.assertRaises(migrations.SchemaProblem) as ctx:
            migrations.apply(self.conn)
        self.assertIn("docker compose down -v", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
