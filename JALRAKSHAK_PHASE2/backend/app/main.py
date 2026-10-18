from contextlib import asynccontextmanager
from typing import Optional

import psycopg2
from fastapi import Depends, FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from . import (admin_service, auth, config, mfa, migrations, network, policy, portal, privacy, report, security,
               service, telemetry)
from . import db
from .db import get_conn
from .errors import (ConflictError, DatabaseUnavailable, Forbidden, InvalidInput, NotFoundError, NotSignedIn,
                     TooManyAttempts)
from .scheduler_client import SchedulerError, SchedulerUnavailable
from .schemas import (AccountCreate, AccountUpdate, ApproveBody, ChangePasswordBody, CommitBody, CsvBody, DecisionBody,
                      DeviceActive, DeviceCreate, IncidentCreate, IncidentUpdate, IssueCreate, IssueUpdate,
                      LanguageBody, LoginBody, MfaCode, MfaLogin, MyRequestCreate, PolicyBody, PrivacyHandleBody,
                      PrivacyRequestBody, ProfileUpdate, ReadingsBody, RegisterBody, RejectBody, RequestCreate,
                      ReservoirCreate, ReservoirNetwork, ReservoirUpdate, ResetBody, ShadowBody, SimulateBody,
                      TankerCreate, TankerUpdate, TimetableBody, UserCreate, UserUpdate, ZoneCreate, ZoneNetwork,
                      ZoneUpdate)

@asynccontextmanager
async def lifespan(_app):
    """On start-up, check the database and upgrade it if it was made by an older version.
    Never stops the API from starting: if the database is not ready yet, the first request retries."""
    try:
        conn = db.connect(owner=True)
        try:
            db.ensure_schema(conn)
            missing = migrations.check(conn)
            print("JalRakshak: database " + ("ready" if not missing else "is missing " + ", ".join(missing)), flush=True)
            removed = privacy.retention(conn)
            if any(removed.values()):
                print(f"JalRakshak: data retention removed {removed}", flush=True)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - report, keep running
        print(f"JalRakshak: database not ready at start-up ({exc}). Will retry on the first request.", flush=True)
    yield


app = FastAPI(title="JalRakshak API", version="2.0.0", lifespan=lifespan,
              description="Water allocation as OS scheduling. Sign in with POST /auth/login, then send "
                          "`Authorization: Bearer <token>` with every request.")

app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    bucket, limit = security.rate_bucket(request.url.path)
    if bucket and request.method != "OPTIONS" and not security.limiter.allow(
            (security.client_address(request), bucket), limit):
        return JSONResponse(status_code=429, content={"detail": "too many requests; wait a minute and try again"},
                            headers={"Retry-After": "60"})
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cache-Control", "no-store")  # API answers can contain personal data
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
    if request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response


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


@app.exception_handler(NotSignedIn)
def _not_signed_in(_, exc):
    return JSONResponse(status_code=401, content={"detail": str(exc)}, headers={"WWW-Authenticate": "Bearer"})


@app.exception_handler(Forbidden)
def _forbidden(_, exc):
    return _error(403, exc)


@app.exception_handler(TooManyAttempts)
def _too_many(_, exc):
    return _error(429, exc)


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


# ---------------------------------------------------------------- who is calling?
def _bearer(authorization: Optional[str]):
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def current_account(authorization: Optional[str] = Header(default=None), conn=Depends(get_conn)):
    """Any signed-in, active account."""
    return auth.account_for_token(conn, _bearer(authorization))


def admin_account(me=Depends(current_account)):
    if me["role"] != "admin":
        raise Forbidden("this needs an administrator account")
    if config.REQUIRE_ADMIN_MFA and not me.get("mfa_enabled"):
        raise Forbidden("set up two-factor sign-in (Profile) before using the admin panel")
    return me


def requester_account(me=Depends(current_account)):
    if me["role"] != "requester" or not me.get("user_id"):
        raise Forbidden("this is for accounts that request water for an organisation")
    return me


