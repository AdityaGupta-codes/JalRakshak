"""Daily zone supply timetable and tanker trips.

The request module (service.py) shares a reservoir's stored water among organisations. This module plans
the regular piped supply: which zone gets water when, for how long, and how much, within each source's
daily release, pump capacity and each zone's inlet-main capacity (see scheduler/include/timetable.hpp).

Demand per zone comes from the policy norm (population x litres per person per day) or, when real meter
data exists, from the average of the last 7 metered days. Synthetic (generated) consumption is never used
for the timetable.

Workflow: build a draft -> review -> publish (one published timetable per date; publishing another one
withdraws the earlier) -> every organisation is told its zone's supply hours. Zones whose delivered litres
per person fall below the policy's tanker trigger can get tanker trips planned from the timetable.
"""
import json
import math
from datetime import date

from . import audit, policy
from .errors import ConflictError, InvalidInput, NotFoundError
from .policy import plain
from .scheduler_client import build_timetable_input, run_scheduler

DEMAND_MODES = ("norm", "meter")


def _json(v):
    return json.loads(v) if isinstance(v, str) else plain(v)


def _network(cur):
    cur.execute("SELECT reservoir_id, name, daily_release_litres, pump_capacity_lph FROM reservoirs "
                "ORDER BY reservoir_id")
    sources = cur.fetchall()
    cur.execute("SELECT zone_id, name, population, reservoir_id, inlet_capacity_lph, supply_priority "
                "FROM zones ORDER BY zone_id")
    return sources, cur.fetchall()


def _meter_demand(cur):
    """Average daily litres per zone over its last 7 days of real (metered / imported) consumption."""
    cur.execute("SELECT zone_id, AVG(litres) AS litres, COUNT(*) AS days FROM ("
                "  SELECT zone_id, litres, ROW_NUMBER() OVER (PARTITION BY zone_id ORDER BY recorded_on DESC) AS k"
                "  FROM consumption WHERE source IN ('meter', 'import')) t "
                "WHERE k <= 7 GROUP BY zone_id")
    return {r["zone_id"]: float(r["litres"]) for r in cur.fetchall()}


def build_draft(conn, actor, for_date=None, demand_mode="norm", releases=None, note=None):
    """Compute a timetable and save it as a draft. releases: {reservoir_id: litres} to override today's
    release of a source (e.g. lower because of a pump failure)."""
    if demand_mode not in DEMAND_MODES:
        raise InvalidInput(f"demand_mode must be one of {', '.join(DEMAND_MODES)}")
    for_date = for_date or date.today()
    releases = {int(k): float(v) for k, v in (releases or {}).items()}
    with conn:
        with conn.cursor() as cur:
            p = policy.active(cur)
            if p is None:
                raise ConflictError("there is no approved water policy")
            sources, zones = _network(cur)
            skipped, src_rows, zone_rows = [], [], []
            for s in sources:
                release = releases.get(s["reservoir_id"], s["daily_release_litres"])
                if release is None or s["pump_capacity_lph"] is None:
                    continue
                if float(release) < 0:
                    raise InvalidInput("a release cannot be negative")
                src_rows.append({"reservoir_id": s["reservoir_id"], "name": s["name"],
                                 "release_litres": float(release), "pump_capacity_lph": float(s["pump_capacity_lph"])})
            usable = {s["reservoir_id"] for s in src_rows}
            meter = _meter_demand(cur) if demand_mode == "meter" else {}
            for z in zones:
                if z["reservoir_id"] is None or z["inlet_capacity_lph"] is None:
                    skipped.append({"zone_id": z["zone_id"], "name": z["name"],
                                    "reason": "no source reservoir or inlet capacity set (Settings > Zones)"})
                    continue
                if z["reservoir_id"] not in usable:
                    skipped.append({"zone_id": z["zone_id"], "name": z["name"],
                                    "reason": "its source has no daily release or pump capacity set"})
                    continue
                demand = None
                if demand_mode == "meter":
                    if z["zone_id"] not in meter:
                        skipped.append({"zone_id": z["zone_id"], "name": z["name"],
                                        "reason": "no metered consumption for this zone yet"})
                        continue
                    demand = meter[z["zone_id"]]
                zone_rows.append({"zone_id": z["zone_id"], "name": z["name"], "population": z["population"],
                                  "reservoir_id": z["reservoir_id"], "inlet_capacity_lph": z["inlet_capacity_lph"],
                                  "demand_litres": demand, "priority": z["supply_priority"],
                                  "min_share": p["zone_min_share"]})
            if not zone_rows:
                raise ConflictError("no zone can be planned: set each zone's source and inlet capacity, and each "
                                    "source's daily release and pump capacity (Settings)")
            payload = build_timetable_input(src_rows, zone_rows, p["lpcd"], p["supply_start_hour"],
                                            p["supply_end_hour"])
            result = run_scheduler(payload)
            result["skipped_zones"] = skipped
            result["demand_mode"] = demand_mode
            cur.execute("INSERT INTO supply_timetables (for_date, status, policy_id, input, result, note, created_by) "
                        "VALUES (%s, 'draft', %s, %s::jsonb, %s::jsonb, %s, %s) RETURNING timetable_id",
                        (str(for_date), p["policy_id"], json.dumps(payload), json.dumps(result),
                         (note or "").strip() or None, actor["account_id"] if actor else None))
            tid = cur.fetchone()["timetable_id"]
            audit.write(cur, actor, "timetable.draft", "timetable", tid,
                        {"for_date": str(for_date), "demand_mode": demand_mode,
                         "served_ratio": result["metrics"]["served_ratio"]})
            return _get(cur, tid)


