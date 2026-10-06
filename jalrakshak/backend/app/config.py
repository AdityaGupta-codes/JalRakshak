import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:jal123@localhost:5432/jalrakshak")
SCHEDULER_BIN = os.getenv("SCHEDULER_BIN", "jalrakshak-scheduler")
SCHEDULER_TIMEOUT_S = float(os.getenv("SCHEDULER_TIMEOUT_S", "10"))

# Passed to the C++ scheduler as "config" (see docs/contracts.md).
DEFAULT_SCHEDULER_CONFIG = {
    "flow_rate_lpm": 500,
    "quantum_litres": 2000,
    "min_allocation_ratio": 0.2,
    "weights": {"priority": 0.30, "urgency": 0.25, "scarcity": 0.15,
                "demand_impact": 0.15, "fairness": 0.15},
}
