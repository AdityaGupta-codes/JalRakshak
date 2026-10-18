# JalRakshak: Intelligent Water Resource Allocation and Scheduling

Water requests are treated like OS processes competing for one limited resource (reservoir water).
A C++ scheduler decides who gets how much, PostgreSQL stores everything and keeps allocations consistent,
a data-science pipeline forecasts demand and finds leaks, a FastAPI backend connects them, and a React web app
gives each organisation a **user panel** and the water department an **admin panel**.

Four allocation rules are implemented: **First come first served**, **Priority**, **Round robin** and the
project's own **Water-aware** scheduler (see `docs/contracts.md` for exactly how each one works).

**Version 2.0** adds what a water department asks for before a pilot (full mapping with evidence in
`docs/compliance.md`):

- a water **policy** with maker-checker approval;
- **officer overrides** with reasons, and printable **decision records**;
- a zone-wise **daily supply timetable** with tanker orders;
- **sensor / meter integration**, a zone map and complaint alerts;
- **two-factor sign-in**, HTTPS, a least-privilege database user, automatic **backups**;
- **DPDP** privacy rights;
- a Hindi interface for organisations.

## Run everything (needs Docker Desktop)

In PowerShell, from this folder:

    docker compose up --build

| What | Where |
|---|---|
| Web app (sign in here) | http://localhost:3000 |
| API and test page | http://localhost:8000/docs |

The first start takes a few minutes (it compiles the scheduler and installs Python packages). The `ml`
container fills in two years of consumption history, forecasts and leak alerts, then stops: that is normal.

- Next time, `docker compose up` is enough. Add `--build` after code changes.
- **Updating to a newer version keeps your data:** the backend upgrades an older database automatically when it
  starts (`db/migrate.sql`). To start completely fresh instead: `docker compose down -v`, then `docker compose up --build`.
- Something wrong? Open http://localhost:8000/health: it says what is missing and how to fix it. See also **Troubleshooting** below.
- Re-run the forecasts and leak detection: `docker compose run --rm ml`

Applying a plan uses up water and closes the requests. Use **Reset demo data** on the admin Overview tab to
put the 8 sample requests back (80,000 L requested) and refill the reservoir to the level you choose (default 50,000 L).
Reset deletes every request, including ones organisations sent themselves; accounts, issues and the audit log are kept.

## Signing in

Everyone signs in at http://localhost:3000. On the very first start the app creates:

| Who | Email | Password |
|---|---|---|
| Administrator | `admin@jalrakshak.local` | `Admin@1234` (you must choose a new one at first sign-in) |
| Second administrator (approves policy changes: maker-checker) | `approver@jalrakshak.local` | `Demo@1234` |
| Each sample organisation | `city.hospital@jalrakshak.local`, `green.valley.school@jalrakshak.local`, `rajpur.colony@jalrakshak.local`, `shanti.apartments@jalrakshak.local`, `metro.steel.works@jalrakshak.local`, `textile.mill@jalrakshak.local`, `central.mall@jalrakshak.local`, `town.park@jalrakshak.local` | `Demo@1234` |

Change these in `.env` (see `.env.example`) before the first start, or turn the demo sign-ins off with `JAL_DEMO_ACCOUNTS=0`.
New organisations can **Create an account** on the sign-in page; an administrator approves them and sets their priority.

**User panel** (organisations): Home (their priority, zone, reservoir status, alerts for their area, delivery
statistics), My requests (request water, follow each request, see how much it received, cancel while waiting),
Report a problem (leak, no supply, low pressure, water quality, and the department's reply), notifications, profile.

**Admin panel** (water department):

- **Overview**.
- **Allocate**: compare the four rules, apply one, or change it first with a recorded reason. A shadow (pilot) mode records a recommendation without applying it.
- **Supply**: daily zone timetable, tanker orders, zone map.
- **Forecast**.
- **Records**: every plan, with a printable decision record and CSV.
- **Policy**: propose a change; a second admin approves it.
- **Accounts** and **Issues**.
- **Field data**: register sensors, import meter CSV.
- **Compliance**: pilot indicators, operations status, privacy requests, incident register, audit log.
- **Settings**: reservoir, zones, organisations, supply network.

The organisation panel also shows its zone's **supply hours** and the policy in force. Both panels have an
**English / हिंदी** switch, **two-factor sign-in** (Profile), and **My data & privacy** (download your data,
ask for correction or erasure).

**Try the v2.0 features in 5 minutes:**

1. On Allocate, change two quantities in the override table, give a reason, apply, then download the decision record in Records.
2. On Policy, propose a change, then sign in as `approver@jalrakshak.local` and approve it.
3. On Supply, compute a timetable with a lower release, publish it, and plan tankers. Then sign in as City Hospital to see the supply hours.
4. On Field data, register a level sensor and run `python scripts/scada_simulator.py --level-key <key>`. The reservoir level then follows the sensor.

**HTTPS** (optional): `docker compose --profile https up --build`, then open https://localhost (web app and API
on one secure address).

**Backups:** the `backup` service dumps the database every 24 hours, keeping 14 days. List them with
`docker compose exec backup ls -lh /backups`; restore with `.\scripts\restore.ps1 <file>`.

For a real deployment, set in `.env`: `REQUIRE_ADMIN_MFA=1`, a new `APP_DB_PASSWORD`, `JAL_DEMO_ACCOUNTS=0`,
`CORS_ORIGINS`, `PRIVACY_CONTACT`.

