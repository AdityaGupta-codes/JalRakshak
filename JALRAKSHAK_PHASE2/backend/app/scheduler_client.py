"""Talks to the C++ scheduler: builds its JSON input from DB rows and runs it as a subprocess.

Pure Python (no web framework, no DB driver) so it can be unit-tested on its own.
"""
import json
import subprocess

from . import config


class SchedulerError(Exception):
    """The scheduler rejected the input or the algorithm (maps to HTTP 400)."""


class SchedulerUnavailable(Exception):
    """The scheduler binary is missing / crashed / timed out (maps to HTTP 500)."""


def _minutes(ts, t0):
    return round((ts - t0).total_seconds() / 60.0, 4)


def resolve_batch_window(setting, requests):
    """Turn the BATCH_WINDOW setting into minutes. "all" = wait until the last pending request has arrived."""
    if setting is None:
        return 0.0
    if isinstance(setting, str):
        if setting.strip().lower() == "all":
            return max((r["arrival_time"] for r in requests), default=0.0)
        try:
            setting = float(setting)
        except ValueError:
            raise SchedulerError(f"BATCH_WINDOW_MINUTES must be 'all' or a number, not {setting!r}")
    if setting < 0:
        raise SchedulerError("BATCH_WINDOW_MINUTES must not be negative")
    return float(setting)


def build_scheduler_input(algorithm, available_litres, pending, zone_ratios=None,
                          predicted_demand=None, scheduler_config=None, batch_window=None,
                          class_shares=None, fixed_litres=None):
    """Convert DB rows into the scheduler's input JSON (times become minutes from the first arrival).

    batch_window: None = use config.BATCH_WINDOW; "all", or a number of minutes. If scheduler_config
    already contains batch_window_minutes, that value wins.
    class_shares: {user_type: guaranteed share} from the active policy (Water-aware floor per class).
    fixed_litres: {request_id: litres} for the "manual" (officer override) algorithm.
    """
    zone_ratios = zone_ratios or {}
    t0 = min((r["arrival_time"] for r in pending), default=None)
    requests = []
    for r in pending:
        requests.append({
            "request_id": int(r["request_id"]),
            "user_id": int(r["user_id"]),
            "user_type": r["user_type"],
            "zone_id": int(r["zone_id"]),
            "priority": int(r["priority"]),
            "urgency": int(r["urgency"]),
            "requested_litres": float(r["requested_litres"]),
            "arrival_time": _minutes(r["arrival_time"], t0),
            "deadline": _minutes(r["deadline"], t0),
            "zone_population": int(r["zone_population"]),
            "zone_received_ratio": float(zone_ratios.get(r["zone_id"], 0.0)),
        })
        if class_shares and r["user_type"] in class_shares:
            requests[-1]["min_share"] = float(class_shares[r["user_type"]])
        if fixed_litres is not None:
            requests[-1]["fixed_litres"] = float(fixed_litres[int(r["request_id"])])
    cfg = dict(scheduler_config if scheduler_config is not None else config.DEFAULT_SCHEDULER_CONFIG)
    if "batch_window_minutes" not in cfg:
        cfg["batch_window_minutes"] = resolve_batch_window(
            config.BATCH_WINDOW if batch_window is None else batch_window, requests)
    return {
        "algorithm": algorithm,
        "available_litres": float(available_litres),
        "predicted_demand_litres": None if predicted_demand is None else float(predicted_demand),
        "config": cfg,
        "requests": requests,
    }


def build_timetable_input(sources, zones, lpcd, start_hour, end_hour, slot_minutes=60):
    """Input for the scheduler's "timetable" mode (zone-wise daily supply). See scheduler/include/timetable.hpp."""
    return {
        "mode": "timetable",
        "lpcd": float(lpcd),
        "day_start_hour": int(start_hour),
        "day_end_hour": int(end_hour),
        "slot_minutes": int(slot_minutes),
        "sources": [{"reservoir_id": int(s["reservoir_id"]), "name": s["name"],
                     "release_litres": float(s["release_litres"]),
                     "pump_capacity_lph": float(s["pump_capacity_lph"])} for s in sources],
        "zones": [{"zone_id": int(z["zone_id"]), "name": z["name"], "population": int(z["population"]),
                   "reservoir_id": int(z["reservoir_id"]), "inlet_capacity_lph": float(z["inlet_capacity_lph"]),
                   "demand_litres": None if z.get("demand_litres") is None else float(z["demand_litres"]),
                   "priority": int(z["priority"]), "min_share": float(z["min_share"])} for z in zones],
    }


def run_scheduler(payload, binary=None, timeout=None):
    binary = binary or config.SCHEDULER_BIN
    timeout = timeout or config.SCHEDULER_TIMEOUT_S
    try:
        proc = subprocess.run([binary], input=json.dumps(payload), capture_output=True,
                              text=True, timeout=timeout)
    except FileNotFoundError:
        raise SchedulerUnavailable(f"scheduler binary not found: {binary}")
    except subprocess.TimeoutExpired:
        raise SchedulerUnavailable(f"scheduler timed out after {timeout}s")

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise SchedulerUnavailable(f"scheduler produced invalid output (exit {proc.returncode}): "
                                   f"{proc.stderr.strip() or proc.stdout.strip()}")
    if proc.returncode != 0:
        raise SchedulerError(data.get("error", "scheduler failed"))
    return data
