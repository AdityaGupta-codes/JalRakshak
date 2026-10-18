# JalRakshak v2.0: requirements from a water-authority review, and how the project answers them

This file answers, point by point, the requirements a water department would set before piloting the system
(see the review in the project handbook). For each requirement:

- **Implemented**: what the code does, and where.
- **Evidence**: the automated test that proves it. The tests ran on 15 Oct 2026: 77 backend tests, 11 ML tests
  and 10,425 C++ checks, all passing, plus a browser walkthrough of every new screen.
- **Not in code**: the part only the department can do. This is stated plainly; nothing here is claimed as done
  when it is not.

---

## 1. Governance

| Requirement | Implemented | Evidence |
|---|---|---|
| Priority classes and minimum shares come from official policy, not from developers | Table `policies` (versioned): priority and guaranteed share per class (hospital, households, school …), per-person norm (135 L/day by default, the CPHEEO urban norm), Water-aware weights, supply window, tanker trigger, government-order reference. Exactly one active policy. Every plan and timetable records the policy version it used. `backend/app/policy.py`, Policy tab | `test_policy_needs_a_second_officer_and_sets_class_priorities`, `test_class_guaranteed_share_changes_the_plan` |
| Rule changes need approval (maker-checker) | Draft → submit → approve or reject by a **different** administrator (`REQUIRE_SECOND_APPROVER=1`). A rejection needs a reason. Approval sets each organisation's priority from its class; organisation-specific exceptions are kept and flagged. All steps are in the audit log | same test; `test_policy_rejection_needs_a_reason_and_bad_values_are_refused` |
| Per-class guaranteed share is really used | The C++ Water-aware scheduler takes a per-request `min_share` from the policy class (`scheduler/src/water_aware.cpp`) | C++: "policy class min_share"; backend test above |
| Manual override with a recorded reason | On the Allocate tab an officer edits litres of any rule's plan and must give a reason (at least 10 characters). The changed plan goes through the scheduler's new `manual` mode, so timing and metrics stay comparable. The reason and the plan-vs-final litres are stored with the run, and an audit row `plan.override` is written. Organisations are told the quantity was set by an officer | `test_officer_override_needs_a_reason_and_is_recorded`; C++: "manual plan …" |
| Decision reports for review meetings, audits and RTI replies | Records tab → **Decision record**: a printable HTML page (Save as PDF) and a CSV. Each covers rule, policy version and government reference, who applied it, override reason, litres per organisation, and the metrics of all four rules compared at the time (stored with the run) | same test (checks the reason, policy and alternatives are in the record) |
| Pilot evidence | **Shadow mode** records the recommended plan without changing water or requests. **Pilot indicators** (Compliance tab): plans, override rate, essential demand delivered, requests left with nothing, complaints by type and resolution time, timetable minimum per-person supply, tanker loads, leak alerts | `test_shadow_plan_changes_nothing_and_kpis_count_it` |

## 2. How water is actually distributed

