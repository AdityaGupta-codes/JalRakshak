# JalRakshak Contracts v0.1

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
- `predicted_demand_litres`: used for the scarcity factor (`predicted / available`). May be `null`, then fall back to the sum of pending requests.
- `quantum_litres`: used by round_robin only. `weights`: used by water_aware only.

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
    "unserved_count": 2
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

### Water-aware score (all factors normalised to 0-1)

```
priority      = priority / 5
urgency       = 0.5 * (urgency / 5) + 0.5 * (1 - clamp((deadline - now) / max_horizon, 0, 1))
scarcity      = clamp(predicted_demand / available, 0, 2) / 2
demand_impact = zone_population / max_zone_population
fairness      = 1 - zone_received_ratio
score = sum(weight_i * factor_i)
```

Higher score is served first. The weights live in config, not in code.

## 2. REST API v0.1 (FastAPI)

| Method | Path | Purpose |
|---|---|---|
| GET | `/resources` | Reservoir list with available/capacity |
| GET | `/requests?status=pending` | List requests |
| POST | `/requests` | Create a request |
| POST | `/allocate/simulate` | Run algorithm(s) on pending requests, **no commit**; body: `{"algorithms": [...], "reservoir_id": 1}` |
| POST | `/allocate/commit` | Run one algorithm and commit in a DB transaction (`FOR UPDATE`) |
| GET | `/runs` | Past runs with metrics (for the comparison view) |
| GET | `/predictions?zone_id=` | Demand forecasts |
| GET | `/alerts` | Active alerts |

Error format: `{"detail": "message"}` with proper HTTP codes (400 validation, 404 not found, 409 insufficient water/conflict).

## 3. Decisions to confirm in the team meeting

- [ ] Round Robin: a quantum is `quantum_litres`; each round gives every unfinished request up to one quantum
- [ ] Minimum partial allocation: `min_allocation_ratio` (below it, reject instead of partial)
- [ ] Single reservoir for MVP (the schema supports many)
- [ ] Flow rate model: a request of L litres takes L / flow_rate_lpm minutes