# ---------------------------------------------------------------- public
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


@app.get("/zones")
def zones(conn=Depends(get_conn)):
    """Zones (public: the sign-up form needs them)."""
    return service.list_zones(conn)


@app.post("/auth/login")
def login(body: LoginBody, conn=Depends(get_conn)):
    """Returns a session token. Send it as `Authorization: Bearer <token>`."""
    return auth.login(conn, body.email, body.password)


@app.post("/auth/register", status_code=201)
def register(body: RegisterBody, conn=Depends(get_conn)):
    """A new organisation signs up; an administrator must approve it before it can sign in.
    `consent` must be true (the person accepted the privacy notice, GET /privacy-notice)."""
    return auth.register(conn, body.full_name, body.email, body.password, body.phone, body.organisation_name,
                         body.organisation_type, body.zone_id, consent=body.consent)


@app.post("/auth/mfa")
def login_mfa(body: MfaLogin, conn=Depends(get_conn)):
    """Second sign-in step when two-factor sign-in is on: the challenge from /auth/login and the 6-digit code."""
    return auth.complete_mfa(conn, body.challenge, body.code)


@app.get("/privacy-notice")
def privacy_notice():
    return privacy.notice()


@app.post("/telemetry/readings")
def telemetry_readings(body: ReadingsBody, x_device_key: Optional[str] = Header(default=None), conn=Depends(get_conn)):
    """For sensors / SCADA gateways. Authenticated with the device key (header X-Device-Key), not a user session."""
    return telemetry.ingest(conn, x_device_key, [r.model_dump() for r in body.readings])


# ---------------------------------------------------------------- any signed-in account
@app.get("/auth/me")
def me_info(me=Depends(current_account)):
    return auth.public_account(me)


@app.post("/auth/logout")
def logout(authorization: Optional[str] = Header(default=None), me=Depends(current_account), conn=Depends(get_conn)):
    return auth.logout(conn, _bearer(authorization), me)


@app.post("/auth/change-password")
def change_password(body: ChangePasswordBody, authorization: Optional[str] = Header(default=None),
                    me=Depends(current_account), conn=Depends(get_conn)):
    """Also signs out every other device."""
    return auth.change_password(conn, me, body.current_password, body.new_password, keep_token=_bearer(authorization))


@app.get("/status")
def status(reservoir_id: int = 1, me=Depends(current_account), conn=Depends(get_conn)):
    return service.get_status_with_forecast(conn, reservoir_id)


# ---------------------------------------------------------------- user panel (/me)
@app.get("/me/overview")
def my_overview(me=Depends(requester_account), conn=Depends(get_conn)):
    return portal.overview(conn, me)


@app.get("/me/requests")
def my_requests(me=Depends(requester_account), conn=Depends(get_conn)):
    return portal.my_requests(conn, me)


@app.post("/me/requests", status_code=201)
def my_request_create(body: MyRequestCreate, me=Depends(requester_account), conn=Depends(get_conn)):
    return portal.create_request(conn, me, body.requested_litres, body.urgency, body.deadline_in_minutes, body.note)


@app.post("/me/requests/{request_id}/cancel")
def my_request_cancel(request_id: int, me=Depends(requester_account), conn=Depends(get_conn)):
    return portal.cancel_request(conn, me, request_id)


@app.get("/me/notifications")
def my_notifications(me=Depends(current_account), conn=Depends(get_conn)):
    return {"unread": portal.unread_count(conn, me), "items": portal.notifications(conn, me)}


@app.post("/me/notifications/read-all")
def my_notifications_read_all(me=Depends(current_account), conn=Depends(get_conn)):
    return portal.mark_read(conn, me)


@app.post("/me/notifications/{notification_id}/read")
def my_notification_read(notification_id: int, me=Depends(current_account), conn=Depends(get_conn)):
    return portal.mark_read(conn, me, notification_id)