def _get(cur, tid):
    cur.execute("SELECT t.timetable_id, t.for_date, t.status, t.policy_id, t.input, t.result, t.note, t.created_at, "
                "t.published_at, ca.email AS created_by_email, pa.email AS published_by_email, p.version AS policy_version "
                "FROM supply_timetables t LEFT JOIN accounts ca ON ca.account_id = t.created_by "
                "LEFT JOIN accounts pa ON pa.account_id = t.published_by "
                "LEFT JOIN policies p ON p.policy_id = t.policy_id WHERE t.timetable_id = %s", (tid,))
    row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"timetable {tid} not found")
    row = dict(row)
    row["input"], row["result"] = _json(row["input"]), _json(row["result"])
    return row


def get(conn, tid):
    with conn.cursor() as cur:
        return _get(cur, tid)


def list_timetables(conn, limit=30):
    with conn.cursor() as cur:
        cur.execute("SELECT timetable_id, for_date, status, policy_id, created_at, published_at, "
                    "result->'metrics' AS metrics FROM supply_timetables ORDER BY for_date DESC, timetable_id DESC "
                    "LIMIT %s", (limit,))
        return [dict(r, metrics=_json(r["metrics"])) for r in cur.fetchall()]


def _blocks_text(zone):
    blocks = zone.get("blocks") or []
    return ", ".join(f"{b['start']}-{b['end']}" for b in blocks) or "no piped supply"


def publish(conn, actor, tid):
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT timetable_id, for_date, status, result FROM supply_timetables "
                        "WHERE timetable_id = %s FOR UPDATE", (tid,))
            t = cur.fetchone()
            if t is None:
                raise NotFoundError(f"timetable {tid} not found")
            if t["status"] != "draft":
                raise ConflictError("only a draft timetable can be published")
            cur.execute("UPDATE supply_timetables SET status = 'withdrawn' WHERE for_date = %s AND status = 'published'",
                        (t["for_date"],))
            cur.execute("UPDATE supply_timetables SET status = 'published', published_by = %s, published_at = now() "
                        "WHERE timetable_id = %s", (actor["account_id"] if actor else None, tid))
            result = _json(t["result"])
            for z in result.get("zones", []):
                cur.execute("INSERT INTO notifications (account_id, kind, title, body) "
                            "SELECT a.account_id, 'timetable', %s, %s FROM accounts a JOIN users u ON u.user_id = a.user_id "
                            "WHERE u.zone_id = %s AND a.status = 'active'",
                            (f"Water supply on {t['for_date']}: {z['name']}",
                             f"Piped supply to your zone: {_blocks_text(z)} "
                             f"(about {z['lpcd_delivered']:.0f} litres per person).", z["zone_id"]))
            audit.write(cur, actor, "timetable.publish", "timetable", tid, {"for_date": str(t["for_date"])})
            return _get(cur, tid)


def zone_supply(conn, zone_id):
    """Latest published timetable entry for a zone (for the user panel)."""
    with conn.cursor() as cur:
        cur.execute("SELECT timetable_id, for_date, result FROM supply_timetables WHERE status = 'published' "
                    "ORDER BY for_date DESC, published_at DESC LIMIT 1")
        t = cur.fetchone()
        if t is None:
            return None
        result = _json(t["result"])
        for z in result.get("zones", []):
            if z["zone_id"] == zone_id:
                return {"timetable_id": t["timetable_id"], "for_date": str(t["for_date"]), "zone": z,
                        "lpcd_norm": result.get("lpcd_norm")}
        return {"timetable_id": t["timetable_id"], "for_date": str(t["for_date"]), "zone": None,
                "lpcd_norm": result.get("lpcd_norm")}


