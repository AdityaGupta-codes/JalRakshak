# JalRakshak: Intelligent Water Resource Allocation and Scheduling

Water requests are treated as OS processes competing for a limited resource.
C++ scheduler + PostgreSQL + FastAPI (+ ML and React dashboard, coming next).

## Run everything (needs Docker Desktop)

    docker rm -f jalrakshak-db        # only if you created it manually earlier (frees port 5432)
    docker compose up --build

- API docs (try every endpoint in the browser): http://localhost:8000/docs
- Fresh database (re-runs schema + seed): `docker compose down -v` then `docker compose up --build`

## Try the API (PowerShell)

    curl http://localhost:8000/resources
    curl -Method POST http://localhost:8000/allocate/simulate -ContentType "application/json" -Body '{"algorithms":["fcfs"]}'
    curl -Method POST http://localhost:8000/allocate/commit   -ContentType "application/json" -Body '{"algorithm":"fcfs"}'

## Tests

    cd scheduler && make test
    cd backend && SCHEDULER_BIN=../scheduler/build/jalrakshak-scheduler python -m unittest discover -s tests -t . -v

## Layout

    scheduler/   C++ scheduler CLI (JSON in -> JSON out)      db/       schema.sql, seed.sql
    backend/     FastAPI + service layer                       docs/     contracts.md
    experiments/ sample scheduler input/output
