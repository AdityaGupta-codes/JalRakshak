import hmac
from contextlib import asynccontextmanager
from typing import Optional

import psycopg2
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import config, migrations, service
from . import db
from .db import get_conn
from .errors import ConflictError, DatabaseUnavailable, InvalidInput, NotFoundError
from .scheduler_client import SchedulerError, SchedulerUnavailable
from .schemas import (CommitBody, RequestCreate, ReservoirUpdate, ResetBody, SimulateBody, UserCreate,
                      UserUpdate, ZoneCreate, ZoneUpdate)

@asynccontextmanager
async def lifespan(_app):
    """On start-up, check the database and upgrade it if it was made by an older version.
    Never stops the API from starting: if the database is not ready yet, the first request retries."""
    try:
        conn = db.connect()
        try:
            db.ensure_schema(conn)
            missing = migrations.check(conn)
            print("JalRakshak: database " + ("ready" if not missing else "is missing " + ", ".join(missing)), flush=True)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - report, keep running
        print(f"JalRakshak: database not ready at start-up ({exc}). Will retry on the first request.", flush=True)
    yield


app = FastAPI(title="JalRakshak API", version="0.5.0", lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _error(status, exc):
    return JSONResponse(status_code=status, content={"detail": str(exc)})


@app.exception_handler(NotFoundError)
def _not_found(_, exc):
    return _error(404, exc)


@app.exception_handler(ConflictError)
def _conflict(_, exc):
    return _error(409, exc)


@app.exception_handler(InvalidInput)
def _invalid(_, exc):
    return _error(400, exc)


@app.exception_handler(SchedulerError)
def _bad_scheduler_input(_, exc):
    return _error(400, exc)


@app.exception_handler(SchedulerUnavailable)
def _scheduler_down(_, exc):
    return _error(500, exc)


@app.exception_handler(DatabaseUnavailable)
def _db_down(_, exc):
    return _error(503, exc)


@app.exception_handler(psycopg2.OperationalError)
def _db_lost(_, exc):  # connection dropped in the middle of a request
    return _error(503, f"database connection lost: {str(exc).strip()}")


@app.exception_handler(psycopg2.Error)
def _db_error(_, exc):
    code = getattr(exc, "pgcode", None)
    first = (getattr(exc, "pgerror", None) or str(exc) or "").strip().splitlines()
    detail = f"database error {code or ''}: {first[0] if first else 'unknown'}".replace("  ", " ")
    if code in ("42P01", "42883", "42703"):  # missing table / function / column: database from an older version
        detail += ". The database looks older than the code. " + migrations.RESET_HINT
    return JSONResponse(status_code=500, content={"detail": detail, "code": code})


def require_admin(x_admin_token: Optional[str] = Header(default=None)):
    """Endpoints that change data need the admin token, but only if JAL_ADMIN_TOKEN is set (see config.py)."""
    if config.ADMIN_TOKEN and not hmac.compare_digest(x_admin_token or "", config.ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="This action needs the admin token. Unlock the dashboard first.")


admin = [Depends(require_admin)]


@app.get("/auth")
def auth_info():
    """Tells the dashboard whether changes need a token."""
    return {"token_required": bool(config.ADMIN_TOKEN)}


@app.post("/auth/check", dependencies=admin)
def auth_check():
    """Returns 200 if the X-Admin-Token header is right (or no token is needed), 401 otherwise."""
    return {"ok": True}


@app.get("/health")
def health(conn=Depends(get_conn)):
    """Proves the API can reach PostgreSQL and that the database has every table the code needs
    (503 with a reason and the fix if not)."""
    missing = migrations.check(conn)
    if missing:
        return JSONResponse(status_code=503, content={
            "detail": "The database is missing " + ", ".join(missing) + ". " + migrations.RESET_HINT,
            "missing": missing})
    return {"status": "ok", "database": "connected", "schema": "up to date"}


@app.get("/status")
def status(reservoir_id: int = 1, conn=Depends(get_conn)):
    return service.get_status_with_forecast(conn, reservoir_id)


@app.get("/resources")
def resources(conn=Depends(get_conn)):
    return service.list_reservoirs(conn)


@app.get("/users")
def users(conn=Depends(get_conn)):
    return service.list_users(conn)


@app.get("/requests")
def requests_list(status: Optional[str] = None, conn=Depends(get_conn)):
    return service.list_requests(conn, status)


@app.post("/requests", status_code=201, dependencies=admin)
def requests_create(body: RequestCreate, conn=Depends(get_conn)):
    return service.create_request(conn, body.user_id, body.reservoir_id, body.requested_litres,
                                  body.urgency, body.deadline_in_minutes)


@app.post("/allocate/simulate")
def allocate_simulate(body: SimulateBody, conn=Depends(get_conn)):
    return service.simulate(conn, body.reservoir_id, body.algorithms)


@app.post("/allocate/commit", dependencies=admin)
def allocate_commit(body: CommitBody, conn=Depends(get_conn)):
    return service.commit_allocation(conn, body.reservoir_id, body.algorithm)


@app.get("/runs")
def runs(conn=Depends(get_conn)):
    return service.list_runs(conn)


@app.get("/runs/{run_id}")
def run_detail(run_id: int, conn=Depends(get_conn)):
    return service.get_run(conn, run_id)


@app.get("/predictions")
def predictions(zone_id: Optional[int] = None, conn=Depends(get_conn)):
    return service.list_predictions(conn, zone_id)


@app.get("/alerts")
def alerts(include_resolved: bool = False, conn=Depends(get_conn)):
    """Stored alerts (leaks, rising demand) plus live ones (low water, forecast above supply)."""
    return service.list_alerts(conn, include_resolved)


@app.post("/alerts/{alert_id}/resolve", dependencies=admin)
def alert_resolve(alert_id: int, conn=Depends(get_conn)):
    return service.resolve_alert(conn, alert_id)


@app.get("/forecast")
def forecast(history_days: int = 60, conn=Depends(get_conn)):
    """Recent daily use per zone, next week's forecast and how accurate each model was."""
    if not 1 <= history_days <= 730:
        raise InvalidInput("history_days must be between 1 and 730")
    return service.get_forecast(conn, history_days)


@app.post("/demo/reset", dependencies=admin)
def demo_reset(body: ResetBody, conn=Depends(get_conn)):
    """Demo helper: clears allocation history, makes all requests pending again and refills the reservoir."""
    return service.reset_demo(conn, body.reservoir_id, body.available_litres)


# ---------- settings: reservoir, zones and requesters (all changes need the admin token, if one is set) ----------

@app.patch("/resources/{reservoir_id}", dependencies=admin)
def reservoir_update(reservoir_id: int, body: ReservoirUpdate, conn=Depends(get_conn)):
    """Change name, capacity and/or water level. Returns the new status."""
    return service.update_reservoir(conn, reservoir_id, body.name, body.capacity_litres, body.available_litres)


@app.get("/zones")
def zones(conn=Depends(get_conn)):
    return service.list_zones(conn)


@app.post("/zones", status_code=201, dependencies=admin)
def zone_create(body: ZoneCreate, conn=Depends(get_conn)):
    return service.create_zone(conn, body.name, body.population)


@app.patch("/zones/{zone_id}", dependencies=admin)
def zone_update(zone_id: int, body: ZoneUpdate, conn=Depends(get_conn)):
    return service.update_zone(conn, zone_id, body.name, body.population)


@app.delete("/zones/{zone_id}", dependencies=admin)
def zone_delete(zone_id: int, conn=Depends(get_conn)):
    return service.delete_zone(conn, zone_id)


@app.post("/users", status_code=201, dependencies=admin)
def user_create(body: UserCreate, conn=Depends(get_conn)):
    return service.create_user(conn, body.name, body.user_type, body.priority, body.zone_id)


@app.patch("/users/{user_id}", dependencies=admin)
def user_update(user_id: int, body: UserUpdate, conn=Depends(get_conn)):
    return service.update_user(conn, user_id, body.name, body.user_type, body.priority, body.zone_id)


@app.delete("/users/{user_id}", dependencies=admin)
def user_delete(user_id: int, conn=Depends(get_conn)):
    return service.delete_user(conn, user_id)
