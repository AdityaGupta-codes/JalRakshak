"""Demo API: the same endpoints and JSON shapes as the FastAPI backend, but the data lives in memory.

It runs the REAL C++ scheduler, so every number is genuine. Use it to try the dashboard without Docker
or PostgreSQL (and to test the dashboard itself):

    python frontend/dev/mock_api.py --bin scheduler/build/jalrakshak-scheduler --port 8000

Only the Python standard library is needed. The real backend (backend/app) is what Docker runs.
"""
import argparse
import json
import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
from app import config  # noqa: E402
from app.scheduler_client import SchedulerError, build_scheduler_input, run_scheduler  # noqa: E402

ALGOS = ["fcfs", "priority", "round_robin", "water_aware"]
LOCK = threading.Lock()
SNAPSHOT = os.path.join(os.path.dirname(__file__), "..", "..", "ml", "results", "forecast_snapshot.json")
ADMIN_TOKEN = os.environ.get("JAL_ADMIN_TOKEN", "")


def iso(dt):
    return dt.isoformat()


class Store:
    def __init__(self):
        # Settings (kept by "Reset demo data", like the real database)
        self.res_name = "Main Reservoir"
        self.capacity = 200000.0
        self.zones = {1: 40000, 2: 65000, 3: 30000}
        self.zone_names = {1: "North Zone", 2: "Central Zone", 3: "South Zone"}
        # user_id: (name, type, priority, zone)
        self.users = {1: ("City Hospital", "hospital", 5, 2), 2: ("Green Valley School", "school", 4, 1),
                      3: ("Rajpur Colony", "domestic", 4, 1), 4: ("Shanti Apartments", "domestic", 4, 2),
                      5: ("Metro Steel Works", "industry", 2, 3), 6: ("Textile Mill", "industry", 2, 3),
                      7: ("Central Mall", "commercial", 2, 2), 8: ("Town Park", "park", 1, 2)}
        self.reset(50000)

    def reset(self, available=None):
        now = datetime.now(timezone.utc)
        available = min(50000.0, self.capacity) if available is None else float(available)
        if not 0 <= available <= self.capacity:
            raise Error(400, f"the water level must be between 0 and the capacity ({self.capacity:,.0f} L)")
        self.available = available
        self.updated = now
        # request_id: user, litres, urgency, arrival offset (min), deadline offset (hours)
        seed = [(5, 20000, 2, 0, 8), (3, 12000, 4, 5, 3), (1, 10000, 5, 10, 1), (7, 8000, 2, 12, 6),
                (2, 6000, 3, 15, 4), (8, 4000, 1, 20, 10), (4, 15000, 4, 25, 3), (6, 5000, 2, 30, 8)]
        base = now - timedelta(minutes=45)
        self.requests = {}
        for i, (u, litres, urg, arr, dl) in enumerate(seed, 1):
            arrival = base + timedelta(minutes=arr)
            self.requests[i] = {"request_id": i, "user_id": u, "reservoir_id": 1, "requested_litres": float(litres),
                                "urgency": urg, "arrival_time": arrival, "deadline": base + timedelta(hours=dl),
                                "status": "pending"}
        self.runs = []
        self.next_request = len(seed) + 1
        self.next_run = 1
        self.load_forecast()

    def load_forecast(self):
        """Forecasts and leak alerts written by the ML pipeline (ml/results), if it has been run."""
        self.forecast, self.alerts = None, []
        try:
            with open(SNAPSHOT) as f:
                snap = json.load(f)
        except (OSError, ValueError):
            return
        # Shift the snapshot's dates so that its first forecast day is today.
        first = min(p["target_date"] for p in snap["predictions"])
        shift = (datetime.now(timezone.utc).date() - datetime.fromisoformat(first).date()).days
        move = lambda d: (datetime.fromisoformat(d).date() + timedelta(days=shift)).isoformat()  # noqa: E731
        preds = [dict(p, target_date=move(p["target_date"])) for p in snap["predictions"]]
        today = datetime.now(timezone.utc).date().isoformat()
        best = next(m for m in snap["models"] if m["is_selected"])
        self.forecast = {
            "zones": snap["zones"],
            "history": [dict(h, date=move(h["date"])) for h in snap["history"]],
            "predictions": preds,
            "models": sorted(snap["models"], key=lambda m: m["mae_litres"]),
            "today_litres": sum(p["predicted_litres"] for p in preds if p["target_date"] == today) or None,
            "selected_model": best["model_name"],
            "available": True,
        }
        self.forecast["used_by_scheduler"] = self.forecast["today_litres"] is not None
        now = iso(datetime.now(timezone.utc))
        self.alerts = [dict(a, alert_id=i, resolved=False, created_at=now, live=False)
                       for i, a in enumerate(snap["alerts"], 1)]

    # ---- helpers ----
    def request_view(self, r):
        name, utype, prio, _ = self.users[r["user_id"]]
        return {"request_id": r["request_id"], "user_id": r["user_id"], "user_name": name, "user_type": utype,
                "priority": prio, "reservoir_id": 1, "requested_litres": r["requested_litres"],
                "urgency": r["urgency"], "arrival_time": iso(r["arrival_time"]), "deadline": iso(r["deadline"]),
                "status": r["status"]}

    def pending_rows(self):
        rows = []
        for r in sorted(self.requests.values(), key=lambda x: (x["arrival_time"], x["request_id"])):
            if r["status"] != "pending":
                continue
            name, utype, prio, zone = self.users[r["user_id"]]
            rows.append({**r, "user_name": name, "user_type": utype, "priority": prio, "zone_id": zone,
                         "zone_population": self.zones[zone]})
        return rows

    def zone_ratios(self):
        got, asked = {}, {}
        for run in self.runs:
            for a in run["allocations"]:
                zone = self.users[self.requests[a["request_id"]]["user_id"]][3]
                got[zone] = got.get(zone, 0) + a["allocated_litres"]
                asked[zone] = asked.get(zone, 0) + self.requests[a["request_id"]]["requested_litres"]
        return {z: min(1.0, got[z] / asked[z]) for z in got if asked.get(z)}

    def plan(self, algorithm, rows):
        predicted = self.forecast["today_litres"] if self.forecast else None
        payload = build_scheduler_input(algorithm, self.available, rows, self.zone_ratios(), predicted,
                                        config.DEFAULT_SCHEDULER_CONFIG)
        return run_scheduler(payload)

    def status(self):
        pend = [r for r in self.requests.values() if r["status"] == "pending"]
        demand = sum(r["requested_litres"] for r in pend)
        if demand <= 0 or demand <= self.available:
            level, ratio = "ok", (demand / self.available if self.available > 0 else 0.0)
        else:
            ratio = demand / self.available if self.available > 0 else None
            level = "moderate" if ratio is not None and ratio <= 1.5 else "severe"
        return {"reservoir_id": 1, "name": self.res_name, "capacity_litres": self.capacity,
                "available_litres": self.available, "fill_ratio": self.available / self.capacity,
                "pending_count": len(pend), "pending_demand_litres": demand, "demand_to_supply_ratio": ratio,
                "scarcity": level, "updated_at": iso(self.updated),
                "forecast_today_litres": self.forecast["today_litres"] if self.forecast else None,
                "forecast_model": self.forecast["selected_model"] if self.forecast else None}

    def all_alerts(self):
        st = self.status()
        live = []
        fill = st["fill_ratio"]
        if fill < 0.30:
            live.append({"alert_id": None, "zone_id": None, "alert_type": "low_water",
                         "severity": "critical" if fill < 0.15 else "warning",
                         "message": f"{self.res_name} is at {fill:.0%} of capacity "
                                    f"({st['available_litres']:,.0f} L of {st['capacity_litres']:,.0f} L).",
                         "resolved": False, "created_at": st["updated_at"], "live": True})
        today = st["forecast_today_litres"]
        if today is not None and today > st["available_litres"]:
            live.append({"alert_id": None, "zone_id": None, "alert_type": "high_demand", "severity": "warning",
                         "message": f"Forecast demand today is {today:,.0f} L, more than the "
                                    f"{st['available_litres']:,.0f} L available.",
                         "resolved": False, "created_at": st["updated_at"], "live": True})
        order = {"critical": 0, "warning": 1, "info": 2}
        return sorted(live + [a for a in self.alerts if not a["resolved"]], key=lambda a: order[a["severity"]])


