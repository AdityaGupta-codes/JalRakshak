import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:jal123@localhost:5432/jalrakshak")
SCHEDULER_BIN = os.getenv("SCHEDULER_BIN", "jalrakshak-scheduler")
SCHEDULER_TIMEOUT_S = float(os.getenv("SCHEDULER_TIMEOUT_S", "10"))

# Database connection: try a few times before giving up (the DB container may still be starting).
DB_CONNECT_RETRIES = int(os.getenv("DB_CONNECT_RETRIES", "5"))
DB_CONNECT_DELAY_S = float(os.getenv("DB_CONNECT_DELAY_S", "1.0"))
DB_CONNECT_TIMEOUT_S = int(os.getenv("DB_CONNECT_TIMEOUT_S", "5"))

# How long the scheduler waits after the first pending request before deciding (minutes).
# "all" (default) ranks every pending request together. That matches how the dashboard is used: when the
# operator clicks "Apply", all pending requests are already waiting. With 0, the first request to arrive is
# served on its own before anyone else has arrived (in the sample data that hands a factory its full
# 20,000 L before the hospital is even considered). A number means "wait this many minutes".
BATCH_WINDOW = os.getenv("BATCH_WINDOW_MINUTES", "all")

# Pass today's ML demand forecast to the scheduler as predicted_demand_litres (scarcity factor).
# When no forecast exists (ML pipeline not run), the scheduler uses the litres still requested instead.
USE_FORECAST = os.getenv("USE_FORECAST", "1").lower() not in ("0", "false", "no")

# ---------- sign-in (see app/auth.py) ----------
# The first administrator, created on first start when no admin exists. They must choose a new password
# at first sign-in (set FORCE_ADMIN_PASSWORD_CHANGE=0 to skip that, e.g. for a quick demo).
ADMIN_EMAIL = os.getenv("JAL_ADMIN_EMAIL", "admin@jalrakshak.local")
ADMIN_PASSWORD = os.getenv("JAL_ADMIN_PASSWORD", "Admin@1234")
FORCE_ADMIN_PASSWORD_CHANGE = os.getenv("FORCE_ADMIN_PASSWORD_CHANGE", "1").lower() not in ("0", "false", "no")

# Demo sign-ins for the 8 sample organisations (created once, together with the first admin).
DEMO_ACCOUNTS = os.getenv("JAL_DEMO_ACCOUNTS", "1").lower() not in ("0", "false", "no")
DEMO_PASSWORD = os.getenv("JAL_DEMO_PASSWORD", "Demo@1234")

SESSION_HOURS = float(os.getenv("SESSION_HOURS", "12"))
# PBKDF2-SHA256 work factor (OWASP 2023 recommends 600,000). Tests lower it to run fast.
PASSWORD_ITERATIONS = int(os.getenv("PASSWORD_ITERATIONS", "600000"))
MAX_FAILED_LOGINS = int(os.getenv("MAX_FAILED_LOGINS", "5"))
LOCKOUT_MINUTES = float(os.getenv("LOCKOUT_MINUTES", "5"))
# A requester can have at most this many requests waiting at the same time.
MAX_PENDING_PER_ORG = int(os.getenv("MAX_PENDING_PER_ORG", "5"))

# Browsers allowed to call the API ("*" = any; tokens are sent in a header, not cookies).
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]

# Passed to the C++ scheduler as "config" (see docs/contracts.md).
# batch_window_minutes is filled in per run from BATCH_WINDOW (see scheduler_client.build_scheduler_input).
DEFAULT_SCHEDULER_CONFIG = {
    "flow_rate_lpm": 500,
    "quantum_litres": 2000,
    "min_allocation_ratio": 0.2,
    "guarantee_floor": 0.3,
    "weights": {"priority": 0.30, "urgency": 0.25, "scarcity": 0.15,
                "demand_impact": 0.15, "fairness": 0.15},
}

ALL_ALGORITHMS = ["fcfs", "priority", "round_robin", "water_aware"]

# ---------- v2.0: governance ----------
def _flag(name, default):
    return os.getenv(name, default).lower() not in ("0", "false", "no")


# Maker-checker: a policy change must be approved by a different administrator. Set to 0 only for a
# one-person installation (self-approvals are then recorded as such in the audit log).
REQUIRE_SECOND_APPROVER = _flag("REQUIRE_SECOND_APPROVER", "1")
# An officer who changes the litres of a plan must say why (at least this many characters).
OVERRIDE_REASON_MIN_CHARS = int(os.getenv("OVERRIDE_REASON_MIN_CHARS", "10"))

# ---------- v2.0: data integration ----------
# A sensor that has not reported for this long is "offline": the dashboard warns and staff enter values by hand.
TELEMETRY_STALE_MINUTES = float(os.getenv("TELEMETRY_STALE_MINUTES", "30"))
# A reservoir level sensor's reading becomes the reservoir's current water level.
LEVEL_FROM_TELEMETRY = _flag("LEVEL_FROM_TELEMETRY", "1")
# This many leak / no-supply complaints from one zone within the window raise a "complaints" alert.
COMPLAINT_CLUSTER_SIZE = int(os.getenv("COMPLAINT_CLUSTER_SIZE", "3"))
COMPLAINT_CLUSTER_HOURS = float(os.getenv("COMPLAINT_CLUSTER_HOURS", "48"))

# ---------- v2.0: security and privacy ----------
# Administrators must use an authenticator app (TOTP) to sign in. Recommended (1) for any real deployment;
# the demo default is 0 so the project can be shown without a phone.
REQUIRE_ADMIN_MFA = _flag("REQUIRE_ADMIN_MFA", "0")
MFA_ISSUER = os.getenv("MFA_ISSUER", "JalRakshak")
# Requests per minute per client address: sign-in endpoints / everything else (0 = no limit).
RATE_LIMIT_AUTH_PER_MINUTE = int(os.getenv("RATE_LIMIT_AUTH_PER_MINUTE", "20"))
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "600"))
# Behind a reverse proxy (Caddy in the https profile), trust its X-Forwarded-For header for the client address.
TRUST_PROXY = _flag("TRUST_PROXY", "0")
# Version of the privacy notice users consent to (change it when the notice text changes).
PRIVACY_NOTICE_VERSION = os.getenv("PRIVACY_NOTICE_VERSION", "2026-10")
# Data retention (days). Expired sessions are always removed.
RETAIN_READ_NOTIFICATIONS_DAYS = int(os.getenv("RETAIN_READ_NOTIFICATIONS_DAYS", "180"))
RETAIN_READINGS_DAYS = int(os.getenv("RETAIN_READINGS_DAYS", "400"))
RETAIN_CLOSED_ISSUES_DAYS = int(os.getenv("RETAIN_CLOSED_ISSUES_DAYS", "730"))
# Least-privilege database user for the running API (created and granted by the backend at start-up using
# the owner connection in DATABASE_URL). Empty = use DATABASE_URL for everything (demo / tests).
APP_DB_USER = os.getenv("APP_DB_USER", "")
APP_DB_PASSWORD = os.getenv("APP_DB_PASSWORD", "")
