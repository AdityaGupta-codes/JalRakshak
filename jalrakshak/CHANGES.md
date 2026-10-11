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