S = Store()


class Error(Exception):
    def __init__(self, status, detail):
        self.status, self.detail = status, detail


def simulate(body):
    algorithms = body.get("algorithms", ALGOS)
    for a in algorithms:
        if a not in ALGOS:
            raise Error(422, [{"loc": ["body", "algorithms"], "msg": f"unknown algorithm {a}"}])
    rows = S.pending_rows()
    results = []
    if rows:
        for a in algorithms:
            try:
                results.append(S.plan(a, rows))
            except SchedulerError as exc:
                results.append({"algorithm": a, "error": str(exc)})
    return {"reservoir_id": 1, "available_litres": S.available, "pending_count": len(rows),
            "requests": [{"request_id": r["request_id"], "user_name": r["user_name"], "user_type": r["user_type"],
                          "priority": r["priority"], "requested_litres": r["requested_litres"]} for r in rows],
            "results": results}


def commit(body):
    algorithm = body.get("algorithm", "fcfs")
    rows = S.pending_rows()
    if not rows:
        raise Error(409, "no pending requests for this reservoir")
    try:
        result = S.plan(algorithm, rows)
    except SchedulerError as exc:
        raise Error(400, str(exc))
    total = sum(a["allocated_litres"] for a in result["allocations"])
    run = {"run_id": S.next_run, "algorithm": algorithm, "scenario": None, "reservoir_id": 1,
           "available_before": S.available, "available_after": result["remaining_litres"],
           "metrics": result["metrics"], "committed": True, "created_at": iso(datetime.now(timezone.utc)),
           "allocations": result["allocations"]}
    S.next_run += 1
    S.runs.append(run)
    for a in result["allocations"]:
        S.requests[a["request_id"]]["status"] = a["status"]
    S.available -= total
    S.updated = datetime.now(timezone.utc)
    return dict(result, run_id=run["run_id"], reservoir_id=1)


