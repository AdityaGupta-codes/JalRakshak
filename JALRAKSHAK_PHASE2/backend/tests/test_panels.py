"""Sign-in security, the user panel and the admin panel, end to end through the real HTTP API and a real
PostgreSQL database. Skipped unless FastAPI and a test database are available (see tests/pgtest.py)."""
import os
import unittest

from tests import pgtest
from tests.test_api import BIN, READY, TestClient, _import_app

ADMIN = "admin@jalrakshak.local"
HOSPITAL = "city.hospital@jalrakshak.local"     # demo account of City Hospital (organisation 1)
PARK = "town.park@jalrakshak.local"             # demo account of Town Park (organisation 8)


@unittest.skipUnless(READY, "needs FastAPI, the scheduler binary and a test database")
class PanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config, cls.db, cls.main = _import_app()
        cls.config.SCHEDULER_BIN = BIN

    def setUp(self):
        from app import auth
        pgtest.reset_database()
        self.conn = pgtest.open_conn()

        def override():
            yield self.conn

        self.main.app.dependency_overrides[self.db.get_conn] = override
        self.config.PASSWORD_ITERATIONS = 1000
        auth.bootstrap(self.conn)
        self.anon = TestClient(self.main.app)
        self.admin = self.as_(ADMIN, self.config.ADMIN_PASSWORD)
        self.hospital = self.as_(HOSPITAL, self.config.DEMO_PASSWORD)

    def tearDown(self):
        self.main.app.dependency_overrides.clear()
        self.conn.close()

    def login(self, email, password):
        return self.anon.post("/auth/login", json={"email": email, "password": password})

    def as_(self, email, password):
        r = self.login(email, password)
        self.assertEqual(r.status_code, 200, r.text)
        return TestClient(self.main.app, headers={"Authorization": "Bearer " + r.json()["token"]})

    def scalar(self, sql, params=()):
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchone().values())[0]

    # ------------------------------------------------------------ sign-in security
    def test_passwords_are_hashed_and_sessions_stored_as_hashes(self):
        stored = self.scalar("SELECT password_hash FROM accounts WHERE email = %s", (ADMIN,))
        self.assertTrue(stored.startswith("pbkdf2_sha256$"))
        self.assertNotIn(self.config.ADMIN_PASSWORD, stored)
        token = self.login(ADMIN, self.config.ADMIN_PASSWORD).json()["token"]
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM sessions WHERE token_hash = %s", (token,)), 0)

    def test_wrong_password_and_unknown_email_look_the_same(self):
        a = self.login(ADMIN, "Wrong1234")
        b = self.login("nobody@example.com", "Wrong1234")
        self.assertEqual((a.status_code, b.status_code), (401, 401))
        self.assertEqual(a.json()["detail"], b.json()["detail"])

    def test_lockout_after_repeated_wrong_passwords(self):
        for _ in range(self.config.MAX_FAILED_LOGINS - 1):
            self.assertEqual(self.login(PARK, "Wrong1234").status_code, 401)
        r = self.login(PARK, "Wrong1234")
        self.assertEqual(r.status_code, 429)
        self.assertIn("Try again", r.json()["detail"])
        self.assertEqual(self.login(PARK, self.config.DEMO_PASSWORD).status_code, 429)  # even the right one
        with self.conn:
            with self.conn.cursor() as cur:
                cur.execute("UPDATE accounts SET locked_until = now() - interval '1 second' WHERE email = %s", (PARK,))
        self.assertEqual(self.login(PARK, self.config.DEMO_PASSWORD).status_code, 200)

    def test_logout_and_change_password_end_sessions(self):
        r = self.login(PARK, self.config.DEMO_PASSWORD)
        other_device = TestClient(self.main.app, headers={"Authorization": "Bearer " + r.json()["token"]})
        me = self.as_(PARK, self.config.DEMO_PASSWORD)
        self.assertEqual(me.post("/auth/change-password", json={"current_password": "Nope12345",
                                                                "new_password": "NewPass123"}).status_code, 400)
        self.assertEqual(me.post("/auth/change-password", json={"current_password": self.config.DEMO_PASSWORD,
                                                                "new_password": "short"}).status_code, 422)
        self.assertEqual(me.post("/auth/change-password", json={"current_password": self.config.DEMO_PASSWORD,
                                                                "new_password": "NewPass123"}).status_code, 200)
        self.assertEqual(other_device.get("/auth/me").status_code, 401)   # other devices signed out
        self.assertEqual(me.get("/auth/me").status_code, 200)             # this one stays signed in
        self.assertEqual(me.post("/auth/logout").status_code, 200)
        self.assertEqual(me.get("/auth/me").status_code, 401)
        self.assertEqual(self.login(PARK, "NewPass123").status_code, 200)

    def test_sign_up_waits_for_approval_then_works(self):
        body = {"full_name": "Asha Rao", "email": "asha@sunrise.edu", "password": "Sunrise123",
                "organisation_name": "Sunrise School", "organisation_type": "school", "zone_id": 3}
        self.assertEqual(self.anon.post("/auth/register", json=dict(body, password="nodigits")).status_code, 400)
        # DPDP: no sign-up without accepting the privacy notice
        self.assertEqual(self.anon.post("/auth/register", json=body).status_code, 400)
        body["consent"] = True
        self.assertEqual(self.anon.post("/auth/register", json=body).status_code, 201)
        self.assertEqual(self.anon.post("/auth/register", json=body).status_code, 409)          # same email
        self.assertEqual(self.login("asha@sunrise.edu", "Sunrise123").status_code, 403)         # waiting
        pending = self.admin.get("/admin/accounts?status=pending").json()
        acc = next(a for a in pending if a["email"] == "asha@sunrise.edu")
        self.assertEqual(self.admin.get("/admin/summary").json()["pending_accounts"], 1)
        self.assertGreaterEqual(self.admin.get("/me/notifications").json()["unread"], 1)        # admins told
        ok = self.admin.post(f"/admin/accounts/{acc['account_id']}/approve", json={"priority": 4})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual((ok.json()["organisation"], ok.json()["priority"]), ("Sunrise School", 4))
        asha = self.as_("asha@sunrise.edu", "Sunrise123")
        self.assertIn("approved", asha.get("/me/notifications").json()["items"][0]["title"])
        self.assertEqual(asha.get("/me/overview").json()["organisation"]["name"], "Sunrise School")
        self.assertEqual(self.admin.post(f"/admin/accounts/{acc['account_id']}/approve", json={}).status_code, 409)

    def test_admin_account_management_guards(self):
        made = self.admin.post("/admin/accounts", json={"email": "ops@city.gov", "full_name": "Ops", "role": "admin"})
        self.assertEqual(made.status_code, 201)
        temp = made.json()["temporary_password"]
        ops = self.as_("ops@city.gov", temp)
        self.assertTrue(ops.get("/auth/me").json()["must_change_password"])
        me_id = self.admin.get("/auth/me").json()["account_id"]
        self.assertEqual(self.admin.patch(f"/admin/accounts/{me_id}", json={"status": "disabled"}).status_code, 403)
        # disabling an account signs it out at once
        park_id = self.scalar("SELECT account_id FROM accounts WHERE email = %s", (PARK,))
        park = self.as_(PARK, self.config.DEMO_PASSWORD)
        self.assertEqual(self.admin.patch(f"/admin/accounts/{park_id}", json={"status": "disabled"}).status_code, 200)
        self.assertEqual(park.get("/auth/me").status_code, 401)
        self.assertEqual(self.login(PARK, self.config.DEMO_PASSWORD).status_code, 403)
        # password reset gives a temporary password that must be changed
        reset = self.admin.post(f"/admin/accounts/{park_id}/reset-password").json()
        self.admin.patch(f"/admin/accounts/{park_id}", json={"status": "active"})
        self.assertEqual(self.login(PARK, reset["temporary_password"]).status_code, 200)
        # the last active admin cannot be demoted
        ops_id = self.scalar("SELECT account_id FROM accounts WHERE email = %s", ("ops@city.gov",))
        self.assertEqual(self.admin.patch(f"/admin/accounts/{ops_id}", json={"status": "disabled"}).status_code, 200)
        self.assertEqual(ops.patch(f"/admin/accounts/{me_id}", json={"status": "disabled"}).status_code, 401)

    # ------------------------------------------------------------ user panel
    def test_requester_sees_and_changes_only_their_own_requests(self):
        ov = self.hospital.get("/me/overview").json()
        self.assertEqual((ov["organisation"]["name"], ov["requests"]["pending"]), ("City Hospital", 1))
        mine = self.hospital.get("/me/requests").json()
        self.assertTrue(all(True for _ in mine) and len(mine) == 1)
        r = self.hospital.post("/me/requests", json={"requested_litres": 3000, "urgency": 5,
                                                     "deadline_in_minutes": 60, "note": "ICU expansion"})
        self.assertEqual(r.status_code, 201)
        new_id = r.json()["request_id"]
        park = self.as_(PARK, self.config.DEMO_PASSWORD)
        self.assertEqual(park.post(f"/me/requests/{new_id}/cancel").status_code, 404)  # not theirs
        self.assertEqual(self.hospital.post(f"/me/requests/{new_id}/cancel").status_code, 200)
        self.assertEqual(self.hospital.post(f"/me/requests/{new_id}/cancel").status_code, 409)
        self.assertEqual(self.admin.get("/me/requests").status_code, 403)
        for _ in range(self.config.MAX_PENDING_PER_ORG - 1):
            self.assertEqual(self.hospital.post("/me/requests", json={"requested_litres": 100}).status_code, 201)
        self.assertEqual(self.hospital.post("/me/requests", json={"requested_litres": 100}).status_code, 409)
        self.assertEqual(self.hospital.post("/me/requests", json={"requested_litres": 999999}).status_code, 409)

    def test_applying_a_plan_notifies_each_organisation(self):
        self.assertEqual(self.admin.post("/allocate/commit", json={"algorithm": "water_aware"}).status_code, 200)
        notes = self.hospital.get("/me/notifications").json()
        self.assertEqual(notes["unread"], 1)
        self.assertIn("approved: 10,000 L", notes["items"][0]["title"])
        park = self.as_(PARK, self.config.DEMO_PASSWORD)
        self.assertIn("partly served", park.get("/me/notifications").json()["items"][0]["title"])
        req = self.hospital.get("/me/requests").json()[0]
        self.assertEqual((req["status"], float(req["allocated_litres"]), req["algorithm"]), ("approved", 10000.0, "water_aware"))
        self.assertEqual(self.hospital.get("/me/overview").json()["litres_received"], 10000)
        self.assertEqual(self.hospital.post("/me/notifications/read-all").status_code, 200)
        self.assertEqual(self.hospital.get("/me/notifications").json()["unread"], 0)

    def test_issue_reported_by_user_and_answered_by_admin(self):
        bad = self.hospital.post("/me/issues", json={"category": "flood", "title": "x", "description": "y"})
        self.assertEqual(bad.status_code, 422)
        r = self.hospital.post("/me/issues", json={"category": "leak", "title": "Pipe burst near gate 2",
                                                   "description": "Water flowing on the road since 6 am",
                                                   "location": "Gate 2, MG Road"})
        self.assertEqual(r.status_code, 201)
        issue_id = r.json()["issue_id"]
        listed = self.admin.get("/admin/issues?status=open").json()
        self.assertEqual(listed[0]["organisation"], "City Hospital")
        self.assertEqual(listed[0]["zone_name"], "Central Zone")
        upd = self.admin.patch(f"/admin/issues/{issue_id}", json={"status": "resolved", "response": "Valve replaced."})
        self.assertEqual(upd.status_code, 200)
        self.assertIsNotNone(upd.json()["resolved_at"])
        mine = self.hospital.get("/me/issues").json()[0]
        self.assertEqual((mine["status"], mine["admin_response"]), ("resolved", "Valve replaced."))
        self.assertIn(f"issue #{issue_id}", self.hospital.get("/me/notifications").json()["items"][0]["title"])

    def test_profile_update(self):
        r = self.hospital.patch("/me/profile", json={"full_name": "Dr. Mehta", "phone": "+91 98765 43210"})
        self.assertEqual(r.json()["full_name"], "Dr. Mehta")
        self.assertEqual(self.hospital.get("/auth/me").json()["phone"], "+91 98765 43210")

    # ------------------------------------------------------------ audit trail
    def test_audit_log_records_actions_and_cannot_be_changed(self):
        self.admin.post("/allocate/commit", json={"algorithm": "priority"})
        self.admin.patch("/resources/1", json={"capacity_litres": 210000})
        actions = [a["action"] for a in self.admin.get("/admin/audit").json()]
        for expected in ("login", "plan.apply", "reservoir.update", "account.bootstrap_admin"):
            self.assertIn(expected, actions)
        plan = self.admin.get("/admin/audit?action=plan.").json()[0]
        self.assertEqual((plan["actor"], plan["details"]["algorithm"]), (ADMIN, "priority"))
        with self.assertRaises(Exception):
            with self.conn:
                with self.conn.cursor() as cur:
                    cur.execute("DELETE FROM audit_log")


if __name__ == "__main__":
    unittest.main()