Security: passwords are stored as salted PBKDF2-SHA256 hashes, sessions are random tokens stored only as hashes and
expire after 12 hours, five wrong passwords lock an account for 5 minutes, every endpoint checks the role, and users
only ever see their own organisation's data. Details in `docs/security.md`.

## Changing the data (reservoir capacity, zones, requesters)

Use the dashboard's **Settings** tab: change the reservoir's name, capacity and water level; add, rename or remove
zones and requesters; change a requester's priority. Changes are stored in PostgreSQL immediately and are used by
the next plan you compare or apply. The same changes are available through the API (`PATCH /resources/1`,
`/zones`, `/users`, see `docs/contracts.md`).

The database still protects itself: water can never exceed capacity, and a zone with requesters or a requester
with requests on record cannot be deleted (rename it instead). Changes survive restarts; `docker compose down -v`
returns everything to the seed data.

To look at the raw tables, open a SQL prompt:

    docker compose exec db psql -U postgres -d jalrakshak

or connect a tool such as DBeaver or pgAdmin to `localhost`, port **5433**, database `jalrakshak`, user `postgres`,
password `jal123`. (Port 5433 on your computer, so it never clashes with a PostgreSQL installed on Windows.)

### Optional settings

Copy `.env.example` to `.env` and edit it, then `docker compose up --build`. It covers the first administrator's
email and password, demo sign-ins, session length, allowed web addresses (CORS), the batch window, and the database port.

## Troubleshooting

Run these in PowerShell in the project folder to see what is happening:

    docker compose ps -a                          # which containers run, which stopped (and their exit codes)
    docker compose logs backend db ml --tail 50   # the last messages from each part

| What you see | Cause | Fix |
|---|---|---|
| Dashboard: "Cannot load data. database error", logs: `relation "…" does not exist` | The database was created by an older version | v0.5+ fixes this itself on start-up: `docker compose up --build`. Or start fresh: `docker compose down -v`, then `docker compose up --build` |
| Dashboard: "Cannot reach the API" | The backend container is not running | `docker compose ps -a`, then `docker compose logs backend` |
| `error during connect` / `dockerDesktopLinuxEngine` | Docker Desktop is not running | Start Docker Desktop, wait for "Engine running", try again |
| `port is already allocated` (8000 or 3000) | Another program uses that port | Close it, or find it with `netstat -ano \| findstr :8000` and end it in Task Manager |
| `password authentication failed for user "postgres"` | The data volume was made with another password | `docker compose down -v`, then `docker compose up --build` |
| `ml-1 exited with code 0` | Normal: the ML job fills the tables, then stops | Nothing to do |
| `ml-1 exited with code 1` | The ML job failed | `docker compose logs ml`; run it again with `docker compose run --rm ml` |
| Sign-in page says "Cannot reach the API" | The backend is not running, or still starting | Wait a minute; `docker compose logs backend` |
| "Wrong email or password" for `admin@jalrakshak.local` | The password was already changed, or `.env` sets another one | Ask another admin to reset it, or start fresh with `docker compose down -v` |
| "Too many wrong passwords" | Five wrong attempts lock the account for 5 minutes | Wait 5 minutes, or an admin resets the password |
| Odd file errors, files missing inside containers | The project folder is in OneDrive and some files are "online-only" | Right-click the folder in Explorer, choose "Always keep on this device", or move the project to e.g. `C:\Projects\jalrakshak` |

## Tests

Everything runs inside Docker, so nothing else needs installing on Windows:

    .\scripts\test.ps1

It runs:

- the C++ tests (10,425 checks, including 1,200 randomised runs, the manual-override mode and the zone timetable);
- 77 backend tests against a real PostgreSQL (service layer, HTTP API, sign-in security, roles, both panels, audit
  log, upgrading an old database, and all v2.0 requirements in `tests/test_v2.py`);
- 11 data-science tests;
- the concurrency demo.
(If PowerShell blocks the script: `Set-ExecutionPolicy -Scope Process Bypass`, then run it again.)

On Linux or macOS, or in Git Bash, without Docker:

    cd scheduler && make test
    cd backend && SCHEDULER_BIN=../scheduler/build/jalrakshak-scheduler python -m unittest discover -s tests -t . -v
    cd ml && python -m unittest discover -s tests -t . -v

The database tests are skipped unless a test database is configured (see `backend/tests/pgtest.py`).
GitHub runs all of this automatically on every push (`.github/workflows/ci.yml`).

## Documents

| File | What it is |
|---|---|
| `docs/REPORT.md` | Draft of the project report, with the real results |
| `docs/contracts.md` | Scheduler input/output, the four algorithms, metrics, REST API |
| `docs/er_diagram.md` (+ `.png`, `.svg`) | Database design and ER diagram |
| `docs/concurrency.md` | Why two operators can never allocate the same water twice, with the experiment |
| `docs/security.md` | Sign-in, roles, sessions, passwords, audit log: how and why |
| `experiments/README.md` | The four rules compared on 600 random scenarios |
| `ml/README.md` | Forecasting and leak detection, with accuracy results |
| `CHANGES.md` | What changed in each version (latest: 1.0) |

## Changing the dashboard

The built dashboard is already in `frontend/public/`. To change the source in `frontend/src/` you need Node.js:

    cd frontend
    npm install
    npm run build

## Layout

    scheduler/    C++ scheduler CLI (JSON in, JSON out)      db/           schema.sql, seed.sql
    backend/      FastAPI: auth, panels, service layer, tests   docs/         report, contracts, ER, concurrency, security
    frontend/     React app: sign-in, user and admin panels    experiments/  scenario runner and results
    ml/           forecasting + leak detection pipeline        scripts/      PowerShell test runner