def create_request(body):
    problems = []
    litres = body.get("requested_litres")
    if not isinstance(litres, (int, float)) or litres <= 0:
        problems.append({"loc": ["body", "requested_litres"], "msg": "Input should be greater than 0"})
    if problems:
        raise Error(422, problems)
    if body.get("user_id") not in S.users:
        raise Error(404, "user_id or reservoir_id does not exist")
    now = datetime.now(timezone.utc)
    rid = S.next_request
    S.next_request += 1
    S.requests[rid] = {"request_id": rid, "user_id": body["user_id"], "reservoir_id": 1,
                       "requested_litres": float(litres), "urgency": int(body.get("urgency", 3)),
                       "arrival_time": now, "deadline": now + timedelta(minutes=float(body.get("deadline_in_minutes", 240))),
                       "status": "pending"}
    return S.request_view(S.requests[rid]) | {"status": "pending"}


def run_view(run, detail=False):
    keys = ("run_id", "algorithm", "scenario", "reservoir_id", "available_before", "available_after",
            "metrics", "committed", "created_at")
    out = {k: run[k] for k in keys}
    if detail:
        allocs = []
        for a in run["allocations"]:
            r = S.requests[a["request_id"]]
            name, utype, prio, _ = S.users[r["user_id"]]
            allocs.append({"request_id": a["request_id"], "user_name": name, "user_type": utype, "priority": prio,
                           "requested_litres": r["requested_litres"], "allocated_litres": a["allocated_litres"],
                           "wait_minutes": a["wait_time"], "score": a["score"]})
        out["allocations"] = allocs
    return out


