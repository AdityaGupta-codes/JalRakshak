"""Admin panel: accounts (approve sign-ups, create, roles, disable, reset password), issues, audit log."""
import secrets

from . import audit, auth, policy
from .errors import ConflictError, Forbidden, InvalidInput, NotFoundError

ACCOUNT_LIST_SQL = (
    "SELECT a.account_id, a.email, a.full_name, a.phone, a.role, a.status, a.user_id, a.org_name, a.org_type, "
    "a.org_zone_id, oz.name AS org_zone_name, a.must_change_password, a.created_at, a.last_login_at, a.mfa_enabled, "
    "(a.locked_until IS NOT NULL AND a.locked_until > now()) AS locked, "
    "u.name AS organisation, u.user_type, u.priority, z.name AS zone_name "
    "FROM accounts a LEFT JOIN users u ON u.user_id = a.user_id LEFT JOIN zones z ON z.zone_id = u.zone_id "
    "LEFT JOIN zones oz ON oz.zone_id = a.org_zone_id")

ISSUE_STATUSES = ("open", "in_progress", "resolved", "closed")
ISSUE_STATUS_TEXT = {"open": "open", "in_progress": "being worked on", "resolved": "resolved", "closed": "closed"}


def _temp_password():
    # readable, meets the password rules (letters + digits), 12+ characters of randomness
    return f"Jal-{secrets.token_urlsafe(6)}-{secrets.randbelow(90) + 10}"


def summary(conn):
    """Counts for the admin panel's badges."""
    with conn.cursor() as cur:
        cur.execute("SELECT "
                    "(SELECT COUNT(*) FROM accounts WHERE status = 'pending') AS pending_accounts, "
                    "(SELECT COUNT(*) FROM issues WHERE status IN ('open', 'in_progress')) AS open_issues, "
                    "(SELECT COUNT(*) FROM water_requests WHERE status = 'pending') AS pending_requests")
        return cur.fetchone()


# ---------------------------------------------------------------- accounts
def list_accounts(conn, status=None, role=None):
    sql, where, params = ACCOUNT_LIST_SQL, [], []
    if status:
        where.append("a.status = %s")
        params.append(status)
    if role:
        where.append("a.role = %s")
        params.append(role)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY (a.status = 'pending') DESC, a.created_at DESC"
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return cur.fetchall()


def _get_account(cur, account_id, lock=True):
    cur.execute("SELECT * FROM accounts WHERE account_id = %s" + (" FOR UPDATE" if lock else ""), (account_id,))
    row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"account {account_id} not found")
    return row


def _account_view(cur, account_id):
    cur.execute(ACCOUNT_LIST_SQL + " WHERE a.account_id = %s", (account_id,))
    return cur.fetchone()


