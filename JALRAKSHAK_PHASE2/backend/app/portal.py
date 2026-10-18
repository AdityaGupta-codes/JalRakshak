"""User panel: what a signed-in requester (a hospital, school, colony ...) can see and do.

Every function takes `me` (the signed-in account) and only ever reads or changes rows that belong to
`me["user_id"]`, the requester's own organisation. This is the access-control rule of the user panel.
"""
from . import audit, config, service
from .errors import ConflictError, InvalidInput, NotFoundError

ISSUE_CATEGORIES = ("leak", "no_supply", "low_pressure", "quality", "other")

MY_REQUESTS_SQL = """
SELECT r.request_id, r.requested_litres, r.urgency, r.arrival_time, r.deadline, r.status, r.note,
       a.allocated_litres, a.wait_minutes, sr.algorithm, sr.created_at AS decided_at, sr.run_id,
       cb.full_name AS created_by_name
FROM water_requests r
LEFT JOIN LATERAL (
    SELECT al.allocated_litres, al.wait_minutes, al.run_id FROM allocations al
    JOIN scheduler_runs s ON s.run_id = al.run_id AND s.committed
    WHERE al.request_id = r.request_id ORDER BY al.run_id DESC LIMIT 1
) a ON TRUE
LEFT JOIN scheduler_runs sr ON sr.run_id = a.run_id
LEFT JOIN accounts cb ON cb.account_id = r.created_by
WHERE r.user_id = %s
ORDER BY r.arrival_time DESC, r.request_id DESC
"""


def _org(me):
    if not me.get("user_id"):
        raise InvalidInput("this account is not linked to an organisation")
    return me["user_id"]


def overview(conn, me):
    org = _org(me)
    status = service.get_status_with_forecast(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT u.user_id, u.name, u.user_type, u.priority, u.zone_id, z.name AS zone_name, z.population "
                    "FROM users u JOIN zones z ON z.zone_id = u.zone_id WHERE u.user_id = %s", (org,))
        organisation = cur.fetchone()
        cur.execute("SELECT COUNT(*) AS total, "
                    "COUNT(*) FILTER (WHERE status = 'pending') AS pending, "
                    "COUNT(*) FILTER (WHERE status = 'approved') AS approved, "
                    "COUNT(*) FILTER (WHERE status = 'partial') AS partial, "
                    "COUNT(*) FILTER (WHERE status = 'rejected') AS rejected, "
                    "COUNT(*) FILTER (WHERE status = 'cancelled') AS cancelled "
                    "FROM water_requests WHERE user_id = %s", (org,))
        counts = cur.fetchone()
        cur.execute("SELECT COALESCE(SUM(r.requested_litres), 0) AS asked, COALESCE(SUM(a.allocated_litres), 0) AS got "
                    "FROM allocations a JOIN scheduler_runs s ON s.run_id = a.run_id AND s.committed "
                    "JOIN water_requests r ON r.request_id = a.request_id WHERE r.user_id = %s", (org,))
        totals = cur.fetchone()
        cur.execute("SELECT alert_id, zone_id, alert_type, severity, message, created_at FROM alerts "
                    "WHERE NOT resolved AND (zone_id = %s OR zone_id IS NULL) ORDER BY created_at DESC LIMIT 5",
                    (organisation["zone_id"],))
        zone_alerts = cur.fetchall()
        cur.execute("SELECT COUNT(*) AS n FROM notifications WHERE account_id = %s AND NOT is_read", (me["account_id"],))
        unread = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM issues WHERE user_id = %s AND status IN ('open', 'in_progress')", (org,))
        open_issues = cur.fetchone()["n"]
    asked, got = float(totals["asked"]), float(totals["got"])
    live = [a for a in service.live_alerts(status) if a["alert_type"] == "low_water"]
    return {
        "organisation": organisation,
        "reservoir": {k: status[k] for k in ("name", "available_litres", "capacity_litres", "fill_ratio", "scarcity",
                                             "pending_count", "pending_demand_litres", "forecast_today_litres")},
        "requests": counts,
        "litres_requested_decided": asked,
        "litres_received": got,
        "fulfilment": (got / asked) if asked > 0 else None,
        "alerts": live + list(zone_alerts),
        "unread_notifications": unread,
        "open_issues": open_issues,
        "max_pending": config.MAX_PENDING_PER_ORG,
    }


def my_requests(conn, me):
    with conn.cursor() as cur:
        cur.execute(MY_REQUESTS_SQL, (_org(me),))
        return cur.fetchall()


