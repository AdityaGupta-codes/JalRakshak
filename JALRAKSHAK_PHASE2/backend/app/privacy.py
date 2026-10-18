"""Personal-data handling for the Digital Personal Data Protection (DPDP) Act, 2023 and its Rules (2025).

What the code does (the legal assessment itself must be done by the department's officers):
  * Notice and consent: a versioned privacy notice; sign-up requires accepting it; anyone who has not
    accepted the current version is asked to (accounts.consent_version / consent_at).
  * Access: a person can download all data held about them (GET /me/data-export).
  * Correction, erasure and grievances: requests recorded in privacy_requests and handled by an
    administrator. Erasure anonymises the account (name, email, phone, password, 2FA secret; sessions and
    notifications are deleted). The audit log is kept unchanged, because the department must be able to
    account for its decisions; the notice says so.
  * Retention: expired sessions and sign-in challenges, old read notifications, raw sensor readings and
    long-closed issues are deleted on a schedule (retention()).
"""
import secrets

from . import audit, auth, config
from .errors import ConflictError, InvalidInput, NotFoundError

NOTICE = {
    "version": None,  # filled from config
    "title": "Privacy notice - JalRakshak water allocation portal",
    "sections": [
        ["Who is responsible", "The water department operating this portal (the data fiduciary). Contact for "
         "privacy questions and grievances: {contact}."],
        ["What we collect", "Your name, work email, optional phone number, the organisation you act for, the "
         "water requests and problem reports you submit, and sign-in records (time, success or failure)."],
        ["Why", "To decide and deliver water allocations, tell you about decisions and supply hours, handle the "
         "problems you report, and keep the department accountable for its decisions."],
        ["How long", "Account data while your account is active. Read notifications for {notif} days, closed "
         "problem reports for {issues} days, sensor readings for {readings} days. The audit log (who did what, "
         "when) is kept permanently as an official record and is not changed when an account is erased."],
        ["Your rights", "Download a copy of your data, ask for corrections, ask for your account to be erased, "
         "withdraw consent (this closes your account), and raise a grievance. Use 'My data & privacy' in your "
         "profile. If not satisfied, you may approach the Data Protection Board of India."],
        ["Security", "Passwords are stored only as salted hashes, sessions expire, administrators can use "
         "two-factor sign-in, and data is kept on servers in India."],
    ],
}


def notice():
    out = dict(NOTICE, version=config.PRIVACY_NOTICE_VERSION)
    contact = config_contact()
    out["sections"] = [[h, t.format(contact=contact, notif=config.RETAIN_READ_NOTIFICATIONS_DAYS,
                                    issues=config.RETAIN_CLOSED_ISSUES_DAYS, readings=config.RETAIN_READINGS_DAYS)]
                       for h, t in NOTICE["sections"]]
    return out


def config_contact():
    import os
    return os.getenv("PRIVACY_CONTACT", "the grievance officer of the water department (set PRIVACY_CONTACT)")


def give_consent(conn, me):
    with conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE accounts SET consent_at = now(), consent_version = %s WHERE account_id = %s",
                        (config.PRIVACY_NOTICE_VERSION, me["account_id"]))
            audit.write(cur, me, "privacy.consent", "account", me["account_id"], {"version": config.PRIVACY_NOTICE_VERSION})
    return {"consent_version": config.PRIVACY_NOTICE_VERSION}


def export(conn, me):
    """Everything stored about the signed-in person (no password hash or 2FA secret)."""
    aid = me["account_id"]
    with conn.cursor() as cur:
        cur.execute("SELECT account_id, email, full_name, phone, role, status, user_id, org_name, org_type, "
                    "created_at, last_login_at, consent_at, consent_version, language, mfa_enabled "
                    "FROM accounts WHERE account_id = %s", (aid,))
        account = cur.fetchone()
        org = None
        if account and account["user_id"]:
            cur.execute("SELECT user_id, name, user_type, priority, zone_id FROM users WHERE user_id = %s",
                        (account["user_id"],))
            org = cur.fetchone()
        cur.execute("SELECT request_id, requested_litres, urgency, arrival_time, deadline, status, note "
                    "FROM water_requests WHERE created_by = %s ORDER BY request_id", (aid,))
        requests = cur.fetchall()
        cur.execute("SELECT issue_id, category, title, description, location, status, admin_response, created_at "
                    "FROM issues WHERE account_id = %s ORDER BY issue_id", (aid,))
        issues = cur.fetchall()
        cur.execute("SELECT kind, title, body, is_read, created_at FROM notifications WHERE account_id = %s "
                    "ORDER BY notification_id", (aid,))
        notes = cur.fetchall()
        cur.execute("SELECT kind, details, status, response, created_at, handled_at FROM privacy_requests "
                    "WHERE account_id = %s ORDER BY request_id", (aid,))
        preq = cur.fetchall()
        cur.execute("SELECT action, entity, entity_id, created_at FROM audit_log WHERE account_id = %s "
                    "ORDER BY audit_id DESC LIMIT 1000", (aid,))
        actions = cur.fetchall()
        cur.execute("SELECT COUNT(*) AS n FROM sessions WHERE account_id = %s AND expires_at > now()", (aid,))
        sessions = cur.fetchone()["n"]
    with conn:
        with conn.cursor() as cur:
            audit.write(cur, me, "privacy.export", "account", aid)
    return {"privacy_notice_version": config.PRIVACY_NOTICE_VERSION, "account": account, "organisation": org,
            "water_requests_submitted": requests, "issues_reported": issues, "notifications": notes,
            "privacy_requests": preq, "active_sessions": sessions, "recorded_actions": actions}


