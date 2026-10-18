"""Daily demand forecasting per zone: two simple baselines against Linear Regression and Random Forest.

Every model predicts a day's litres for one zone from information available the evening before:
    lag_1, lag_7, lag_14       litres 1, 7 and 14 days earlier
    mean_7, mean_28            average of the previous 7 and 28 days
    weekday (one-hot), season (sine/cosine of day of year), zone (one-hot)

Baselines (no learning):
    seasonal_naive   = lag_7               ("same as the same weekday last week")
    moving_average   = mean_7

Evaluation: the last TEST_DAYS days are held out. Models are trained on everything before, then scored on
one-day-ahead predictions (yesterday's real value is known each evening). Injected leak days are left out
of the score, because a leak cannot be forecast; they are reported by the anomaly detector instead.

The selected model (lowest mean absolute error) produces the next HORIZON days recursively: each
predicted day becomes the lag for the next one.
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression

TEST_DAYS = 90
HORIZON = 7
MODELS = ("seasonal_naive", "moving_average", "linear_regression", "random_forest")
MODEL_LABELS = {
    "seasonal_naive": "Same weekday last week (baseline)",
    "moving_average": "7-day average (baseline)",
    "linear_regression": "Linear regression",
    "random_forest": "Random forest",
}


def _features_for_series(s, dates, zone_id, zone_ids):
    """Feature rows for one zone. s: litres (pd.Series aligned with dates)."""
    f = pd.DataFrame(index=s.index)
    f["lag_1"] = s.shift(1)
    f["lag_7"] = s.shift(7)
    f["lag_14"] = s.shift(14)
    f["mean_7"] = s.shift(1).rolling(7).mean()
    f["mean_28"] = s.shift(1).rolling(28).mean()
    d = pd.to_datetime(pd.Series(dates, index=s.index))
    for k in range(7):
        f[f"dow_{k}"] = (d.dt.dayofweek == k).astype(float)
    doy = d.dt.dayofyear.astype(float)
    f["season_sin"] = np.sin(2 * np.pi * doy / 365.25)
    f["season_cos"] = np.cos(2 * np.pi * doy / 365.25)
    for z in zone_ids:
        f[f"zone_{z}"] = float(z == zone_id)
    return f


def build_dataset(df):
    """All zones stacked: features X, target y and bookkeeping columns."""
    zone_ids = sorted(df["zone_id"].unique())
    rows = []
    for z, g in df.sort_values(["zone_id", "recorded_on"]).groupby("zone_id"):
        g = g.reset_index(drop=True)
        f = _features_for_series(g["litres"].astype(float), g["recorded_on"], z, zone_ids)
        f["y"] = g["litres"].astype(float)
        f["zone_id"] = z
        f["recorded_on"] = g["recorded_on"]
        f["is_injected_leak"] = g.get("is_injected_leak", False)
        rows.append(f)
    data = pd.concat(rows, ignore_index=True).dropna()
    feature_cols = [c for c in data.columns if c not in ("y", "zone_id", "recorded_on", "is_injected_leak")]
    return data, feature_cols


def _fit(name, X, y, seed):
    if name == "linear_regression":
        return LinearRegression().fit(X, y)
    if name == "random_forest":
        return RandomForestRegressor(n_estimators=300, min_samples_leaf=3, max_features=0.6,
                                     random_state=seed, n_jobs=-1).fit(X, y)
    return None  # baselines need no training


def _predict(name, model, X):
    if name == "seasonal_naive":
        return X["lag_7"].values
    if name == "moving_average":
        return X["mean_7"].values
    return model.predict(X)


def evaluate_models(train_df, truth_df=None, test_days=TEST_DAYS, seed=2026):
    """Train on all but the last test_days, score one-day-ahead predictions on those days.

    train_df: series used for features and training (normally leak-cleaned).
    truth_df: real series to score against (default: train_df). Its is_injected_leak column marks days
              that are excluded from the score.
    Returns (scores list, predictions DataFrame for the test period).
    """
    data, cols = build_dataset(train_df)
    truth = (truth_df if truth_df is not None else train_df)[["zone_id", "recorded_on", "litres", "is_injected_leak"]]
    data = data.drop(columns=["is_injected_leak"]).merge(truth, on=["zone_id", "recorded_on"], how="left")
    cutoff = sorted(data["recorded_on"].unique())[-test_days]
    train, test = data[data["recorded_on"] < cutoff], data[data["recorded_on"] >= cutoff]
    scored = test[~test["is_injected_leak"].astype(bool)]

    scores, preds = [], {"zone_id": test["zone_id"].values, "recorded_on": test["recorded_on"].values,
                         "actual": test["litres"].values}
    for name in MODELS:
        model = _fit(name, train[cols], train["y"], seed)
        p_all = _predict(name, model, test[cols])
        preds[name] = p_all
        p = _predict(name, model, scored[cols])
        err = scored["litres"].values - p
        scores.append({
            "model_name": name,
            "label": MODEL_LABELS[name],
            "mae_litres": round(float(np.mean(np.abs(err))), 2),
            "rmse_litres": round(float(np.sqrt(np.mean(err ** 2))), 2),
            "mape": round(float(np.mean(np.abs(err) / scored["litres"].values)), 4),
            "test_days": int(test_days),
        })
    best = min(scores, key=lambda s: s["mae_litres"])
    for s in scores:
        s["is_selected"] = s is best
    return scores, pd.DataFrame(preds)


def forecast_ahead(df, model_name, horizon=HORIZON, seed=2026):
    """Train model_name on the whole (cleaned) history, then forecast the next `horizon` days per zone."""
    data, cols = build_dataset(df)
    model = _fit(model_name, data[cols], data["y"], seed)
    zone_ids = sorted(df["zone_id"].unique())
    history = {z: g.sort_values("recorded_on")[["recorded_on", "litres"]].reset_index(drop=True)
               for z, g in df.groupby("zone_id")}
    out = []
    last = max(df["recorded_on"])
    for step in range(1, horizon + 1):
        day = pd.Timestamp(last) + pd.Timedelta(days=step)
        for z in zone_ids:
            h = history[z]
            ext = pd.concat([h, pd.DataFrame({"recorded_on": [day.date()], "litres": [np.nan]})],
                            ignore_index=True)
            f = _features_for_series(ext["litres"].astype(float), ext["recorded_on"], z, zone_ids).iloc[[-1]]
            value = float(_predict(model_name, model, f[cols])[0])
            history[z] = pd.concat([h, pd.DataFrame({"recorded_on": [day.date()], "litres": [value]})],
                                   ignore_index=True)
            out.append({"zone_id": int(z), "target_date": day.date(), "predicted_litres": round(value, 2),
                        "model_name": model_name})
    return pd.DataFrame(out)
