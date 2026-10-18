# What changed in version 2.0

Requirements a water department would set before a pilot. Each one is mapped to code and tests in `docs/compliance.md`.

## Governance
- **Water policy** (`policies` table, Policy tab): per-class priority and guaranteed share, the per-person norm
  (135 L/day by default), Water-aware weights, supply window, tanker trigger, government reference. Versioned:
  one active policy; plans and timetables record the version they used.
- **Maker-checker**: a policy change is proposed by one administrator and approved or rejected by another.
  Approval re-sets organisation priorities from their class, keeping marked exceptions.
- **Per-class guaranteed share** in the Water-aware scheduler (`min_share` per request).
- **Officer override**: edit a plan's litres before applying, with a required reason. The changed plan runs
  through the scheduler's new `manual` mode. Recorded with the run and in the audit log; organisations are told.
- **Decision records**: printable HTML (Save as PDF) and CSV for every plan, including the alternatives compared.
- **Shadow mode** and **pilot indicators** (override rate, essential demand delivered, complaints, …).

## Network
- **Zone-wise daily supply timetable**: new scheduler mode `"timetable"`. It respects each source's daily release
  and pump capacity and each zone's inlet main, gives every zone a guaranteed share, and assigns continuous supply
  hours. Draft → publish; organisations see their supply hours.
- **Several sources**: add reservoirs and set the network in Settings › Supply network.
- **Tanker orders** planned from the timetable (or by hand): planned → dispatched → delivered, with notifications.

## Data integration
- **Sensors / SCADA**: registered devices push readings with their own key. Level readings update the reservoir;
  flow readings build metered daily consumption. Offline sensors raise alerts. `scripts/scada_simulator.py` for demos.
- **Meter CSV import**; the ML job keeps real data and prefers it (120+ days per zone); the timetable can use metered demand.
- **Zone map** from GeoJSON boundaries; **complaint clusters** (3 in 48 h) raise alerts.

## Security and privacy
- **Two-factor sign-in** (authenticator apps, TOTP); can be required for administrators (`REQUIRE_ADMIN_MFA`).
- **HTTPS** profile (Caddy); **least-privilege database user** for the API (no schema changes, no audit-log edits);
  audit log also protected against TRUNCATE.
- **Backups** service with restore scripts; **rate limiting**; strict **Content-Security-Policy** in nginx.
- **DPDP Act**: privacy notice and consent, download my data, correction / erasure / grievance requests
  (erasure anonymises the account), data retention, security **incident register** with CERT-In / Board deadlines.
- **Hindi** for sign-in, the organisation panel, privacy and two-factor screens.

## Database (by `db/migrate.sql`, automatic, keeps data)
New tables: `policies`, `supply_timetables`, `tanker_trips`, `devices`, `readings`, `login_challenges`,
`privacy_requests`, `security_incidents`. New columns on `users`, `zones`, `reservoirs`, `scheduler_runs`,
`consumption`, `accounts`. Schema version 2.0.

## Changed
- Sign-up requires accepting the privacy notice. New organisations get their priority from the policy.
- History tab renamed **Records**; the Audit log moved into **Compliance**.
- The demo installation has a second administrator (`approver@jalrakshak.local`) for maker-checker.

# What changed in version 1.0

## Added: sign-in, a user panel and an admin panel

**Sign-in for everyone** (replaces the shared admin token)
- Accounts with roles: **administrator** (water department) and **user** (belongs to one organisation).
- Passwords stored as salted PBKDF2-SHA256 hashes (600,000 iterations); sessions are random tokens stored only as
  hashes, expire after 12 hours, and end on sign-out. Five wrong passwords lock the account for 5 minutes.
- New organisations **sign up** on the sign-in page; an administrator approves them and sets their priority.
- First start creates the administrator (`admin@jalrakshak.local` / `Admin@1234`, must be changed at first
  sign-in) and a demo sign-in for each of the 8 sample organisations (password `Demo@1234`). See README.

**User panel** (organisations)
- **Home:** organisation, priority and zone; reservoir status; alerts for their area; requests waiting, water
  received, share delivered, open issues.
- **My requests:** request water (litres, urgency, needed within, note), see each request's status and how much it
  received in which plan, cancel while waiting. At most 5 waiting at a time.
- **Report a problem:** leak, no supply, low pressure, water quality; follow the status and the department's reply.
- **Notifications:** plan decisions ("approved: 10,000 L", "partly served …"), issue replies, approval.
- **Profile:** name, phone, password.

**Admin panel** (water department): everything the dashboard had, plus
- **Accounts:** approve or reject sign-ups (new organisation with a priority, or link to an existing one),
  create accounts with a one-time temporary password, disable or enable, reset passwords.