# ---------------------------------------------------------------- tankers
TRIP_STATUSES = ("planned", "dispatched", "delivered", "cancelled")
TRIP_COLUMNS = ("t.trip_id, t.zone_id, z.name AS zone_name, t.timetable_id, t.planned_for, t.litres, t.loads, t.status, "
                "t.origin, t.vehicle, t.notes, t.created_at, t.updated_at, t.delivered_at")


def list_trips(conn, from_date=None):
    sql = f"SELECT {TRIP_COLUMNS} FROM tanker_trips t JOIN zones z ON z.zone_id = t.zone_id"
    params = ()
    if from_date:
        sql += " WHERE t.planned_for >= %s"
        params = (str(from_date),)
    sql += " ORDER BY t.planned_for DESC, t.trip_id DESC LIMIT 300"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _trip(cur, trip_id):
    cur.execute(f"SELECT {TRIP_COLUMNS} FROM tanker_trips t JOIN zones z ON z.zone_id = t.zone_id "
                "WHERE t.trip_id = %s", (trip_id,))
    row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"tanker trip {trip_id} not found")
    return row


def create_trip(conn, actor, zone_id, litres, planned_for=None, vehicle=None, notes=None, loads=1):
    if not litres or float(litres) <= 0:
        raise InvalidInput("litres must be more than 0")
    if int(loads) < 1:
        raise InvalidInput("loads must be at least 1")
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT zone_id FROM zones WHERE zone_id = %s", (zone_id,))
            if cur.fetchone() is None:
                raise NotFoundError(f"zone {zone_id} not found")
            cur.execute("INSERT INTO tanker_trips (zone_id, planned_for, litres, loads, origin, vehicle, notes, created_by) "
                        "VALUES (%s, %s, %s, %s, 'manual', %s, %s, %s) RETURNING trip_id",
                        (zone_id, str(planned_for or date.today()), float(litres), int(loads), vehicle, notes,
                         actor["account_id"] if actor else None))
            tid = cur.fetchone()["trip_id"]
            audit.write(cur, actor, "tanker.create", "tanker", tid, {"zone_id": zone_id, "litres": float(litres)})
            return _trip(cur, tid)


def plan_tankers(conn, actor, timetable_id):
    """Plan tanker supply for zones whose piped supply in this timetable is below the policy's tanker trigger
    (litres per person per day): one order per zone, with enough loads of the policy's tanker capacity to
    bring the zone up to the trigger. Zones that already have an order from this timetable are skipped."""
    with conn:
        with conn.cursor() as cur:
            t = _get(cur, timetable_id)
            p = policy.active(cur)
            trigger, cap = float(p["tanker_trigger_lpcd"]), float(p["tanker_capacity_litres"])
            created = []
            for z in t["result"].get("zones", []):
                if z["lpcd_delivered"] >= trigger:
                    continue
                cur.execute("SELECT 1 AS x FROM tanker_trips WHERE timetable_id = %s AND zone_id = %s AND status <> 'cancelled'",
                            (timetable_id, z["zone_id"]))
                if cur.fetchone():
                    continue
                needed = (trigger - z["lpcd_delivered"]) * z["population"]
                loads = max(1, math.ceil(needed / cap - 1e-9))
                cur.execute("INSERT INTO tanker_trips (zone_id, timetable_id, planned_for, litres, loads, origin, notes, "
                            "created_by) VALUES (%s, %s, %s, %s, %s, 'timetable', %s, %s) RETURNING trip_id",
                            (z["zone_id"], timetable_id, str(t["for_date"]), loads * cap, loads,
                             f"Piped supply {z['lpcd_delivered']:.0f} L/person, below the {trigger:.0f} L/person trigger: "
                             f"{loads} loads of {cap:,.0f} L", actor["account_id"] if actor else None))
                created.append(cur.fetchone()["trip_id"])
            audit.write(cur, actor, "tanker.plan", "timetable", timetable_id, {"orders": len(created)})
            return [_trip(cur, i) for i in created]


