# JalRakshak Contracts v2.0

v1.0: sign-in with roles; user panel (`/me/...`) and admin panel (`/admin/...`) endpoints; every endpoint is role-checked.

v0.4: settings endpoints (reservoir, zones, requesters) and a chosen refill level for the demo reset.

Changes since v0.2: `deadline_miss_count` metric; backend batch window ("all" by default); predicted demand
from the ML forecast; endpoints `/forecast`, `/alerts/{id}/resolve`, `/auth`, `/auth/check`; optional admin token.

Once all four members sign off, changes need a pull request and everyone's approval.

## 1. C++ scheduler contract (stdin JSON -> stdout JSON)

Run: `./scheduler < input.json > output.json`
All times are **simulation minutes** from t=0 (FastAPI converts timestamps).

### Input

```json
{
  "algorithm": "water_aware",
  "available_litres": 50000,
  "predicted_demand_litres": 70000,
  "config": {
    "flow_rate_lpm": 500,
    "quantum_litres": 2000,
    "min_allocation_ratio": 0.2,
    "guarantee_floor": 0.3,
    "batch_window_minutes": 0,
    "weights": {
      "priority": 0.30, "urgency": 0.25, "scarcity": 0.15,
      "demand_impact": 0.15, "fairness": 0.15
    }
  },
  "requests": [
    {
      "request_id": 3,
      "user_id": 1,
      "user_type": "hospital",
      "zone_id": 2,
      "priority": 5,
      "urgency": 5,
      "requested_litres": 10000,
      "arrival_time": 10,
      "deadline": 60,
      "zone_population": 65000,
      "zone_received_ratio": 0.40
    }
  ]
}
```

- `algorithm`: `fcfs | priority | round_robin | water_aware`
- `zone_received_ratio`: litres this zone received in recent history divided by litres it asked for (0-1). Used for the fairness factor.
- `predicted_demand_litres`: expected demand, used for the scarcity factor. May be `null`; the scheduler then uses the litres still requested by waiting requests.
- `quantum_litres`: round_robin only. `weights` and `guarantee_floor`: water_aware only.
- `min_allocation_ratio` (0-1): a grant smaller than this share of the request is rejected instead (not applied by round_robin, because water already handed out cannot be taken back).
- `batch_window_minutes` (default 0): the scheduler waits this long after the first arrival, so requests that arrive in the window are ranked together. Applies to every algorithm equally. The backend sets it from `BATCH_WINDOW_MINUTES`: `all` (default) = the last pending request's arrival, so every pending request is ranked together, as it is when an operator clicks Apply.
- `predicted_demand_litres`: the backend sends today's forecast for all zones from the selected ML model (`predictions` + `model_evaluations.is_selected`), or `null` when there is none (set `USE_FORECAST=0` to always send `null`).
- Grants are whole 0.01 L, matching the database's `numeric(12,2)`.

### How each algorithm works

All four share one simulation clock and a single pipeline: a grant of L litres occupies it for `L / flow_rate_lpm` minutes. A request's `wait_time` is the minutes from its arrival until its water first starts.

| Algorithm | Rule |
|---|---|
| `fcfs` | Among requests that have arrived, serve the earliest arrival (ties: lowest id). |
| `priority` | Serve the highest priority (5 = hospital ... 1 = park). Non-preemptive: a request already being served is not interrupted. |
| `round_robin` | Queue of requests; each turn gives up to `quantum_litres`, then the request rejoins the back of the queue until it is satisfied or the water runs out. |
| `water_aware` | Score every arrived request (below), serve the best, with a guaranteed minimum share (`guarantee_floor`) for everyone still waiting. |

### Output

```json
{
  "algorithm": "water_aware",
  "allocations": [
    {
      "request_id": 3,
      "allocated_litres": 10000,
      "start_time": 10,
      "finish_time": 30,
      "wait_time": 0,
      "score": 0.87,
      "status": "approved"
    }
  ],
  "remaining_litres": 0,
  "metrics": {
    "avg_wait_time": 12.4,
    "utilization": 1.0,
    "satisfaction_ratio": 0.62,
    "priority_weighted_satisfaction": 0.81,
    "jain_fairness": 0.74,
    "unserved_count": 2,
    "deadline_miss_count": 1
  }
}
```

