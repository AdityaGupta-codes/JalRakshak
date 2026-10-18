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

-- ---------------------------------------------------------------
-- v1.0: accounts, sign-in sessions, notifications, issues, audit log
-- ---------------------------------------------------------------

-- People who sign in. role 'admin' runs the system; role 'requester' belongs to an organisation in `users`
-- (a hospital, school, colony ...). A sign-up waits in status 'pending' until an admin approves it.
CREATE TABLE IF NOT EXISTS accounts (
    account_id           SERIAL PRIMARY KEY,
    email                TEXT NOT NULL,
    password_hash        TEXT NOT NULL,                  -- pbkdf2_sha256$iterations$salt$hash, never the password
    full_name            TEXT NOT NULL,
    phone                TEXT,
    role                 TEXT NOT NULL CHECK (role IN ('admin', 'requester')),
    status               TEXT NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending', 'active', 'disabled', 'rejected')),
    user_id              INTEGER REFERENCES users(user_id),           -- the organisation (requesters)
    org_name             TEXT,                                        -- what a sign-up asked for
    org_type             TEXT,
    org_zone_id          INTEGER REFERENCES zones(zone_id),
    must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
    failed_logins        INTEGER NOT NULL DEFAULT 0,
    locked_until         TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at        TIMESTAMPTZ,
    CHECK (role = 'admin' OR status <> 'active' OR user_id IS NOT NULL)  -- an active requester has an organisation
);
CREATE UNIQUE INDEX IF NOT EXISTS accounts_email_key ON accounts (lower(email));
CREATE INDEX IF NOT EXISTS idx_accounts_user ON accounts(user_id);