- **Issues:** all reports with reporter, organisation and zone; set status and reply (the reporter is notified).
- **Audit log:** every sign-in (and failed attempt), plan, request, issue, account and settings change.
  Append-only: the database refuses to change or delete entries.
- Notifications for new sign-ups and new issues; badges on the Accounts, Issues and Forecast tabs.

**Database** (added by `db/migrate.sql`, so existing databases are upgraded automatically, keeping their data)
- New tables `accounts`, `sessions`, `notifications`, `issues`, `audit_log`; `water_requests` gains `created_by`
  and `note`. Unique email (case-insensitive), "active user must have an organisation" check, append-only trigger.

**API:** `/auth/*`, `/me/*` (user panel), `/admin/*` (admin panel); every endpoint checks the role. Full list in
`docs/contracts.md`; security explained in `docs/security.md`.

## Improved
- History tab: removed the out-of-date text and the duplicate raw forecast table (forecasts live on the Forecast tab).
- Shorter admin tab names; on phones the tabs scroll sideways in one row and menus open full-width.
- Fixed wide tables pushing pages sideways on phones (Accounts, History).
- Security headers on every API response; allowed web origins configurable (`CORS_ORIGINS`).
- ER diagram, README, contracts and report draft updated.

## Removed
- The shared admin token (`JAL_ADMIN_TOKEN`) and the dashboard's Unlock button: replaced by real sign-in.
- The in-memory demo API (`frontend/dev/mock_api.py`) and `scripts/demo-without-docker.ps1`: the app now needs the
  real database for accounts. Run it with Docker.

## Verified
- Backend: 57 tests pass (11 new for sign-in security and both panels), including real PostgreSQL and the HTTP API.
  C++: 10,353 checks. ML: 10 tests.
- Browser walkthrough against the real API and PostgreSQL: sign-in, forced password change, sign-up and approval,
  a user's request and leak report, applying a plan, notifications, replying to an issue, audit log, sign-out,
  and every tab at phone width with no sideways scrolling and no console errors.

---

# What changed in version 0.5

## Fixed: "Cannot load data. database error" after updating

**Cause.** Docker creates the database tables only the first time its data volume is created. After updating
the code, the old database (from v0.2) was still used, so it had no `model_evaluations` table and no
`load_sample_requests()` function. Every `/status` call failed with `relation "model_evaluations" does not exist`.

**Fix.**
- New `db/migrate.sql`: a safe-to-repeat upgrade script. The **backend runs it at start-up** (and before the first
  request if the database was not ready yet), and the ML job runs it too. An older database is brought up to date
  automatically and **keeps its data**. A `schema_info` table records the version (0.5).
- `/health` now checks that every required table and function exists. If not, it answers 503 with what is
  missing and the exact fix. The dashboard banner shows that message.
- Database errors now show the real reason (for example `relation "…" does not exist`) and, for
  "older database" errors, the command to fix it, instead of just "database error".

## Made more robust on Windows

- The database image now contains the schema files (`db/Dockerfile`) instead of reading them from your folder at
  start-up, which avoids problems with OneDrive "online-only" files.
- The database is published on **port 5433** on your computer (was 5432), so it never clashes with a PostgreSQL
  installed on Windows. Inside Docker nothing changes. Set `DB_PORT` in `.env` to change it.
- `.dockerignore` keeps old compiled files and docs out of the images (faster, cleaner builds).
- README: new **Troubleshooting** table for the common errors.

## Verified

- An actual v0.2 database (old schema and seed) is detected, upgraded without losing rows, and then everything
  works: compare, apply, reset, forecasts, alerts, settings, the ML job, and the HTTP API (5 new tests).
- Backend: 46 tests pass. C++: 10,353 checks pass. ML: 10 tests pass.

---

# What changed in version 0.4

## Added: Settings tab (edit the database from the dashboard)

- **Reservoir:** change its name, capacity and current water level. Water can never exceed capacity; lowering only
  the capacity below the water already stored is refused with a clear message ("Lower the water level as well").
- **Zones:** add, rename, change population, delete (only when no requesters are in the zone).
- **Requesters:** add, rename, change type, priority and zone, delete (only when they have no requests on record).
- Changes take effect on the next plan you compare or apply (for example, raising Town Park to priority 5 makes the
  Priority rule give it water).
- **Reset demo data** now asks what level to refill to (default 50,000 L, or the capacity if smaller) and keeps
  your Settings changes.
- New API endpoints: `PATCH /resources/{id}`, `GET/POST /zones`, `PATCH/DELETE /zones/{id}`, `POST /users`,
  `PATCH/DELETE /users/{id}`. All changes need the admin token when one is set.
- The demo API (`frontend/dev/mock_api.py`) supports all of it too.

## Fixed

- **Phones: the page scrolled sideways** on the Compare tab (and the new Settings tab). Hidden screen-reader labels
  inside wide tables were stretching the page. This was already in v0.2/v0.3.
