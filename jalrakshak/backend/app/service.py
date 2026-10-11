"""Business logic: DB access + scheduler orchestration. Takes a DB connection (psycopg2-style,
cursors returning dict rows) so it can be tested with a fake connection."""
import json
from decimal import Decimal

from . import config
from .errors import ConflictError, InvalidInput, NotFoundError
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


# Today's demand forecast for all zones, from the model the ML pipeline selected (see ml/README.md).
FORECAST_TODAY_SQL = """
SELECT SUM(p.predicted_litres) AS total, MIN(p.model_name) AS model_name
FROM predictions p
JOIN model_evaluations m ON m.model_name = p.model_name AND m.is_selected
WHERE p.target_date = CURRENT_DATE
"""


def _forecast_today(cur):
    """(litres, model_name) for today, or (None, None) when the ML pipeline has not produced a forecast."""
    cur.execute(FORECAST_TODAY_SQL)
    row = cur.fetchone()
    if not row or row.get("total") is None:
        return None, None
    return float(row["total"]), row.get("model_name")


def _plan(cur, algorithm, available, pending):
    predicted = _forecast_today(cur)[0] if config.USE_FORECAST else None
    payload = build_scheduler_input(algorithm, available, pending, _zone_ratios(cur),
                                    predicted, config.DEFAULT_SCHEDULER_CONFIG)
    return run_scheduler(payload)


# ---------- reads ----------

def list_reservoirs(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT reservoir_id, name, capacity_litres, available_litres, updated_at "
                    "FROM reservoirs ORDER BY reservoir_id")
        return cur.fetchall()


def list_requests(conn, status=None):
    if status is not None and status not in REQUEST_STATUSES:
        raise InvalidInput(f"status must be one of {', '.join(REQUEST_STATUSES)}")
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


LOW_WATER_WARNING = 0.30   # reservoir below 30 % of capacity
LOW_WATER_CRITICAL = 0.15


def live_alerts(status, forecast_today=None):
    """Alerts worked out from the current state (never stored, so they disappear when the problem does)."""
    out = []
    fill = status["fill_ratio"]
    if fill < LOW_WATER_WARNING:
        out.append({"alert_id": None, "zone_id": None, "alert_type": "low_water",
                    "severity": "critical" if fill < LOW_WATER_CRITICAL else "warning",
                    "message": (f"{status['name']} is at {fill:.0%} of capacity "
                                f"({status['available_litres']:,.0f} L of {status['capacity_litres']:,.0f} L)."),
                    "resolved": False, "created_at": status["updated_at"], "live": True})
    if forecast_today is not None and forecast_today > status["available_litres"]:
        out.append({"alert_id": None, "zone_id": None, "alert_type": "high_demand",
                    "severity": "warning",
                    "message": (f"Forecast demand today is {forecast_today:,.0f} L, more than the "
                                f"{status['available_litres']:,.0f} L available."),
                    "resolved": False, "created_at": status["updated_at"], "live": True})
    return out


def list_alerts(conn, include_resolved=False, reservoir_id=1):
    """Stored alerts (leaks found by the ML pipeline, rising demand) plus live alerts about the reservoir."""
    sql = "SELECT alert_id, zone_id, alert_type, severity, message, resolved, created_at FROM alerts"
    if not include_resolved:
        sql += " WHERE NOT resolved"
    sql += " ORDER BY created_at DESC, alert_id DESC"
    with conn.cursor() as cur:
        cur.execute(sql)
        stored = [dict(r, live=False) for r in cur.fetchall()]
        forecast_today = _forecast_today(cur)[0]
    try:
        live = live_alerts(get_status(conn, reservoir_id), forecast_today)
    except NotFoundError:
        live = []
    order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(live + stored, key=lambda a: order.get(a["severity"], 3))


def resolve_alert(conn, alert_id):
    with conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE alerts SET resolved = TRUE WHERE alert_id = %s "
                        "RETURNING alert_id, zone_id, alert_type, severity, message, resolved, created_at",
                        (alert_id,))
            row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"alert {alert_id} not found")
    return row


