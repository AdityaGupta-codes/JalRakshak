"""Brings an existing database up to the current schema (runs db/migrate.sql, which is safe to repeat).

Docker only creates the tables the first time the database volume is created, so a database made by an
older version of the project would otherwise be missing newer tables or functions.
"""
import logging
import os
from pathlib import Path

log = logging.getLogger("jalrakshak")

# Local checkout: <repo>/db/migrate.sql. Docker image: /db/migrate.sql (see backend/Dockerfile).
MIGRATE_SQL = Path(os.getenv("MIGRATE_SQL", Path(__file__).resolve().parents[2] / "db" / "migrate.sql"))

# Tables and functions the current code needs, used by /health to explain problems.
REQUIRED_TABLES = ("zones", "users", "reservoirs", "water_requests", "scheduler_runs", "allocations",
                   "consumption", "predictions", "alerts", "model_evaluations",
                   "accounts", "sessions", "notifications", "issues", "audit_log",
                   "policies", "supply_timetables", "tanker_trips", "devices", "readings", "login_challenges",
                   "privacy_requests", "security_incidents")
REQUIRED_FUNCTIONS = ("load_sample_requests", "audit_log_append_only", "audit_log_no_truncate")

RESET_HINT = ("To start again with a fresh database (this deletes all data), run in the project folder: "
              "docker compose down -v   then   docker compose up --build")


class SchemaProblem(Exception):
    """The database is reachable but is not a (complete) JalRakshak database."""


def apply(conn):
    """Run migrate.sql on conn. Returns False if the file is not available (nothing done)."""
    if not MIGRATE_SQL.exists():
        log.warning("migration file not found: %s", MIGRATE_SQL)
        return False
    sql = MIGRATE_SQL.read_text(encoding="utf-8")
    old = getattr(conn, "autocommit", None)
    try:
        if old is not None:
            conn.autocommit = True  # the file manages its own transaction
        with conn.cursor() as cur:
            cur.execute(sql)
    except Exception as exc:
        code = getattr(exc, "pgcode", None)
        if code == "42P01":  # undefined table: the base tables were never created
            raise SchemaProblem("The database has no JalRakshak tables (the first-time setup did not run). "
                                + RESET_HINT) from exc
        raise
    finally:
        if old is not None:
            conn.autocommit = old
    return True


def check(conn):
    """List what is missing. Empty list = the database matches the code."""
    with conn.cursor() as cur:
        cur.execute("SELECT c.relname AS name FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = current_schema() AND c.relkind = 'r'")
        tables = {r["name"] for r in cur.fetchall()}
        cur.execute("SELECT p.proname AS name FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = current_schema()")
        functions = {r["name"] for r in cur.fetchall()}
    missing = [f"table {t}" for t in REQUIRED_TABLES if t not in tables]
    missing += [f"function {f}()" for f in REQUIRED_FUNCTIONS if f not in functions]
    return missing


def ensure_app_role(conn, user, password):
    """Create / update the least-privilege login the API uses at run time (APP_DB_USER).

    It may read and change data, but cannot change the schema, and cannot UPDATE, DELETE or TRUNCATE the
    audit log (a second guard next to the triggers). Run with the owner connection after migrate.sql;
    re-granting every start covers tables added by later versions.
    """
    if not user:
        return False
    if not password:
        raise SchemaProblem("APP_DB_USER is set but APP_DB_PASSWORD is empty")
    old = getattr(conn, "autocommit", None)
    try:
        if old is not None:
            conn.autocommit = False
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(724004) AS locked")
                cur.execute("SELECT 1 AS x FROM pg_roles WHERE rolname = %s", (user,))
                verb = "ALTER" if cur.fetchone() else "CREATE"
                # format() quotes the name (%I) and the password (%L) safely on the server
                cur.execute(f"SELECT format('{verb} ROLE %%I WITH LOGIN PASSWORD %%L', %s::text, %s::text) AS sql",
                            (user, password))
                cur.execute(cur.fetchone()["sql"])
                for stmt in ("GRANT CONNECT ON DATABASE %I TO %I", "GRANT USAGE ON SCHEMA public TO %I",
                             "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I",
                             "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO %I",
                             "REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM %I",
                             "REVOKE TRUNCATE ON ALL TABLES IN SCHEMA public FROM %I"):
                    if stmt.startswith("GRANT CONNECT"):
                        cur.execute("SELECT format(%s, current_database(), %s::text) AS sql", (stmt, user))
                    else:
                        cur.execute("SELECT format(%s, %s::text) AS sql", (stmt, user))
                    cur.execute(cur.fetchone()["sql"])
    finally:
        if old is not None:
            conn.autocommit = old
    return True
