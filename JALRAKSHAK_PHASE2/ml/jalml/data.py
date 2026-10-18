"""Synthetic daily water consumption per zone.

Real municipal meter data was not available, so this generator produces a realistic-looking history
with the patterns a forecaster should learn, plus injected leaks whose dates are known (ground truth
for testing the anomaly detector). Everything is reproducible from the seed.

Litres are the water each zone draws from the project's reservoir per day, on the same scale as the
reservoir in seed.sql (tens of thousands of litres), so forecasts can be fed straight into the scheduler.

Patterns built in:
  * size:      proportional to zone population (LITRES_PER_PERSON per day)
  * season:    hot, dry months (April to June) use more; monsoon months (July to September) use less
  * weekday:   each zone has its own weekly shape (homes use more at weekends, industry less)
  * trend:     slow growth, TREND_PER_YEAR
  * heat:      random multi-day heat waves add demand
  * noise:     day-to-day randomness (NOISE_SD)
  * leaks:     a few multi-day events per zone add 25-60 % on top (flag is_injected_leak = True)
"""
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

LITRES_PER_PERSON = 0.55
TREND_PER_YEAR = 0.04
NOISE_SD = 0.045

# Monthly multipliers, January..December (Indian climate: summer peak, monsoon dip)
SEASON = np.array([0.92, 0.95, 1.03, 1.14, 1.22, 1.18, 0.95, 0.90, 0.93, 0.98, 0.96, 0.93])


@dataclass(frozen=True)
class Zone:
    zone_id: int
    name: str
    population: int
    # Monday..Sunday multipliers
    weekly: tuple = (1.0, 1.0, 1.0, 1.0, 1.0, 1.05, 1.08)


DEFAULT_ZONES = (
    Zone(1, "North Zone", 40000, (0.98, 0.98, 0.99, 0.99, 1.00, 1.04, 1.07)),    # mostly homes and a school
    Zone(2, "Central Zone", 65000, (1.02, 1.02, 1.02, 1.02, 1.03, 0.98, 0.92)),  # hospital, mall, offices
    Zone(3, "South Zone", 30000, (1.06, 1.06, 1.06, 1.06, 1.05, 0.90, 0.82)),    # factories: quiet weekends
)


def zones_from_rows(rows):
    """Build Zone objects from database rows (zone_id, name, population), keeping known weekly shapes."""
    known = {z.name: z for z in DEFAULT_ZONES}
    out = []
    for r in rows:
        base = known.get(r["name"])
        weekly = base.weekly if base else Zone(0, "", 1).weekly
        out.append(Zone(int(r["zone_id"]), r["name"], int(r["population"]), weekly))
    return tuple(out)


def generate_consumption(zones=DEFAULT_ZONES, days=730, end_date=None, seed=2026, leaks_per_year=6,
                         recent_leak=True):
    """Return a DataFrame with columns zone_id, recorded_on (date), litres, is_injected_leak.

    The history ends on end_date (default: yesterday), so forecasts start from today.
    recent_leak: also inject one leak in the largest zone 2 to 4 days before the end, so the dashboard
    always has a current leak alert to demonstrate (it is flagged as injected like every other leak).
    """
    rng = np.random.default_rng(seed)
    end_date = end_date or (date.today() - timedelta(days=1))
    dates = pd.date_range(end=pd.Timestamp(end_date), periods=days, freq="D")
    t_years = np.arange(days) / 365.0

    # Heat waves are city-wide: every zone feels them on the same days.
    heat = np.zeros(days)
    for _ in range(int(days / 365 * 4)):
        start = rng.integers(0, days)
        length = rng.integers(3, 8)
        heat[start:start + length] += rng.uniform(0.04, 0.10)

    frames = []
    for z in zones:
        base = z.population * LITRES_PER_PERSON
        season = SEASON[dates.month.values - 1]
        weekly = np.array(z.weekly)[dates.dayofweek.values]
        trend = 1.0 + TREND_PER_YEAR * t_years
        noise = rng.normal(0.0, NOISE_SD, days)
        litres = base * season * weekly * trend * (1.0 + heat) * (1.0 + noise)

        leak = np.zeros(days, dtype=bool)
        n_leaks = max(1, int(round(days / 365 * leaks_per_year)))
        for _ in range(n_leaks):
            start = int(rng.integers(30, days))  # keep the first month clean so detectors have history
            length = int(rng.integers(1, 5))
            extra = rng.uniform(0.25, 0.60)
            litres[start:start + length] *= 1.0 + extra
            leak[start:start + length] = True

        if recent_leak and z.population == max(x.population for x in zones):
            litres[days - 4:days - 1] *= 1.45
            leak[days - 4:days - 1] = True

        frames.append(pd.DataFrame({
            "zone_id": z.zone_id,
            "recorded_on": dates.date,
            "litres": np.round(litres, 2),
            "is_injected_leak": leak,
        }))
    return pd.concat(frames, ignore_index=True)
