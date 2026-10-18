"""v2.0 requirements from the water-authority review, end to end through the HTTP API and a real PostgreSQL:
governance (policy with maker-checker, officer override with reason, decision records, shadow mode, KPIs),
network (zone timetable, tankers, map), field data (sensors, meter import, complaint signal) and
security / privacy (two-factor sign-in, least-privilege DB user, DPDP rights, retention, incidents).
Skipped unless FastAPI and a test database are available (see tests/pgtest.py)."""
import time
import unittest
from datetime import date, datetime, timedelta, timezone

from tests import pgtest
from tests.test_api import BIN, READY, TestClient, _import_app

ADMIN = "admin@jalrakshak.local"
APPROVER = "approver@jalrakshak.local"
HOSPITAL = "city.hospital@jalrakshak.local"   # City Hospital, zone 2 (Central)
PARK = "town.park@jalrakshak.local"           # Town Park, zone 2


@unittest.skipUnless(READY, "needs FastAPI, the scheduler binary and a test database")
class V2Tests(unittest.TestCase):
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
        self.config.REQUIRE_ADMIN_MFA = False
        self.config.REQUIRE_SECOND_APPROVER = True
        auth.bootstrap(self.conn)
        auth.ensure_demo_approver(self.conn)
        self.anon = TestClient(self.main.app)
        self.admin = self.as_(ADMIN, self.config.ADMIN_PASSWORD)
        self.approver = self.as_(APPROVER, self.config.DEMO_PASSWORD)
        self.hospital = self.as_(HOSPITAL, self.config.DEMO_PASSWORD)

    def tearDown(self):
        self.main.app.dependency_overrides.clear()
        self.config.REQUIRE_ADMIN_MFA = False
        self.conn.close()

    def as_(self, email, password):
        r = self.anon.post("/auth/login", json={"email": email, "password": password})
        self.assertEqual(r.status_code, 200, r.text)
        return TestClient(self.main.app, headers={"Authorization": "Bearer " + r.json()["token"]})

    def scalar(self, sql, params=()):
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchone().values())[0]

    def ok(self, response, code=200):
        self.assertEqual(response.status_code, code, response.text)
        return response.json()

    # ================================================================ governance
    def test_policy_needs_a_second_officer_and_sets_class_priorities(self):
        active = self.ok(self.admin.get("/policy/active"))
        self.assertEqual(active["version"], 1)
        self.assertEqual(active["lpcd"], 135)
        # Town Park's priority was set by hand: it is an organisation-specific exception and must survive
        self.ok(self.admin.patch("/users/8", json={"priority": 2}))
        self.assertTrue(self.scalar("SELECT priority_override FROM users WHERE user_id = 8"))

        draft = self.ok(self.admin.post("/admin/policies", json={
            "title": "Summer scarcity order", "reference": "G.O. No. 12/2026 (example)",
            "class_priority": {"hospital": 5, "school": 5, "domestic": 4, "commercial": 2, "industry": 1, "park": 1},
            "class_min_share": {"hospital": 0.6, "school": 0.4, "domestic": 0.4, "commercial": 0.2, "industry": 0.2,
                                "park": 0.1}}), 201)
        self.assertEqual(draft["status"], "draft")
        self.ok(self.admin.post(f"/admin/policies/{draft['policy_id']}/submit"))
        # maker-checker: the proposer cannot approve
        r = self.admin.post(f"/admin/policies/{draft['policy_id']}/decide", json={"approve": True})
        self.assertEqual(r.status_code, 403, r.text)
        self.assertIn("maker-checker", r.json()["detail"])
        approved = self.ok(self.approver.post(f"/admin/policies/{draft['policy_id']}/decide",
                                              json={"approve": True, "note": "As per the order"}))
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["decided_by_email"], APPROVER)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM policies WHERE status = 'approved'"), 1)
        self.assertEqual(self.scalar("SELECT status FROM policies WHERE version = 1"), "superseded")
        self.assertEqual(self.scalar("SELECT priority FROM users WHERE user_id = 2"), 5)   # school: class priority
        self.assertEqual(self.scalar("SELECT priority FROM users WHERE user_id = 5"), 1)   # industry
        self.assertEqual(self.scalar("SELECT priority FROM users WHERE user_id = 8"), 2)   # park: hand-set, kept
        # back to the policy class on request
        self.ok(self.admin.patch("/users/8", json={"reset_priority": True}))
        self.assertEqual(self.scalar("SELECT priority FROM users WHERE user_id = 8"), 1)
        self.assertFalse(self.scalar("SELECT priority_override FROM users WHERE user_id = 8"))
        actions = [a["action"] for a in self.ok(self.admin.get("/admin/audit"))]
        for a in ("policy.draft", "policy.submit", "policy.approve"):
            self.assertIn(a, actions)

    def test_policy_rejection_needs_a_reason_and_bad_values_are_refused(self):
        bad = self.admin.post("/admin/policies", json={"class_min_share": {"hospital": 1.5}})
        self.assertEqual(bad.status_code, 400, bad.text)
        d = self.ok(self.admin.post("/admin/policies", json={"lpcd": 100}), 201)
        self.ok(self.admin.post(f"/admin/policies/{d['policy_id']}/submit"))
        self.assertEqual(self.approver.post(f"/admin/policies/{d['policy_id']}/decide",
                                            json={"approve": False}).status_code, 400)
        rej = self.ok(self.approver.post(f"/admin/policies/{d['policy_id']}/decide",
                                         json={"approve": False, "note": "Norm cannot be lowered"}))
        self.assertEqual(rej["status"], "rejected")
        self.assertEqual(self.ok(self.admin.get("/policy/active"))["version"], 1)

    def test_class_guaranteed_share_changes_the_plan(self):
        before = {a["request_id"]: a["allocated_litres"] for a in self._plan("water_aware")}
        self._activate({"class_min_share": {"hospital": 0.3, "school": 0.3, "domestic": 0.3, "commercial": 0.3,
                                            "industry": 0.3, "park": 0.9}})
        after = {a["request_id"]: a["allocated_litres"] for a in self._plan("water_aware")}
        self.assertGreaterEqual(after[6], 0.9 * 4000 - 0.01)      # Town Park (request 6) guaranteed 90 %
        self.assertGreater(after[6], before[6])

    def _plan(self, alg):
        sim = self.ok(self.admin.post("/allocate/simulate", json={"algorithms": [alg]}))
        return sim["results"][0]["allocations"]

    def _activate(self, changes):
        d = self.ok(self.admin.post("/admin/policies", json=changes), 201)
        self.ok(self.admin.post(f"/admin/policies/{d['policy_id']}/submit"))
        return self.ok(self.approver.post(f"/admin/policies/{d['policy_id']}/decide", json={"approve": True}))

    def test_officer_override_needs_a_reason_and_is_recorded(self):
        plan = {a["request_id"]: a["allocated_litres"] for a in self._plan("priority")}
        # give the park 2,000 L, taken from the factory
        overrides = {"6": 2000, "1": plan[1] - 2000}
        r = self.admin.post("/allocate/commit", json={"algorithm": "priority", "overrides": overrides, "reason": "short"})
        self.assertEqual(r.status_code, 400)
        too_much = self.admin.post("/allocate/commit", json={"algorithm": "priority", "overrides": {"6": 4000},
                                                              "reason": "Park nursery needs water urgently"})
        self.assertEqual(too_much.status_code, 400)        # 50,000 L already used by the plan
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM scheduler_runs"), 0)
        res = self.ok(self.admin.post("/allocate/commit", json={
            "algorithm": "priority", "overrides": overrides, "reason": "Park nursery seedlings would die"}))
        got = {a["request_id"]: a["allocated_litres"] for a in res["allocations"]}
        self.assertEqual(got[6], 2000)
        self.assertEqual(got[1], plan[1] - 2000)
        self.assertEqual(len(res["overrides"]), 2)
        run = self.ok(self.admin.get(f"/runs/{res['run_id']}"))
        self.assertEqual(run["override_reason"], "Park nursery seedlings would die")
        self.assertEqual(set(run["comparison"]), {"fcfs", "priority", "round_robin", "water_aware"})
        self.assertEqual(self.scalar("SELECT available_litres FROM reservoirs"), 0)
        self.assertIn("plan.override", [a["action"] for a in self.ok(self.admin.get("/admin/audit"))])
        note = self.scalar("SELECT body FROM notifications n JOIN accounts a ON a.account_id = n.account_id "
                           "WHERE a.email = %s AND n.kind = 'allocation'", (PARK,))
        self.assertIn("officer", note)
        # decision record for audit / RTI
        html = self.admin.get(f"/runs/{res['run_id']}/report")
        self.assertEqual(html.status_code, 200)
        for text in ("Park nursery seedlings would die", "Initial demo policy", "Alternatives that were compared",
                     "Town Park"):
            self.assertIn(text, html.text)
        csv_text = self.admin.get(f"/runs/{res['run_id']}/report?format=csv").text
        self.assertIn("Officer override reason,Park nursery seedlings would die", csv_text)
        self.assertEqual(self.hospital.get(f"/runs/{res['run_id']}/report").status_code, 403)

    def test_shadow_plan_changes_nothing_and_kpis_count_it(self):
        self.ok(self.admin.post("/allocate/shadow", json={"algorithm": "water_aware", "note": "pilot week 1"}))
        self.assertEqual(self.scalar("SELECT available_litres FROM reservoirs"), 50000)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM water_requests WHERE status = 'pending'"), 8)
        self.ok(self.admin.post("/allocate/commit", json={"algorithm": "water_aware"}))
        k = self.ok(self.admin.get("/admin/kpis"))
        self.assertEqual(k["shadow_plans"], 1)
        self.assertEqual(k["plans_applied"], 1)
        self.assertEqual(k["override_rate"], 0)
        self.assertEqual(k["requests_left_empty"], 0)
        self.assertIsNotNone(k["essential_share_delivered"])
        self.assertEqual(k["active_policy"]["version"], 1)

    # ================================================================ network
    def test_timetable_draft_publish_and_user_sees_supply_hours(self):
        t = self.ok(self.admin.post("/timetables", json={"for_date": str(date.today())}), 201)
        res = t["result"]
        self.assertEqual(t["status"], "draft")
        self.assertEqual(len(res["zones"]), 3)
        self.assertEqual(res["lpcd_norm"], 135)
        self.assertLessEqual(res["metrics"]["total_delivered_litres"], 12000000)
        for z in res["zones"]:
            self.assertLessEqual(z["delivered_litres"], z["demand_litres"])
            for b in z["blocks"]:
                self.assertGreaterEqual(b["start_minute"], 5 * 60)
                self.assertLessEqual(b["end_minute"], 23 * 60)
        self.assertEqual(self.ok(self.hospital.get("/me/supply"))["supply"], None)   # nothing published yet
        self.ok(self.admin.post(f"/timetables/{t['timetable_id']}/publish"))
        mine = self.ok(self.hospital.get("/me/supply"))["supply"]
        self.assertEqual(mine["zone"]["zone_id"], 2)
        self.assertTrue(mine["zone"]["blocks"])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM notifications n JOIN accounts a ON a.account_id = n.account_id "
                                     "WHERE a.email = %s AND n.kind = 'timetable'", (HOSPITAL,)), 1)
        # a second timetable for the same day replaces the first
        t2 = self.ok(self.admin.post("/timetables", json={"for_date": str(date.today()), "releases": {"1": 9000000}}), 201)
        self.ok(self.admin.post(f"/timetables/{t2['timetable_id']}/publish"))
        self.assertEqual(self.ok(self.admin.get(f"/timetables/{t['timetable_id']}"))["status"], "withdrawn")
        self.assertEqual(self.admin.post(f"/timetables/{t['timetable_id']}/publish").status_code, 409)

    def test_short_supply_plans_tankers_and_dispatch_notifies_zone(self):
        t = self.ok(self.admin.post("/timetables", json={"releases": {"1": 3000000}}), 201)
        low = [z for z in t["result"]["zones"] if z["lpcd_delivered"] < 70]
        self.assertTrue(low)
        trips = self.ok(self.admin.post(f"/timetables/{t['timetable_id']}/tankers"))
        self.assertEqual({tr["zone_id"] for tr in trips}, {z["zone_id"] for z in low})
        self.assertEqual(len(trips), len(low))                  # one order per zone, with a number of loads
        for tr in trips:
            z = next(z for z in low if z["zone_id"] == tr["zone_id"])
            self.assertEqual(tr["litres"], tr["loads"] * 10000)
            # enough loads to lift the zone to the 70 L/person trigger, and not one load more
            self.assertGreaterEqual(z["delivered_litres"] + tr["litres"], 70 * z["population"])
            self.assertLess(z["delivered_litres"] + tr["litres"] - 10000, 70 * z["population"])
        self.assertEqual(self.ok(self.admin.post(f"/timetables/{t['timetable_id']}/tankers")), [])  # not twice
        central = next(tr for tr in trips if tr["zone_id"] == 2)
        self.ok(self.admin.patch(f"/tankers/{central['trip_id']}", json={"status": "dispatched", "vehicle": "KA-01-T-1234"}))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM notifications n JOIN accounts a ON a.account_id = n.account_id "
                                     "WHERE a.email = %s AND n.kind = 'tanker'", (HOSPITAL,)), 1)
        done = self.ok(self.admin.patch(f"/tankers/{central['trip_id']}", json={"status": "delivered"}))
        self.assertIsNotNone(done["delivered_at"])
        self.assertEqual(self.admin.patch(f"/tankers/{central['trip_id']}", json={"status": "planned"}).status_code, 409)
        manual = self.ok(self.admin.post("/tankers", json={"zone_id": 3, "litres": 5000, "vehicle": "KA-02"}), 201)
        self.assertEqual(manual["origin"], "manual")

    def test_second_source_new_zone_without_network_and_map(self):
        src = self.ok(self.admin.post("/resources", json={"name": "Hill Reservoir", "capacity_litres": 2000000,
                                                          "available_litres": 1500000, "daily_release_litres": 5000000,
                                                          "pump_capacity_lph": 400000}), 201)
        self.ok(self.admin.patch("/zones/3/network", json={"reservoir_id": src["reservoir_id"]}))
        self.ok(self.admin.post("/zones", json={"name": "East Zone", "population": 20000}), 201)
        t = self.ok(self.admin.post("/timetables", json={}), 201)["result"]
        self.assertEqual(len(t["sources"]), 2)
        south = next(z for z in t["zones"] if z["zone_id"] == 3)
        self.assertEqual(south["delivered_litres"], 30000 * 135)                 # alone on its own source
        self.assertEqual([s["name"] for s in t["skipped_zones"]], ["East Zone"])  # no network data yet
        bad = self.admin.patch("/zones/1/network", json={"boundary": {"type": "Polygon", "coordinates": [[[1, 2], [3, 4]]]}})
        self.assertEqual(bad.status_code, 400)
        square = {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[77.6, 13.0], [77.7, 13.0],
                                                                                      [77.7, 13.1], [77.6, 13.0]]]}}
        self.ok(self.admin.patch("/zones/1/network", json={"boundary": square}))
        zones = self.ok(self.hospital.get("/map/zones"))
        self.assertEqual(zones[0]["boundary"]["type"], "Polygon")
        self.assertTrue(all("open_issues" in z for z in zones))

    # ================================================================ field data
    def test_level_sensor_updates_reservoir_and_rejects_bad_keys(self):
        dev = self.ok(self.admin.post("/admin/devices", json={"name": "Main level SCADA", "kind": "reservoir_level",
                                                              "reservoir_id": 1}), 201)
        key = dev["device_key"]
        self.assertTrue(key.startswith("jdk_"))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM devices WHERE key_hash = %s", (key,)), 0)  # only a hash
        self.assertEqual(self.anon.post("/telemetry/readings", json={"readings": [{"value": 1}]}).status_code, 401)
        self.assertEqual(self.anon.post("/telemetry/readings", json={"readings": [{"value": 1}]},
                                        headers={"X-Device-Key": "jdk_wrong"}).status_code, 401)
        dev_client = TestClient(self.main.app, headers={"X-Device-Key": key})
        t0 = datetime.now(timezone.utc) - timedelta(minutes=10)
        body = {"readings": [{"value": 41000, "measured_at": t0.isoformat()},
                             {"value": 42000, "measured_at": (t0 + timedelta(minutes=5)).isoformat()}]}
        out = self.ok(dev_client.post("/telemetry/readings", json=body))
        self.assertEqual(out["stored"], 2)
        self.assertEqual(out["reservoir_level"], 42000)
        self.assertEqual(self.scalar("SELECT available_litres FROM reservoirs WHERE reservoir_id = 1"), 42000)
        self.assertEqual(self.ok(dev_client.post("/telemetry/readings", json=body))["duplicates"], 2)
        self.assertEqual(dev_client.post("/telemetry/readings", json={"readings": [{"value": 999999}]}).status_code, 400)
        self.assertEqual(self.scalar("SELECT available_litres FROM reservoirs WHERE reservoir_id = 1"), 42000)
        self.assertIn("telemetry.level", [a["action"] for a in self.ok(self.admin.get("/admin/audit"))])
        self.ok(self.admin.patch(f"/admin/devices/{dev['device_id']}", json={"active": False}))
        self.assertEqual(dev_client.post("/telemetry/readings", json={"readings": [{"value": 1}]}).status_code, 401)

    def test_flow_meter_builds_metered_consumption_and_offline_sensor_alerts(self):
        dev = self.ok(self.admin.post("/admin/devices", json={"name": "Central inlet meter", "kind": "zone_flow",
                                                              "zone_id": 2}), 201)
        silent = self.ok(self.admin.post("/admin/devices", json={"name": "North inlet meter", "kind": "zone_flow",
                                                                 "zone_id": 1}), 201)
        c = TestClient(self.main.app, headers={"X-Device-Key": dev["device_key"]})
        day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        readings = [{"value": 400000, "measured_at": (day + timedelta(hours=h)).isoformat()} for h in range(6, 12)]
        self.ok(c.post("/telemetry/readings", json={"readings": readings}))
        self.assertEqual(self.scalar("SELECT litres FROM consumption WHERE zone_id = 2 AND recorded_on = %s",
                                     (str(day.date()),)), 2400000)
        self.assertEqual(self.scalar("SELECT source FROM consumption WHERE zone_id = 2 AND recorded_on = %s",
                                     (str(day.date()),)), "meter")
        alerts = self.ok(self.admin.get("/alerts"))
        offline = [a for a in alerts if a["alert_type"] == "telemetry"]
        self.assertEqual(len(offline), 1)
        self.assertIn(silent["name"], offline[0]["message"])
        # timetable on metered demand: only the metered zone can be planned
        t = self.ok(self.admin.post("/timetables", json={"demand_mode": "meter"}), 201)["result"]
        self.assertEqual([z["zone_id"] for z in t["zones"]], [2])
        self.assertEqual(t["zones"][0]["demand_litres"], 2400000)

    def test_meter_csv_import_is_all_or_nothing(self):
        bad = "zone_id,date,litres\n1,2026-01-01,5000\n9,2026-01-02,5000\n"
        self.assertEqual(self.admin.post("/admin/import/consumption", json={"csv": bad}).status_code, 400)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM consumption"), 0)
        good = "zone,date,litres\nNorth Zone,2026-01-01,5000\nSouth Zone,2026-01-01,4000\n"
        self.assertEqual(self.ok(self.admin.post("/admin/import/consumption", json={"csv": good}))["rows"], 2)
        src = {r["source"]: r["days"] for r in self.ok(self.admin.get("/admin/data-sources"))}
        self.assertEqual(src, {"import": 2})

    def test_complaints_from_one_zone_raise_an_alert(self):
        for i in range(3):
            self.ok(self.hospital.post("/me/issues", json={"category": "no_supply", "title": f"No water {i}",
                                                           "description": "Taps dry since morning"}), 201)
        alerts = [a for a in self.ok(self.admin.get("/alerts")) if a["alert_type"] == "complaints"]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["zone_id"], 2)

    # ================================================================ security and privacy
    def test_two_factor_sign_in(self):
        from app import mfa
        setup = self.ok(self.admin.post("/me/mfa/setup"))
        self.assertTrue(setup["otpauth_uri"].startswith("otpauth://totp/"))
        self.assertEqual(self.admin.post("/me/mfa/confirm", json={"code": "000000"}).status_code, 400)
        code = mfa.code_at(setup["secret"], int(time.time() // 30))
        self.ok(self.admin.post("/me/mfa/confirm", json={"code": code}))
        first = self.ok(self.anon.post("/auth/login", json={"email": ADMIN, "password": self.config.ADMIN_PASSWORD}))
        self.assertTrue(first["mfa_required"])
        self.assertNotIn("token", first)
        wrong = self.anon.post("/auth/mfa", json={"challenge": first["challenge"], "code": "123456"})
        self.assertEqual(wrong.status_code, 400)
        done = self.ok(self.anon.post("/auth/mfa", json={"challenge": first["challenge"], "code": code}))
        self.assertTrue(done["account"]["mfa_enabled"])
        self.assertEqual(self.anon.post("/auth/mfa", json={"challenge": first["challenge"], "code": code}).status_code, 401)
        # lost phone: another admin resets it
        aid = self.scalar("SELECT account_id FROM accounts WHERE email = %s", (ADMIN,))
        self.ok(self.approver.post(f"/admin/accounts/{aid}/reset-mfa"))
        self.assertIn("token", self.ok(self.anon.post("/auth/login", json={"email": ADMIN,
                                                                           "password": self.config.ADMIN_PASSWORD})))

    def test_admin_two_factor_can_be_required(self):
        self.config.REQUIRE_ADMIN_MFA = True
        me = self.ok(self.admin.get("/auth/me"))
        self.assertTrue(me["mfa_setup_required"])
        r = self.admin.get("/admin/kpis")
        self.assertEqual(r.status_code, 403)
        self.assertIn("two-factor", r.json()["detail"])
        self.assertEqual(self.hospital.get("/me/overview").status_code, 200)   # requesters are not affected

    def test_least_privilege_db_user_cannot_touch_the_audit_log(self):
        from app import migrations
        self.assertTrue(migrations.ensure_app_role(self.conn, "jal_app_test", "Str0ng-test-pw"))
        self.assertTrue(migrations.ensure_app_role(self.conn, "jal_app_test", "Str0ng-test-pw"))  # idempotent
        with self.conn.cursor() as cur:
            cur.execute("SELECT has_table_privilege('jal_app_test', 'audit_log', 'INSERT') AS ins, "
                        "has_table_privilege('jal_app_test', 'audit_log', 'DELETE') AS del, "
                        "has_table_privilege('jal_app_test', 'audit_log', 'UPDATE') AS upd, "
                        "has_table_privilege('jal_app_test', 'water_requests', 'UPDATE') AS req, "
                        "has_database_privilege('jal_app_test', current_database(), 'CREATE') AS ddl")
            p = cur.fetchone()
        self.assertEqual((p["ins"], p["del"], p["upd"], p["req"], p["ddl"]), (True, False, False, True, False))

    def test_audit_log_cannot_be_truncated(self):
        with self.assertRaises(Exception):
            with self.conn:
                with self.conn.cursor() as cur:
                    cur.execute("TRUNCATE audit_log")
        self.assertGreater(self.scalar("SELECT COUNT(*) FROM audit_log"), 0)

    def test_privacy_notice_consent_export_and_erasure(self):
        notice = self.ok(self.anon.get("/privacy-notice"))
        self.assertEqual(notice["version"], self.config.PRIVACY_NOTICE_VERSION)
        park = self.as_(PARK, self.config.DEMO_PASSWORD)
        self.assertTrue(self.ok(park.get("/auth/me"))["consent_required"])
        self.ok(park.post("/me/consent"))
        self.assertFalse(self.ok(park.get("/auth/me"))["consent_required"])
        data = self.ok(park.get("/me/data-export"))
        self.assertEqual(data["account"]["email"], PARK)
        self.assertNotIn("password_hash", data["account"])
        self.assertEqual(data["organisation"]["name"], "Town Park")
        req = self.ok(park.post("/me/privacy-requests", json={"kind": "erasure", "details": "Leaving the job"}), 201)
        self.assertEqual(park.post("/me/privacy-requests", json={"kind": "erasure"}).status_code, 409)
        self.ok(self.admin.post(f"/admin/privacy-requests/{req['request_id']}",
                                json={"status": "completed", "response": "Account erased"}))
        self.assertEqual(self.anon.post("/auth/login", json={"email": PARK, "password": self.config.DEMO_PASSWORD}
                                        ).status_code, 401)
        self.assertEqual(park.get("/auth/me").status_code, 401)
        self.assertEqual(self.scalar("SELECT full_name FROM accounts WHERE email LIKE 'erased-%%'"), "Erased user")
        # the audit log keeps the record (legal obligation), unchanged
        self.assertGreater(self.scalar("SELECT COUNT(*) FROM audit_log WHERE actor = %s", (PARK,)), 0)

    def test_retention_removes_old_data_only(self):
        aid = self.scalar("SELECT account_id FROM accounts WHERE email = %s", (HOSPITAL,))
        with self.conn:
            with self.conn.cursor() as cur:
                cur.execute("INSERT INTO sessions (token_hash, account_id, expires_at) VALUES ('old', %s, now() - interval '1 day')", (aid,))
                cur.execute("INSERT INTO notifications (account_id, kind, title, body, is_read, created_at) VALUES "
                            "(%s, 'info', 'old', 'x', TRUE, now() - interval '400 days'), "
                            "(%s, 'info', 'new', 'x', TRUE, now())", (aid, aid))
        removed = self.ok(self.admin.post("/admin/maintenance/retention"))
        self.assertEqual(removed["expired_sessions"], 1)
        self.assertEqual(removed["old_read_notifications"], 1)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM notifications WHERE title = 'new'"), 1)

    def test_incident_register_shows_reporting_deadlines(self):
        inc = self.ok(self.admin.post("/admin/incidents", json={"title": "Phishing mail to operators",
                                                                "description": "Two staff clicked a link",
                                                                "severity": "high", "personal_data_affected": True}), 201)
        detected = datetime.fromisoformat(str(inc["detected_at"]))
        self.assertEqual(datetime.fromisoformat(str(inc["cert_in_due"])) - detected, timedelta(hours=6))
        self.assertEqual(datetime.fromisoformat(str(inc["board_due"])) - detected, timedelta(hours=72))
        upd = self.ok(self.admin.patch(f"/admin/incidents/{inc['incident_id']}",
                                       json={"status": "contained", "reported_cert_in": True}))
        self.assertIsNotNone(upd["reported_cert_in_at"])
        ops = self.ok(self.admin.get("/admin/ops"))
        self.assertEqual(ops["schema_version"], "2.0")
        self.assertEqual(ops["open_incidents"], 1)

    def test_security_headers_and_rate_limit(self):
        r = self.admin.get("/auth/me")
        self.assertEqual(r.headers["x-frame-options"], "DENY")
        self.assertIn("default-src 'none'", r.headers["content-security-policy"])
        from app.security import RateLimiter
        lim = RateLimiter()
        self.assertTrue(all(lim.allow("a", 3, now=100 + i) for i in range(3)))
        self.assertFalse(lim.allow("a", 3, now=103))
        self.assertTrue(lim.allow("b", 3, now=103))         # per client
        self.assertTrue(lim.allow("a", 3, now=161))         # window slides


if __name__ == "__main__":
    unittest.main()
