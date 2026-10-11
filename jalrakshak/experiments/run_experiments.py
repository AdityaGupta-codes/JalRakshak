"""Compare the four schedulers on many random scenarios.

    python run_experiments.py [--bin ../scheduler/build/jalrakshak-scheduler] [--trials 200]

Writes results.csv (one row per scenario x algorithm, averaged over the trials) and prints a table.
Scenarios differ only in how much water is available compared with total demand.
"""
import argparse
import csv
import json
import random
import statistics
import subprocess
import sys

ALGORITHMS = ["fcfs", "priority", "round_robin", "water_aware"]
SCENARIOS = {"normal": 1.2, "moderate_scarcity": 0.75, "severe_scarcity": 0.5}  # water / demand
METRICS = ["avg_wait_time", "utilization", "satisfaction_ratio",
           "priority_weighted_satisfaction", "jain_fairness", "unserved_count", "deadline_miss_count"]

# (user_type, priority, typical urgency, litres range)
USER_TYPES = [("hospital", 5, 5, (5000, 12000)), ("school", 4, 3, (3000, 8000)),
              ("domestic", 4, 4, (8000, 18000)), ("commercial", 2, 2, (5000, 12000)),
              ("industry", 2, 2, (10000, 25000)), ("park", 1, 1, (2000, 6000))]
ZONE_POP = {1: 40000, 2: 65000, 3: 30000}


def make_scenario(rng, water_ratio, n_requests=10, floor=None, window=0.0):
    zone_ratio = {z: rng.uniform(0.2, 1.0) for z in ZONE_POP}  # how well each zone was served recently
    requests = []
    for i in range(1, n_requests + 1):
        utype, prio, urg, (lo, hi) = rng.choice(USER_TYPES)
        arrival = round(rng.uniform(0, 60), 1)
        zone = rng.choice(list(ZONE_POP))
        requests.append({
            "request_id": i, "user_id": i, "user_type": utype, "zone_id": zone, "priority": prio,
            "urgency": max(1, min(5, urg + rng.choice([-1, 0, 0, 1]))),
            "requested_litres": float(rng.randrange(lo, hi, 500)),
            "arrival_time": arrival, "deadline": arrival + rng.choice([60, 120, 240, 480]),
            "zone_population": ZONE_POP[zone], "zone_received_ratio": round(zone_ratio[zone], 3)})
    demand = sum(r["requested_litres"] for r in requests)
    config = {"flow_rate_lpm": 500, "quantum_litres": 2000, "min_allocation_ratio": 0.2}
    if floor is not None:
        config["guarantee_floor"] = floor  # water_aware tuning knob (default is set in the scheduler)
    if window == "all":  # wait until the last request has arrived (the backend's default)
        config["batch_window_minutes"] = max(r["arrival_time"] for r in requests)
    elif window:
        config["batch_window_minutes"] = float(window)  # collect requests this long before deciding (all algorithms)
    return {"available_litres": round(demand * water_ratio), "predicted_demand_litres": demand,
            "config": config, "requests": requests}


def run(binary, scenario, algorithm):
    proc = subprocess.run([binary], input=json.dumps(dict(scenario, algorithm=algorithm)),
                          capture_output=True, text=True)
    out = json.loads(proc.stdout)
    if proc.returncode != 0:
        sys.exit(f"{algorithm} failed: {out.get('error')}")
    return out["metrics"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default="../scheduler/build/jalrakshak-scheduler")
    ap.add_argument("--trials", type=int, default=200)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--out", default="results.csv")
    ap.add_argument("--floor", type=float, default=None, help="water_aware guarantee_floor (0..1)")
    ap.add_argument("--window", default="0",
                    help="batch_window_minutes for every algorithm: a number, or 'all' (wait for the last arrival)")
    args = ap.parse_args()

    rows = []
    for name, ratio in SCENARIOS.items():
        rng = random.Random(args.seed)
        scenarios = [make_scenario(rng, ratio, floor=args.floor, window=args.window if args.window == "all" else float(args.window))
                     for _ in range(args.trials)]
        for alg in ALGORITHMS:
            runs = [run(args.bin, s, alg) for s in scenarios]
            rows.append({"scenario": name, "algorithm": alg, "trials": args.trials,
                         **{m: round(statistics.mean(r[m] for r in runs), 4) for m in METRICS}})

    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    short = {"avg_wait_time": "wait(min)", "utilization": "util", "satisfaction_ratio": "satisf",
             "priority_weighted_satisfaction": "prio-satisf", "jain_fairness": "jain", "unserved_count": "unserved",
             "deadline_miss_count": "late"}
    print(f"{'scenario':<18}{'algorithm':<13}" + "".join(f"{short[m]:>12}" for m in METRICS))
    for r in rows:
        print(f"{r['scenario']:<18}{r['algorithm']:<13}" + "".join(f"{r[m]:>12.3f}" for m in METRICS))


if __name__ == "__main__":
    main()
