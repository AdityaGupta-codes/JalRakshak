export const fmtL = (v) => `${Math.round(Number(v) || 0).toLocaleString("en-US")} L`;
export const fmtNum = (v) => Math.round(Number(v) || 0).toLocaleString("en-US");
export const fmtPct = (v) => `${Math.round((Number(v) || 0) * 100)}%`;
export const fmtMin = (v) => `${Math.round(Number(v) || 0)} min`;

export function fmtTime(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function fmtDateTime(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

export const USER_TYPE = {
  hospital: "Hospital", domestic: "Households", school: "School",
  industry: "Industry", commercial: "Commercial", park: "Park",
};
export const typeName = (t) => USER_TYPE[t] || t;

export const ALGOS = [
  { id: "fcfs", name: "First come, first served",
    blurb: "Serves requests in the order they arrived." },
  { id: "priority", name: "Priority",
    blurb: "Serves the most important users first." },
  { id: "round_robin", name: "Round robin",
    blurb: "Hands out water in small turns, so everyone gets some." },
  { id: "water_aware", name: "Water-aware",
    blurb: "Scores each request on importance, urgency, scarcity, people affected and fairness, and guarantees everyone a minimum share." },
];
export const algoName = (id) => (ALGOS.find((a) => a.id === id) || { name: id }).name;

// better: which direction is good. Used to highlight the best algorithm in each row.
export const METRICS = [
  { key: "avg_wait_time", label: "Average wait", better: "low", fmt: (v) => fmtMin(v),
    hint: "Minutes from a request arriving until its water starts." },
  { key: "utilization", label: "Water used", better: "high", fmt: fmtPct,
    hint: "Share of the available water that was handed out." },
  { key: "satisfaction_ratio", label: "Litres delivered", better: "high", fmt: fmtPct,
    hint: "Litres delivered divided by litres requested." },
  { key: "priority_weighted_satisfaction", label: "Importance-weighted delivery", better: "high", fmt: fmtPct,
    hint: "Like the line above, but important users count more." },
  { key: "jain_fairness", label: "Fairness", better: "high", fmt: (v) => Number(v).toFixed(2),
    hint: "Jain index. 1.00 means every request got the same share of what it asked for." },
  { key: "unserved_count", label: "Requests left with nothing", better: "low", fmt: (v) => String(Math.round(v)),
    hint: "Requests that received no water at all." },
  { key: "deadline_miss_count", label: "Missed deadlines", better: "low", fmt: (v) => String(Math.round(v || 0)),
    hint: "Requests whose water finished after the time they needed it by." },
];

export const fmtDay = (iso) => {
  const d = new Date(`${String(iso).slice(0, 10)}T12:00:00`);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString([], { day: "numeric", month: "short" });
};

export const MODEL_NAME = {
  seasonal_naive: "Same weekday last week",
  moving_average: "7-day average",
  linear_regression: "Linear regression",
  random_forest: "Random forest",
};
export const modelName = (id) => MODEL_NAME[id] || id;

export const ALERT_TYPE = { low_water: "Low water", high_demand: "Demand", anomaly: "Possible leak" };