- Renaming one of the 8 sample requesters would have made "Reset demo data" skip its request; the sample now refers
  to requesters by id.
- Text inputs now look the same as number inputs.

## Verified

- Backend: 41 tests pass (8 new: reservoir/zone/requester changes in PostgreSQL and through the HTTP API, including
  refused changes and the admin token). C++ and ML suites unchanged and passing.
- Dashboard tested in a headless browser: every Settings action, error messages, the new requester appearing in
  the request form, the new capacity used by Compare, reset to a chosen level, all five tabs at phone width.
- After updating, run `docker compose down -v` once (the database function for the sample requests changed).

---

# What changed in version 0.3

## Bugs fixed

| Problem | Fix |
|---|---|
| **Water-aware looked worse than FCFS on the dashboard.** The scheduler started as soon as the first request arrived, so in the sample the factory (priority 2) got its full 20,000 L before the hospital had even arrived (importance-weighted delivery 0.56). | The backend now ranks every pending request together (`BATCH_WINDOW_MINUTES=all`, the default), which is what happens when an operator clicks Apply. Water-aware: 0.68; hospital and both housing colonies served in full; nobody left empty. `backend/app/config.py`, `scheduler_client.py` |
| **"Reset demo data" did not really reset.** Requests added by hand stayed, so the total was no longer 80,000 L, and the sample requests kept their old times, so their deadlines were in the past. | Reset now deletes all requests and loads exactly the 8 sample requests with fresh times, from one definition shared with the seed (`load_sample_requests()` in `db/schema.sql`). |
| **Database health check could pass too early.** During the first start, Postgres reports "ready" on its local socket while the schema is still loading. | The check now uses TCP (`-h 127.0.0.1`), which only answers after the init scripts finish. `docker-compose.yml` |
| **Deadlines were ignored.** A request served after its deadline was not reported anywhere. | New metric **Missed deadlines** (`deadline_miss_count`) in the scheduler, the experiments and the dashboard. Deadlines stay soft: late requests are still served. |
| **Commands did not work on Windows.** `make` and `VAR=value python ...` are Linux/bash syntax. | `scripts/test.ps1` runs every test inside Docker; `scripts/demo-without-docker.ps1`; README written for PowerShell. |
| Stale comments: `seed.sql` pointed to a non-existent `ml/generate_data.py`; schema said "v0.1, freeze by Day 2". | Updated. |
| No `.gitignore` / line-ending rules. Windows Git could convert scripts to CRLF and break them in Linux containers. | `.gitignore`, `.gitattributes`, `.env.example`. |

## Built (was listed as "not built yet")

- **Data science module** (`ml/`): two years of synthetic consumption with known leaks; leak detection
  (robust z-score vs Isolation forest); demand forecasting (2 baselines vs Linear regression vs Random forest).
  Results go into `consumption`, `predictions`, new table `model_evaluations`, and `alerts`. Runs
  automatically in Docker. See `ml/README.md`.
- **Forecast feeds the scheduler**: today's forecast becomes `predicted_demand_litres` (Water-aware scarcity factor).
- **Dashboard tab "Forecast and alerts"**: alerts with "Mark as handled", per-zone charts (4 weeks + 7-day
  forecast), model accuracy table; "Forecast use today" in the header; alert count on the tab.
- **Live alerts**: reservoir below 30 % of capacity, forecast demand above available water.
- **Optional login protection**: set `JAL_ADMIN_TOKEN` and every change needs the token (dashboard Unlock button).
- **New API endpoints**: `/forecast`, `/alerts/{id}/resolve`, `/auth`, `/auth/check`.
- **HTTP-level API tests** (`backend/tests/test_api.py`) against a real database.
- **CI**: GitHub Actions runs all tests on every push (`.github/workflows/ci.yml`).
- **ER diagram** (`docs/er_diagram.md/.png/.svg`), **architecture diagram**, **concurrency write-up with the
  recorded experiment** (`docs/concurrency.md`), **report draft** (`docs/REPORT.md`).
- Experiments: new "Missed deadlines" column and a run with the backend's "all" window (`results_window_all.csv`).

## Verified

- C++: 10,353 checks, 0 failures.
- Backend: 33 tests pass, **including the 13 PostgreSQL tests that had never run before** and 7 new HTTP API tests
  (run against PostgreSQL 16 through the `psql` stand-in driver; the real `psycopg2` path runs in CI and `test.ps1`).
- ML: 10 tests pass.
- Dashboard: tested in a headless browser against the demo API (compare, apply, history, reset, forecasts,
  alerts, lock/unlock, phone width; no console errors).
- **Not verified here:** `docker compose up --build` itself (no Docker in the environment where this was made).
  Run it once after updating, with `docker compose down -v` first.
