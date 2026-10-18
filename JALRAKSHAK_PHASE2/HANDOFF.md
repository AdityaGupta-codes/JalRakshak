# JalRakshak: handoff notes (v2.0)

To continue in a new chat, connect this folder (or attach a zip of it) and say "read HANDOFF.md and continue".

## Project in one paragraph
Team HydroLab's PBL project: water requests are treated as OS processes. A C++ scheduler (JSON in, JSON out)
allocates reservoir water with FCFS, Priority, Round robin or Water-aware; PostgreSQL stores everything; FastAPI
orchestrates, locking the reservoir row (`SELECT ... FOR UPDATE`) when a plan is committed; a Python ML pipeline
forecasts demand and finds leaks; a React app has a sign-in page, a **user panel** for organisations and an
**admin panel** for the water department. The user is a student on Windows with Docker Desktop and little backend
experience: explain in plain language, give PowerShell commands.

## State (12 Oct 2026)
- v1.0 adds accounts and roles, user panel, admin panel (accounts, issues, audit log). See `CHANGES.md`.
- v2.0 implements the water-authority requirements: policy with maker-checker, override with reason, decision
  records, shadow mode and KPIs, zone timetable (C++ `mode: timetable`), tankers, sensors and meter import, zone
  map, complaint alerts, TOTP 2FA, HTTPS profile, least-privilege DB user, backups, rate limits, CSP, DPDP
  (consent, export, erasure, retention), incident register, Hindi. Mapping and evidence: `docs/compliance.md`.
- v2.0 verified 15 Oct 2026 in the cloud test environment: C++ 10,425 checks, backend 77, ML 11, concurrency demo,
  and a browser walkthrough of every new screen (no CSP violations, no sideways scrolling at 390 px).
  NOT verified: Docker on the user's laptop, the HTTPS profile, the backup container, the restricted DB user under
  real psycopg2 (tested with the test driver and has_table_privilege), and GitHub CI.
- Tests: C++ 10,353 checks; backend 57 tests (real PostgreSQL + HTTP API + sign-in/panels + DB upgrade);
  ML 10 tests. Browser walkthrough of both panels against the real API passed.
- `.\scripts\test.ps1` runs all tests in Docker; GitHub Actions runs them on every push.
- Not yet confirmed on the user's laptop: v0.5 and v1.0. The user's old database (from v0.2) caused
  `relation "model_evaluations" does not exist`; v0.5+ upgrades it automatically at start-up
  (`docker compose up --build`), or start fresh with `docker compose down -v`.

## Sign-ins created on first start
admin@jalrakshak.local / Admin@1234 (forced change at first sign-in); city.hospital@jalrakshak.local,
town.park@jalrakshak.local, ... (one per sample organisation) / Demo@1234. Configurable in `.env`.

## Key findings to keep honest
- Water-aware is NOT best on everything. Round robin is fairer and faster; Priority gives more to important users
  at severe scarcity. Water-aware never leaves anyone with nothing; best importance-weighted delivery at moderate
  scarcity. `guarantee_floor` (0.3) trades fairness against priority.
- ML: Random forest MAE 1,223 L (5.1 %) vs 1,849 L baseline; leak detection precision 0.88, recall 0.66. Synthetic data.
- Concurrency: with locks 1 plan / 80,000 L; without, 2 plans / 160,000 L.

## Design decisions
- Backend batch window "all"; scarcity factor per request; forecast feeds predicted demand.
- Schema changes: edit schema.sql or add to db/migrate.sql (idempotent); existing volumes are never re-initialised,
  the backend runs migrate.sql at start-up. v1.0 tables live only in migrate.sql (it runs after schema.sql on new DBs).
- Auth: PBKDF2 (stdlib, no extra packages), DB sessions storing token hashes, Bearer header (no cookies, no CSRF).
- Audit entries written in the same transaction as the change; audit_log append-only via trigger.
- Users only see their own organisation's rows; other organisations' rows answer 404.
- Demo reset deletes all requests (including users' own) but keeps accounts, issues, audit log.

## Possible next steps
Password reset by email; finer roles (operator/viewer); real data (CWC reservoir bulletins, IMD rainfall);
optimal LP baseline; heap-based scheduler + benchmarks; live sensors (MQTT); deploy online with HTTPS.

## Commands (PowerShell)
    docker compose up --build                # app :3000, API docs :8000/docs
    docker compose down -v; docker compose up --build   # completely fresh database
    docker compose run --rm ml               # recompute forecasts and alerts
    .\scripts\test.ps1                       # every test suite, in Docker