def update_trip(conn, actor, trip_id, status=None, vehicle=None, notes=None):
    if status is not None and status not in TRIP_STATUSES:
        raise InvalidInput(f"status must be one of {', '.join(TRIP_STATUSES)}")
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT trip_id, status, zone_id, litres, loads FROM tanker_trips WHERE trip_id = %s FOR UPDATE",
                        (trip_id,))
            row = cur.fetchone()
            if row is None:
                raise NotFoundError(f"tanker trip {trip_id} not found")
            if row["status"] in ("delivered", "cancelled") and status not in (None, row["status"]):
                raise ConflictError("this trip is already finished")
            cur.execute("UPDATE tanker_trips SET status = COALESCE(%s, status), vehicle = COALESCE(%s, vehicle), "
                        "notes = COALESCE(%s, notes), updated_at = now(), "
                        "delivered_at = CASE WHEN %s = 'delivered' THEN now() ELSE delivered_at END WHERE trip_id = %s",
                        (status, vehicle, notes, status, trip_id))
            if status == "dispatched" and row["status"] != "dispatched":
                cur.execute("INSERT INTO notifications (account_id, kind, title, body) "
                            "SELECT a.account_id, 'tanker', %s, %s FROM accounts a JOIN users u ON u.user_id = a.user_id "
                            "WHERE u.zone_id = %s AND a.status = 'active'",
                            ("Water tankers on their way",
                             f"{row['loads']} tanker load(s), {float(row['litres']):,.0f} L in total, are on their way to your zone"
                             + (f" (vehicle {vehicle})." if vehicle else "."), row["zone_id"]))
            audit.write(cur, actor, "tanker.update", "tanker", trip_id, {"status": status, "vehicle": vehicle})
            return _trip(cur, trip_id)


# ---------------------------------------------------------------- network settings (sources, zones, map)
def _num(v, what, minimum=0.0, allow_none=True):
    if v is None:
        if allow_none:
            return None
        raise InvalidInput(f"{what} is required")
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise InvalidInput(f"{what} must be a number")
    if v < minimum or v > 1e12:
        raise InvalidInput(f"{what} must be at least {minimum:g}")
    return v


def create_reservoir(conn, actor, name, capacity_litres, available_litres=0, daily_release_litres=None,
                     pump_capacity_lph=None):
    name = (name or "").strip()
    if not name:
        raise InvalidInput("the reservoir needs a name")
    cap = _num(capacity_litres, "capacity", 1, allow_none=False)
    avail = _num(available_litres or 0, "water level")
    if avail > cap:
        raise InvalidInput("the water level cannot be more than the capacity")
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS x FROM reservoirs WHERE name = %s", (name,))
            if cur.fetchone():
                raise ConflictError("another reservoir already has that name")
            cur.execute("INSERT INTO reservoirs (name, capacity_litres, available_litres, daily_release_litres, "
                        "pump_capacity_lph) VALUES (%s, %s, %s, %s, %s) RETURNING reservoir_id, name, capacity_litres, "
                        "available_litres, daily_release_litres, pump_capacity_lph, updated_at",
                        (name, cap, avail, _num(daily_release_litres, "daily release"),
                         _num(pump_capacity_lph, "pump capacity", 1)))
            row = cur.fetchone()
            audit.write(cur, actor, "reservoir.create", "reservoir", row["reservoir_id"], {"name": name, "capacity": cap})
            return row


def update_reservoir_network(conn, actor, reservoir_id, daily_release_litres=None, pump_capacity_lph=None):
    with conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE reservoirs SET daily_release_litres = COALESCE(%s, daily_release_litres), "
                        "pump_capacity_lph = COALESCE(%s, pump_capacity_lph), updated_at = now() WHERE reservoir_id = %s "
                        "RETURNING reservoir_id, name, capacity_litres, available_litres, daily_release_litres, "
                        "pump_capacity_lph, updated_at",
                        (_num(daily_release_litres, "daily release"), _num(pump_capacity_lph, "pump capacity", 1),
                         reservoir_id))
            row = cur.fetchone()
            if row is None:
                raise NotFoundError(f"reservoir {reservoir_id} not found")
            audit.write(cur, actor, "reservoir.network", "reservoir", reservoir_id,
                        {"daily_release_litres": daily_release_litres, "pump_capacity_lph": pump_capacity_lph})
            return row


