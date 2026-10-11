import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import { ALERT_TYPE, fmtDay, fmtL, fmtPct, modelName } from "./format.js";
import { Empty } from "./ui.jsx";

const W = 380;
const H = 190;
const PAD = { l: 34, r: 8, t: 20, b: 22 };
const CHART_DAYS = 28;

// Last weeks of real use (solid) followed by the 7-day forecast (dashed), for one zone.
function ZoneChart({ zone, history, predictions }) {
  const pts = [
    ...history.map((h) => ({ date: String(h.date), v: Number(h.litres), f: false })),
    ...predictions.map((p) => ({ date: String(p.target_date), v: Number(p.predicted_litres), f: true })),
  ];
  if (pts.length < 2) return null;
  const vals = pts.map((p) => p.v);
  const lo = Math.min(...vals) * 0.92;
  const hi = Math.max(...vals) * 1.04;
  const x = (i) => PAD.l + (i / (pts.length - 1)) * (W - PAD.l - PAD.r);
  const y = (v) => PAD.t + (1 - (v - lo) / (hi - lo)) * (H - PAD.t - PAD.b);
  const split = history.length; // index of the first forecast day
  const line = (from, to) =>
    pts.slice(from, to).map((p, k) => `${k ? "L" : "M"}${x(from + k).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
  const ticks = [lo + (hi - lo) * 0.1, (lo + hi) / 2, hi - (hi - lo) * 0.1];
  const nextWeek = predictions.reduce((s, p) => s + Number(p.predicted_litres), 0) / Math.max(1, predictions.length);
  const recent = history.slice(-28);
  const before = recent.reduce((s, h) => s + Number(h.litres), 0) / Math.max(1, recent.length);
  const change = before > 0 ? nextWeek / before - 1 : 0;
  const trend = Math.abs(change) < 0.01
    ? "about the same as the last 4 weeks"
    : `${change > 0 ? "+" : "−"}${fmtPct(Math.abs(change))} on the last 4 weeks`;

  return (
    <figure className="zone-chart">
      <figcaption>
        <span className="strong">{zone.name}</span>
        <span className="sub">
          Next 7 days: about {fmtL(nextWeek)} a day, {trend}
        </span>
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img"
        aria-label={`${zone.name}: daily use for ${history.length} days and a ${predictions.length}-day forecast averaging ${Math.round(nextWeek)} litres a day`}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)} className="grid" />
            <text x={PAD.l - 6} y={y(t) + 4} textAnchor="end" className="tick">{Math.round(t / 1000)}k</text>
          </g>
        ))}
        {split < pts.length && (
          <>
            <rect x={x(split) - 3} y={PAD.t} width={W - PAD.r - x(split) + 3} height={H - PAD.t - PAD.b} className="future" />
            <text x={(x(split) + W - PAD.r) / 2} y={PAD.t - 6} textAnchor="middle" className="tick">next 7 days</text>
          </>
        )}
        <path d={line(0, split)} className="actual" />
        {split < pts.length && <path d={line(split - 1, pts.length)} className="predicted" />}
        {pts.map((p, i) => (
          <circle key={p.date} cx={x(i)} cy={y(p.v)} r={p.f ? 3 : 1.6} className={p.f ? "dot-f" : "dot-a"}>
            <title>{`${fmtDay(p.date)}: ${fmtL(p.v)}${p.f ? " (forecast)" : ""}`}</title>
          </circle>
        ))}
        <text x={PAD.l} y={H - 6} className="tick">{fmtDay(pts[0].date)}</text>
        {split < pts.length && <text x={x(split)} y={H - 6} textAnchor="middle" className="tick">today</text>}
      </svg>
    </figure>
  );
}

function Alerts({ alerts, zones, onResolve }) {
  if (alerts.length === 0) {
    return <Empty title="No active alerts.">Leaks, low water and demand above supply will show up here.</Empty>;
  }
  const zoneName = (id) => (zones.find((z) => z.zone_id === id) || {}).name;
  return (
    <ul className="alerts">
      {alerts.map((a, i) => (
        <li key={a.alert_id ?? `live-${i}`} className={`alert alert-${a.severity}`}>
          <div className="alert-head">
            <span className={`badge sev-${a.severity}`}>{a.severity === "critical" ? "Critical" : a.severity === "warning" ? "Warning" : "Notice"}</span>
            <span className="strong">{ALERT_TYPE[a.alert_type] || a.alert_type}</span>
            {a.zone_id != null && <span className="muted">{zoneName(a.zone_id)}</span>}
          </div>
          <p>{a.message}</p>
          {a.live ? (
            <p className="sub">Clears by itself when the situation changes.</p>
          ) : (
            <button type="button" className="btn btn-quiet" onClick={() => onResolve(a)}>Mark as handled</button>
          )}
        </li>
      ))}
    </ul>
  );
}

export default function Forecast({ status, notify, onAlertsChanged }) {
  const [fc, setFc] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      const [f, a] = await Promise.all([api.forecast(60), api.alerts()]);
      setFc(f);
      setAlerts(a);
      setError(null);
      onAlertsChanged(a);
    } catch (e) {
      setError(e.message);
    }
  }, [onAlertsChanged]);

  const available = status ? status.available_litres : null;
  useEffect(() => {
    load();
  }, [load, available]);

  const resolve = async (alert) => {
    try {
      await api.resolveAlert(alert.alert_id);
      notify("Alert marked as handled.");
      load();
    } catch (e) {
      notify(e.message, true);
    }
  };

  if (error) return <p className="form-error" role="alert">{error}</p>;
  if (!fc) return <p className="muted">Loading forecasts…</p>;

  const zones = fc.zones || [];
  const best = (fc.models || []).find((m) => m.is_selected);

  return (
    <div className="forecast">
      <section aria-labelledby="alerts-title">
        <div className="section-head">
          <h2 id="alerts-title">Alerts</h2>
          <button type="button" className="btn" onClick={load}>Refresh</button>
        </div>
        <Alerts alerts={alerts} zones={zones} onResolve={resolve} />
      </section>

      <section aria-labelledby="fc-title">
        <h2 id="fc-title">Demand forecast</h2>
        {!fc.available ? (
          <Empty title="No forecast yet.">
            The data-science pipeline has not run. With Docker it runs automatically on start-up; by hand:
            <code> docker compose run --rm ml</code>
          </Empty>
        ) : (
          <>
            <p className="lede">
              Expected use today across all zones: <span className="strong">{fmtL(fc.today_litres)}</span>
              {available != null && <> against {fmtL(available)} in the reservoir</>}.
              {fc.used_by_scheduler && " The Water-aware rule uses this figure to judge how scarce water is."}
              {best && <> Forecasts come from the most accurate model, <span className="strong">{modelName(best.model_name)}</span>.</>}
            </p>
            <div className="zone-charts">
              {zones.map((z) => (
                <ZoneChart
                  key={z.zone_id}
                  zone={z}
                  history={fc.history.filter((h) => h.zone_id === z.zone_id).slice(-CHART_DAYS)}
                  predictions={fc.predictions.filter((p) => p.zone_id === z.zone_id)}
                />
              ))}
            </div>
            <p className="note">Solid line: measured daily use over the last 4 weeks. Dashed: forecast. Hover a point for the exact figure.</p>

            <h3 className="sub-title">How accurate each model was</h3>
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th scope="col">Model</th>
                    <th scope="col" className="num">Typical error a day</th>
                    <th scope="col" className="num">Error as % of use</th>
                    <th scope="col">Used</th>
                  </tr>
                </thead>
                <tbody>
                  {fc.models.map((m) => (
                    <tr key={m.model_name}>
                      <td>{modelName(m.model_name)}</td>
                      <td className="num">{fmtL(m.mae_litres)}</td>
                      <td className="num">{(Number(m.mape) * 100).toFixed(1)}%</td>
                      <td>{m.is_selected ? <span className="badge badge-approved">Selected</span> : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="note">
              Scored on the last {best ? best.test_days : 90} days, which the models never saw while training.
              Typical error is the mean absolute error per zone per day.
            </p>
          </>
        )}
      </section>
    </div>
  );
}