- `status`: `approved` (full), `partial`, `rejected` (0 litres).
- `score` is `null` for non-water-aware algorithms.
- Invariants (assert in tests): `sum(allocated) <= available_litres`; `allocated <= requested` for every request; no negative values.
- Errors: exit code 1 and `{"error": "message"}` on stdout.

### Metric definitions

| Metric | Formula |
|---|---|
| avg_wait_time | mean(start_time - arrival_time) over served requests |
| utilization | sum(allocated) / available_litres |
| satisfaction_ratio | sum(allocated) / sum(requested) |
| priority_weighted_satisfaction | sum(priority * allocated/requested) / sum(priority) |
| jain_fairness | (sum x)^2 / (n * sum x^2), where x = allocated/requested per request |
| unserved_count | requests with allocated = 0 |
| deadline_miss_count | served requests whose finish_time is after their deadline (deadlines are soft: late requests are still served) |

### Water-aware score (all factors normalised to 0-1)

```
priority      = priority / 5
urgency       = 0.5 * (urgency / 5) + 0.5 * time_pressure
                time_pressure = 1 - clamp((deadline - now) / longest_deadline_window, 0, 1)
scarcity      = scarcity_level * (1 - share_of_remaining_water_this_request_takes)
                scarcity_level = clamp(expected_demand / water_left, 0, 2) / 2
demand_impact = zone_population / largest_zone_population
fairness      = 1 - zone_received_ratio
score = sum(weight_i * factor_i) / sum(weight_i)
```

Note: the scarcity factor in v0.1 was one number shared by all requests, which can never change the order. In v0.2 it is per request: the scarcer the water, the more small requests are favoured, so more users are served.

The highest score is served next. The weights live in config, not in code. All-zero weights are allowed (ties are then broken by arrival).

**Guarantee floor.** Serving the best-scored request in full would leave the last ones with nothing. So each grant is capped at

```
cap = max(floor * requested, water_left - floor * litres_still_requested_by_others)
```

If there is water for every floor, the best-scored requests get the extra; if not, floors are served in score order. `guarantee_floor = 0` is pure score order (favours priority); a high floor gives nearly equal shares (fairest). Tuning result: see `experiments/README.md`.

## 2. REST API v1.0 (FastAPI)

The interactive reference is at http://localhost:8000/docs.

**Signing in.** `POST /auth/login` returns `{"token", "expires_at", "account"}`. Send the token as
`Authorization: Bearer <token>` with every other request. Access levels below: **public** (no token), **any**
(any signed-in account), **user** (role `requester`, linked to an organisation), **admin** (role `admin`).
A wrong role answers 403; a missing, expired or revoked token answers 401.