-- Signed-in sessions. Only a SHA-256 hash of the token is stored, so a database leak does not leak sessions.
CREATE TABLE IF NOT EXISTS sessions (
    token_hash    TEXT PRIMARY KEY,
    account_id    INTEGER NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ NOT NULL,
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sessions_account ON sessions(account_id);

-- Messages shown in a user's notification list (plan decisions, issue replies, account approval).
CREATE TABLE IF NOT EXISTS notifications (
    notification_id SERIAL PRIMARY KEY,
    account_id      INTEGER NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    kind            TEXT NOT NULL CHECK (kind IN ('allocation', 'issue', 'account', 'alert', 'info')),
    title           TEXT NOT NULL,
    body            TEXT NOT NULL,
    is_read         BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_notifications_account ON notifications(account_id, is_read);

-- Problems reported by users (leaks, no supply ...), handled by admins.
CREATE TABLE IF NOT EXISTS issues (
    issue_id        SERIAL PRIMARY KEY,
    account_id      INTEGER REFERENCES accounts(account_id),
    user_id         INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    zone_id         INTEGER REFERENCES zones(zone_id) ON DELETE SET NULL,
    category        TEXT NOT NULL CHECK (category IN ('leak', 'no_supply', 'low_pressure', 'quality', 'other')),
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,
    location        TEXT,
    status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'in_progress', 'resolved', 'closed')),
    admin_response  TEXT,
    handled_by      INTEGER REFERENCES accounts(account_id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_issues_status ON issues(status);
CREATE INDEX IF NOT EXISTS idx_issues_account ON issues(account_id);

-- Who did what, when. Append-only: a trigger refuses UPDATE and DELETE, so history cannot be rewritten.
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id    BIGSERIAL PRIMARY KEY,
    account_id  INTEGER REFERENCES accounts(account_id),
    actor       TEXT,                     -- email at the time (kept even if the account changes)
    action      TEXT NOT NULL,            -- e.g. plan.apply, reservoir.update, account.approve
    entity      TEXT,
    entity_id   TEXT,
    details     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);

CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger AS $fn$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only: rows cannot be changed or deleted';
END
$fn$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS audit_log_no_change ON audit_log;
CREATE TRIGGER audit_log_no_change BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();

-- Who created a request, and an optional note from the requester.
ALTER TABLE water_requests ADD COLUMN IF NOT EXISTS created_by INTEGER REFERENCES accounts(account_id);
ALTER TABLE water_requests ADD COLUMN IF NOT EXISTS note TEXT;


-- ===============================================================================================
-- v2.0: governance (policy with maker-checker), network timetable, tankers, telemetry, GIS,
--       privacy (DPDP), security (2FA, incidents). Every step is idempotent.
-- ===============================================================================================

-- Water policy: who has which priority and guaranteed share, the per-person norm, sharing weights.
-- A change is proposed (draft -> pending) by one officer and approved by a different one (maker-checker).
CREATE TABLE IF NOT EXISTS policies (
    policy_id              SERIAL PRIMARY KEY,
    version                INTEGER NOT NULL,
    title                  TEXT NOT NULL,
    reference              TEXT,                       -- government order / circular the policy follows
    status                 TEXT NOT NULL DEFAULT 'draft'
                           CHECK (status IN ('draft', 'pending', 'approved', 'rejected', 'superseded')),
    class_priority         JSONB NOT NULL,             -- {"hospital": 5, ...}
    class_min_share        JSONB NOT NULL,             -- {"hospital": 0.5, ...} guaranteed share when short
    essential_classes      JSONB NOT NULL DEFAULT '["hospital", "domestic"]',
    weights                JSONB NOT NULL,             -- water-aware factor weights
    guarantee_floor        NUMERIC(4,3) NOT NULL CHECK (guarantee_floor BETWEEN 0 AND 1),
    lpcd                   NUMERIC(8,2) NOT NULL CHECK (lpcd > 0),   -- litres per person per day norm
    zone_min_share         NUMERIC(4,3) NOT NULL DEFAULT 0.3 CHECK (zone_min_share BETWEEN 0 AND 1),
    tanker_trigger_lpcd    NUMERIC(8,2) NOT NULL DEFAULT 70 CHECK (tanker_trigger_lpcd >= 0),
    tanker_capacity_litres NUMERIC(12,2) NOT NULL DEFAULT 10000 CHECK (tanker_capacity_litres > 0),
    supply_start_hour      SMALLINT NOT NULL DEFAULT 5 CHECK (supply_start_hour BETWEEN 0 AND 23),
    supply_end_hour        SMALLINT NOT NULL DEFAULT 23 CHECK (supply_end_hour BETWEEN 1 AND 24),
    notes                  TEXT,
    proposed_by            INTEGER REFERENCES accounts(account_id),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    submitted_at           TIMESTAMPTZ,
    decided_by             INTEGER REFERENCES accounts(account_id),
    decided_at             TIMESTAMPTZ,
    decision_note          TEXT,
    CHECK (supply_start_hour < supply_end_hour)
);
CREATE UNIQUE INDEX IF NOT EXISTS policies_one_active ON policies ((TRUE)) WHERE status = 'approved';
CREATE UNIQUE INDEX IF NOT EXISTS policies_version_key ON policies (version);
-- The first policy reproduces v1.0 behaviour (same priorities, 30 % floor for everyone). It is NOT an
-- official order: an administrator should propose the real one and a second officer approve it.
INSERT INTO policies (version, title, reference, status, class_priority, class_min_share, weights,
                      guarantee_floor, lpcd, notes, decided_at, decision_note)
SELECT 1, 'Initial demo policy', 'Demo values - replace with the applicable government order', 'approved',
       '{"hospital": 5, "school": 4, "domestic": 4, "commercial": 2, "industry": 2, "park": 1}',
       '{"hospital": 0.3, "school": 0.3, "domestic": 0.3, "commercial": 0.3, "industry": 0.3, "park": 0.3}',
       '{"priority": 0.30, "urgency": 0.25, "scarcity": 0.15, "demand_impact": 0.15, "fairness": 0.15}',
       0.3, 135, '135 litres per person per day is the CPHEEO norm for urban piped supply with sewerage.',
       now(), 'Created automatically by the v2.0 upgrade'
WHERE NOT EXISTS (SELECT 1 FROM policies);

-- An organisation whose priority was set by hand (not from its class in the policy).
ALTER TABLE users ADD COLUMN IF NOT EXISTS priority_override BOOLEAN NOT NULL DEFAULT FALSE;

-- Applied plans remember the policy, any officer override and the alternatives that were compared.
ALTER TABLE scheduler_runs ADD COLUMN IF NOT EXISTS policy_id INTEGER REFERENCES policies(policy_id);
ALTER TABLE scheduler_runs ADD COLUMN IF NOT EXISTS override_reason TEXT;
ALTER TABLE scheduler_runs ADD COLUMN IF NOT EXISTS overrides JSONB;
ALTER TABLE scheduler_runs ADD COLUMN IF NOT EXISTS comparison JSONB;
ALTER TABLE scheduler_runs ADD COLUMN IF NOT EXISTS created_by INTEGER REFERENCES accounts(account_id);

-- Network: which source feeds a zone, how much its inlet main carries, the zone's supply priority, map shape.
ALTER TABLE zones ADD COLUMN IF NOT EXISTS reservoir_id INTEGER REFERENCES reservoirs(reservoir_id);
ALTER TABLE zones ADD COLUMN IF NOT EXISTS inlet_capacity_lph NUMERIC(14,2) CHECK (inlet_capacity_lph > 0);
ALTER TABLE zones ADD COLUMN IF NOT EXISTS supply_priority SMALLINT NOT NULL DEFAULT 3
    CHECK (supply_priority BETWEEN 1 AND 5);
ALTER TABLE zones ADD COLUMN IF NOT EXISTS boundary JSONB;          -- GeoJSON Polygon / MultiPolygon
ALTER TABLE reservoirs ADD COLUMN IF NOT EXISTS daily_release_litres NUMERIC(14,2) CHECK (daily_release_litres >= 0);
ALTER TABLE reservoirs ADD COLUMN IF NOT EXISTS pump_capacity_lph NUMERIC(14,2) CHECK (pump_capacity_lph > 0);

-- Demo network values (only where nothing was set yet). Sizes are illustrative, not from a real city.
UPDATE reservoirs SET daily_release_litres = 12000000, pump_capacity_lph = 1000000
 WHERE name = 'Main Reservoir' AND daily_release_litres IS NULL;
UPDATE zones z SET reservoir_id = (SELECT MIN(reservoir_id) FROM reservoirs) WHERE z.reservoir_id IS NULL;
UPDATE zones SET inlet_capacity_lph = CASE name WHEN 'North Zone' THEN 600000 WHEN 'Central Zone' THEN 900000
                                               WHEN 'South Zone' THEN 450000 END
 WHERE inlet_capacity_lph IS NULL AND name IN ('North Zone', 'Central Zone', 'South Zone');
UPDATE zones SET boundary = CASE name
    WHEN 'North Zone'   THEN '{"type":"Polygon","coordinates":[[[77.580,12.990],[77.640,12.990],[77.650,13.030],[77.600,13.045],[77.570,13.020],[77.580,12.990]]]}'
    WHEN 'Central Zone' THEN '{"type":"Polygon","coordinates":[[[77.570,12.950],[77.630,12.945],[77.640,12.990],[77.580,12.990],[77.565,12.970],[77.570,12.950]]]}'
    WHEN 'South Zone'   THEN '{"type":"Polygon","coordinates":[[[77.575,12.905],[77.625,12.900],[77.630,12.945],[77.570,12.950],[77.575,12.905]]]}'
    END::jsonb
 WHERE boundary IS NULL AND name IN ('North Zone', 'Central Zone', 'South Zone');

-- Daily zone supply timetables. One published timetable per day.
CREATE TABLE IF NOT EXISTS supply_timetables (
    timetable_id  SERIAL PRIMARY KEY,
    for_date      DATE NOT NULL,
    status        TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'withdrawn')),
    policy_id     INTEGER REFERENCES policies(policy_id),
    input         JSONB NOT NULL,
    result        JSONB NOT NULL,
    note          TEXT,
    created_by    INTEGER REFERENCES accounts(account_id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_by  INTEGER REFERENCES accounts(account_id),
    published_at  TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS timetables_one_published ON supply_timetables (for_date) WHERE status = 'published';

-- Tanker orders: extra supply to zones the piped timetable cannot serve enough (one row per zone and day,
-- with the number of tanker loads).
CREATE TABLE IF NOT EXISTS tanker_trips (
    trip_id       SERIAL PRIMARY KEY,
    zone_id       INTEGER NOT NULL REFERENCES zones(zone_id),
    timetable_id  INTEGER REFERENCES supply_timetables(timetable_id),
    planned_for   DATE NOT NULL,
    litres        NUMERIC(12,2) NOT NULL CHECK (litres > 0),   -- total litres of this order
    loads         INTEGER NOT NULL DEFAULT 1 CHECK (loads >= 1), -- number of tanker loads
    status        TEXT NOT NULL DEFAULT 'planned' CHECK (status IN ('planned', 'dispatched', 'delivered', 'cancelled')),
    origin        TEXT NOT NULL DEFAULT 'manual' CHECK (origin IN ('timetable', 'manual')),
    vehicle       TEXT,
    notes         TEXT,
    created_by    INTEGER REFERENCES accounts(account_id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_tankers_date ON tanker_trips(planned_for);

-- Telemetry: SCADA level sensors and zone flow meters push readings with their own key.
CREATE TABLE IF NOT EXISTS devices (
    device_id     SERIAL PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    kind          TEXT NOT NULL CHECK (kind IN ('reservoir_level', 'zone_flow')),
    reservoir_id  INTEGER REFERENCES reservoirs(reservoir_id),
    zone_id       INTEGER REFERENCES zones(zone_id),
    key_hash      TEXT NOT NULL UNIQUE,              -- SHA-256 of the device key (the key is shown once)
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    last_seen_at  TIMESTAMPTZ,
    last_value    NUMERIC(14,2),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((kind = 'reservoir_level' AND reservoir_id IS NOT NULL) OR (kind = 'zone_flow' AND zone_id IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS readings (
    reading_id   BIGSERIAL PRIMARY KEY,
    device_id    INTEGER NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    measured_at  TIMESTAMPTZ NOT NULL,
    value        NUMERIC(14,2) NOT NULL CHECK (value >= 0),
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (device_id, measured_at)
);
CREATE INDEX IF NOT EXISTS idx_readings_device_time ON readings(device_id, measured_at DESC);

-- Where a consumption row came from: generated by the ML job, aggregated from meters, or imported.
ALTER TABLE consumption ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'synthetic';
ALTER TABLE consumption DROP CONSTRAINT IF EXISTS consumption_source_check;
ALTER TABLE consumption ADD CONSTRAINT consumption_source_check CHECK (source IN ('synthetic', 'meter', 'import'));

-- New alert and notification kinds
ALTER TABLE alerts DROP CONSTRAINT IF EXISTS alerts_alert_type_check;
ALTER TABLE alerts ADD CONSTRAINT alerts_alert_type_check
    CHECK (alert_type IN ('low_water', 'high_demand', 'anomaly', 'complaints', 'telemetry'));
ALTER TABLE notifications DROP CONSTRAINT IF EXISTS notifications_kind_check;
ALTER TABLE notifications ADD CONSTRAINT notifications_kind_check
    CHECK (kind IN ('allocation', 'issue', 'account', 'alert', 'info', 'policy', 'timetable', 'tanker', 'privacy', 'security'));

-- Accounts: two-factor sign-in, consent to the privacy notice (DPDP), language, anonymisation.
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS mfa_secret TEXT;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS mfa_enabled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS consent_at TIMESTAMPTZ;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS consent_version TEXT;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS language TEXT NOT NULL DEFAULT 'en';
ALTER TABLE accounts DROP CONSTRAINT IF EXISTS accounts_language_check;
ALTER TABLE accounts ADD CONSTRAINT accounts_language_check CHECK (language IN ('en', 'hi'));
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS anonymised_at TIMESTAMPTZ;

-- Second step of a sign-in with two-factor authentication (short-lived).
CREATE TABLE IF NOT EXISTS login_challenges (
    token_hash  TEXT PRIMARY KEY,
    account_id  INTEGER NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    expires_at  TIMESTAMPTZ NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0
);

-- Data-principal requests under the DPDP Act: a copy of my data, correction, erasure.
CREATE TABLE IF NOT EXISTS privacy_requests (
    request_id   SERIAL PRIMARY KEY,
    account_id   INTEGER REFERENCES accounts(account_id),
    kind         TEXT NOT NULL CHECK (kind IN ('erasure', 'correction', 'grievance')),
    details      TEXT,
    status       TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'completed', 'rejected')),
    response     TEXT,
    handled_by   INTEGER REFERENCES accounts(account_id),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    handled_at   TIMESTAMPTZ
);

-- Security incident register (CERT-In and Data Protection Board reporting deadlines are tracked here).
CREATE TABLE IF NOT EXISTS security_incidents (
    incident_id            SERIAL PRIMARY KEY,
    title                  TEXT NOT NULL,
    description            TEXT NOT NULL,
    severity               TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    personal_data_affected BOOLEAN NOT NULL DEFAULT FALSE,
    status                 TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'contained', 'closed')),
    detected_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    reported_cert_in_at    TIMESTAMPTZ,
    reported_board_at      TIMESTAMPTZ,
    actions                TEXT,
    created_by             INTEGER REFERENCES accounts(account_id),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The audit log also refuses TRUNCATE (row triggers do not fire on TRUNCATE).
CREATE OR REPLACE FUNCTION audit_log_no_truncate() RETURNS trigger AS $fn$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only: it cannot be truncated';
END
$fn$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log;
CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION audit_log_no_truncate();

-- Remember which version this database has been upgraded to.
CREATE TABLE IF NOT EXISTS schema_info (
    id          INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    version     TEXT NOT NULL,
    upgraded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO schema_info (id, version) VALUES (1, '2.0')
ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version, upgraded_at = now();
COMMIT;