def get_forecast(conn, history_days=60):
    """Everything the dashboard's forecast tab needs: recent use, next week's forecast and model scores."""
    with conn.cursor() as cur:
        cur.execute("SELECT zone_id, name, population FROM zones ORDER BY zone_id")
        zones = cur.fetchall()
        cur.execute("SELECT zone_id, recorded_on AS date, litres FROM consumption "
                    "WHERE recorded_on > (SELECT MAX(recorded_on) FROM consumption) - %s "
                    "ORDER BY zone_id, recorded_on", (history_days,))
        history = cur.fetchall()
        cur.execute("SELECT model_name, mae_litres, rmse_litres, mape, test_days, is_selected, created_at "
                    "FROM model_evaluations ORDER BY mae_litres")
        models = cur.fetchall()
        cur.execute("SELECT p.zone_id, p.target_date, p.predicted_litres, p.model_name FROM predictions p "
                    "JOIN model_evaluations m ON m.model_name = p.model_name AND m.is_selected "
                    "ORDER BY p.target_date, p.zone_id")
        predictions = cur.fetchall()
        today, model = _forecast_today(cur)
    return {"zones": zones, "history": history, "predictions": predictions, "models": models,
            "today_litres": today, "selected_model": model,
            "available": bool(history), "used_by_scheduler": bool(config.USE_FORECAST and today is not None)}


def get_status(conn, reservoir_id=1):
    """One-glance summary for the dashboard: water level, pending demand and how scarce water is."""
    with conn.cursor() as cur:
        cur.execute("SELECT reservoir_id, name, capacity_litres, available_litres, updated_at "
                    "FROM reservoirs WHERE reservoir_id = %s", (reservoir_id,))
        reservoir = cur.fetchone()
        if reservoir is None:
            raise NotFoundError(f"reservoir {reservoir_id} not found")
        cur.execute("SELECT COUNT(*) AS n, COALESCE(SUM(requested_litres), 0) AS demand "
                    "FROM water_requests WHERE reservoir_id = %s AND status = 'pending'", (reservoir_id,))
        pending = cur.fetchone()

    available = float(reservoir["available_litres"])
    capacity = float(reservoir["capacity_litres"])
    demand = float(pending["demand"])
    if demand <= 0 or demand <= available:
        level, ratio = "ok", (demand / available if available > 0 else 0.0)
    else:
        ratio = demand / available if available > 0 else None
        level = "moderate" if (ratio is not None and ratio <= 1.5) else "severe"
    return {"reservoir_id": reservoir["reservoir_id"], "name": reservoir["name"],
            "capacity_litres": capacity, "available_litres": available,
            "fill_ratio": available / capacity if capacity > 0 else 0.0,
            "pending_count": int(pending["n"]), "pending_demand_litres": demand,
            "demand_to_supply_ratio": ratio, "scarcity": level,
            "updated_at": reservoir["updated_at"]}


def get_status_with_forecast(conn, reservoir_id=1):
    st = get_status(conn, reservoir_id)
    with conn.cursor() as cur:
        st["forecast_today_litres"], st["forecast_model"] = _forecast_today(cur)
    return st


def list_users(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT u.user_id, u.name, u.user_type, u.priority, u.zone_id, z.name AS zone_name "
                    "FROM users u JOIN zones z ON z.zone_id = u.zone_id ORDER BY u.user_id")
        return cur.fetchall()


def get_run(conn, run_id):
    with conn.cursor() as cur:
        cur.execute("SELECT run_id, algorithm, scenario, reservoir_id, available_before, available_after, "
                    "metrics, committed, created_at FROM scheduler_runs WHERE run_id = %s", (run_id,))
        run = cur.fetchone()
        if run is None:
            raise NotFoundError(f"run {run_id} not found")
        cur.execute("SELECT a.request_id, u.name AS user_name, u.user_type, u.priority, "
                    "r.requested_litres, a.allocated_litres, a.wait_minutes, a.score "
                    "FROM allocations a JOIN water_requests r ON r.request_id = a.request_id "
                    "JOIN users u ON u.user_id = r.user_id "
                    "WHERE a.run_id = %s ORDER BY a.allocation_id", (run_id,))
        allocations = cur.fetchall()
    return dict(run, allocations=allocations)


DEMO_LEVEL = 50000.0


