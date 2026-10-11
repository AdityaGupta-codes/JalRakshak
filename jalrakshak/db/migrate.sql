-- JalRakshak database upgrade (safe to run any number of times).
--
-- Docker only runs schema.sql the FIRST time the database volume is created. A database made by an older
-- version of the project therefore lacks newer tables and functions, and the app would fail with
-- "relation does not exist" errors. The backend and the ML job run this file every time they start, so an
-- older database is brought up to date automatically, without losing its data.
--
-- Rule for future changes: every change to schema.sql also gets an idempotent step here
-- (CREATE ... IF NOT EXISTS, CREATE OR REPLACE, ADD COLUMN IF NOT EXISTS).

BEGIN;
-- Only one program upgrades at a time (backend and ML job start together).
SELECT pg_advisory_xact_lock(724001);

-- v0.3: forecasting model scores
CREATE TABLE IF NOT EXISTS model_evaluations (
    evaluation_id  SERIAL PRIMARY KEY,
    model_name     TEXT NOT NULL,
    mae_litres     NUMERIC(12,2) NOT NULL,
    rmse_litres    NUMERIC(12,2) NOT NULL,
    mape           NUMERIC(8,4) NOT NULL,          -- mean absolute percentage error, 0.05 = 5 percent
    test_days      INTEGER NOT NULL,
    is_selected    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- v0.3 / v0.4: the 8 sample requests used by the seed and "Reset demo data"
CREATE OR REPLACE FUNCTION load_sample_requests(rid INTEGER) RETURNS INTEGER AS $$
    INSERT INTO water_requests (user_id, reservoir_id, requested_litres, urgency, arrival_time, deadline)
    SELECT u.user_id, rid, s.litres, s.urgency,
           now() + s.arrives * interval '1 minute', now() + s.deadline * interval '1 minute'
    FROM (VALUES
        (5, 20000, 2,  0, 480),   -- Metro Steel Works
        (3, 12000, 4,  5, 180),   -- Rajpur Colony
        (1, 10000, 5, 10,  60),   -- City Hospital
        (7,  8000, 2, 12, 360),   -- Central Mall
        (2,  6000, 3, 15, 240),   -- Green Valley School
        (8,  4000, 1, 20, 600),   -- Town Park
        (4, 15000, 4, 25, 180),   -- Shanti Apartments
        (6,  5000, 2, 30, 480)    -- Textile Mill
    ) AS s(uid, litres, urgency, arrives, deadline)
    JOIN users u ON u.user_id = s.uid
    ORDER BY s.arrives;
    SELECT COUNT(*)::INTEGER FROM water_requests WHERE reservoir_id = rid AND status = 'pending';
$$ LANGUAGE sql;

-- Remember which version this database has been upgraded to.
CREATE TABLE IF NOT EXISTS schema_info (
    id          INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    version     TEXT NOT NULL,
    upgraded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO schema_info (id, version) VALUES (1, '0.5')
ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version, upgraded_at = now();
COMMIT;
