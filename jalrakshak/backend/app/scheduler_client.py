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


def build_scheduler_input(algorithm, available_litres, pending, zone_ratios=None,
                          predicted_demand=None, scheduler_config=None):
    """Convert DB rows into the scheduler's input JSON (times become minutes from the first arrival)."""
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
    return {
        "algorithm": algorithm,
        "available_litres": float(available_litres),
        "predicted_demand_litres": None if predicted_demand is None else float(predicted_demand),
        "config": scheduler_config if scheduler_config is not None else config.DEFAULT_SCHEDULER_CONFIG,
        "requests": requests,
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
