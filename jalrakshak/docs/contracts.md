# JalRakshak Contracts v0.4

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

## 2. REST API v0.2 (FastAPI)

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Proves the API can reach PostgreSQL (503 with a reason if not) |
| GET | `/status` | Water level, pending demand, scarcity level and today's forecast (dashboard header) |
| GET | `/users` | Requesters, for the "add request" form |
| GET | `/resources` | Reservoir list with available/capacity |
| GET | `/requests?status=pending` | List requests |
| POST | `/requests` | Create a request (admin) |
| POST | `/allocate/simulate` | Run algorithm(s) on pending requests, **no commit**; body: `{"algorithms": [...], "reservoir_id": 1}` |
| POST | `/allocate/commit` | Run one algorithm and commit in a DB transaction (`FOR UPDATE`) (admin) |
| GET | `/runs` | Past runs with metrics |
| GET | `/runs/{id}` | One run with its per-request allocations |
| POST | `/demo/reset` | Demo helper: clear history, restore the 8 sample requests (arriving from now), refill the reservoir to `available_litres` (default 50,000 L, or the capacity if smaller) (admin) |
| PATCH | `/resources/{id}` | Change `name`, `capacity_litres` and/or `available_litres` (only the fields sent). Water can never exceed capacity: lowering only the capacity below the stored water is refused with advice (400) (admin) |
| GET | `/zones` | Zones with population and number of requesters |
| POST | `/zones` | Add a zone `{name, population}` (admin). 409 if the name is taken |
| PATCH | `/zones/{id}` | Change name and/or population (admin) |
| DELETE | `/zones/{id}` | Delete a zone with no requesters (409 otherwise); its history, forecasts and alerts go with it (admin) |
| POST | `/users` | Add a requester `{name, user_type, priority 1-5, zone_id}` (admin) |
| PATCH | `/users/{id}` | Change any of those fields (admin). Pending requests use the new values the next time plans are made |
| DELETE | `/users/{id}` | Delete a requester with no requests on record (409 otherwise) (admin) |
| GET | `/predictions?zone_id=` | Demand forecasts (all models) |
| GET | `/forecast?history_days=60` | Recent daily use per zone, next 7 days from the selected model, model scores |
| GET | `/alerts?include_resolved=false` | Stored alerts (leaks, rising demand) plus live ones (`low_water` below 30 % of capacity, forecast above supply); live ones have `alert_id: null` and `live: true` |
| POST | `/alerts/{id}/resolve` | Mark a stored alert as handled (admin) |
| GET | `/auth` | `{"token_required": bool}` |
| POST | `/auth/check` | 200 if the `X-Admin-Token` header is right, 401 otherwise |

**Admin token (optional).** If the backend has `JAL_ADMIN_TOKEN` set, endpoints marked (admin) need the header
`X-Admin-Token: <token>` and return 401 without it. Reading and `/allocate/simulate` stay open. With no token set
(the default, for demos) everything is open. The dashboard shows an Unlock button when a token is required.

Error format: `{"detail": "message"}` with proper HTTP codes (400 or 422 validation, 401 admin token missing or wrong, 404 not found, 409 conflict such as no pending requests, 503 database unreachable).

## 3. Decisions (agreed in v0.2)

- [x] Round Robin: a quantum is `quantum_litres`; each round gives every unfinished request up to one quantum
- [x] Minimum partial allocation: `min_allocation_ratio` (below it, reject instead of partial)
- [x] Single reservoir for MVP (the schema supports many)
- [x] Flow rate model: a request of L litres takes L / flow_rate_lpm minutes