def route(method, path, query, body, token=None):
    if method == "GET":
        if path == "/health":
            return {"status": "ok", "database": "connected"}
        if path == "/status":
            return S.status()
        if path == "/resources":
            return [{"reservoir_id": 1, "name": S.res_name, "capacity_litres": S.capacity,
                     "available_litres": S.available, "updated_at": iso(S.updated)}]
        if path == "/users":
            return [user_view(i) for i in sorted(S.users)]
        if path == "/zones":
            return [zone_view(z) for z in sorted(S.zones)]
        if path == "/requests":
            status = (query.get("status") or [None])[0]
            rows = sorted(S.requests.values(), key=lambda r: (r["arrival_time"], r["request_id"]))
            return [S.request_view(r) for r in rows if not status or r["status"] == status]
        if path == "/runs":
            return [run_view(r) for r in reversed(S.runs)]
        if path.startswith("/runs/"):
            for r in S.runs:
                if str(r["run_id"]) == path.split("/")[2]:
                    return run_view(r, detail=True)
            raise Error(404, f"run {path.split('/')[2]} not found")
        if path == "/auth":
            return {"token_required": bool(ADMIN_TOKEN)}
        if path == "/alerts":
            return S.all_alerts()
        if path == "/predictions":
            return S.forecast["predictions"] if S.forecast else []
        if path == "/forecast":
            if S.forecast:
                return S.forecast
            return {"zones": [], "history": [], "predictions": [], "models": [], "today_litres": None,
                    "selected_model": None, "available": False, "used_by_scheduler": False}
    if method == "POST" and path not in ("/allocate/simulate",) and ADMIN_TOKEN and token != ADMIN_TOKEN:
        raise Error(401, "This action needs the admin token. Unlock the dashboard first.")
    if method == "POST":
        if path == "/auth/check":
            return {"ok": True}
        if path.startswith("/alerts/") and path.endswith("/resolve"):
            for a in S.alerts:
                if str(a["alert_id"]) == path.split("/")[2]:
                    a["resolved"] = True
                    return a
            raise Error(404, f"alert {path.split('/')[2]} not found")
        if path == "/requests":
            return create_request(body)
        if path == "/allocate/simulate":
            return simulate(body)
        if path == "/allocate/commit":
            return commit(body)
        if path == "/demo/reset":
            S.reset(body.get("available_litres"))
            return S.status()
        if path == "/zones":
            return zone_save(None, body)
        if path == "/users":
            return user_save(None, body)
    if method in ("PATCH", "DELETE") and ADMIN_TOKEN and token != ADMIN_TOKEN:
        raise Error(401, "This action needs the admin token. Unlock the dashboard first.")
    parts = path.strip("/").split("/")
    if method == "PATCH" and len(parts) == 2:
        if parts[0] == "resources":
            return reservoir_save(int(parts[1]), body)
        if parts[0] == "zones":
            return zone_save(int(parts[1]), body)
        if parts[0] == "users":
            return user_save(int(parts[1]), body)
    if method == "DELETE" and len(parts) == 2:
        if parts[0] == "zones":
            zid = int(parts[1])
            if zid not in S.zones:
                raise Error(404, f"zone {zid} not found")
            if any(u[3] == zid for u in S.users.values()):
                raise Error(409, "this zone still has requesters. Move or remove them first.")
            del S.zones[zid], S.zone_names[zid]
            return {"deleted": zid}
        if parts[0] == "users":
            uid = int(parts[1])
            if uid not in S.users:
                raise Error(404, f"requester {uid} not found")
            if any(r["user_id"] == uid for r in S.requests.values()):
                raise Error(409, "this requester has water requests on record, so it cannot be removed. "
                                 "You can still rename it or change its priority.")
            del S.users[uid]
            return {"deleted": uid}
    raise Error(404, "not found")


USER_TYPES = ("hospital", "domestic", "school", "industry", "commercial", "park")


