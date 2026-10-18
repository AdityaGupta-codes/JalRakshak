# Data science module

Forecasts how much water each zone will need, finds leaks, and feeds both into the rest of JalRakshak:

- **Forecasts** go into the `predictions` table. The backend adds up today's forecast for all zones and
  gives it to the scheduler as `predicted_demand_litres`, which drives the Water-aware **scarcity** factor.
- **Leak alerts** go into the `alerts` table and appear on the dashboard's *Forecast and alerts* tab.
- **Model scores** go into `model_evaluations`. The most accurate model is marked `is_selected`.

## Running it

`docker compose up --build` runs it automatically once the database is ready (the `ml` service starts,
fills the tables and exits). To run it again by hand:

    docker compose run --rm ml

Without Docker (Python 3.10+):

    cd ml
    pip install -r requirements.txt
    python -m jalml.pipeline --offline                    # results files only, no database
    python -m jalml.pipeline --database-url postgresql://postgres:jal123@localhost:5432/jalrakshak
    python -m unittest discover -s tests -t . -v          # 10 tests

Results land in `ml/results/`: `metrics.json`, `forecast_test.png`, `leak_detection.png` and
`forecast_snapshot.json` (used by the demo API in `frontend/dev/mock_api.py`).

## The data

Real meter readings were not available, so `jalml/data.py` generates **two years of daily consumption**
for the three zones in the database, on the reservoir's scale (about 75,000 L a day in total). It
contains the patterns a forecaster should learn:

| Pattern | How it is built |
|---|---|
| Zone size | 0.55 L per resident per day from this reservoir |
| Season | hot, dry April to June up to 22 % above average; July to September monsoon below |
| Weekday | homes use more at weekends; the factory zone uses about 20 % less |
| Trend | 4 % growth a year |
| Heat waves | about four a year, 3 to 7 days, city-wide, +4 to 10 % |
| Noise | 4.5 % day-to-day randomness |
| **Leaks** | about 6 a year per zone, 1 to 4 days, +25 to 60 %, with **known dates** (ground truth) |

One extra leak is always placed in Central Zone 2 to 4 days before today, so the dashboard has a
current alert to demonstrate. It is marked as injected like the others.

## Leak detection

**Robust z-score (used):** "normal" for a day is the median of the same weekday in the previous four
weeks. A day is flagged when its use is more than 3 robust standard deviations above normal (and at
least 15 % above). Medians and median absolute deviations are used because an earlier leak would
distort an ordinary mean and standard deviation.

**Isolation forest (compared):** scikit-learn's unsupervised outlier model on the same features.

Day-level results against the injected leaks (2 years, 3 zones, seed 2026, 92 leak days):

| Detector | Precision | Recall | F1 | Leak days found | False alarms |
|---|---|---|---|---|---|
| **Robust z-score** | 0.88 | 0.66 | 0.76 | 61 of 92 | 8 |
| Isolation forest | 1.00 | 0.43 | 0.61 | 40 of 92 | 0 |

The threshold (3) was chosen on a **different** generated dataset (seed 7) so it was not tuned on the
data it is scored on:

| Threshold (std devs) | 2.0 | 2.5 | **3.0** | 3.5 | 4.0 |
|---|---|---|---|---|---|
| Precision | 0.69 | 0.74 | **0.80** | 0.88 | 0.97 |
| Recall | 0.92 | 0.88 | **0.83** | 0.76 | 0.70 |
| F1 | 0.79 | 0.80 | **0.82** | 0.81 | 0.81 |

Across five more datasets (seeds 1 to 4) precision stays between 0.79 and 0.95 and recall between 0.75
and 0.94. Seed 2026 is the hardest of them for recall.

**Where it misses** (see `results/leak_detection.png`): small leaks (+25 %) during the drop at the start
of the monsoon, when "the same weekday in recent weeks" is still the high summer level; and the later
days of a long leak, once the leak itself has entered the comparison weeks.

## Forecasting

Each model predicts one zone's litres for a day from what is known the evening before: use 1, 7 and 14
days earlier, the 7- and 28-day averages, the weekday, the time of year, and the zone.

- **Baselines:** "same weekday last week" and "7-day average" (no learning)
- **Linear regression** and **Random forest** (scikit-learn, 300 trees)

Training uses the history with flagged leak days replaced by their normal value. Scores are on the last
**90 days**, held out, excluding injected leak days (a leak cannot be forecast; the detector reports it).

| Model | Mean absolute error | RMSE | MAPE |
|---|---|---|---|
| Same weekday last week (baseline) | 1,849 L | 3,009 L | 7.5 % |
| 7-day average (baseline) | 1,735 L | 2,369 L | 7.8 % |
| Linear regression | 1,461 L | 1,946 L | 6.1 % |
| **Random forest (selected)** | **1,223 L** | **1,606 L** | **5.1 %** |

**Is this robust?** On five generated datasets (seeds 2026 and 1 to 4) Random forest had the lowest
error every time (1,097 to 1,275 L), Linear regression was second every time, and both learned models
beat both baselines.

**Does cleaning the leaks help?** Yes. Trained on the raw history, Random forest's error rises from
1,223 L to 1,380 L (seed 2026), and it is higher on all five datasets.

The next 7 days are forecast recursively (each predicted day becomes the "yesterday" of the next).

## Limits

The data is synthetic, so these numbers show the method works on the patterns we built in, not how
accurate it would be for a real city. The generator and the detector were written by the same team, so
the detector "knows" what a leak looks like. A real deployment would need real meter data, and
probably weather as an input.