### Sign-in and shared

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/health` | public | Database reachable and schema complete (503 with what is missing and the fix) |
| GET | `/zones` | public | Zones (used by the sign-up form) |
| POST | `/auth/login` | public | `{email, password}` → session token. 401 wrong email or password (same message for both), 403 pending / rejected / disabled, 429 locked after 5 wrong passwords |
| POST | `/auth/register` | public | `{full_name, email, password, phone?, organisation_name, organisation_type, zone_id}` → account `pending` until an admin approves |
| GET | `/auth/me` | any | The signed-in account (with organisation, priority and zone for users) |
| POST | `/auth/logout` | any | Ends this session |
| POST | `/auth/change-password` | any | `{current_password, new_password}`; signs out every other device |
| GET | `/status` | any | Water level, pending demand, scarcity level, today's forecast |
| GET | `/me/notifications` | any | `{unread, items}` newest first |
| POST | `/me/notifications/{id}/read`, `/me/notifications/read-all` | any | Mark as read |
| PATCH | `/me/profile` | any | `{full_name?, phone?}` |

### User panel

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/me/overview` | user | Organisation, reservoir summary, request counts, litres received, alerts for the zone, unread count |
| GET | `/me/requests` | user | This organisation's requests, each with what it received, in which plan, and when |
| POST | `/me/requests` | user | `{requested_litres, urgency 1-5, deadline_in_minutes, note?}`. 409 when 5 are already waiting or the amount exceeds the reservoir |
| POST | `/me/requests/{id}/cancel` | user | Only own, only while `pending` (404 for other organisations' requests, 409 once decided) |
| GET | `/me/issues` | user | Own reports with status and the department's reply |
| POST | `/me/issues` | user | `{category: leak, no_supply, low_pressure, quality or other, title, description, location?}`; admins are notified |

### Admin panel: operations and settings

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/resources`, `/users`, `/requests?status=` | admin | Reservoirs, organisations, all requests |
| POST | `/requests` | admin | Record a request on behalf of an organisation |
| POST | `/allocate/simulate` | admin | Run algorithm(s) on pending requests, **no commit** |
| POST | `/allocate/commit` | admin | Run one algorithm and commit in one transaction (`FOR UPDATE`); notifies every organisation of its result; audited |
| GET | `/runs`, `/runs/{id}` | admin | Applied plans and their allocations |
| GET | `/predictions`, `/forecast?history_days=60` | admin | Forecasts, recent use, model scores |
| GET | `/alerts` | admin | Stored alerts plus live ones (low water, forecast above supply) |
| POST | `/alerts/{id}/resolve` | admin | Mark a stored alert as handled |
| POST | `/demo/reset` | admin | Clear plans and requests, restore the 8 sample requests, refill to `available_litres` |
| PATCH | `/resources/{id}` | admin | `{name?, capacity_litres?, available_litres?}`; water can never exceed capacity |
| POST/PATCH/DELETE | `/zones`, `/zones/{id}` | admin | Manage zones (a zone with organisations cannot be deleted) |
| POST/PATCH/DELETE | `/users`, `/users/{id}` | admin | Manage organisations (one with requests or sign-ins cannot be deleted) |

### Admin panel: people, issues, audit

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/admin/summary` | admin | Badge counts: pending sign-ups, open issues, pending requests, unread notifications |
| GET | `/admin/accounts?status=&role=` | admin | All accounts with organisation, last sign-in, locked flag |
| POST | `/admin/accounts` | admin | `{email, full_name, role, user_id?}` → active account and a one-time `temporary_password` |
| PATCH | `/admin/accounts/{id}` | admin | `{full_name?, phone?, role?, status: active or disabled, user_id?}`; disabling signs out at once; you cannot disable or demote yourself; there is always an active admin |
| POST | `/admin/accounts/{id}/approve` | admin | `{priority}` creates the organisation the sign-up asked for, or `{user_id}` links to an existing one |
| POST | `/admin/accounts/{id}/reject` | admin | `{reason?}` |
| POST | `/admin/accounts/{id}/reset-password` | admin | New one-time `temporary_password`; must be changed at next sign-in; ends all sessions; unlocks |
| GET | `/admin/issues?status=` | admin | All reports with reporter, organisation, zone |
| PATCH | `/admin/issues/{id}` | admin | `{status?, response?}`; the reporter is notified |
| GET | `/admin/audit?limit=&action=&account_id=` | admin | Audit trail, newest first; `action` is a prefix (e.g. `plan.`, `account.`) |

Error format: `{"detail": "message"}` with HTTP codes 400 / 422 (invalid input), 401 (not signed in), 403 (wrong role
or account state), 404, 409 (conflict, e.g. no pending requests), 429 (locked after wrong passwords), 503 (database
unreachable or schema incomplete).

## 2b. Additions in v2.0

### Scheduler
- Request fields: `min_share` (0..1, optional): this request's guaranteed share under Water-aware (from the policy
  class); `fixed_litres` (optional): used only by the `manual` algorithm.
- Algorithm `manual`: serves the requests in the given order with exactly `fixed_litres` each (officer override);
  the minimum-allocation rule does not apply. The total of `fixed_litres` must not exceed `available_litres`.
