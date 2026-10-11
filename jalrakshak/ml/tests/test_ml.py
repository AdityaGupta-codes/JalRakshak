"""Run from the ml/ folder:  python -m unittest discover -s tests -t . -v"""
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from jalml import anomaly, data, forecast, pipeline

END = date(2026, 10, 10)


class DataTests(unittest.TestCase):
    def test_generator_is_reproducible_and_complete(self):
        a = data.generate_consumption(days=400, end_date=END, seed=1)
        b = data.generate_consumption(days=400, end_date=END, seed=1)
        pd.testing.assert_frame_equal(a, b)
        self.assertEqual(len(a), 400 * len(data.DEFAULT_ZONES))
        self.assertEqual(max(a["recorded_on"]), END)
        self.assertTrue((a["litres"] > 0).all())
        self.assertGreater(a["is_injected_leak"].sum(), 0)

    def test_bigger_zones_use_more_water(self):
        d = data.generate_consumption(days=365, end_date=END, seed=3)
        mean = d.groupby("zone_id")["litres"].mean()
        self.assertGreater(mean[2], mean[1])  # Central (65,000 people) > North (40,000)
        self.assertGreater(mean[1], mean[3])  # North > South (30,000)

    def test_zones_from_database_rows(self):
        zones = data.zones_from_rows([{"zone_id": 9, "name": "New Zone", "population": 1000}])
        self.assertEqual((zones[0].zone_id, zones[0].population), (9, 1000))


class AnomalyTests(unittest.TestCase):
    def setUp(self):
        self.df = data.generate_consumption(days=500, end_date=END, seed=5)

    def test_detector_finds_most_leaks_with_few_false_alarms(self):
        score = anomaly.evaluate(anomaly.robust_zscore(self.df))
        self.assertGreater(score["precision"], 0.7)
        self.assertGreater(score["recall"], 0.5)

    def test_obvious_leak_is_always_found(self):
        df = data.generate_consumption(days=200, end_date=END, seed=8, leaks_per_year=0, recent_leak=False)
        df["is_injected_leak"] = False
        idx = df[(df["zone_id"] == 1)].index[150]
        df.loc[idx, "litres"] *= 2.0
        df.loc[idx, "is_injected_leak"] = True
        flagged = anomaly.robust_zscore(df)
        self.assertTrue(bool(flagged.loc[(flagged["zone_id"] == 1)].iloc[150]["is_anomaly"]))

    def test_cleaning_replaces_flagged_days_only(self):
        flagged = anomaly.robust_zscore(self.df)
        clean = anomaly.cleaned(flagged)
        changed = (clean["litres"] != flagged["litres"])
        self.assertTrue((changed <= flagged["is_anomaly"]).all())


class ForecastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.df = data.generate_consumption(days=500, end_date=END, seed=11)
        cls.clean = anomaly.cleaned(anomaly.robust_zscore(cls.df))[["zone_id", "recorded_on", "litres",
                                                                    "is_injected_leak"]]

    def test_learned_models_beat_the_baselines(self):
        scores, preds = forecast.evaluate_models(self.clean, self.df, test_days=60)
        mae = {s["model_name"]: s["mae_litres"] for s in scores}
        self.assertLess(min(mae["linear_regression"], mae["random_forest"]),
                        min(mae["seasonal_naive"], mae["moving_average"]))
        self.assertEqual(sum(s["is_selected"] for s in scores), 1)
        self.assertEqual(len(preds), 60 * len(data.DEFAULT_ZONES))

    def test_forecast_ahead_covers_every_zone_and_day(self):
        fc = forecast.forecast_ahead(self.clean, "linear_regression", horizon=5)
        self.assertEqual(len(fc), 5 * len(data.DEFAULT_ZONES))
        self.assertEqual(min(fc["target_date"]), END + timedelta(days=1))
        self.assertTrue((fc["predicted_litres"] > 0).all())


class PipelineTests(unittest.TestCase):
    def test_alerts_merge_consecutive_days(self):
        flagged = pd.DataFrame({
            "zone_id": [2, 2, 2, 2], "recorded_on": [END - timedelta(days=k) for k in (5, 4, 3, 1)],
            "litres": [150.0, 160, 140, 130], "expected_litres": [100.0, 100, 100, 100],
            "excess": [0.5, 0.6, 0.4, 0.3], "is_anomaly": [True, True, True, True],
            "is_injected_leak": [True] * 4})
        preds = pd.DataFrame({"zone_id": [2], "target_date": [END + timedelta(days=1)],
                              "predicted_litres": [100.0], "model_name": ["x"]})
        alerts = pipeline.build_alerts(flagged, preds, data.DEFAULT_ZONES, END + timedelta(days=1))
        leaks = [a for a in alerts if a["alert_type"] == "anomaly"]
        self.assertEqual(len(leaks), 2)                     # 3-day run + single day
        self.assertEqual(leaks[0]["severity"], "critical")  # peak 60 %
        self.assertEqual(leaks[1]["severity"], "warning")   # 30 %

    def test_offline_run_writes_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = pipeline.run(None, out=tmp, days=400, end_date=END, log=lambda *_: None)
            metrics = json.loads((Path(tmp) / "metrics.json").read_text())
            self.assertIn(metrics["selected_model"], forecast.MODELS)
            snap = json.loads((Path(tmp) / "forecast_snapshot.json").read_text())
            self.assertEqual(len(snap["predictions"]), forecast.HORIZON * len(data.DEFAULT_ZONES))
            self.assertTrue(any(a["alert_type"] == "anomaly" for a in res["alerts"]))  # the recent leak


if __name__ == "__main__":
    unittest.main()