| Requirement | Implemented | Evidence |
|---|---|---|
| Zone-wise supply timetable | New scheduler mode `"mode": "timetable"` (`scheduler/src/timetable.cpp`). Demand per zone = population × norm, or real metered use. It shares each source's daily release: every zone first gets its guaranteed share, the rest goes by priority × remaining need. It then places each zone in continuous hourly blocks within the supply window. Output: hours per zone, litres, litres per person against the norm, shortfall. Draft → publish; publishing replaces that day's earlier timetable and notifies every organisation of its zone's hours (user Home page) | C++: 4 timetable tests; `test_timetable_draft_publish_and_user_sees_supply_hours` |
| Several sources, pump and pipe limits | Each reservoir has a daily release and a pump capacity (L/h); each zone has a source, an inlet-main capacity (L/h) and a supply priority. The timetable never exceeds any of them (checked inside the scheduler). Add sources in Settings › Supply network | C++: "timetable: scarcity respects limits", "pump limit, 2 sources"; `test_second_source_new_zone_without_network_and_map` |
| Tanker supply | Supply tab: plan tanker orders for zones below the policy's trigger (litres per person). Each order has enough loads of the policy's tanker size, with no extra load. Orders can also be added by hand. Status goes planned → dispatched (the zone's organisations are notified) → delivered | `test_short_supply_plans_tankers_and_dispatch_notifies_zone` |

**Not modelled:** pipe-network hydraulics (pressures, valves, looped networks). Limits are per source and per zone
inlet, which is the level at which supply timetables are usually set. A hydraulic model (e.g. EPANET) would be the
next step with a utility's network data.

## 3. Data and integration

| Requirement | Implemented | Evidence |
|---|---|---|
| SCADA / meter readings | Devices (reservoir level sensors, zone flow meters) are registered in the Field data tab. Each gets a secret key, shown once; only its SHA-256 hash is stored. Readings go to `POST /telemetry/readings` with header `X-Device-Key`. Duplicates are ignored, so gateways can resend. A level reading becomes the reservoir's water level (with a row lock and a capacity check). Flow readings become daily consumption with source `meter`. `scripts/scada_simulator.py` acts as a gateway for demos | `test_level_sensor_updates_reservoir_and_rejects_bad_keys`, `test_flow_meter_builds_metered_consumption_and_offline_sensor_alerts`; browser walkthrough |
| Works when sensors or the internet fail | A device silent for 30 minutes raises a "sensor offline" alert. Staff keep entering values by hand in Settings | same test |
| Use real data instead of synthetic | Meter CSV import (all-or-nothing). The ML job never deletes real rows and uses a zone's real data once it has 120 days. The timetable can use metered demand. Leak-detector accuracy is reported only for zones with known (generated) leaks | `test_meter_csv_import_is_all_or_nothing`; ML `test_real_meter_data_replaces_generated_data_only_where_there_is_enough` |
| Map of zones (GIS) | Zones carry a GeoJSON boundary (validated; upload in Settings). The Supply tab draws the map coloured by today's per-person supply, with open issues | `test_second_source_new_zone_without_network_and_map` |
| Complaints as a signal | 3 leak / no-supply / low-pressure complaints from one zone within 48 hours raise a "complaints" alert (possible main break) | `test_complaints_from_one_zone_raise_an_alert` |

**Not in code:** connecting to the department's actual SCADA (vendor protocol, OPC UA or Modbus gateway), and
validating the models on its meter history.

## 4. Security and privacy

| Requirement | Implemented | Evidence |
|---|---|---|
| Two-factor sign-in for administrators | TOTP authenticator apps (RFC 6238, standard library only). Set up in Profile. Sign-in asks for the code (5 attempts, 5 minutes). `REQUIRE_ADMIN_MFA=1` blocks admin screens until it is set up. Another admin can reset it for a lost phone | `test_two_factor_sign_in`, `test_admin_two_factor_can_be_required`; browser walkthrough |
| HTTPS | `docker compose --profile https up --build` starts Caddy in front of everything, on one address with `/api`. It uses a local certificate for `localhost`, or a public one for a real domain (`SITE_ADDRESS`, `TLS_MODE`). HSTS header | configuration (`proxy/Caddyfile`); not run in the test environment |
| Least-privilege database user | The running API logs in as `jal_app`, created and granted by the backend with the owner login. It can read and write data, but cannot change the schema or UPDATE, DELETE or TRUNCATE the audit log. TRUNCATE on the audit log is also blocked by a trigger | `test_least_privilege_db_user_cannot_touch_the_audit_log`, `test_audit_log_cannot_be_truncated` |
| Backups and recovery | A `backup` service: `pg_dump` every 24 h, kept 14 days. `scripts/restore.ps1` / `restore.sh` restore a backup. The Operations page lists backups | configuration; not run in the test environment |
| Rate limiting, browser protections | 20 sign-in requests per minute per address (600 for the rest of the API), returning 429. API responses carry CSP, X-Frame-Options and nosniff. The web app is served by nginx with a strict Content-Security-Policy (only its own scripts, and calls to the API) | `test_security_headers_and_rate_limit`; browser walkthrough found no CSP violations |
| DPDP Act: notice, consent, rights, retention | Versioned privacy notice. Sign-up requires consent, and existing users are asked to accept. **Download my data** (JSON). Requests for correction, erasure and grievance go to the Compliance tab; erasure anonymises the account. Retention runs at start-up and on demand (expired sessions, old read notifications, readings over 400 days, issues closed over 2 years). The audit log is kept as the department's record, and the notice says so | `test_privacy_notice_consent_export_and_erasure`, `test_retention_removes_old_data_only` |
| Incident handling | Incident register showing the CERT-In 6-hour deadline and, when personal data is affected, the 72-hour detailed report to the Data Protection Board; marks when each was reported | `test_incident_register_shows_reporting_deadlines` |
| GIGW: bilingual, accessible | English / हिंदी switch (remembered per account). It covers sign-in, the whole organisation panel, privacy and two-factor screens, and navigation. Labels, keyboard focus and phone layouts were checked | browser walkthrough (Hindi sign-in, Hindi user panel, no sideways scrolling at 390 px) |

**Not in code (the department must do these):**

- A CERT-In-empanelled security audit before go-live.
- Hosting in an NIC / state data centre or a MeitY-empanelled cloud.
- Onboarding to Parichay / government SSO. It would replace the sign-in page; the role checks stay.
- Encrypting the 2FA secrets with a key held outside the database.
- A formal GIGW / STQC compliance check. Admin working screens and server messages are English only.
- The legal review of the privacy notice text, and naming the grievance officer (`PRIVACY_CONTACT`).

## 5. Operations

| Requirement | Implemented |
|---|---|
| Monitoring | Compliance › Operations: database version and size, whether the restricted DB user is in use, admins with 2FA, sensors offline, last forecasting and retention runs, open incidents and privacy requests, backups. `/health` explains database problems |
| Restart after failure | `restart: unless-stopped` for the database, API, web app and backup service |
| Manual fallback | Every automatic input (sensor level, forecast) has a manual alternative in Settings; the system works with no sensors and no forecast |

**Not in code:** an annual maintenance contract, support staff, uptime targets, and training. These are
procurement and staffing decisions.

---

### What to say in one line

"v2.0 adds what a water department asks for before a pilot:

- policy-driven priorities with maker-checker approval;
- officer overrides with reasons and printable decision records;
- a zone-wise supply timetable with tankers;
- sensor, meter and map integration;
- two-factor sign-in, HTTPS, least-privilege database access, backups and DPDP rights.

All of this is tested. Real deployment still needs the department's audit, hosting, SSO and its own data."
