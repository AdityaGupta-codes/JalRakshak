# Database design (ER diagram)

![ER diagram](er_diagram.png)

(`er_diagram.svg` is the same picture as a vector file for the report; `er_diagram.mmd` is its Mermaid source,
which GitHub also renders below.)

## Tables

| Table | One row per | Written by |
|---|---|---|
| `zones` | area of the city, with its population | seed data |
| `users` | requester (hospital, school, homes, industry, shop, park) with a priority 1 to 5 | seed data |
| `reservoirs` | water source, with capacity and water available now | seed data, every committed plan |
| `water_requests` | request for litres by a deadline; status pending / approved / partial / rejected | dashboard form, committed plans |
| `scheduler_runs` | applied allocation plan: algorithm, water before and after, metrics (JSON) | `POST /allocate/commit` |
| `allocations` | litres one request received in one run, with wait and score | `POST /allocate/commit` |
| `consumption` | zone's measured use on one day (`is_injected_leak` = ground truth for testing) | ML pipeline |
| `predictions` | forecast of a zone's use on a future day, per model | ML pipeline |
| `model_evaluations` | accuracy of one forecasting model; `is_selected` marks the one in use | ML pipeline |
| `alerts` | leak / rising demand notice; can be marked resolved | ML pipeline, dashboard |

## Rules the database enforces itself

- `reservoirs.available_litres` must stay between 0 and capacity (`CHECK`). Even a buggy program cannot
  hand out water that is not there: the transaction fails and nothing is saved.
- `requested_litres > 0`, `allocated_litres >= 0`, `priority` and `urgency` between 1 and 5, `deadline > arrival_time`.
- `UNIQUE (run_id, request_id)`: a request appears at most once in a plan.
- Foreign keys: no allocation can point at a request, run or reservoir that does not exist.

## Normalisation

The schema is in third normal form: every non-key column depends only on its table's key. For example a
request's priority is not copied into `water_requests`; it is read from the requester in `users`, and a zone's
population lives only in `zones`. `scheduler_runs.metrics` is stored as JSON on purpose: it is a read-only
snapshot of the run's results, never queried column by column.

The dashed link between `model_evaluations` and `predictions` is by `model_name`, not a foreign key: the
pipeline replaces both tables together in one transaction.

```mermaid
erDiagram
    zones ||--o{ users : "has"
    zones ||--o{ consumption : "daily use"
    zones ||--o{ predictions : "forecast for"
    zones |o--o{ alerts : "about"
    users ||--o{ water_requests : "makes"
    reservoirs ||--o{ water_requests : "asked from"
    reservoirs |o--o{ scheduler_runs : "planned for"
    scheduler_runs ||--o{ allocations : "contains"
    water_requests ||--o{ allocations : "receives"
    reservoirs ||--o{ allocations : "drawn from"
    model_evaluations ||..o{ predictions : "model_name"

    zones {
        int zone_id PK
        text name UK
        int population
    }
    users {
        int user_id PK
        text name
        text user_type "hospital, domestic, school, industry, commercial, park"
        smallint priority "1 to 5"
        int zone_id FK
    }
    reservoirs {
        int reservoir_id PK
        text name UK
        numeric capacity_litres
        numeric available_litres "0 to capacity (CHECK)"
        timestamptz updated_at
    }
    water_requests {
        int request_id PK
        int user_id FK
        int reservoir_id FK
        numeric requested_litres "> 0"
        smallint urgency "1 to 5"
        timestamptz arrival_time
        timestamptz deadline "> arrival_time"
        text status "pending, approved, partial, rejected, cancelled"
    }
    scheduler_runs {
        int run_id PK
        text algorithm "fcfs, priority, round_robin, water_aware"
        text scenario
        int reservoir_id FK
        numeric available_before
        numeric available_after
        jsonb metrics
        boolean committed
        timestamptz created_at
    }
    allocations {
        int allocation_id PK
        int run_id FK
        int request_id FK
        int reservoir_id FK
        numeric allocated_litres ">= 0"
        numeric wait_minutes
        numeric score
        timestamptz allocated_at
    }
    consumption {
        bigint consumption_id PK
        int zone_id FK
        date recorded_on
        numeric litres
        boolean is_injected_leak
    }
    predictions {
        int prediction_id PK
        int zone_id FK
        date target_date
        numeric predicted_litres
        text model_name
        timestamptz created_at
    }
    model_evaluations {
        int evaluation_id PK
        text model_name
        numeric mae_litres
        numeric rmse_litres
        numeric mape
        int test_days
        boolean is_selected
        timestamptz created_at
    }
    alerts {
        int alert_id PK
        int zone_id FK
        text alert_type "low_water, high_demand, anomaly"
        text severity "info, warning, critical"
        text message
        boolean resolved
        timestamptz created_at
    }
```