def _check_org(cur, user_id):
    cur.execute("SELECT user_id, name FROM users WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"organisation {user_id} not found")
    return row


def approve(conn, actor, account_id, priority=None, user_id=None):
    """Approve a pending sign-up: link it to an existing organisation, or create the organisation it asked for."""
    with conn:
        with conn.cursor() as cur:
            acc = _get_account(cur, account_id)
            if acc["status"] != "pending":
                raise ConflictError("only sign-ups waiting for approval can be approved")
            if user_id is not None:
                org = _check_org(cur, user_id)
                org_id, org_name = org["user_id"], org["name"]
            else:
                class_p = policy.class_priority(cur, acc["org_type"], auth.DEFAULT_PRIORITY.get(acc["org_type"], 3))
                p = priority if priority is not None else class_p
                if not 1 <= int(p) <= 5:
                    raise InvalidInput("priority must be between 1 and 5")
                cur.execute("INSERT INTO users (name, user_type, priority, zone_id, priority_override) "
                            "VALUES (%s, %s, %s, %s, %s) RETURNING user_id",
                            (acc["org_name"], acc["org_type"], int(p), acc["org_zone_id"], int(p) != class_p))
                org_id, org_name = cur.fetchone()["user_id"], acc["org_name"]
            cur.execute("UPDATE accounts SET status = 'active', user_id = %s WHERE account_id = %s", (org_id, account_id))
            audit.notify(cur, account_id, "account", "Your account is approved",
                         f"You can now request water for {org_name}.")
            audit.write(cur, actor, "account.approve", "account", account_id,
                        {"email": acc["email"], "organisation_id": org_id, "organisation": org_name})
            return _account_view(cur, account_id)


def reject(conn, actor, account_id, reason=None):
    with conn:
        with conn.cursor() as cur:
            acc = _get_account(cur, account_id)
            if acc["status"] != "pending":
                raise ConflictError("only sign-ups waiting for approval can be rejected")
            cur.execute("UPDATE accounts SET status = 'rejected' WHERE account_id = %s", (account_id,))
            audit.write(cur, actor, "account.reject", "account", account_id, {"email": acc["email"], "reason": reason})
            return _account_view(cur, account_id)


def create_account(conn, actor, email, full_name, role, user_id=None, phone=None):
    """An admin creates an account directly (active). Returns a one-time temporary password."""
    email = auth.clean_email(email)
    full_name = (full_name or "").strip()
    if not full_name:
        raise InvalidInput("the name is required")
    if role not in ("admin", "requester"):
        raise InvalidInput("role must be admin or requester")
    if role == "requester" and user_id is None:
        raise InvalidInput("choose the organisation this person requests water for")
    temp = _temp_password()
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS x FROM accounts WHERE lower(email) = %s", (email,))
            if cur.fetchone():
                raise ConflictError("an account with this email already exists")
            if role == "requester":
                _check_org(cur, user_id)
            cur.execute("INSERT INTO accounts (email, password_hash, full_name, phone, role, status, user_id, "
                        "must_change_password) VALUES (%s, %s, %s, %s, %s, 'active', %s, TRUE) RETURNING account_id",
                        (email, auth.hash_password(temp), full_name, phone, role,
                         user_id if role == "requester" else None))
            account_id = cur.fetchone()["account_id"]
            audit.write(cur, actor, "account.create", "account", account_id, {"email": email, "role": role})
            view = _account_view(cur, account_id)
    return dict(view, temporary_password=temp)


def _active_admins(cur, excluding=None):
    cur.execute("SELECT COUNT(*) AS n FROM accounts WHERE role = 'admin' AND status = 'active' AND account_id <> %s",
                (excluding or 0,))
    return cur.fetchone()["n"]


def update_account(conn, actor, account_id, full_name=None, phone=None, role=None, status=None, user_id=None):
    if status is not None and status not in ("active", "disabled"):
        raise InvalidInput("status can be set to active or disabled")
    if role is not None and role not in ("admin", "requester"):
        raise InvalidInput("role must be admin or requester")
    with conn:
        with conn.cursor() as cur:
            acc = _get_account(cur, account_id)
            if acc["status"] in ("pending", "rejected") and status == "active":
                raise ConflictError("approve the sign-up instead")
            demoting = role == "requester" and acc["role"] == "admin"
            disabling = status == "disabled" and acc["status"] == "active"
            if account_id == actor["account_id"] and (demoting or disabling):
                raise Forbidden("you cannot disable or demote your own account")
            if acc["role"] == "admin" and (demoting or disabling) and _active_admins(cur, account_id) == 0:
                raise ConflictError("there must always be at least one active administrator")
            new_role = role or acc["role"]
            new_user = user_id if user_id is not None else acc["user_id"]
            if new_role == "requester" and new_user is None:
                raise InvalidInput("choose the organisation this person requests water for")
            if user_id is not None:
                _check_org(cur, user_id)
            cur.execute("UPDATE accounts SET full_name = COALESCE(%s, full_name), phone = COALESCE(%s, phone), "
                        "role = %s, status = COALESCE(%s, status), user_id = %s WHERE account_id = %s",
                        ((full_name or "").strip() or None, phone, new_role, status,
                         None if new_role == "admin" else new_user, account_id))
            if disabling or role is not None:
                cur.execute("DELETE FROM sessions WHERE account_id = %s", (account_id,))  # takes effect at once
            changes = {k: v for k, v in {"full_name": full_name, "phone": phone, "role": role, "status": status,
                                         "user_id": user_id}.items() if v is not None}
            audit.write(cur, actor, "account.update", "account", account_id, dict(changes, email=acc["email"]))
            return _account_view(cur, account_id)


def reset_password(conn, actor, account_id):
    temp = _temp_password()
    with conn:
        with conn.cursor() as cur:
            acc = _get_account(cur, account_id)
            cur.execute("UPDATE accounts SET password_hash = %s, must_change_password = TRUE, failed_logins = 0, "
                        "locked_until = NULL WHERE account_id = %s", (auth.hash_password(temp), account_id))
            cur.execute("DELETE FROM sessions WHERE account_id = %s", (account_id,))
            audit.write(cur, actor, "account.password_reset", "account", account_id, {"email": acc["email"]})
    return {"account_id": account_id, "temporary_password": temp}


# ---------------------------------------------------------------- issues
ISSUE_SQL = ("SELECT i.issue_id, i.category, i.title, i.description, i.location, i.status, i.admin_response, "
             "i.created_at, i.updated_at, i.resolved_at, i.account_id, a.full_name AS reporter, a.email AS reporter_email, "
             "u.name AS organisation, z.name AS zone_name, i.zone_id, h.full_name AS handled_by_name "
             "FROM issues i LEFT JOIN accounts a ON a.account_id = i.account_id "
             "LEFT JOIN users u ON u.user_id = i.user_id LEFT JOIN zones z ON z.zone_id = i.zone_id "
             "LEFT JOIN accounts h ON h.account_id = i.handled_by")


def list_issues(conn, status=None):
    sql, params = ISSUE_SQL, ()
    if status:
        if status not in ISSUE_STATUSES:
            raise InvalidInput(f"status must be one of {', '.join(ISSUE_STATUSES)}")
        sql += " WHERE i.status = %s"
        params = (status,)
    sql += " ORDER BY (i.status IN ('open', 'in_progress')) DESC, i.created_at DESC"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def update_issue(conn, actor, issue_id, status=None, response=None):
    if status is not None and status not in ISSUE_STATUSES:
        raise InvalidInput(f"status must be one of {', '.join(ISSUE_STATUSES)}")
    response = (response or "").strip() or None
    if status is None and response is None:
        raise InvalidInput("change the status or write a response")
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT issue_id, account_id, title, status FROM issues WHERE issue_id = %s FOR UPDATE", (issue_id,))
            issue = cur.fetchone()
            if issue is None:
                raise NotFoundError(f"issue {issue_id} not found")
            cur.execute("UPDATE issues SET status = COALESCE(%s, status), admin_response = COALESCE(%s, admin_response), "
                        "handled_by = %s, updated_at = now(), resolved_at = CASE WHEN COALESCE(%s, status) IN "
                        "('resolved', 'closed') THEN COALESCE(resolved_at, now()) ELSE NULL END WHERE issue_id = %s",
                        (status, response, actor["account_id"], status, issue_id))
            if issue["account_id"]:
                parts = []
                if status and status != issue["status"]:
                    parts.append(f"Status: {ISSUE_STATUS_TEXT[status]}.")
                if response:
                    parts.append(f"Response: {response}")
                audit.notify(cur, issue["account_id"], "issue", f"Update on your issue #{issue_id}: {issue['title']}",
                             " ".join(parts))
            audit.write(cur, actor, "issue.update", "issue", issue_id, {"status": status, "responded": bool(response)})
            cur.execute(ISSUE_SQL + " WHERE i.issue_id = %s", (issue_id,))
            return cur.fetchone()


# ---------------------------------------------------------------- audit log
def list_audit(conn, limit=200, action=None, account_id=None):
    sql, where, params = ("SELECT audit_id, account_id, actor, action, entity, entity_id, details, created_at "
                          "FROM audit_log"), [], []
    if action:
        where.append("action LIKE %s")
        params.append(action.rstrip("*") + "%")
    if account_id:
        where.append("account_id = %s")
        params.append(account_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY audit_id DESC LIMIT %s"
    params.append(max(1, min(int(limit), 1000)))
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return cur.fetchall()


def reset_mfa(conn, actor, account_id):
    """Turn two-factor sign-in off for an account whose phone was lost; it must be set up again."""
    with conn:
        with conn.cursor() as cur:
            acc = _get_account(cur, account_id)
            cur.execute("UPDATE accounts SET mfa_enabled = FALSE, mfa_secret = NULL WHERE account_id = %s", (account_id,))
            cur.execute("DELETE FROM sessions WHERE account_id = %s", (account_id,))
            audit.write(cur, actor, "mfa.reset", "account", account_id, {"email": acc["email"]})
            audit.notify(cur, account_id, "security", "Two-factor sign-in was reset",
                         "An administrator turned off two-factor sign-in for your account. Set it up again in your profile.")
    return {"account_id": account_id, "mfa_enabled": False}
