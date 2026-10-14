"""Leak / anomaly detection on daily consumption.

Two detectors are compared against the injected leaks (ground truth):

robust_zscore (the one used for alerts)
    expected  = median of the same weekday in the previous 4 weeks (so weekly patterns are not "anomalies")
    excess    = litres / expected - 1
    spread    = median absolute deviation of `excess` over the previous 8 weeks (robust to earlier leaks)
    flag the day when excess > max(MIN_EXCESS, K * 1.4826 * spread)

isolation_forest (scikit-learn, for comparison)
    an unsupervised outlier model on the same `excess` feature plus weekday and recent-trend features.
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

K = 3.0            
MIN_EXCESS = 0.15  


def _per_zone(df):
    for zone_id, g in df.sort_values(["zone_id", "recorded_on"]).groupby("zone_id", sort=True):
        yield zone_id, g.reset_index(drop=True)


def excess_over_normal(g):
    """Expected litres (same weekday, previous 4 weeks) and the relative excess for one zone's series."""
    s = g["litres"].astype(float)
    same_weekday = pd.concat([s.shift(7 * k) for k in (1, 2, 3, 4)], axis=1)
    expected = same_weekday.median(axis=1)
    excess = s / expected - 1.0
    return expected, excess


def robust_zscore(df):
    """Return df with columns expected_litres, excess, is_anomaly (bool) added."""
    parts = []
    for _, g in _per_zone(df):
        expected, excess = excess_over_normal(g)
        med = excess.shift(1).rolling(56, min_periods=21).median()
        mad = (excess.shift(1) - med).abs().rolling(56, min_periods=21).median()
        threshold = np.maximum(MIN_EXCESS, K * 1.4826 * mad)
        g = g.assign(expected_litres=expected, excess=excess,
                     is_anomaly=(excess > threshold).fillna(False).astype(bool))
        parts.append(g)
    return pd.concat(parts, ignore_index=True)


def isolation_forest(df, contamination=0.02, seed=0):
    parts = []
    for _, g in _per_zone(df):
        expected, excess = excess_over_normal(g)
        g = g.assign(expected_litres=expected, excess=excess)
        feats = pd.DataFrame({
            "excess": excess,
            "dow": pd.to_datetime(g["recorded_on"]).dt.dayofweek,
            "trend": g["litres"].astype(float) / g["litres"].astype(float).shift(1).rolling(28).mean() - 1,
        })
        ok = feats.notna().all(axis=1)
        flags = np.zeros(len(g), dtype=bool)
        if ok.sum() > 50:
            model = IsolationForest(contamination=contamination, random_state=seed, n_estimators=200)
            pred = model.fit_predict(feats[ok])
            # only unusually HIGH use is a leak; unusually low use is not reported
            flags[np.where(ok)[0]] = (pred == -1) & (feats.loc[ok, "excess"].values > 0)
        parts.append(g.assign(is_anomaly=flags))
    return pd.concat(parts, ignore_index=True)


def evaluate(flagged, truth_col="is_injected_leak"):
    """Day-level precision, recall and F1 against the injected leaks (first 4 weeks excluded: no history)."""
    d = flagged.dropna(subset=["expected_litres"])
    pred, truth = d["is_anomaly"].astype(bool), d[truth_col].astype(bool)
    tp = int((pred & truth).sum())
    fp = int((pred & ~truth).sum())
    fn = int((~pred & truth).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"true_positives": tp, "false_positives": fp, "missed": fn,
            "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}


def cleaned(flagged):
    """Replace flagged days by their expected value, so leaks do not teach the forecaster wrong habits."""
    out = flagged.copy()
    mask = out["is_anomaly"] & out["expected_litres"].notna()
    out.loc[mask, "litres"] = out.loc[mask, "expected_litres"]
    return out