def validate_boundary(geo):
    """A GeoJSON Polygon or MultiPolygon with [longitude, latitude] positions; rings must be closed."""
    if not isinstance(geo, dict) or geo.get("type") not in ("Polygon", "MultiPolygon"):
        raise InvalidInput("the zone boundary must be a GeoJSON Polygon or MultiPolygon")
    polys = [geo.get("coordinates")] if geo["type"] == "Polygon" else geo.get("coordinates")
    if not isinstance(polys, list) or not polys:
        raise InvalidInput("the boundary has no coordinates")
    points = 0
    for poly in polys:
        if not isinstance(poly, list) or not poly:
            raise InvalidInput("each polygon needs at least one ring")
        for ring in poly:
            if not isinstance(ring, list) or len(ring) < 4 or ring[0] != ring[-1]:
                raise InvalidInput("each ring needs at least 4 positions and must end where it starts")
            for pt in ring:
                if (not isinstance(pt, list) or len(pt) < 2 or not all(isinstance(c, (int, float)) for c in pt[:2])
                        or not -180 <= pt[0] <= 180 or not -90 <= pt[1] <= 90):
                    raise InvalidInput("positions must be [longitude, latitude] in degrees")
                points += 1
    if points > 20000:
        raise InvalidInput("the boundary is too detailed (more than 20,000 points); simplify it first")
    return {"type": geo["type"], "coordinates": geo["coordinates"]}


def update_zone_network(conn, actor, zone_id, reservoir_id=None, inlet_capacity_lph=None, supply_priority=None,
                        boundary=None, clear_boundary=False):
    if supply_priority is not None and not 1 <= int(supply_priority) <= 5:
        raise InvalidInput("supply priority must be between 1 and 5")
    if isinstance(boundary, dict) and boundary.get("type") == "Feature":
        boundary = boundary.get("geometry")
    geo = validate_boundary(boundary) if boundary is not None else None
    with conn:
        with conn.cursor() as cur:
            if reservoir_id is not None:
                cur.execute("SELECT 1 AS x FROM reservoirs WHERE reservoir_id = %s", (reservoir_id,))
                if cur.fetchone() is None:
                    raise NotFoundError(f"reservoir {reservoir_id} not found")
            cur.execute("UPDATE zones SET reservoir_id = COALESCE(%s, reservoir_id), "
                        "inlet_capacity_lph = COALESCE(%s, inlet_capacity_lph), "
                        "supply_priority = COALESCE(%s, supply_priority), "
                        "boundary = CASE WHEN %s THEN NULL ELSE COALESCE(%s::jsonb, boundary) END "
                        "WHERE zone_id = %s RETURNING zone_id, name, population, reservoir_id, inlet_capacity_lph, "
                        "supply_priority, boundary",
                        (reservoir_id, _num(inlet_capacity_lph, "inlet capacity", 1), supply_priority,
                         bool(clear_boundary), json.dumps(geo) if geo else None, zone_id))
            row = cur.fetchone()
            if row is None:
                raise NotFoundError(f"zone {zone_id} not found")
            audit.write(cur, actor, "zone.network", "zone", zone_id,
                        {"reservoir_id": reservoir_id, "inlet_capacity_lph": inlet_capacity_lph,
                         "supply_priority": supply_priority, "boundary": "updated" if geo else
                         ("cleared" if clear_boundary else None)})
            return row


def zone_map(conn):
    """Zones with boundary and current status, for the map: latest published timetable and open alerts."""
    with conn.cursor() as cur:
        cur.execute("SELECT zone_id, name, population, boundary, supply_priority FROM zones ORDER BY zone_id")
        zones = [dict(z, boundary=_json(z["boundary"]) if z["boundary"] is not None else None) for z in cur.fetchall()]
        cur.execute("SELECT result FROM supply_timetables WHERE status = 'published' ORDER BY for_date DESC, "
                    "published_at DESC LIMIT 1")
        t = cur.fetchone()
        by_zone = {z["zone_id"]: z for z in (_json(t["result"]).get("zones", []) if t else [])}
        cur.execute("SELECT zone_id, COUNT(*) AS n FROM alerts WHERE NOT resolved AND zone_id IS NOT NULL GROUP BY zone_id")
        alerts = {r["zone_id"]: r["n"] for r in cur.fetchall()}
        cur.execute("SELECT zone_id, COUNT(*) AS n FROM issues WHERE status IN ('open', 'in_progress') "
                    "AND zone_id IS NOT NULL GROUP BY zone_id")
        issues = {r["zone_id"]: r["n"] for r in cur.fetchall()}
    for z in zones:
        tt = by_zone.get(z["zone_id"])
        z["lpcd_delivered"] = tt["lpcd_delivered"] if tt else None
        z["meets_norm"] = tt["meets_norm"] if tt else None
        z["supply_blocks"] = tt["blocks"] if tt else []
        z["open_alerts"] = alerts.get(z["zone_id"], 0)
        z["open_issues"] = issues.get(z["zone_id"], 0)
    return zones
