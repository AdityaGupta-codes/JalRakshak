# JalRakshak: handoff notes (v0.5)

To continue in a new chat, connect this folder (or attach a zip of it) and say "read HANDOFF.md and continue".

## Project in one paragraph
Team HydroLab's PBL project: water requests are treated as OS processes. A C++ scheduler (JSON in, JSON out)
allocates reservoir water with FCFS, Priority, Round robin or Water-aware; PostgreSQL stores users, requests,
runs, allocations, consumption, forecasts and alerts; FastAPI orchestrates, locking the reservoir row
(`SELECT ... FOR UPDATE`) when a plan is committed; a Python ML pipeline forecasts demand and finds leaks; a
React dashboard compares the algorithms. The user is a student on Windows with Docker Desktop and little
backend experience: explain in plain language, give PowerShell commands.

## State (11 Oct 2026)
Everything in the original plan is built and tested; see `CHANGES.md` for what v0.3 and v0.4 fixed and added.
v0.4 added the dashboard Settings tab (edit reservoir capacity/level, zones, requesters) with matching API endpoints.
v0.5 fixed the user's real error (old Docker volume lacked model_evaluations): db/migrate.sql is applied
automatically by the backend and the ML job; /health explains schema problems; DB host port is now 5433.
- C++ `make test`: 10,353 checks pass. Backend: 46 tests pass (incl. real-PostgreSQL and HTTP API tests).
  ML: 10 tests pass. Dashboard tested in headless Chromium against `frontend/dev/mock_api.py`.
- `.\scripts\test.ps1` runs all of it in Docker; GitHub Actions runs it on every push.
- **Not yet confirmed on the user's machine:** `docker compose up --build` for v0.3/v0.4 (needs `docker compose down -v`
  first because the schema changed), and `scripts/test.ps1`.

## Key findings to keep honest
- Water-aware is NOT best on everything. Round robin is fairer and much faster; Priority gives more to important
  users at severe scarcity. Water-aware never leaves anyone with nothing and has the best importance-weighted
  delivery at moderate scarcity. `guarantee_floor` (0.3) trades fairness against priority.
- Sample scenario (dashboard): Water-aware 0.68 importance-weighted, 0 left empty, 0 missed deadlines; Priority
  0.74 but 3 left empty; Round robin 0.91 fairness, 29 min wait.
- ML: Random forest MAE 1,223 L (5.1 %) vs 1,849 L baseline; robust z-score leak detection precision 0.88,
  recall 0.66. All data is synthetic.
- Concurrency: with locks 1 plan / 80,000 L; without, 2 plans / 160,000 L.

## Design decisions
- Backend batch window "all": every pending request is ranked together (fixes the factory-first problem).
- Scarcity factor is per request; expected demand = today's forecast from the selected model, else pending litres.
- Demo reset reloads the 8 sample requests via `load_sample_requests()` in schema.sql (by user id, so renames are safe)
  and refills to a chosen level; Settings changes are kept.
- Schema changes: edit schema.sql AND add an idempotent step to db/migrate.sql (existing volumes are never re-initialised).
- Deleting is only allowed when nothing refers to the row (zone without requesters, requester without requests).
- Frontend is React pre-bundled into `frontend/public/` so no Node is needed to run it.
- Admin token is optional (`JAL_ADMIN_TOKEN`), off by default for demos.

## Possible next steps
Fill in names and format of `docs/REPORT.md` (and convert to the department's Word template); presentation slides;
preemptive Water-aware; multiple reservoirs; user accounts with roles.

## Commands (PowerShell)
    docker compose down -v; docker compose up --build   # first time after updating
    docker compose up --build                           # dashboard :3000, API docs :8000/docs
    docker compose run --rm ml                          # recompute forecasts and alerts
    .\scripts\test.ps1                                  # every test suite, in Docker