def reset_demo(conn, reservoir_id, available_litres=None):
    """DEMO helper: forget all allocation runs, put back exactly the 8 sample requests (arriving from now on,
    so their deadlines are in the future again) and refill the reservoir. Requests added by hand are removed.
    available_litres None = 50,000 L, or the capacity if the reservoir is smaller."""
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT reservoir_id, capacity_litres FROM reservoirs WHERE reservoir_id = %s FOR UPDATE",
                            (reservoir_id,))
                row = cur.fetchone()
                if row is None:
                    raise NotFoundError(f"reservoir {reservoir_id} not found")
                capacity = float(row["capacity_litres"])
                if available_litres is None:
                    available_litres = min(DEMO_LEVEL, capacity)
                if not 0 <= available_litres <= capacity:
                    raise InvalidInput(f"the water level must be between 0 and the capacity ({capacity:,.0f} L)")
                cur.execute("DELETE FROM allocations WHERE reservoir_id = %s", (reservoir_id,))
                cur.execute("DELETE FROM scheduler_runs WHERE reservoir_id = %s", (reservoir_id,))
                cur.execute("DELETE FROM water_requests WHERE reservoir_id = %s", (reservoir_id,))
                cur.execute("SELECT load_sample_requests(%s) AS n", (reservoir_id,))
                cur.execute("UPDATE reservoirs SET available_litres = %s, updated_at = now() "
                            "WHERE reservoir_id = %s", (available_litres, reservoir_id))
    except (NotFoundError, InvalidInput):
        raise
    except Exception as exc:
        if getattr(exc, "pgcode", None) == "23514":
            raise InvalidInput("the water level must be between 0 and the reservoir capacity")
        raise
    return get_status(conn, reservoir_id)


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
        code = getattr(exc, "pgcode", None)
        if code == "23503":  # foreign_key_violation
            raise NotFoundError("user_id or reservoir_id does not exist")
        if code in ("23514", "22003", "22P02"):  # check violation / numeric out of range / bad text
            raise InvalidInput("the request breaks a database rule (check the values)")
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
            # exact decimal sum: the scheduler returns whole 0.01 L, the database stores numeric(12,2)
            total = sum((Decimal(str(a["allocated_litres"])) for a in result["allocations"]), Decimal(0))

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


# ---------- settings: reservoir, zones and requesters ----------

USER_TYPES = ("hospital", "domestic", "school", "industry", "commercial", "park")


def _db_errors(exc, what):
    """Turn PostgreSQL error codes into messages a person can act on."""
    code = getattr(exc, "pgcode", None)
    if code == "23505":  # unique_violation
        return ConflictError(f"another {what} already has that name")
    if code == "23503":  # foreign_key_violation
        return ConflictError(f"this {what} is still in use, so it cannot be removed")
    if code in ("23514", "22003", "22P02", "23502"):  # check / out of range / bad value / missing value
        return InvalidInput(f"the {what} breaks a database rule (check the values)")
    return None


def _run(conn, what, fn):
    """Run fn(cur) in one transaction, translating database errors."""
    try:
        with conn:
            with conn.cursor() as cur:
                return fn(cur)
    except (NotFoundError, ConflictError, InvalidInput):
        raise
    except Exception as exc:
        friendly = _db_errors(exc, what)
        if friendly is not None:
            raise friendly
        raise


def update_reservoir(conn, reservoir_id, name=None, capacity_litres=None, available_litres=None):
    """Change a reservoir's name, capacity and/or current water level.

    Water can never exceed capacity. If only the capacity is lowered below the water currently stored,
    the request is refused with an explanation (send available_litres as well to lower both together).
    """
    def work(cur):
        cur.execute("SELECT reservoir_id, name, capacity_litres, available_litres FROM reservoirs "
                    "WHERE reservoir_id = %s FOR UPDATE", (reservoir_id,))
        row = cur.fetchone()
        if row is None:
            raise NotFoundError(f"reservoir {reservoir_id} not found")
        new_name = row["name"] if name is None else name.strip()
        if not new_name:
            raise InvalidInput("the reservoir needs a name")
        cap = float(row["capacity_litres"] if capacity_litres is None else capacity_litres)
        avail = float(row["available_litres"] if available_litres is None else available_litres)
        if cap <= 0:
            raise InvalidInput("capacity must be more than 0 L")
        if avail < 0:
            raise InvalidInput("the water level cannot be negative")
        if avail > cap:
            if available_litres is None:
                raise InvalidInput(f"capacity cannot be below the water already in the reservoir "
                                   f"({avail:,.0f} L). Lower the water level as well.")
            raise InvalidInput(f"the water level ({avail:,.0f} L) cannot be more than the capacity ({cap:,.0f} L)")
        cur.execute("UPDATE reservoirs SET name = %s, capacity_litres = %s, available_litres = %s, "
                    "updated_at = now() WHERE reservoir_id = %s", (new_name, cap, avail, reservoir_id))
    _run(conn, "reservoir", work)
    return get_status(conn, reservoir_id)


def list_zones(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT z.zone_id, z.name, z.population, COUNT(u.user_id) AS user_count "
                    "FROM zones z LEFT JOIN users u ON u.zone_id = z.zone_id "
                    "GROUP BY z.zone_id ORDER BY z.zone_id")
        return cur.fetchall()


def _zone_values(name, population):
    if name is not None and not name.strip():
        raise InvalidInput("the zone needs a name")
    if population is not None and population <= 0:
        raise InvalidInput("population must be more than 0")