def create_request(conn, me, requested_litres, urgency, deadline_in_minutes, note=None):
    org = _org(me)
    note = (note or "").strip()[:500] or None
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users WHERE user_id = %s FOR UPDATE", (org,))  # one request at a time per org
            cur.execute("SELECT COUNT(*) AS n FROM water_requests WHERE user_id = %s AND status = 'pending'", (org,))
            if cur.fetchone()["n"] >= config.MAX_PENDING_PER_ORG:
                raise ConflictError(f"you already have {config.MAX_PENDING_PER_ORG} requests waiting. "
                                    "Cancel one or wait until a plan decides them.")
            cur.execute("SELECT reservoir_id, capacity_litres FROM reservoirs ORDER BY reservoir_id LIMIT 1")
            res = cur.fetchone()
            if requested_litres > float(res["capacity_litres"]):
                raise InvalidInput(f"a request cannot be larger than the reservoir ({float(res['capacity_litres']):,.0f} L)")
            cur.execute("INSERT INTO water_requests (user_id, reservoir_id, requested_litres, urgency, arrival_time, "
                        "deadline, created_by, note) VALUES (%s, %s, %s, %s, now(), now() + (%s * interval '1 minute'), "
                        "%s, %s) RETURNING request_id, requested_litres, urgency, arrival_time, deadline, status, note",
                        (org, res["reservoir_id"], requested_litres, urgency, deadline_in_minutes, me["account_id"], note))
            row = cur.fetchone()
            audit.write(cur, me, "request.create", "request", row["request_id"],
                        {"litres": requested_litres, "urgency": urgency})
            return row


def cancel_request(conn, me, request_id):
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT request_id, user_id, status FROM water_requests WHERE request_id = %s FOR UPDATE",
                        (request_id,))
            row = cur.fetchone()
            if row is None or row["user_id"] != _org(me):
                raise NotFoundError(f"request {request_id} not found")  # never reveal other organisations' requests
            if row["status"] != "pending":
                raise ConflictError("only requests that are still waiting can be cancelled")
            cur.execute("UPDATE water_requests SET status = 'cancelled' WHERE request_id = %s", (request_id,))
            audit.write(cur, me, "request.cancel", "request", request_id)
    return {"request_id": request_id, "status": "cancelled"}


# ---------------------------------------------------------------- notifications
def notifications(conn, me, limit=50):
    with conn.cursor() as cur:
        cur.execute("SELECT notification_id, kind, title, body, is_read, created_at FROM notifications "
                    "WHERE account_id = %s ORDER BY notification_id DESC LIMIT %s", (me["account_id"], limit))
        return cur.fetchall()


def mark_read(conn, me, notification_id=None):
    with conn:
        with conn.cursor() as cur:
            if notification_id is None:
                cur.execute("UPDATE notifications SET is_read = TRUE WHERE account_id = %s AND NOT is_read",
                            (me["account_id"],))
            else:
                cur.execute("UPDATE notifications SET is_read = TRUE WHERE account_id = %s AND notification_id = %s "
                            "RETURNING notification_id", (me["account_id"], notification_id))
                if cur.fetchone() is None:
                    raise NotFoundError(f"notification {notification_id} not found")
    return {"ok": True}


def unread_count(conn, me):
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM notifications WHERE account_id = %s AND NOT is_read", (me["account_id"],))
        return cur.fetchone()["n"]


# ---------------------------------------------------------------- issues
def my_issues(conn, me):
    with conn.cursor() as cur:
        cur.execute("SELECT i.issue_id, i.category, i.title, i.description, i.location, i.status, i.admin_response, "
                    "i.created_at, i.updated_at, i.resolved_at, z.name AS zone_name FROM issues i "
                    "LEFT JOIN zones z ON z.zone_id = i.zone_id WHERE i.account_id = %s ORDER BY i.issue_id DESC",
                    (me["account_id"],))
        return cur.fetchall()


def create_issue(conn, me, category, title, description, location=None):
    if category not in ISSUE_CATEGORIES:
        raise InvalidInput(f"category must be one of {', '.join(ISSUE_CATEGORIES)}")
    title, description = (title or "").strip(), (description or "").strip()
    if not title or not description:
        raise InvalidInput("a title and a description are required")
    with conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO issues (account_id, user_id, zone_id, category, title, description, location) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING issue_id, status, created_at",
                        (me["account_id"], me.get("user_id"), me.get("zone_id"), category, title[:120],
                         description[:2000], (location or "").strip()[:200] or None))
            row = cur.fetchone()
            audit.notify_admins(cur, "issue", f"New issue #{row['issue_id']}: {title[:120]}",
                                f"{me.get('org_display_name') or me['full_name']} reported a {category.replace('_', ' ')} problem.")
            audit.write(cur, me, "issue.create", "issue", row["issue_id"], {"category": category})
            return dict(row, category=category, title=title[:120])


# ---------------------------------------------------------------- profile
def update_profile(conn, me, full_name=None, phone=None):
    full_name = (full_name or "").strip() or None
    if full_name is None and phone is None:
        raise InvalidInput("nothing to change")
    with conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE accounts SET full_name = COALESCE(%s, full_name), phone = COALESCE(%s, phone) "
                        "WHERE account_id = %s RETURNING account_id, full_name, phone, email",
                        (full_name, (phone or "").strip() if phone is not None else None, me["account_id"]))
            row = cur.fetchone()
            audit.write(cur, me, "account.profile_update", "account", me["account_id"])
            return row


def set_language(conn, me, language):
    if language not in ("en", "hi"):
        raise InvalidInput("language must be en or hi")
    with conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE accounts SET language = %s WHERE account_id = %s", (language, me["account_id"]))
    return {"language": language}
