"""Operations and security support: incident register, rate limiting, operations status.

Incident register: CERT-In's directions (April 2022) require reporting specified cyber incidents within
6 hours of noticing them; under the DPDP Rules a personal-data breach must be intimated to the Data
Protection Board without delay, with a detailed report within 72 hours. The register records each incident
and shows these deadlines; it does not file reports itself.
"""
import os
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta
from pathlib import Path

from . import audit, config
from .errors import InvalidInput, NotFoundError

CERT_IN_HOURS = 6
BOARD_DETAILED_HOURS = 72
SEVERITIES = ("low", "medium", "high", "critical")
INCIDENT_STATUSES = ("open", "contained", "closed")
INC_COLUMNS = ("incident_id, title, description, severity, personal_data_affected, status, detected_at, "
               "reported_cert_in_at, reported_board_at, actions, created_by, updated_at")


def _with_deadlines(row):
    row = dict(row)
    row["cert_in_due"] = row["detected_at"] + timedelta(hours=CERT_IN_HOURS)
    row["board_due"] = (row["detected_at"] + timedelta(hours=BOARD_DETAILED_HOURS)
                        if row["personal_data_affected"] else None)
    return row


def list_incidents(conn):
    with conn.cursor() as cur:
        cur.execute(f"SELECT {INC_COLUMNS} FROM security_incidents ORDER BY (status <> 'closed') DESC, incident_id DESC")
        return [_with_deadlines(r) for r in cur.fetchall()]


def create_incident(conn, actor, title, description, severity, personal_data_affected=False):
    if severity not in SEVERITIES:
        raise InvalidInput(f"severity must be one of {', '.join(SEVERITIES)}")
    if not (title or "").strip() or not (description or "").strip():
        raise InvalidInput("title and description are required")
    with conn:
        with conn.cursor() as cur:
            cur.execute(f"INSERT INTO security_incidents (title, description, severity, personal_data_affected, created_by) "
                        f"VALUES (%s, %s, %s, %s, %s) RETURNING {INC_COLUMNS}",
                        (title.strip(), description.strip(), severity, bool(personal_data_affected), actor["account_id"]))
            row = cur.fetchone()
            audit.notify_admins(cur, "security", f"Security incident: {title.strip()}",
                                f"Severity {severity}. Report to CERT-In within {CERT_IN_HOURS} hours if it is a "
                                "reportable incident" + ("; personal data is affected: inform the Data Protection Board."
                                                         if personal_data_affected else "."))
            audit.write(cur, actor, "incident.create", "incident", row["incident_id"], {"severity": severity})
            return _with_deadlines(row)


def update_incident(conn, actor, incident_id, status=None, actions=None, reported_cert_in=None, reported_board=None):
    if status is not None and status not in INCIDENT_STATUSES:
        raise InvalidInput(f"status must be one of {', '.join(INCIDENT_STATUSES)}")
    with conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE security_incidents SET status = COALESCE(%s, status), actions = COALESCE(%s, actions), "
                        "reported_cert_in_at = CASE WHEN %s AND reported_cert_in_at IS NULL THEN now() ELSE reported_cert_in_at END, "
                        "reported_board_at = CASE WHEN %s AND reported_board_at IS NULL THEN now() ELSE reported_board_at END, "
                        f"updated_at = now() WHERE incident_id = %s RETURNING {INC_COLUMNS}",
                        (status, actions, bool(reported_cert_in), bool(reported_board), incident_id))
            row = cur.fetchone()
            if row is None:
                raise NotFoundError(f"incident {incident_id} not found")
            audit.write(cur, actor, "incident.update", "incident", incident_id,
                        {"status": status, "reported_cert_in": reported_cert_in or None,
                         "reported_board": reported_board or None})
            return _with_deadlines(row)


# ---------------------------------------------------------------- rate limiting
class RateLimiter:
    """Sliding one-minute window per (client address, bucket). In memory: correct for one API process;
    several processes or servers would share the counters in Redis instead."""

    def __init__(self):
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()  # FastAPI runs requests on several threads

    def allow(self, key, limit, now=None):
        if limit <= 0:
            return True
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - 60:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            if len(self._hits) > 50000:  # forget idle clients
                for k in [k for k, v in self._hits.items() if not v][:10000]:
                    del self._hits[k]
            return True


limiter = RateLimiter()


def client_address(request):
    if config.TRUST_PROXY:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_bucket(path):
    if path.startswith("/auth/"):
        return "auth", config.RATE_LIMIT_AUTH_PER_MINUTE
    if path in ("/health",):
        return None, 0
    return "api", config.RATE_LIMIT_PER_MINUTE


# ---------------------------------------------------------------- operations status
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "/backups"))


def backups():
    if not BACKUP_DIR.is_dir():
        return {"configured": False, "files": []}
    files = sorted(BACKUP_DIR.glob("*.dump"), key=lambda p: p.stat().st_mtime, reverse=True)
    return {"configured": True, "files": [{"name": p.name, "bytes": p.stat().st_size,
                                           "modified": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(p.stat().st_mtime))}
                                          for p in files[:20]]}


def ops_status(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT version, upgraded_at FROM schema_info WHERE id = 1")
        schema = cur.fetchone()
        cur.execute("SELECT pg_database_size(current_database()) AS bytes, current_user AS db_user")
        db = cur.fetchone()
        cur.execute("SELECT COUNT(*) FILTER (WHERE role = 'admin' AND status = 'active') AS admins, "
                    "COUNT(*) FILTER (WHERE role = 'admin' AND status = 'active' AND mfa_enabled) AS admins_mfa "
                    "FROM accounts")
        acc = cur.fetchone()
        cur.execute("SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE active AND (last_seen_at IS NULL OR last_seen_at < "
                    "now() - (%s * interval '1 minute'))) AS stale FROM devices", (config.TELEMETRY_STALE_MINUTES,))
        dev = cur.fetchone()
        cur.execute("SELECT MAX(created_at) AS last_at FROM model_evaluations")
        ml = cur.fetchone()["last_at"]
        cur.execute("SELECT MAX(created_at) AS last_at FROM audit_log WHERE action = 'maintenance.retention'")
        ret = cur.fetchone()["last_at"]
        cur.execute("SELECT COUNT(*) AS n FROM security_incidents WHERE status <> 'closed'")
        inc = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM privacy_requests WHERE status = 'open'")
        preq = cur.fetchone()["n"]
    return {
        "schema_version": schema["version"] if schema else None,
        "schema_upgraded_at": schema["upgraded_at"] if schema else None,
        "database_bytes": int(db["bytes"]), "database_user": db["db_user"],
        "least_privilege_db_user": bool(config.APP_DB_USER),
        "admins": int(acc["admins"]), "admins_with_2fa": int(acc["admins_mfa"]),
        "admin_2fa_required": config.REQUIRE_ADMIN_MFA,
        "maker_checker": config.REQUIRE_SECOND_APPROVER,
        "devices": int(dev["n"]), "devices_offline": int(dev["stale"]),
        "last_ml_run": ml, "last_retention_run": ret,
        "open_incidents": int(inc), "open_privacy_requests": int(preq),
        "rate_limits": {"sign_in_per_minute": config.RATE_LIMIT_AUTH_PER_MINUTE,
                        "api_per_minute": config.RATE_LIMIT_PER_MINUTE},
        "backups": backups(),
    }
