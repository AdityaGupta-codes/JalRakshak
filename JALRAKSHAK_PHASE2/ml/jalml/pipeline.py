"""JalRakshak data-science pipeline: data -> leak detection -> forecasting -> database.

    python -m jalml.pipeline --database-url postgresql://postgres:jal123@localhost:5432/jalrakshak
    python -m jalml.pipeline --offline            # no database: write results files only

Steps
  1. Generate two years of daily consumption per zone (ending yesterday) with known injected leaks.
  2. Detect leaks (robust z-score) and score both detectors against the injected leaks.
  3. Replace flagged days by their normal value, then compare four forecasting models on the last 90 days.
  4. The most accurate model forecasts the next 7 days for every zone.
  5. Store consumption, forecasts, model scores and leak alerts in PostgreSQL (one transaction), and
     write metrics.json, charts and a snapshot for the demo API into the results folder.

The backend sums today's forecast over all zones and passes it to the scheduler as
predicted_demand_litres (the water-aware scarcity factor).
"""
import argparse
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd

from . import anomaly, data, forecast

RESULTS = Path(__file__).resolve().parents[1] / "results"
ALERT_LOOKBACK_DAYS = 14
RISING_DEMAND = 1.10  # forecast week more than 10 % above the last 4 weeks -> "high_demand" notice


# ------------------------------------------------------------------ database helpers
def connect(url):
    import psycopg2
    from psycopg2.extras import RealDictCursor
    return psycopg2.connect(url, cursor_factory=RealDictCursor)


MIGRATE_SQL = Path(os.environ.get("MIGRATE_SQL", Path(__file__).resolve().parents[2] / "db" / "migrate.sql"))


def upgrade_schema(conn, log=print):
    """Bring a database made by an older version up to date (db/migrate.sql is safe to repeat)."""
    if not MIGRATE_SQL.exists():
        log(f"(no {MIGRATE_SQL}, skipping the database upgrade)")
        return
    old = getattr(conn, "autocommit", None)
    try:
        if old is not None:
            conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(MIGRATE_SQL.read_text(encoding="utf-8"))
    finally:
        if old is not None:
            conn.autocommit = old