@app.get("/me/issues")
def my_issues(me=Depends(requester_account), conn=Depends(get_conn)):
    return portal.my_issues(conn, me)


@app.post("/me/issues", status_code=201)
def my_issue_create(body: IssueCreate, me=Depends(requester_account), conn=Depends(get_conn)):
    return portal.create_issue(conn, me, body.category, body.title, body.description, body.location)


@app.patch("/me/profile")
def my_profile(body: ProfileUpdate, me=Depends(current_account), conn=Depends(get_conn)):
    return portal.update_profile(conn, me, body.full_name, body.phone)


# ---------------------------------------------------------------- admin: operations
@app.get("/resources")
def resources(me=Depends(admin_account), conn=Depends(get_conn)):
    return service.list_reservoirs(conn)


@app.get("/users")
def users(me=Depends(admin_account), conn=Depends(get_conn)):
    """Organisations that request water."""
    return service.list_users(conn)


@app.get("/requests")
def requests_list(status: Optional[str] = None, me=Depends(admin_account), conn=Depends(get_conn)):
    return service.list_requests(conn, status)


@app.post("/requests", status_code=201)
def requests_create(body: RequestCreate, me=Depends(admin_account), conn=Depends(get_conn)):
    """An administrator records a request on behalf of an organisation."""
    return service.create_request(conn, body.user_id, body.reservoir_id, body.requested_litres,
                                  body.urgency, body.deadline_in_minutes, actor=me)


@app.post("/allocate/simulate")
def allocate_simulate(body: SimulateBody, me=Depends(admin_account), conn=Depends(get_conn)):
    return service.simulate(conn, body.reservoir_id, body.algorithms)


@app.post("/allocate/commit")
def allocate_commit(body: CommitBody, me=Depends(admin_account), conn=Depends(get_conn)):
    """Apply a plan. `overrides` ({request_id: litres}) changes the computed plan; `reason` is then required."""
    return service.commit_allocation(conn, body.reservoir_id, body.algorithm, actor=me,
                                     overrides=body.overrides, reason=body.reason)


@app.post("/allocate/shadow")
def allocate_shadow(body: ShadowBody, me=Depends(admin_account), conn=Depends(get_conn)):
    """Pilot mode: record the recommended plan without changing water or requests."""
    return service.shadow_plan(conn, body.reservoir_id, body.algorithm, actor=me, note=body.note)


@app.get("/runs")
def runs(me=Depends(admin_account), conn=Depends(get_conn)):
    return service.list_runs(conn)


@app.get("/runs/{run_id}")
def run_detail(run_id: int, me=Depends(admin_account), conn=Depends(get_conn)):
    return service.get_run(conn, run_id)


