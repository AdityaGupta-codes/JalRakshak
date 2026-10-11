"""HTTP-level tests: the real FastAPI app (routes, validation, error codes, admin token) on a real database.

Skipped unless FastAPI (with its test client) is installed and a test database is configured
(see tests/pgtest.py). The ML pipeline test also needs the ml/ requirements (pandas, scikit-learn).
"""
import importlib
import os
import sys
import types
import unittest
from pathlib import Path

from tests import pgtest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None

BIN = os.environ.get("SCHEDULER_BIN")
READY = TestClient is not None and pgtest.available() and BIN and os.path.exists(BIN)


def _import_app():
    """Import app.main; if psycopg2 is not installed (the psql stand-in is used), give it a tiny stub."""
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        stub = types.ModuleType("psycopg2")
        stub.Error = type("Error", (Exception,), {})
        stub.OperationalError = type("OperationalError", (stub.Error,), {})
        stub.connect = lambda *a, **k: (_ for _ in ()).throw(stub.OperationalError("stub"))
        extras = types.ModuleType("psycopg2.extras")
        extras.RealDictCursor = object
        stub.extras = extras
        sys.modules["psycopg2"] = stub
        sys.modules["psycopg2.extras"] = extras
    from app import config, db, main
    return config, db, main


@unittest.skipUnless(READY, "needs FastAPI, the scheduler binary and a test database")
class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config, cls.db, cls.main = _import_app()
        cls.config.SCHEDULER_BIN = BIN

    def setUp(self):
        pgtest.reset_database()
        self.conn = pgtest.open_conn()

        def override():
            yield self.conn

        self.main.app.dependency_overrides[self.db.get_conn] = override
        self.client = TestClient(self.main.app)
        self.config.ADMIN_TOKEN = ""

    def tearDown(self):
        self.main.app.dependency_overrides.clear()
        self.config.ADMIN_TOKEN = ""
        self.conn.close()

    def test_health_status_users_requests(self):
        self.assertEqual(self.client.get("/health").json()["status"], "ok")
        st = self.client.get("/status").json()
        self.assertEqual((st["available_litres"], st["pending_count"], st["scarcity"]), (50000, 8, "severe"))
        self.assertIsNone(st["forecast_today_litres"])  # ML pipeline not run in this test
        self.assertEqual(len(self.client.get("/users").json()), 8)
        self.assertEqual(len(self.client.get("/requests?status=pending").json()), 8)
        self.assertEqual(self.client.get("/requests?status=bogus").status_code, 400)

    def test_create_request_validation_and_errors(self):
        ok = self.client.post("/requests", json={"user_id": 1, "requested_litres": 1500, "urgency": 5})
        self.assertEqual(ok.status_code, 201)
        self.assertEqual(ok.json()["status"], "pending")
        self.assertEqual(self.client.post("/requests", json={"user_id": 1, "requested_litres": -5}).status_code, 422)
        self.assertEqual(self.client.post("/requests", json={"user_id": 99, "requested_litres": 5}).status_code, 404)

    def test_simulate_commit_runs_and_conflict(self):
        sim = self.client.post("/allocate/simulate", json={}).json()
        self.assertEqual([r["algorithm"] for r in sim["results"]], self.config.ALL_ALGORITHMS)
        self.assertIn("deadline_miss_count", sim["results"][0]["metrics"])
        res = self.client.post("/allocate/commit", json={"algorithm": "water_aware"})
        self.assertEqual(res.status_code, 200)
        run_id = res.json()["run_id"]
        self.assertEqual(self.client.get("/status").json()["available_litres"], 0)
        self.assertEqual(len(self.client.get(f"/runs/{run_id}").json()["allocations"]), 8)
        self.assertEqual(self.client.get("/runs/999").status_code, 404)
        self.assertEqual(self.client.post("/allocate/commit", json={"algorithm": "fcfs"}).status_code, 409)
        self.assertEqual(self.client.post("/allocate/commit", json={"algorithm": "magic"}).status_code, 422)
        self.assertEqual(self.client.post("/demo/reset", json={}).json()["pending_count"], 8)

    def test_admin_token_protects_changes_only(self):
        self.config.ADMIN_TOKEN = "s3cret"
        self.assertTrue(self.client.get("/auth").json()["token_required"])
        self.assertEqual(self.client.post("/demo/reset", json={}).status_code, 401)
        self.assertEqual(self.client.post("/allocate/commit", json={}).status_code, 401)
        self.assertEqual(self.client.post("/requests", json={"user_id": 1, "requested_litres": 5}).status_code, 401)
        self.assertEqual(self.client.post("/auth/check", headers={"X-Admin-Token": "wrong"}).status_code, 401)
        good = {"X-Admin-Token": "s3cret"}
        self.assertEqual(self.client.post("/auth/check", headers=good).status_code, 200)
        self.assertEqual(self.client.post("/demo/reset", json={}, headers=good).status_code, 200)
        # reading and comparing stay open
        self.assertEqual(self.client.get("/status").status_code, 200)
        self.assertEqual(self.client.post("/allocate/simulate", json={}).status_code, 200)

    def test_settings_endpoints(self):
        c = self.client
        r = c.patch("/resources/1", json={"capacity_litres": 300000, "available_litres": 100000})
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()["capacity_litres"], r.json()["available_litres"]), (300000, 100000))
        bad = c.patch("/resources/1", json={"capacity_litres": 50000})  # below the 100,000 L stored
        self.assertEqual(bad.status_code, 400)
        self.assertIn("Lower the water level", bad.json()["detail"])
        self.assertEqual(c.patch("/resources/1", json={"capacity_litres": -1}).status_code, 422)
        self.assertEqual(c.patch("/resources/7", json={"name": "x"}).status_code, 404)

        z = c.post("/zones", json={"name": "East Zone", "population": 20000})
        self.assertEqual(z.status_code, 201)
        zid = z.json()["zone_id"]
        self.assertEqual(c.post("/zones", json={"name": "East Zone", "population": 5}).status_code, 409)
        self.assertEqual(c.patch(f"/zones/{zid}", json={"population": 25000}).json()["population"], 25000)
        u = c.post("/users", json={"name": "East Clinic", "user_type": "hospital", "priority": 5, "zone_id": zid})
        self.assertEqual(u.status_code, 201)
        uid = u.json()["user_id"]
        self.assertEqual(c.post("/users", json={"name": "Y", "user_type": "moon", "priority": 1,
                                                "zone_id": zid}).status_code, 422)
        self.assertEqual(c.delete(f"/zones/{zid}").status_code, 409)  # East Clinic is in it
        self.assertEqual(c.patch(f"/users/{uid}", json={"priority": 3}).json()["priority"], 3)
        self.assertIn("East Zone", [x["name"] for x in c.get("/zones").json()])
        self.assertEqual(c.delete("/users/1").status_code, 409)
        self.assertEqual(c.delete(f"/users/{uid}").status_code, 200)
        self.assertEqual(c.delete(f"/zones/{zid}").status_code, 200)
        self.assertEqual(c.post("/demo/reset", json={"available_litres": 400000}).status_code, 400)

        self.config.ADMIN_TOKEN = "s3cret"
        self.assertEqual(c.patch("/resources/1", json={"name": "x"}).status_code, 401)
        self.assertEqual(c.post("/zones", json={"name": "Q", "population": 1}).status_code, 401)
        self.assertEqual(c.delete("/users/2").status_code, 401)
        self.assertEqual(c.get("/zones").status_code, 200)

    def test_live_low_water_alert(self):
        alerts = self.client.get("/alerts").json()  # 50,000 of 200,000 L = 25 %
        low = [a for a in alerts if a["alert_type"] == "low_water"]
        self.assertEqual(len(low), 1)
        self.assertEqual(low[0]["severity"], "warning")
        self.assertTrue(low[0]["live"])
        self.client.post("/demo/reset", json={"available_litres": 150000})
        self.assertFalse([a for a in self.client.get("/alerts").json() if a["alert_type"] == "low_water"])

    def test_forecast_endpoint_and_ml_pipeline_feed_the_scheduler(self):
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ml"))
            pipeline = importlib.import_module("jalml.pipeline")
        except ImportError:
            self.skipTest("ml requirements (pandas, scikit-learn) not installed")
        empty = self.client.get("/forecast").json()
        self.assertFalse(empty["available"])

        pipeline.run(self.conn, out=None, days=400, log=lambda *_: None)
        fc = self.client.get("/forecast?history_days=30").json()
        self.assertTrue(fc["available"] and fc["used_by_scheduler"])
        self.assertEqual(len(fc["zones"]), 3)
        self.assertEqual(len(fc["history"]), 30 * 3)
        self.assertEqual(len(fc["predictions"]), 7 * 3)
        self.assertEqual(sum(m["is_selected"] for m in fc["models"]), 1)
        self.assertGreater(fc["today_litres"], 0)
        self.assertEqual(self.client.get("/forecast?history_days=0").status_code, 400)
        self.assertGreater(self.client.get("/status").json()["forecast_today_litres"], 0)

        alerts = self.client.get("/alerts").json()
        leak = next(a for a in alerts if a["alert_type"] == "anomaly")
        self.assertIn("Central Zone", leak["message"])
        self.assertTrue(self.client.post(f"/alerts/{leak['alert_id']}/resolve").json()["resolved"])
        self.assertNotIn(leak["alert_id"], [a["alert_id"] for a in self.client.get("/alerts").json()])
        self.assertEqual(self.client.post("/alerts/99999/resolve").status_code, 404)


if __name__ == "__main__":
    unittest.main()
