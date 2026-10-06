"""Business logic: DB access + scheduler orchestration. Takes a DB connection (psycopg2-style,
cursors returning dict rows) so it can be tested with a fake connection."""
import json

from . import config
from .errors import ConflictError, NotFoundError
from .scheduler_client import SchedulerError, build_scheduler_input, run_scheduler

REQUEST_STATUSES = ("pending", "approved", "partial", "rejected", "cancelled")

PENDING_SQL = """
SELECT r.request_id, r.user_id, u.name AS user_name, u.user_type, u.priority, u.zone_id,
       z.population AS zone_population, r.urgency, r.requested_litres,
       r.arrival_time, r.deadline
FROM water_requests r
JOIN users u ON u.user_id = r.user_id
JOIN zones z ON z.zone_id = u.zone_id
WHERE r.reservoir_id = %s AND r.status = 'pending'
ORDER BY r.arrival_time, r.request_id
"""

ZONE_RATIO_SQL = """
SELECT u.zone_id,
       COALESCE(SUM(a.allocated_litres) / NULLIF(SUM(r.requested_litres), 0), 0) AS ratio
FROM allocations a
JOIN scheduler_runs sr ON sr.run_id = a.run_id AND sr.committed
JOIN water_requests r ON r.request_id = a.request_id
JOIN users u ON u.user_id = r.user_id
GROUP BY u.zone_id
"""


def _zone_ratios(cur):
    cur.execute(ZONE_RATIO_SQL)
    return {row["zone_id"]: min(1.0, float(row["ratio"])) for row in cur.fetchall()}


def _fetch_pending(cur, reservoir_id, lock=False):
    cur.execute(PENDING_SQL + (" FOR UPDATE OF r" if lock else ""), (reservoir_id,))
    return cur.fetchall()


def _plan(cur, algorithm, available, pending):
    payload = build_scheduler_input(algorithm, available, pending, _zone_ratios(cur),
                                    None, config.DEFAULT_SCHEDULER_CONFIG)
    return run_scheduler(payload)


# ---------- reads ----------

def list_reservoirs(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT reservoir_id, name, capacity_litres, available_litres, updated_at "
                    "FROM reservoirs ORDER BY reservoir_id")
        return cur.fetchall()


def list_requests(conn, status=None):
    if status is not None and status not in REQUEST_STATUSES:
        raise ValueError(f"status must be one of {', '.join(REQUEST_STATUSES)}")
    sql = ("SELECT r.request_id, r.user_id, u.name AS user_name, u.user_type, u.priority, "
           "r.reservoir_id, r.requested_litres, r.urgency, r.arrival_time, r.deadline, r.status "
           "FROM water_requests r JOIN users u ON u.user_id = r.user_id")
    params = ()
    if status:
        sql += " WHERE r.status = %s"
        params = (status,)
    sql += " ORDER BY r.arrival_time, r.request_id"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def list_runs(conn, limit=50):
    with conn.cursor() as cur:
        cur.execute("SELECT run_id, algorithm, scenario, reservoir_id, available_before, "
                    "available_after, metrics, committed, created_at "
                    "FROM scheduler_runs ORDER BY run_id DESC LIMIT %s", (limit,))
        return cur.fetchall()


def list_predictions(conn, zone_id=None):
    sql = ("SELECT prediction_id, zone_id, target_date, predicted_litres, model_name, created_at "
           "FROM predictions")
    params = ()
    if zone_id is not None:
        sql += " WHERE zone_id = %s"
        params = (zone_id,)
    sql += " ORDER BY target_date, zone_id"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def list_alerts(conn, include_resolved=False):
    sql = "SELECT alert_id, zone_id, alert_type, severity, message, resolved, created_at FROM alerts"
    if not include_resolved:
        sql += " WHERE NOT resolved"
    sql += " ORDER BY created_at DESC"
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


# ---------- writes ----------