def read_zones(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT zone_id, name, population FROM zones ORDER BY zone_id")
        rows = cur.fetchall()
    conn.commit()
    return data.zones_from_rows(rows) if rows else data.DEFAULT_ZONES


def _insert_many(cur, table, cols, rows, chunk=400, suffix=""):
    for i in range(0, len(rows), chunk):
        part = rows[i:i + chunk]
        one = "(" + ", ".join(["%s"] * len(cols)) + ")"
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES " + ", ".join([one] * len(part)) + suffix
        cur.execute(sql, tuple(v for row in part for v in row))


# A zone with at least this many days of real (metered or imported) consumption is modelled on its real
# data; other zones keep using generated data. Real rows are never deleted or overwritten by this job.
MIN_REAL_DAYS = 120


def read_real_consumption(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT zone_id, recorded_on, litres FROM consumption WHERE source IN ('meter', 'import') "
                    "ORDER BY zone_id, recorded_on")
        rows = cur.fetchall()
    conn.commit()
    if not rows:
        return pd.DataFrame(columns=["zone_id", "recorded_on", "litres", "is_injected_leak"])
    df = pd.DataFrame([{"zone_id": int(r["zone_id"]), "recorded_on": pd.Timestamp(r["recorded_on"]).date(),
                        "litres": float(r["litres"]), "is_injected_leak": False} for r in rows])
    return df


def merge_real(synthetic, real, min_days=MIN_REAL_DAYS):
    """Replace a zone's generated series by its real one when the zone has at least min_days real days.
    Returns (consumption, {zone_id: "real" | "synthetic"})."""
    sources = {int(z): "synthetic" for z in synthetic["zone_id"].unique()}
    if real is None or real.empty:
        return synthetic, sources
    counts = real.groupby("zone_id").size()
    real_zones = [int(z) for z, n in counts.items() if n >= min_days]
    if not real_zones:
        return synthetic, sources
    keep = synthetic[~synthetic["zone_id"].isin(real_zones)]
    merged = pd.concat([keep, real[real["zone_id"].isin(real_zones)]], ignore_index=True)
    merged = merged.sort_values(["zone_id", "recorded_on"]).reset_index(drop=True)
    for z in real_zones:
        sources[z] = "real"
    return merged, sources


def store(conn, consumption, predictions, scores, alerts, sources=None):
    """Replace the generated data in one transaction (alerts a person already resolved are kept).
    Real (metered / imported) consumption rows are never deleted or overwritten."""
    sources = sources or {}
    with conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM consumption WHERE source = 'synthetic'")
            _insert_many(cur, "consumption", ["zone_id", "recorded_on", "litres", "is_injected_leak", "source"],
                         [(int(r.zone_id), r.recorded_on.isoformat(), float(r.litres), bool(r.is_injected_leak),
                           "synthetic")
                          for r in consumption.itertuples() if sources.get(int(r.zone_id), "synthetic") == "synthetic"],
                         suffix=" ON CONFLICT (zone_id, recorded_on) DO NOTHING")
            cur.execute("DELETE FROM predictions")
            _insert_many(cur, "predictions", ["zone_id", "target_date", "predicted_litres", "model_name"],
                         [(int(r.zone_id), r.target_date.isoformat(), float(r.predicted_litres), r.model_name)
                          for r in predictions.itertuples()])
            cur.execute("DELETE FROM model_evaluations")
            _insert_many(cur, "model_evaluations",
                         ["model_name", "mae_litres", "rmse_litres", "mape", "test_days", "is_selected"],
                         [(s["model_name"], s["mae_litres"], s["rmse_litres"], s["mape"], s["test_days"],
                           s["is_selected"]) for s in scores])
            cur.execute("DELETE FROM alerts WHERE alert_type IN ('anomaly', 'high_demand') AND NOT resolved")
            if alerts:
                _insert_many(cur, "alerts", ["zone_id", "alert_type", "severity", "message"],
                             [(a["zone_id"], a["alert_type"], a["severity"], a["message"]) for a in alerts])


# ------------------------------------------------------------------ alerts
def _fmt_day(d):
    return pd.Timestamp(d).strftime("%d %b")


def build_alerts(flagged, predictions, zones, today):
    """Leak alerts for the last two weeks (consecutive days merged) and rising-demand notices."""
    names = {z.zone_id: z.name for z in zones}
    alerts = []
    recent = flagged[(flagged["is_anomaly"]) &
                     (pd.to_datetime(flagged["recorded_on"]) >= pd.Timestamp(today - timedelta(days=ALERT_LOOKBACK_DAYS)))]
    for zone_id, g in recent.sort_values("recorded_on").groupby("zone_id"):
        days = list(g.itertuples())
        groups, current = [], [days[0]]
        for d in days[1:]:
            if (pd.Timestamp(d.recorded_on) - pd.Timestamp(current[-1].recorded_on)).days == 1:
                current.append(d)
            else:
                groups.append(current)
                current = [d]
        groups.append(current)
        for grp in groups:
            peak = max(x.excess for x in grp)
            extra = sum(x.litres - x.expected_litres for x in grp)
            span = _fmt_day(grp[0].recorded_on) + ("" if len(grp) == 1 else f" to {_fmt_day(grp[-1].recorded_on)}")
            alerts.append({
                "zone_id": int(zone_id), "alert_type": "anomaly",
                "severity": "critical" if peak >= 0.4 else "warning",
                "message": (f"Possible leak in {names.get(zone_id, zone_id)}: use was up to {peak:.0%} above normal "
                            f"on {span} (about {extra:,.0f} L extra). Check pipes and meters."),
            })

    selected = predictions
    last28 = flagged[pd.to_datetime(flagged["recorded_on"]) > pd.Timestamp(today - timedelta(days=29))]
    for zone_id, g in selected.groupby("zone_id"):
        normal = last28[(last28["zone_id"] == zone_id) & (~last28["is_anomaly"])]["litres"].mean()
        ahead = g["predicted_litres"].mean()
        if normal and ahead > RISING_DEMAND * normal:
            alerts.append({
                "zone_id": int(zone_id), "alert_type": "high_demand", "severity": "info",
                "message": (f"Demand in {names.get(zone_id, zone_id)} is forecast to rise: about {ahead:,.0f} L a day "
                            f"next week, {ahead / normal - 1:.0%} above the last four weeks."),
            })
    return alerts


# ------------------------------------------------------------------ charts and files
def write_outputs(out, consumption, flagged, scores, detector_scores, test_preds, predictions, alerts, zones):
    out.mkdir(parents=True, exist_ok=True)
    best = next(s["model_name"] for s in scores if s["is_selected"])
    (out / "metrics.json").write_text(json.dumps({
        "forecast_models": scores, "leak_detectors": detector_scores,
        "selected_model": best, "history_days": int(consumption["recorded_on"].nunique()),
        "injected_leak_days": int(consumption["is_injected_leak"].sum()),
    }, indent=2))

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        names = {z.zone_id: z.name for z in zones}
        fig, axes = plt.subplots(len(zones), 1, figsize=(10, 2.6 * len(zones)), sharex=True)
        for ax, z in zip(axes, sorted(names)):
            t = test_preds[test_preds["zone_id"] == z]
            ax.plot(pd.to_datetime(t["recorded_on"]), t["actual"] / 1000, color="#1f2937", lw=1.4, label="Actual")
            ax.plot(pd.to_datetime(t["recorded_on"]), t[best] / 1000, color="#2563eb", lw=1.4,
                    label=forecast.MODEL_LABELS[best])
            ax.plot(pd.to_datetime(t["recorded_on"]), t["seasonal_naive"] / 1000, color="#9ca3af", lw=1, ls="--",
                    label=forecast.MODEL_LABELS["seasonal_naive"])
            ax.set_title(names[z], loc="left", fontsize=10)
            ax.set_ylabel("thousand L / day")
            ax.grid(alpha=0.25)
        axes[0].legend(loc="upper left", fontsize=8, frameon=False, ncol=3)
        fig.suptitle("One-day-ahead forecasts on the 90 held-out days", x=0.01, ha="left")
        fig.tight_layout()
        fig.savefig(out / "forecast_test.png", dpi=130)
        plt.close(fig)

        fig, axes = plt.subplots(len(zones), 1, figsize=(10, 2.6 * len(zones)), sharex=True)
        for ax, z in zip(axes, sorted(names)):
            g = flagged[flagged["zone_id"] == z]
            d = pd.to_datetime(g["recorded_on"])
            ax.plot(d, g["litres"] / 1000, color="#6b7280", lw=0.8)
            truth = g["is_injected_leak"].astype(bool)
            hit = g["is_anomaly"].astype(bool)
            ax.scatter(d[truth & hit], g["litres"][truth & hit] / 1000, s=16, color="#16a34a", label="Leak found")
            ax.scatter(d[truth & ~hit], g["litres"][truth & ~hit] / 1000, s=16, color="#f59e0b", label="Leak missed")
            ax.scatter(d[~truth & hit], g["litres"][~truth & hit] / 1000, s=16, color="#dc2626", marker="x",
                       label="False alarm")
            ax.set_title(names[z], loc="left", fontsize=10)
            ax.set_ylabel("thousand L / day")
            ax.grid(alpha=0.25)
        axes[0].legend(loc="upper left", fontsize=8, frameon=False, ncol=3)
        fig.suptitle("Leak detection against the injected leaks (two years)", x=0.01, ha="left")
        fig.tight_layout()
        fig.savefig(out / "leak_detection.png", dpi=130)
        plt.close(fig)
    except ImportError:  # charts are optional
        pass

    # Snapshot for frontend/dev/mock_api.py (the dashboard demo without Docker)
    hist = consumption[pd.to_datetime(consumption["recorded_on"]) >
                       pd.Timestamp(max(consumption["recorded_on"])) - pd.Timedelta(days=60)]
    (out / "forecast_snapshot.json").write_text(json.dumps({
        "zones": [{"zone_id": z.zone_id, "name": z.name, "population": z.population} for z in zones],
        "history": [{"zone_id": int(r.zone_id), "date": r.recorded_on.isoformat(), "litres": float(r.litres)}
                    for r in hist.itertuples()],
        "predictions": [{"zone_id": int(r.zone_id), "target_date": r.target_date.isoformat(),
                         "predicted_litres": float(r.predicted_litres), "model_name": r.model_name}
                        for r in predictions[predictions["model_name"] == best].itertuples()],
        "models": scores,
        "alerts": alerts,
    }, indent=1))


# ------------------------------------------------------------------ main
def run(conn=None, out=RESULTS, days=730, seed=2026, end_date=None, log=print):
    if conn is not None:
        upgrade_schema(conn, log)
    zones = read_zones(conn) if conn is not None else data.DEFAULT_ZONES
    consumption = data.generate_consumption(zones, days=days, end_date=end_date, seed=seed)
    log(f"Generated {len(consumption):,} days of consumption for {len(zones)} zones "
        f"({int(consumption['is_injected_leak'].sum())} injected leak days).")
    real = read_real_consumption(conn) if conn is not None else None
    consumption, sources = merge_real(consumption, real)
    real_zones = [z for z, src in sources.items() if src == "real"]
    if real_zones:
        log(f"Using real metered data for zone(s) {real_zones}; generated data for the others.")
    today = max(consumption["recorded_on"]) + timedelta(days=1)

    flagged = anomaly.robust_zscore(consumption)
    # Detectors can only be scored where the true leak days are known: the generated zones.
    synth = consumption["zone_id"].isin([z for z, src in sources.items() if src == "synthetic"])
    if synth.any():
        detector_scores = {"robust_zscore": anomaly.evaluate(flagged[flagged["zone_id"].isin(
                               consumption[synth]["zone_id"].unique())]),
                           "isolation_forest": anomaly.evaluate(anomaly.isolation_forest(consumption[synth]))}
    else:
        detector_scores = {}
    log("Leak detection (day level, generated zones): " + ", ".join(
        f"{k}: precision {v['precision']:.2f}, recall {v['recall']:.2f}" for k, v in detector_scores.items()))

    clean = anomaly.cleaned(flagged)[["zone_id", "recorded_on", "litres", "is_injected_leak"]]
    scores, test_preds = forecast.evaluate_models(clean, consumption, seed=seed)
    for s in scores:
        log(f"  {s['label']:<36} MAE {s['mae_litres']:>8,.0f} L   MAPE {s['mape']:.1%}"
            + ("   <- selected" if s["is_selected"] else ""))
    best = next(s["model_name"] for s in scores if s["is_selected"])

    predictions = pd.concat([forecast.forecast_ahead(clean, m, seed=seed) for m in forecast.MODELS],
                            ignore_index=True)
    chosen = predictions[predictions["model_name"] == best]
    alerts = build_alerts(flagged, chosen, zones, today)
    log(f"Forecast for {today}: {chosen[chosen['target_date'] == today]['predicted_litres'].sum():,.0f} L "
        f"across all zones. {len(alerts)} alert(s).")

    if conn is not None:
        store(conn, consumption, predictions, scores, alerts, sources)
        log("Stored consumption, predictions, model scores and alerts in the database.")
    if out is not None:
        write_outputs(Path(out), consumption, flagged, scores, detector_scores, test_preds, predictions, alerts, zones)
        log(f"Wrote results to {out}")
    return {"scores": scores, "detectors": detector_scores, "predictions": predictions, "alerts": alerts,
            "data_sources": sources}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--offline", action="store_true", help="do not use a database")
    ap.add_argument("--out", default=str(RESULTS), help="folder for metrics.json and charts")
    ap.add_argument("--days", type=int, default=730)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--retries", type=int, default=30, help="wait for the database this many times (2 s apart)")
    args = ap.parse_args(argv)

    conn = None
    if not args.offline:
        if not args.database_url:
            ap.error("give --database-url (or set DATABASE_URL), or use --offline")
        import time
        for attempt in range(args.retries):
            try:
                conn = connect(args.database_url)
                break
            except Exception as exc:  # database still starting
                if attempt + 1 == args.retries:
                    print(f"Cannot connect to the database: {exc}", file=sys.stderr)
                    return 1
                time.sleep(2)
    try:
        run(conn, out=args.out, days=args.days, seed=args.seed)
    except Exception as exc:
        code = getattr(exc, "pgcode", None)
        print(f"ML pipeline failed: {exc}", file=sys.stderr)
        if code in ("42P01", "42883", "42703"):
            print("The database looks older than the code. To start fresh (deletes all data): "
                  "docker compose down -v   then   docker compose up --build", file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
