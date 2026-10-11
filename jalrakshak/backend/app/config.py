import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:jal123@localhost:5432/jalrakshak")
SCHEDULER_BIN = os.getenv("SCHEDULER_BIN", "jalrakshak-scheduler")
SCHEDULER_TIMEOUT_S = float(os.getenv("SCHEDULER_TIMEOUT_S", "10"))

# Database connection: try a few times before giving up (the DB container may still be starting).
DB_CONNECT_RETRIES = int(os.getenv("DB_CONNECT_RETRIES", "5"))
DB_CONNECT_DELAY_S = float(os.getenv("DB_CONNECT_DELAY_S", "1.0"))
DB_CONNECT_TIMEOUT_S = int(os.getenv("DB_CONNECT_TIMEOUT_S", "5"))

# How long the scheduler waits after the first pending request before deciding (minutes).
# "all" (default) ranks every pending request together. That matches how the dashboard is used: when the
# operator clicks "Apply", all pending requests are already waiting. With 0, the first request to arrive is
# served on its own before anyone else has arrived (in the sample data that hands a factory its full
# 20,000 L before the hospital is even considered). A number means "wait this many minutes".
BATCH_WINDOW = os.getenv("BATCH_WINDOW_MINUTES", "all")

# Pass today's ML demand forecast to the scheduler as predicted_demand_litres (scarcity factor).
# When no forecast exists (ML pipeline not run), the scheduler uses the litres still requested instead.
USE_FORECAST = os.getenv("USE_FORECAST", "1").lower() not in ("0", "false", "no")

# Optional protection for the endpoints that change data. Empty (default) = open, for demos.
# If set, POST requests must send the header  X-Admin-Token: <this value>.
ADMIN_TOKEN = os.getenv("JAL_ADMIN_TOKEN", "")

# Passed to the C++ scheduler as "config" (see docs/contracts.md).
# batch_window_minutes is filled in per run from BATCH_WINDOW (see scheduler_client.build_scheduler_input).
DEFAULT_SCHEDULER_CONFIG = {
    "flow_rate_lpm": 500,
    "quantum_litres": 2000,
    "min_allocation_ratio": 0.2,
    "guarantee_floor": 0.3,
    "weights": {"priority": 0.30, "urgency": 0.25, "scarcity": 0.15,
                "demand_impact": 0.15, "fairness": 0.15},
}

ALL_ALGORITHMS = ["fcfs", "priority", "round_robin", "water_aware"]
