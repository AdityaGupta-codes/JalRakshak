-- JalRakshak schema v0.1 (freeze this by end of Day 2)
-- Run: psql -U postgres -d jalrakshak -f db/schema.sql

DROP TABLE IF EXISTS alerts, predictions, consumption, allocations,
                     scheduler_runs, water_requests, reservoirs, users, zones CASCADE;

CREATE TABLE zones (
    zone_id     SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    population  INTEGER NOT NULL CHECK (population > 0)
);

CREATE TABLE users (
    user_id    SERIAL PRIMARY KEY,
    name       TEXT NOT NULL,
    user_type  TEXT NOT NULL CHECK (user_type IN
               ('hospital','domestic','school','industry','commercial','park')),
    priority   SMALLINT NOT NULL CHECK (priority BETWEEN 1 AND 5),  -- 5 = highest
    zone_id    INTEGER NOT NULL REFERENCES zones(zone_id)
);

CREATE TABLE reservoirs (
    reservoir_id      SERIAL PRIMARY KEY,
    name              TEXT NOT NULL UNIQUE,
    capacity_litres   NUMERIC(12,2) NOT NULL CHECK (capacity_litres > 0),
    available_litres  NUMERIC(12,2) NOT NULL CHECK (available_litres >= 0),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (available_litres <= capacity_litres)   -- DB-level safety net
);

CREATE TABLE water_requests (
    request_id        SERIAL PRIMARY KEY,
    user_id           INTEGER NOT NULL REFERENCES users(user_id),
    reservoir_id      INTEGER NOT NULL REFERENCES reservoirs(reservoir_id),
    requested_litres  NUMERIC(12,2) NOT NULL CHECK (requested_litres > 0),
    urgency           SMALLINT NOT NULL DEFAULT 3 CHECK (urgency BETWEEN 1 AND 5),
    arrival_time      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deadline          TIMESTAMPTZ NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending' CHECK (status IN
                      ('pending','approved','partial','rejected','cancelled')),
    CHECK (deadline > arrival_time)
);
CREATE INDEX idx_requests_status   ON water_requests(status);
CREATE INDEX idx_requests_user     ON water_requests(user_id);
CREATE INDEX idx_requests_arrival  ON water_requests(arrival_time);

-- One row per scheduler execution (also stores experiment results)
CREATE TABLE scheduler_runs (
    run_id            SERIAL PRIMARY KEY,
    algorithm         TEXT NOT NULL CHECK (algorithm IN
                      ('fcfs','priority','round_robin','water_aware')),
    scenario          TEXT,                       -- e.g. 'normal','severe_scarcity'
    reservoir_id      INTEGER REFERENCES reservoirs(reservoir_id),
    available_before  NUMERIC(12,2) NOT NULL,
    available_after   NUMERIC(12,2) NOT NULL,
    metrics           JSONB,                      -- waiting time, Jain index, ...
    committed         BOOLEAN NOT NULL DEFAULT FALSE,  -- FALSE = simulation only
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE allocations (
    allocation_id     SERIAL PRIMARY KEY,
    run_id            INTEGER NOT NULL REFERENCES scheduler_runs(run_id),
    request_id        INTEGER NOT NULL REFERENCES water_requests(request_id),
    reservoir_id      INTEGER NOT NULL REFERENCES reservoirs(reservoir_id),
    allocated_litres  NUMERIC(12,2) NOT NULL CHECK (allocated_litres >= 0),
    wait_minutes      NUMERIC(10,2),
    score             NUMERIC(8,4),               -- water-aware score (nullable)
    allocated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, request_id)
);
CREATE INDEX idx_alloc_request ON allocations(request_id);

CREATE TABLE consumption (
    consumption_id  BIGSERIAL PRIMARY KEY,
    zone_id         INTEGER NOT NULL REFERENCES zones(zone_id),
    recorded_on     DATE NOT NULL,
    litres          NUMERIC(12,2) NOT NULL CHECK (litres >= 0),
    is_injected_leak BOOLEAN NOT NULL DEFAULT FALSE,  -- ground truth for anomaly eval
    UNIQUE (zone_id, recorded_on)
);

CREATE TABLE predictions (
    prediction_id    SERIAL PRIMARY KEY,
    zone_id          INTEGER NOT NULL REFERENCES zones(zone_id),
    target_date      DATE NOT NULL,
    predicted_litres NUMERIC(12,2) NOT NULL,
    model_name       TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (zone_id, target_date, model_name)
);

CREATE TABLE alerts (
    alert_id    SERIAL PRIMARY KEY,
    zone_id     INTEGER REFERENCES zones(zone_id),
    alert_type  TEXT NOT NULL CHECK (alert_type IN
                ('low_water','high_demand','anomaly')),
    severity    TEXT NOT NULL CHECK (severity IN ('info','warning','critical')),
    message     TEXT NOT NULL,
    resolved    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------
-- Allocation transaction pattern (used by FastAPI /allocate/commit)
-- ---------------------------------------------------------------
-- BEGIN;
--   SELECT available_litres FROM reservoirs
--    WHERE reservoir_id = :rid FOR UPDATE;      -- row lock: serialises writers
--   -- run scheduler on the locked value, then:
--   INSERT INTO scheduler_runs (...) RETURNING run_id;
--   INSERT INTO allocations (...);
--   UPDATE reservoirs SET available_litres = available_litres - :total,
--                         updated_at = now() WHERE reservoir_id = :rid;
--   UPDATE water_requests SET status = :new_status WHERE request_id = :id;
-- COMMIT;     -- CHECK (available_litres >= 0) rejects any over-allocation