@app.get("/runs/{run_id}/report")
def run_report(run_id: int, format: str = "html", me=Depends(admin_account), conn=Depends(get_conn)):
    """Decision record of a plan for review meetings, audits and RTI replies: html (printable) or csv."""
    if format == "csv":
        return PlainTextResponse(report.run_report_csv(conn, run_id), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="plan-{run_id}-record.csv"'})
    if format != "html":
        raise InvalidInput("format must be html or csv")
    return HTMLResponse(report.run_report_html(conn, run_id), headers={
        "Content-Disposition": f'attachment; filename="plan-{run_id}-record.html"',
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'"})


@app.get("/predictions")
def predictions(zone_id: Optional[int] = None, me=Depends(admin_account), conn=Depends(get_conn)):
    return service.list_predictions(conn, zone_id)


@app.get("/alerts")
def alerts(include_resolved: bool = False, me=Depends(admin_account), conn=Depends(get_conn)):
    """Stored alerts (leaks, rising demand) plus live ones (low water, forecast above supply)."""
    return service.list_alerts(conn, include_resolved)


@app.post("/alerts/{alert_id}/resolve")
def alert_resolve(alert_id: int, me=Depends(admin_account), conn=Depends(get_conn)):
    return service.resolve_alert(conn, alert_id, actor=me)


@app.get("/forecast")
def forecast(history_days: int = 60, me=Depends(admin_account), conn=Depends(get_conn)):
    """Recent daily use per zone, next week's forecast and how accurate each model was."""
    if not 1 <= history_days <= 730:
        raise InvalidInput("history_days must be between 1 and 730")
    return service.get_forecast(conn, history_days)


@app.post("/demo/reset")
def demo_reset(body: ResetBody, me=Depends(admin_account), conn=Depends(get_conn)):
    """Demo helper: clears allocation history, restores the 8 sample requests and refills the reservoir."""
    return service.reset_demo(conn, body.reservoir_id, body.available_litres, actor=me)


# ---------------------------------------------------------------- admin: settings
@app.patch("/resources/{reservoir_id}")
def reservoir_update(reservoir_id: int, body: ReservoirUpdate, me=Depends(admin_account), conn=Depends(get_conn)):
    """Change name, capacity and/or water level. Returns the new status."""
    return service.update_reservoir(conn, reservoir_id, body.name, body.capacity_litres, body.available_litres, actor=me)


@app.post("/zones", status_code=201)
def zone_create(body: ZoneCreate, me=Depends(admin_account), conn=Depends(get_conn)):
    return service.create_zone(conn, body.name, body.population, actor=me)


@app.patch("/zones/{zone_id}")
def zone_update(zone_id: int, body: ZoneUpdate, me=Depends(admin_account), conn=Depends(get_conn)):
    return service.update_zone(conn, zone_id, body.name, body.population, actor=me)


@app.delete("/zones/{zone_id}")
def zone_delete(zone_id: int, me=Depends(admin_account), conn=Depends(get_conn)):
    return service.delete_zone(conn, zone_id, actor=me)


@app.post("/users", status_code=201)
def user_create(body: UserCreate, me=Depends(admin_account), conn=Depends(get_conn)):
    """Without a priority, the organisation gets the priority of its class in the active policy."""
    return service.create_user(conn, body.name, body.user_type, body.priority, body.zone_id, actor=me)


@app.patch("/users/{user_id}")
def user_update(user_id: int, body: UserUpdate, me=Depends(admin_account), conn=Depends(get_conn)):
    return service.update_user(conn, user_id, body.name, body.user_type, body.priority, body.zone_id, actor=me,
                               reset_priority=body.reset_priority)


@app.delete("/users/{user_id}")
def user_delete(user_id: int, me=Depends(admin_account), conn=Depends(get_conn)):
    return service.delete_user(conn, user_id, actor=me)


# ---------------------------------------------------------------- admin: people, issues, audit
@app.get("/admin/summary")
def admin_summary(me=Depends(admin_account), conn=Depends(get_conn)):
    return dict(admin_service.summary(conn), unread_notifications=portal.unread_count(conn, me))


@app.get("/admin/accounts")
def admin_accounts(status: Optional[str] = None, role: Optional[str] = None,
                   me=Depends(admin_account), conn=Depends(get_conn)):
    return admin_service.list_accounts(conn, status, role)


@app.post("/admin/accounts", status_code=201)
def admin_account_create(body: AccountCreate, me=Depends(admin_account), conn=Depends(get_conn)):
    """Creates an active account and returns a one-time temporary password (changed at first sign-in)."""
    return admin_service.create_account(conn, me, body.email, body.full_name, body.role, body.user_id, body.phone)


@app.patch("/admin/accounts/{account_id}")
def admin_account_update(account_id: int, body: AccountUpdate, me=Depends(admin_account), conn=Depends(get_conn)):
    return admin_service.update_account(conn, me, account_id, body.full_name, body.phone, body.role, body.status,
                                        body.user_id)


@app.post("/admin/accounts/{account_id}/approve")
def admin_account_approve(account_id: int, body: ApproveBody, me=Depends(admin_account), conn=Depends(get_conn)):
    return admin_service.approve(conn, me, account_id, body.priority, body.user_id)


@app.post("/admin/accounts/{account_id}/reject")
def admin_account_reject(account_id: int, body: RejectBody, me=Depends(admin_account), conn=Depends(get_conn)):
    return admin_service.reject(conn, me, account_id, body.reason)


@app.post("/admin/accounts/{account_id}/reset-password")
def admin_account_reset_password(account_id: int, me=Depends(admin_account), conn=Depends(get_conn)):
    return admin_service.reset_password(conn, me, account_id)


@app.get("/admin/issues")
def admin_issues(status: Optional[str] = None, me=Depends(admin_account), conn=Depends(get_conn)):
    return admin_service.list_issues(conn, status)


@app.patch("/admin/issues/{issue_id}")
def admin_issue_update(issue_id: int, body: IssueUpdate, me=Depends(admin_account), conn=Depends(get_conn)):
    return admin_service.update_issue(conn, me, issue_id, body.status, body.response)


@app.get("/admin/audit")
def admin_audit(limit: int = 200, action: Optional[str] = None, account_id: Optional[int] = None,
                me=Depends(admin_account), conn=Depends(get_conn)):
    return admin_service.list_audit(conn, limit, action, account_id)



# ================================================================ v2.0
# ---------------------------------------------------------------- any signed-in account
@app.post("/me/mfa/setup")
def my_mfa_setup(me=Depends(current_account), conn=Depends(get_conn)):
    """Start two-factor set-up: returns the key / otpauth link for an authenticator app."""
    return mfa.begin(conn, me)


@app.post("/me/mfa/confirm")
def my_mfa_confirm(body: MfaCode, me=Depends(current_account), conn=Depends(get_conn)):
    return mfa.confirm(conn, me, body.code)


@app.post("/me/mfa/disable")
def my_mfa_disable(body: MfaCode, me=Depends(current_account), conn=Depends(get_conn)):
    return mfa.disable(conn, me, body.code)


@app.post("/me/consent")
def my_consent(me=Depends(current_account), conn=Depends(get_conn)):
    """Accept the current privacy notice."""
    return privacy.give_consent(conn, me)


@app.get("/me/data-export")
def my_data_export(me=Depends(current_account), conn=Depends(get_conn)):
    """Everything stored about me (DPDP Act: right to access)."""
    return privacy.export(conn, me)


@app.get("/me/privacy-requests")
def my_privacy_requests(me=Depends(current_account), conn=Depends(get_conn)):
    return privacy.my_requests(conn, me)


@app.post("/me/privacy-requests", status_code=201)
def my_privacy_request(body: PrivacyRequestBody, me=Depends(current_account), conn=Depends(get_conn)):
    return privacy.create_request(conn, me, body.kind, body.details)


@app.patch("/me/language")
def my_language(body: LanguageBody, me=Depends(current_account), conn=Depends(get_conn)):
    return portal.set_language(conn, me, body.language)


@app.get("/me/supply")
def my_supply(me=Depends(requester_account), conn=Depends(get_conn)):
    """My zone's piped-supply hours from the latest published timetable."""
    return {"supply": network.zone_supply(conn, me["zone_id"])}


@app.get("/policy/active")
def policy_active(me=Depends(current_account), conn=Depends(get_conn)):
    """The approved water policy (visible to everyone signed in: transparency)."""
    return policy.get_active(conn)


# ---------------------------------------------------------------- admin: governance
@app.get("/admin/policies")
def admin_policies(me=Depends(admin_account), conn=Depends(get_conn)):
    return policy.list_policies(conn)


@app.post("/admin/policies", status_code=201)
def admin_policy_draft(body: PolicyBody, me=Depends(admin_account), conn=Depends(get_conn)):
    """New draft: the active policy with the given changes."""
    return policy.create_draft(conn, me, body.model_dump(exclude_none=True))


@app.patch("/admin/policies/{policy_id}")
def admin_policy_edit(policy_id: int, body: PolicyBody, me=Depends(admin_account), conn=Depends(get_conn)):
    return policy.update_draft(conn, me, policy_id, body.model_dump(exclude_none=True))


@app.post("/admin/policies/{policy_id}/submit")
def admin_policy_submit(policy_id: int, me=Depends(admin_account), conn=Depends(get_conn)):
    return policy.submit(conn, me, policy_id)


@app.post("/admin/policies/{policy_id}/decide")
def admin_policy_decide(policy_id: int, body: DecisionBody, me=Depends(admin_account), conn=Depends(get_conn)):
    """Approve or reject. Maker-checker: must be a different administrator than the proposer."""
    return policy.decide(conn, me, policy_id, body.approve, body.note)


@app.get("/admin/kpis")
def admin_kpis(days: int = 30, me=Depends(admin_account), conn=Depends(get_conn)):
    if not 1 <= days <= 3650:
        raise InvalidInput("days must be between 1 and 3650")
    return report.kpis(conn, days)


# ---------------------------------------------------------------- admin: network, timetable, tankers
@app.post("/resources", status_code=201)
def reservoir_create(body: ReservoirCreate, me=Depends(admin_account), conn=Depends(get_conn)):
    return network.create_reservoir(conn, me, body.name, body.capacity_litres, body.available_litres,
                                    body.daily_release_litres, body.pump_capacity_lph)


@app.patch("/resources/{reservoir_id}/network")
def reservoir_network(reservoir_id: int, body: ReservoirNetwork, me=Depends(admin_account), conn=Depends(get_conn)):
    return network.update_reservoir_network(conn, me, reservoir_id, body.daily_release_litres, body.pump_capacity_lph)


@app.patch("/zones/{zone_id}/network")
def zone_network(zone_id: int, body: ZoneNetwork, me=Depends(admin_account), conn=Depends(get_conn)):
    return network.update_zone_network(conn, me, zone_id, body.reservoir_id, body.inlet_capacity_lph,
                                       body.supply_priority, body.boundary, body.clear_boundary)


@app.get("/map/zones")
def map_zones(me=Depends(current_account), conn=Depends(get_conn)):
    """Zone boundaries (GeoJSON) with supply and alert status, for the map."""
    return network.zone_map(conn)


@app.get("/timetables")
def timetables(me=Depends(admin_account), conn=Depends(get_conn)):
    return network.list_timetables(conn)


@app.post("/timetables", status_code=201)
def timetable_build(body: TimetableBody, me=Depends(admin_account), conn=Depends(get_conn)):
    """Compute a zone-wise supply timetable as a draft."""
    return network.build_draft(conn, me, body.for_date, body.demand_mode, body.releases, body.note)


@app.get("/timetables/{timetable_id}")
def timetable_detail(timetable_id: int, me=Depends(admin_account), conn=Depends(get_conn)):
    return network.get(conn, timetable_id)


@app.post("/timetables/{timetable_id}/publish")
def timetable_publish(timetable_id: int, me=Depends(admin_account), conn=Depends(get_conn)):
    """Publish (replaces an earlier published timetable for the same day) and tell every organisation."""
    return network.publish(conn, me, timetable_id)


@app.post("/timetables/{timetable_id}/tankers")
def timetable_tankers(timetable_id: int, me=Depends(admin_account), conn=Depends(get_conn)):
    """Plan tanker trips for zones below the policy's tanker trigger in this timetable."""
    return network.plan_tankers(conn, me, timetable_id)


@app.get("/tankers")
def tankers(from_date: Optional[str] = None, me=Depends(admin_account), conn=Depends(get_conn)):
    return network.list_trips(conn, from_date)


@app.post("/tankers", status_code=201)
def tanker_create(body: TankerCreate, me=Depends(admin_account), conn=Depends(get_conn)):
    return network.create_trip(conn, me, body.zone_id, body.litres, body.planned_for, body.vehicle, body.notes, body.loads)


@app.patch("/tankers/{trip_id}")
def tanker_update(trip_id: int, body: TankerUpdate, me=Depends(admin_account), conn=Depends(get_conn)):
    return network.update_trip(conn, me, trip_id, body.status, body.vehicle, body.notes)


# ---------------------------------------------------------------- admin: field data
@app.get("/admin/devices")
def admin_devices(me=Depends(admin_account), conn=Depends(get_conn)):
    return telemetry.list_devices(conn)


@app.post("/admin/devices", status_code=201)
def admin_device_create(body: DeviceCreate, me=Depends(admin_account), conn=Depends(get_conn)):
    """Registers a sensor and returns its key ONCE (configure it in the device / SCADA gateway)."""
    return telemetry.create_device(conn, me, body.name, body.kind, body.reservoir_id, body.zone_id)


@app.patch("/admin/devices/{device_id}")
def admin_device_active(device_id: int, body: DeviceActive, me=Depends(admin_account), conn=Depends(get_conn)):
    return telemetry.set_device_active(conn, me, device_id, body.active)


@app.get("/admin/devices/{device_id}/readings")
def admin_device_readings(device_id: int, me=Depends(admin_account), conn=Depends(get_conn)):
    return telemetry.recent_readings(conn, device_id)


@app.post("/admin/import/consumption")
def admin_import_consumption(body: CsvBody, me=Depends(admin_account), conn=Depends(get_conn)):
    """Daily metered consumption CSV (zone_id or zone, date, litres)."""
    return telemetry.import_consumption_csv(conn, me, body.csv)


@app.get("/admin/data-sources")
def admin_data_sources(me=Depends(admin_account), conn=Depends(get_conn)):
    return telemetry.data_summary(conn)


# ---------------------------------------------------------------- admin: security, privacy, operations
@app.post("/admin/accounts/{account_id}/reset-mfa")
def admin_reset_mfa(account_id: int, me=Depends(admin_account), conn=Depends(get_conn)):
    """For a lost phone: turns two-factor sign-in off for that account (they must set it up again)."""
    return admin_service.reset_mfa(conn, me, account_id)


@app.get("/admin/privacy-requests")
def admin_privacy_requests(me=Depends(admin_account), conn=Depends(get_conn)):
    return privacy.list_requests(conn)


@app.post("/admin/privacy-requests/{request_id}")
def admin_privacy_handle(request_id: int, body: PrivacyHandleBody, me=Depends(admin_account), conn=Depends(get_conn)):
    """Complete (an erasure request anonymises the account) or reject, with a response to the person."""
    return privacy.handle_request(conn, me, request_id, body.status, body.response)


@app.get("/admin/incidents")
def admin_incidents(me=Depends(admin_account), conn=Depends(get_conn)):
    return security.list_incidents(conn)


@app.post("/admin/incidents", status_code=201)
def admin_incident_create(body: IncidentCreate, me=Depends(admin_account), conn=Depends(get_conn)):
    return security.create_incident(conn, me, body.title, body.description, body.severity, body.personal_data_affected)


@app.patch("/admin/incidents/{incident_id}")
def admin_incident_update(incident_id: int, body: IncidentUpdate, me=Depends(admin_account), conn=Depends(get_conn)):
    return security.update_incident(conn, me, incident_id, body.status, body.actions, body.reported_cert_in,
                                    body.reported_board)


@app.get("/admin/ops")
def admin_ops(me=Depends(admin_account), conn=Depends(get_conn)):
    """Operations status: schema version, database size, backups, sensors, 2FA coverage, open incidents."""
    return security.ops_status(conn)


@app.post("/admin/maintenance/retention")
def admin_retention(me=Depends(admin_account), conn=Depends(get_conn)):
    return privacy.retention(conn, me)