KINDS = ("erasure", "correction", "grievance")


def create_request(conn, me, kind, details=None):
    if kind not in KINDS:
        raise InvalidInput(f"kind must be one of {', '.join(KINDS)}")
    details = (details or "").strip()[:2000] or None
    if kind in ("correction", "grievance") and not details:
        raise InvalidInput("describe what should be corrected or what the grievance is")
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS x FROM privacy_requests WHERE account_id = %s AND kind = %s AND status = 'open'",
                        (me["account_id"], kind))
            if cur.fetchone():
                raise ConflictError(f"you already have an open {kind} request")
            cur.execute("INSERT INTO privacy_requests (account_id, kind, details) VALUES (%s, %s, %s) "
                        "RETURNING request_id, kind, details, status, created_at", (me["account_id"], kind, details))
            row = cur.fetchone()
            audit.notify_admins(cur, "privacy", f"Privacy request ({kind}) from {me['email']}",
                                details or "Please handle it in Security & privacy.")
            audit.write(cur, me, "privacy.request", "privacy_request", row["request_id"], {"kind": kind})
            return row


def my_requests(conn, me):
    with conn.cursor() as cur:
        cur.execute("SELECT request_id, kind, details, status, response, created_at, handled_at FROM privacy_requests "
                    "WHERE account_id = %s ORDER BY request_id DESC", (me["account_id"],))
        return cur.fetchall()


def list_requests(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT p.request_id, p.account_id, a.email, a.full_name, p.kind, p.details, p.status, p.response, "
                    "p.created_at, p.handled_at FROM privacy_requests p LEFT JOIN accounts a ON a.account_id = p.account_id "
                    "ORDER BY (p.status = 'open') DESC, p.request_id DESC")
        return cur.fetchall()


def handle_request(conn, actor, request_id, status, response=None):
    if status not in ("completed", "rejected"):
        raise InvalidInput("status must be completed or rejected")
    response = (response or "").strip()
    if not response:
        raise InvalidInput("write a response to the person")
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT request_id, account_id, kind, status FROM privacy_requests WHERE request_id = %s FOR UPDATE",
                        (request_id,))
            req = cur.fetchone()
            if req is None:
                raise NotFoundError(f"privacy request {request_id} not found")
            if req["status"] != "open":
                raise ConflictError("this request was already handled")
            cur.execute("UPDATE privacy_requests SET status = %s, response = %s, handled_by = %s, handled_at = now() "
                        "WHERE request_id = %s", (status, response, actor["account_id"], request_id))
            audit.write(cur, actor, "privacy.handle", "privacy_request", request_id,
                        {"kind": req["kind"], "status": status})
            if req["kind"] == "erasure" and status == "completed":
                if req["account_id"] == actor["account_id"]:
                    raise ConflictError("an administrator cannot erase their own account")
                _anonymise(cur, actor, req["account_id"])
            else:
                audit.notify(cur, req["account_id"], "privacy", f"Your {req['kind']} request was {status}", response)
    return {"request_id": request_id, "status": status}


def _anonymise(cur, actor, account_id):
    cur.execute("SELECT role FROM accounts WHERE account_id = %s FOR UPDATE", (account_id,))
    row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"account {account_id} not found")
    if row["role"] == "admin":
        cur.execute("SELECT COUNT(*) AS n FROM accounts WHERE role = 'admin' AND status = 'active' AND account_id <> %s",
                    (account_id,))
        if cur.fetchone()["n"] == 0:
            raise ConflictError("the last active administrator cannot be erased")
    cur.execute("UPDATE accounts SET email = %s, full_name = 'Erased user', phone = NULL, password_hash = %s, "
                "mfa_secret = NULL, mfa_enabled = FALSE, status = 'disabled', anonymised_at = now(), "
                "org_name = NULL WHERE account_id = %s",
                (f"erased-{account_id}@anonymised.invalid", auth.hash_password(secrets.token_urlsafe(24)), account_id))
    cur.execute("DELETE FROM sessions WHERE account_id = %s", (account_id,))
    cur.execute("DELETE FROM login_challenges WHERE account_id = %s", (account_id,))
    cur.execute("DELETE FROM notifications WHERE account_id = %s", (account_id,))
    audit.write(cur, actor, "privacy.erase", "account", account_id)


def retention(conn, actor=None):
    """Delete data past its retention period. Returns how many rows were removed per kind."""
    removed = {}
    with conn:
        with conn.cursor() as cur:
            for name, sql, params in (
                ("expired_sessions", "DELETE FROM sessions WHERE expires_at < now() RETURNING 1", ()),
                ("expired_sign_in_steps", "DELETE FROM login_challenges WHERE expires_at < now() RETURNING 1", ()),
                ("old_read_notifications", "DELETE FROM notifications WHERE is_read AND created_at < now() - "
                 "(%s * interval '1 day') RETURNING 1", (config.RETAIN_READ_NOTIFICATIONS_DAYS,)),
                ("old_sensor_readings", "DELETE FROM readings WHERE measured_at < now() - (%s * interval '1 day') "
                 "RETURNING 1", (config.RETAIN_READINGS_DAYS,)),
                ("old_closed_issues", "DELETE FROM issues WHERE status IN ('resolved', 'closed') AND updated_at < now() - "
                 "(%s * interval '1 day') RETURNING 1", (config.RETAIN_CLOSED_ISSUES_DAYS,)),
            ):
                cur.execute(sql, params)
                removed[name] = len(cur.fetchall())
            audit.write(cur, actor, "maintenance.retention", None, None, removed)
    return removed