- `"mode": "timetable"`: a different input and output (zone-wise daily supply):

```json
{"mode": "timetable", "lpcd": 135, "day_start_hour": 5, "day_end_hour": 23, "slot_minutes": 60,
 "sources": [{"reservoir_id": 1, "name": "Main", "release_litres": 12000000, "pump_capacity_lph": 1000000}],
 "zones": [{"zone_id": 1, "name": "North", "population": 40000, "reservoir_id": 1,
            "inlet_capacity_lph": 600000, "demand_litres": null, "priority": 3, "min_share": 0.3}]}
```
Output, per zone:
- `demand_litres`, `allocated_litres`, `delivered_litres`, `shortfall_litres`
- `lpcd_delivered`, `meets_norm`, `supply_hours`
- `blocks` [{`start`, `end`, `litres`}]

Also:
- `sources` [{`release_litres`, `delivered_litres`, `pump_utilization`}];
- `metrics` {`total_demand_litres`, `total_delivered_litres`, `served_ratio`, `min_lpcd`, `zones_below_norm`, `jain_fairness`}.

Invariants (checked by the scheduler):
- release per source ≤ available;
- per-slot pump capacity is never exceeded;
- per-slot inlet capacity is never exceeded;
- delivered ≤ demand.

### API (all admin-only unless noted)
| Method and path | Purpose |
|---|---|
| `POST /auth/mfa` (public) | second sign-in step: `{challenge, code}` |
| `GET /privacy-notice` (public) | current privacy notice |
| `POST /telemetry/readings` (device key in `X-Device-Key`) | `{readings: [{value, measured_at?}]}` |
| `POST /me/mfa/setup`, `/me/mfa/confirm`, `/me/mfa/disable` (any account) | two-factor sign-in |
| `POST /me/consent`, `GET /me/data-export`, `GET/POST /me/privacy-requests`, `PATCH /me/language` (any account) | privacy and language |
| `GET /me/supply` (requester) | my zone's hours in the latest published timetable |
| `GET /policy/active` (any account), `GET/POST /admin/policies`, `PATCH /admin/policies/{id}`, `POST /admin/policies/{id}/submit`, `POST /admin/policies/{id}/decide` | water policy, maker-checker |
| `POST /allocate/commit` | now also `overrides` `{request_id: litres}` and `reason` |
| `POST /allocate/shadow` | record a recommendation without applying it |
| `GET /runs/{id}/report?format=html|csv` | decision record |
| `GET /admin/kpis?days=` | pilot indicators |
| `POST /resources`, `PATCH /resources/{id}/network`, `PATCH /zones/{id}/network`, `GET /map/zones` (any account) | sources, network, map |
| `GET/POST /timetables`, `GET /timetables/{id}`, `POST /timetables/{id}/publish`, `POST /timetables/{id}/tankers` | supply timetable |
| `GET/POST /tankers`, `PATCH /tankers/{id}` | tanker orders |
| `GET/POST /admin/devices`, `PATCH /admin/devices/{id}`, `GET /admin/devices/{id}/readings` | sensors |
| `POST /admin/import/consumption`, `GET /admin/data-sources` | meter data |
| `POST /admin/accounts/{id}/reset-mfa` | lost phone |
| `GET /admin/privacy-requests`, `POST /admin/privacy-requests/{id}` | DPDP requests |
| `GET/POST /admin/incidents`, `PATCH /admin/incidents/{id}` | incident register |
| `GET /admin/ops`, `POST /admin/maintenance/retention` | operations |

New status codes: 429 also for rate limiting; 403 for an admin without required two-factor sign-in, and for a
self-approval under maker-checker.

## 3. Decisions (agreed in v0.2)

- [x] Round Robin: a quantum is `quantum_litres`; each round gives every unfinished request up to one quantum
- [x] Minimum partial allocation: `min_allocation_ratio` (below it, reject instead of partial)
- [x] Single reservoir for MVP (the schema supports many)
- [x] Flow rate model: a request of L litres takes L / flow_rate_lpm minutes