def create_zone(conn, name, population):
    _zone_values(name, population)

    def work(cur):
        cur.execute("INSERT INTO zones (name, population) VALUES (%s, %s) "
                    "RETURNING zone_id, name, population", (name.strip(), population))
        return cur.fetchone()
    return _run(conn, "zone", work)


def update_zone(conn, zone_id, name=None, population=None):
    _zone_values(name, population)

    def work(cur):
        cur.execute("UPDATE zones SET name = COALESCE(%s, name), population = COALESCE(%s, population) "
                    "WHERE zone_id = %s RETURNING zone_id, name, population",
                    (None if name is None else name.strip(), population, zone_id))
        row = cur.fetchone()
        if row is None:
            raise NotFoundError(f"zone {zone_id} not found")
        return row
    return _run(conn, "zone", work)


def delete_zone(conn, zone_id):
    def work(cur):
        cur.execute("SELECT COUNT(*) AS n FROM users WHERE zone_id = %s", (zone_id,))
        if cur.fetchone()["n"]:
            raise ConflictError("this zone still has requesters. Move or remove them first.")
        # forecasts, history and alerts belong to the zone and go with it
        for table in ("consumption", "predictions", "alerts"):
            cur.execute(f"DELETE FROM {table} WHERE zone_id = %s", (zone_id,))
        cur.execute("DELETE FROM zones WHERE zone_id = %s RETURNING zone_id", (zone_id,))
        if cur.fetchone() is None:
            raise NotFoundError(f"zone {zone_id} not found")
        return {"deleted": zone_id}
    return _run(conn, "zone", work)


def _user_values(name, user_type, priority):
    if name is not None and not name.strip():
        raise InvalidInput("the requester needs a name")
    if user_type is not None and user_type not in USER_TYPES:
        raise InvalidInput(f"type must be one of {', '.join(USER_TYPES)}")
    if priority is not None and not 1 <= priority <= 5:
        raise InvalidInput("priority must be between 1 and 5")


USER_COLUMNS = "user_id, name, user_type, priority, zone_id"


def _with_zone_name(cur, row):
    cur.execute("SELECT name FROM zones WHERE zone_id = %s", (row["zone_id"],))
    z = cur.fetchone()
    return dict(row, zone_name=z["name"] if z else None)


def create_user(conn, name, user_type, priority, zone_id):
    _user_values(name, user_type, priority)

    def work(cur):
        cur.execute("SELECT zone_id FROM zones WHERE zone_id = %s", (zone_id,))
        if cur.fetchone() is None:
            raise NotFoundError(f"zone {zone_id} not found")
        cur.execute(f"INSERT INTO users (name, user_type, priority, zone_id) VALUES (%s, %s, %s, %s) "
                    f"RETURNING {USER_COLUMNS}", (name.strip(), user_type, priority, zone_id))
        return _with_zone_name(cur, cur.fetchone())
    return _run(conn, "requester", work)


def update_user(conn, user_id, name=None, user_type=None, priority=None, zone_id=None):
    """Change a requester. Pending requests pick up the new priority/zone the next time plans are made;
    plans already applied keep what they recorded."""
    _user_values(name, user_type, priority)

    def work(cur):
        if zone_id is not None:
            cur.execute("SELECT zone_id FROM zones WHERE zone_id = %s", (zone_id,))
            if cur.fetchone() is None:
                raise NotFoundError(f"zone {zone_id} not found")
        cur.execute(f"UPDATE users SET name = COALESCE(%s, name), user_type = COALESCE(%s, user_type), "
                    f"priority = COALESCE(%s, priority), zone_id = COALESCE(%s, zone_id) "
                    f"WHERE user_id = %s RETURNING {USER_COLUMNS}",
                    (None if name is None else name.strip(), user_type, priority, zone_id, user_id))
        row = cur.fetchone()
        if row is None:
            raise NotFoundError(f"requester {user_id} not found")
        return _with_zone_name(cur, row)
    return _run(conn, "requester", work)


def delete_user(conn, user_id):
    def work(cur):
        cur.execute("SELECT COUNT(*) AS n FROM water_requests WHERE user_id = %s", (user_id,))
        if cur.fetchone()["n"]:
            raise ConflictError("this requester has water requests on record, so it cannot be removed. "
                                "You can still rename it or change its priority.")
        cur.execute("DELETE FROM users WHERE user_id = %s RETURNING user_id", (user_id,))
        if cur.fetchone() is None:
            raise NotFoundError(f"requester {user_id} not found")
        return {"deleted": user_id}
    return _run(conn, "requester", work)
