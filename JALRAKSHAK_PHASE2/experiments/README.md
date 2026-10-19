# Experiments

`run_experiments.py` generates random scenarios (10 requests each: hospitals, schools, households, shops, factories, parks across three zones) and runs all four algorithms on the same scenarios. Scarcity is the ratio of water available to water requested: **normal** 1.2, **moderate** 0.75, **severe** 0.5. Each row of `results.csv` is the average over 200 scenarios (seed 2026).

    cd experiments
    python run_experiments.py --bin ../scheduler/build/jalrakshak-scheduler

Options: `--trials N`, `--seed S`, `--floor F` (Water-aware guarantee floor), `--window M` (batch window in minutes for every algorithm, or `all` to wait for the last arrival, which is what the backend does by default).

## Results (default settings, 200 scenarios per level)

| Scenario | Rule | Avg wait (min) | Fairness (Jain) | Importance-weighted delivery | Requests left with nothing | Missed deadlines |
|---|---|---|---|---|---|---|
| moderate scarcity | First come, first served | 42.7 | 0.77 | 0.74 | 2.14 | 1.05 |
| moderate scarcity | Priority | 36.8 | 0.73 | 0.85 | 2.46 | 0.91 |
| moderate scarcity | Round robin | 13.1 | 0.94 | 0.83 | 0.00 | 2.06 |
| moderate scarcity | Water-aware | 48.4 | 0.86 | 0.86 | 0.00 | 1.19 |
| severe scarcity | First come, first served | 26.1 | 0.52 | 0.50 | 4.63 | 0.38 |
| severe scarcity | Priority | 21.1 | 0.53 | 0.64 | 4.46 | 0.29 |
| severe scarcity | Round robin | 12.8 | 0.79 | 0.58 | 0.24 | 0.78 |
| severe scarcity | Water-aware | 27.7 | 0.74 | 0.56 | 0.00 | 0.36 |

"Missed deadlines" counts served requests whose water finished after the time they needed it by (deadlines
are soft: a late request is still served). Averages per scenario of 10 requests.

When water is plentiful (normal) every rule serves everyone in full and the rules tie on every measure except waiting time.

## What the numbers say

- **Water-aware leaves nobody empty-handed** (0 requests) under both kinds of scarcity, where first come first served and Priority leave 2 to 5 requests with nothing.
- **At moderate scarcity** Water-aware has the best importance-weighted delivery of the four rules, and is much fairer than FCFS and Priority.
- **Round robin is the fairest rule** (higher Jain index) and has by far the shortest waits, because it never makes anyone wait for a large request. It does not consider importance at all.
- **At severe scarcity Priority delivers more to important users** than Water-aware, because Water-aware deliberately spends part of the water on guaranteed minimum shares.
- Water-aware's average wait is similar to or longer than FCFS. It is not the fastest rule.

So Water-aware is not best on every measure. It is a middle path between Priority (favours the important, ignores the rest) and Round robin (favours everyone equally, ignores importance), and the guarantee floor lets you choose where on that line to sit.

## Tuning the guarantee floor

`guarantee_floor` is the minimum share of its request that every waiting request is guaranteed. Averages over 100 scenarios (tuning seed 7), Water-aware only:

| Floor | Moderate: importance-weighted | Moderate: fairness | Moderate: left with nothing | Severe: importance-weighted | Severe: fairness | Severe: left with nothing |
|---|---|---|---|---|---|---|
| 0.0 | 0.855 | 0.78 | 1.97 | 0.648 | 0.54 | 4.29 |
| 0.1 | 0.852 | 0.80 | 1.50 | 0.627 | 0.60 | 3.16 |
| 0.2 | 0.850 | 0.83 | 0.00 | 0.582 | 0.65 | 0.00 |
| **0.3 (default)** | 0.845 | 0.85 | 0.00 | 0.544 | 0.74 | 0.00 |
| 0.4 | 0.836 | 0.88 | 0.00 | 0.515 | 0.86 | 0.00 |
| 0.5 | 0.821 | 0.91 | 0.00 | 0.500 | 1.00 | 0.00 |

There is no setting that wins on everything: a higher floor buys fairness and costs importance-weighted delivery. Of the values tried, 0.2 is the smallest floor that leaves nobody with nothing; 0.3 was kept as a balanced default and was not tuned further. Decide the floor by policy: how much should a park or a factory be guaranteed while a hospital is waiting?

## Batch window

`results_window60.csv` repeats the experiment with `--window 60`: every rule waits 60 minutes after the first request before deciding, so late arrivals are ranked together with early ones. Waiting times grow by roughly 40 to 60 minutes for every rule. At severe scarcity Water-aware's importance-weighted delivery improves (0.56 to 0.62) and so does Priority's (0.64 to 0.68), so Priority still leads on that measure.

## Waiting for every request ("all", the backend default)

`results_window_all.csv` makes every rule wait until the last request of the scenario has arrived (up to an
hour), so all requests are ranked together. This is what the backend does when the operator clicks Apply,
because by then every pending request is already waiting.

| Scenario | Rule | Avg wait (min) | Fairness | Importance-weighted | Left with nothing | Missed deadlines |
|---|---|---|---|---|---|---|
| moderate scarcity | Priority | 88.7 | 0.72 | 0.85 | 2.60 | 2.37 |
| moderate scarcity | Round robin | 48.2 | 0.95 | 0.85 | 0.00 | 4.28 |
| moderate scarcity | Water-aware | 102.0 | 0.86 | **0.87** | 0.00 | 3.06 |
| severe scarcity | Priority | 70.8 | 0.52 | **0.68** | 4.54 | 1.31 |
| severe scarcity | Round robin | 48.2 | 0.86 | 0.60 | 0.00 | 3.15 |
| severe scarcity | Water-aware | 81.3 | 0.74 | 0.63 | 0.00 | 2.25 |

The ranking does not change: Water-aware still has the best importance-weighted delivery at moderate
scarcity and still leaves nobody empty-handed, Priority still leads on importance at severe scarcity, and
Round robin is still the fairest and fastest. Waiting is longer for every rule, because the clock starts
at the first arrival, and so more deadlines are missed. In a real system the deciding moment would be
when the operator acts, not when the first request came in.

In the dashboard's sample scenario the effect is larger: without the wait, Water-aware gave the factory that
arrived first its full 20,000 L before the hospital had even arrived (importance-weighted delivery 0.56);
with it, the factory gets 6,000 L, the hospital and both housing colonies are served in full, and the
figure rises to 0.68.

## Limits of these results

The scenarios are randomly generated, not real municipal data, and the numbers depend on the generator's choices (request sizes, arrival times, priorities). They show how the rules behave relative to each other, not how a real city would fare.
