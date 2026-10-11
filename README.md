# JalRakshak: Intelligent Water Resource Allocation and Scheduling

Water requests are treated like OS processes competing for one limited resource (reservoir water).
A C++ scheduler decides who gets how much, PostgreSQL stores everything and keeps allocations consistent,
a data-science pipeline forecasts demand and finds leaks, a FastAPI backend connects them, and a React
dashboard shows and compares the results.

Four allocation rules are implemented: **First come first served**, **Priority**, **Round robin** and the
project's own **Water-aware** scheduler (see `docs/contracts.md` for exactly how each one works).

## Run everything (needs Docker Desktop)

In PowerShell, from this folder:

    docker compose up --build

| What | Where |
|---|---|
| Dashboard | http://localhost:3000 |
| API and test page | http://localhost:8000/docs |

The first start takes a few minutes (it compiles the scheduler and installs Python packages). The `ml`
container fills in two years of consumption history, forecasts and leak alerts, then stops: that is normal.

- Next time, `docker compose up` is enough. Add `--build` after code changes.
- **Updating to a newer version keeps your data:** the backend upgrades an older database automatically when it
  starts (`db/migrate.sql`). To start completely fresh instead: `docker compose down -v`, then `docker compose up --build`.
- Something wrong? Open http://localhost:8000/health: it says what is missing and how to fix it. See also **Troubleshooting** below.
- Re-run the forecasts and leak detection: `docker compose run --rm ml`

Applying a plan uses up water and closes the requests. Use **Reset demo data** on the dashboard's Overview tab to
put the 8 sample requests back (80,000 L requested) and refill the reservoir to the level you choose (default 50,000 L).

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

Copy `.env.example` to `.env` and edit it, then `docker compose up --build`:

- `JAL_ADMIN_TOKEN=some-secret`: adding requests, applying plans, resetting, Settings changes and resolving alerts then need
  this token (the dashboard shows an **Unlock** button). Empty = open, which is easiest for demos.
- `BATCH_WINDOW_MINUTES=all` (default): rank every pending request together. A number makes the scheduler start
  that many minutes after the first request instead.

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
| Odd file errors, files missing inside containers | The project folder is in OneDrive and some files are "online-only" | Right-click the folder in Explorer, choose "Always keep on this device", or move the project to e.g. `C:\Projects\jalrakshak` |

## Tests

Everything runs inside Docker, so nothing else needs installing on Windows:

    .\scripts\test.ps1

It runs the C++ tests (10,353 checks, including 1,200 randomised runs), the backend tests against a real
PostgreSQL (service layer, the HTTP API, the admin token, and upgrading an old database), the data-science tests,
and the concurrency demo.
(If PowerShell blocks the script: `Set-ExecutionPolicy -Scope Process Bypass`, then run it again.)

On Linux or macOS, or in Git Bash, without Docker:

    cd scheduler && make test
    cd backend && SCHEDULER_BIN=../scheduler/build/jalrakshak-scheduler python -m unittest discover -s tests -t . -v
    cd ml && python -m unittest discover -s tests -t . -v

The database tests are skipped unless a test database is configured (see `backend/tests/pgtest.py`).
GitHub runs all of this automatically on every push (`.github/workflows/ci.yml`).

## Try the dashboard without Docker or PostgreSQL

A small demo API keeps the data in memory and runs the real C++ scheduler (Python standard library only).
On Windows, with Python and a g++ compiler (MSYS2): `.\scripts\demo-without-docker.ps1`. Elsewhere:

    cd scheduler && make && cd ..
    python frontend/dev/mock_api.py --bin scheduler/build/jalrakshak-scheduler --port 8000
    cd frontend/public && python -m http.server 3000

Open http://localhost:3000.

## Documents

| File | What it is |
|---|---|
| `docs/REPORT.md` | Draft of the project report, with the real results |
| `docs/contracts.md` | Scheduler input/output, the four algorithms, metrics, REST API |
| `docs/er_diagram.md` (+ `.png`, `.svg`) | Database design and ER diagram |
| `docs/concurrency.md` | Why two operators can never allocate the same water twice, with the experiment |
| `experiments/README.md` | The four rules compared on 600 random scenarios |
| `ml/README.md` | Forecasting and leak detection, with accuracy results |
| `CHANGES.md` | What changed in versions 0.3 to 0.5 |

## Changing the dashboard

The built dashboard is already in `frontend/public/`. To change the source in `frontend/src/` you need Node.js:

    cd frontend
    npm install
    npm run build

## Layout

    scheduler/    C++ scheduler CLI (JSON in, JSON out)      db/           schema.sql, seed.sql
    backend/      FastAPI + service layer + tests            docs/         report draft, contracts, ER, concurrency
    frontend/     React dashboard (src/ and built public/)   experiments/  scenario runner and results
    ml/           forecasting + leak detection pipeline      scripts/      PowerShell helpers (tests, demo)