def create_request(conn, user_id, reservoir_id, requested_litres, urgency, deadline_in_minutes):
    try:
        with conn:  # commit on success, rollback on error
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO water_requests (user_id, reservoir_id, requested_litres, urgency, "
                    "arrival_time, deadline) VALUES (%s, %s, %s, %s, now(), "
                    "now() + (%s * interval '1 minute')) "
                    "RETURNING request_id, user_id, reservoir_id, requested_litres, urgency, "
                    "arrival_time, deadline, status",
                    (user_id, reservoir_id, requested_litres, urgency, deadline_in_minutes))
                return cur.fetchone()
    except Exception as exc:
        if getattr(exc, "pgcode", None) == "23503":  # foreign_key_violation
            raise NotFoundError("user_id or reservoir_id does not exist")
        raise


def simulate(conn, reservoir_id, algorithms):
    """Run algorithms on the pending requests WITHOUT changing the database."""
    with conn.cursor() as cur:
        cur.execute("SELECT reservoir_id, available_litres FROM reservoirs WHERE reservoir_id = %s",
                    (reservoir_id,))
        reservoir = cur.fetchone()
        if reservoir is None:
            raise NotFoundError(f"reservoir {reservoir_id} not found")
        pending = _fetch_pending(cur, reservoir_id)

        results = []
        if pending:
            for algorithm in algorithms:
                try:
                    results.append(_plan(cur, algorithm, reservoir["available_litres"], pending))
                except SchedulerError as exc:
                    results.append({"algorithm": algorithm, "error": str(exc)})

    return {
        "reservoir_id": reservoir_id,
        "available_litres": float(reservoir["available_litres"]),
        "pending_count": len(pending),
        "requests": [{"request_id": r["request_id"], "user_name": r["user_name"],
                      "user_type": r["user_type"], "priority": r["priority"],
                      "requested_litres": float(r["requested_litres"])} for r in pending],
        "results": results,
    }


def commit_allocation(conn, reservoir_id, algorithm):
    """Run one algorithm and store the result atomically.

    The reservoir row is locked with SELECT ... FOR UPDATE first, so two concurrent commits
    are serialised and can never allocate the same water twice. The CHECK constraint on
    reservoirs.available_litres is a second safety net.
    """
    with conn:  # one transaction: commit on success, rollback on any exception
        with conn.cursor() as cur:
            cur.execute("SELECT reservoir_id, available_litres FROM reservoirs "
                        "WHERE reservoir_id = %s FOR UPDATE", (reservoir_id,))
            reservoir = cur.fetchone()
            if reservoir is None:
                raise NotFoundError(f"reservoir {reservoir_id} not found")

            pending = _fetch_pending(cur, reservoir_id, lock=True)
            if not pending:
                raise ConflictError("no pending requests for this reservoir")

            result = _plan(cur, algorithm, reservoir["available_litres"], pending)
            total = sum(a["allocated_litres"] for a in result["allocations"])

            cur.execute(
                "INSERT INTO scheduler_runs (algorithm, reservoir_id, available_before, "
                "available_after, metrics, committed) VALUES (%s, %s, %s, %s, %s::jsonb, TRUE) "
                "RETURNING run_id",
                (algorithm, reservoir_id, reservoir["available_litres"],
                 result["remaining_litres"], json.dumps(result["metrics"])))
            run_id = cur.fetchone()["run_id"]

            for a in result["allocations"]:
                cur.execute(
                    "INSERT INTO allocations (run_id, request_id, reservoir_id, allocated_litres, "
                    "wait_minutes, score) VALUES (%s, %s, %s, %s, %s, %s)",
                    (run_id, a["request_id"], reservoir_id, a["allocated_litres"],
                     a["wait_time"], a["score"]))
                cur.execute("UPDATE water_requests SET status = %s WHERE request_id = %s",
                            (a["status"], a["request_id"]))

            cur.execute("UPDATE reservoirs SET available_litres = available_litres - %s, "
                        "updated_at = now() WHERE reservoir_id = %s", (total, reservoir_id))

    result["run_id"] = run_id
    result["reservoir_id"] = reservoir_id
    return result
