from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import service
from .db import get_conn
from .errors import ConflictError, NotFoundError
from .scheduler_client import SchedulerError, SchedulerUnavailable
from .schemas import CommitBody, RequestCreate, SimulateBody

app = FastAPI(title="JalRakshak API", version="0.1.0")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(NotFoundError)
def _not_found(_, exc):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(ConflictError)
def _conflict(_, exc):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(SchedulerError)
def _bad_scheduler_input(_, exc):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(SchedulerUnavailable)
def _scheduler_down(_, exc):
    return JSONResponse(status_code=500, content={"detail": str(exc)})


@app.get("/health")
def health(conn=Depends(get_conn)):
    with conn.cursor() as cur:
        cur.execute("SELECT 1 AS ok")
        cur.fetchone()
    return {"status": "ok"}


@app.get("/resources")
def resources(conn=Depends(get_conn)):
    return service.list_reservoirs(conn)


@app.get("/requests")
def requests_list(status: Optional[str] = None, conn=Depends(get_conn)):
    try:
        return service.list_requests(conn, status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/requests", status_code=201)
def requests_create(body: RequestCreate, conn=Depends(get_conn)):
    return service.create_request(conn, body.user_id, body.reservoir_id, body.requested_litres,
                                  body.urgency, body.deadline_in_minutes)


@app.post("/allocate/simulate")
def allocate_simulate(body: SimulateBody, conn=Depends(get_conn)):
    return service.simulate(conn, body.reservoir_id, body.algorithms)


@app.post("/allocate/commit")
def allocate_commit(body: CommitBody, conn=Depends(get_conn)):
    return service.commit_allocation(conn, body.reservoir_id, body.algorithm)


@app.get("/runs")
def runs(conn=Depends(get_conn)):
    return service.list_runs(conn)


@app.get("/predictions")
def predictions(zone_id: Optional[int] = None, conn=Depends(get_conn)):
    return service.list_predictions(conn, zone_id)


@app.get("/alerts")
def alerts(conn=Depends(get_conn)):
    return service.list_alerts(conn)