def _invalid(field, msg):
    return Error(422, [{"loc": ["body", field], "msg": msg}])


def reservoir_save(rid, body):
    if rid != 1:
        raise Error(404, f"reservoir {rid} not found")
    name = body.get("name", S.res_name)
    cap = float(body.get("capacity_litres", S.capacity))
    avail = float(body.get("available_litres", S.available))
    if not str(name).strip():
        raise _invalid("name", "String should have at least 1 character")
    if cap <= 0:
        raise _invalid("capacity_litres", "Input should be greater than 0")
    if avail < 0:
        raise _invalid("available_litres", "Input should be greater than or equal to 0")
    if avail > cap:
        if "available_litres" not in body:
            raise Error(400, f"capacity cannot be below the water already in the reservoir ({avail:,.0f} L). "
                             "Lower the water level as well.")
        raise Error(400, f"the water level ({avail:,.0f} L) cannot be more than the capacity ({cap:,.0f} L)")
    S.res_name, S.capacity, S.available = str(name).strip(), cap, avail
    S.updated = datetime.now(timezone.utc)
    return S.status()


def zone_view(zid):
    return {"zone_id": zid, "name": S.zone_names[zid], "population": S.zones[zid],
            "user_count": sum(1 for u in S.users.values() if u[3] == zid)}


def zone_save(zid, body):
    if zid is not None and zid not in S.zones:
        raise Error(404, f"zone {zid} not found")
    name = str(body.get("name", S.zone_names.get(zid, ""))).strip()
    pop = body.get("population", S.zones.get(zid))
    if not name:
        raise _invalid("name", "String should have at least 1 character")
    if not isinstance(pop, int) or pop <= 0:
        raise _invalid("population", "Input should be greater than 0")
    if any(n == name and z != zid for z, n in S.zone_names.items()):
        raise Error(409, "another zone already has that name")
    if zid is None:
        zid = max(S.zones, default=0) + 1
    S.zones[zid], S.zone_names[zid] = pop, name
    return zone_view(zid)


def user_view(uid):
    n, t, p, z = S.users[uid]
    return {"user_id": uid, "name": n, "user_type": t, "priority": p, "zone_id": z, "zone_name": S.zone_names[z]}


def user_save(uid, body):
    if uid is not None and uid not in S.users:
        raise Error(404, f"requester {uid} not found")
    old = S.users.get(uid, ("", None, None, None))
    name = str(body.get("name", old[0])).strip()
    utype = body.get("user_type", old[1])
    prio = body.get("priority", old[2])
    zone = body.get("zone_id", old[3])
    if not name:
        raise _invalid("name", "String should have at least 1 character")
    if utype not in USER_TYPES:
        raise _invalid("user_type", f"Input should be one of {', '.join(USER_TYPES)}")
    if not isinstance(prio, int) or not 1 <= prio <= 5:
        raise _invalid("priority", "Input should be between 1 and 5")
    if zone not in S.zones:
        raise Error(404, f"zone {zone} not found")
    if uid is None:
        uid = max(S.users, default=0) + 1
    S.users[uid] = (name, utype, prio, zone)
    return user_view(uid)


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "*")
        self.end_headers()
        self.wfile.write(data)

    def _handle(self, method):
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        try:
            with LOCK:
                result = route(method, url.path, parse_qs(url.query), body, self.headers.get("X-Admin-Token"))
            created = method == "POST" and url.path in ("/requests", "/zones", "/users")
            self._send(201 if created else 200, result)
        except Error as exc:
            self._send(exc.status, {"detail": exc.detail})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PATCH(self):
        self._handle("PATCH")

    def do_DELETE(self):
        self._handle("DELETE")

    def do_OPTIONS(self):
        self._send(200, {})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default=os.environ.get("SCHEDULER_BIN", "scheduler/build/jalrakshak-scheduler"))
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    config.SCHEDULER_BIN = args.bin
    print(f"Demo API on http://localhost:{args.port} (scheduler: {args.bin})")
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()
