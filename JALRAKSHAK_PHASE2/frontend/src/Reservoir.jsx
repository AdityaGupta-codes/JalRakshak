import { fmtL, fmtNum } from "./format.js";

const SCARCITY_TEXT = {
  ok: "There is enough water for every pending request.",
  moderate: "Water is short: not every pending request can be served in full.",
  severe: "Water is very short: pending requests ask for far more than is available.",
};

// The reservoir drawn on one scale: capacity (track), available water (fill), pending demand (marker),
// and the shortfall between them (hatched).
export default function Reservoir({ status }) {
  if (!status) return <section className="reservoir reservoir-loading" aria-busy="true" />;

  const cap = status.capacity_litres || 1;
  const avail = status.available_litres;
  const demand = status.pending_demand_litres;
  const clamp = (v) => Math.max(0, Math.min(100, v));
  const availPct = clamp((avail / cap) * 100);
  const demandPct = clamp((demand / cap) * 100);
  const short = Math.max(0, demand - avail);
  const level = status.scarcity || "ok";
  const idle = status.pending_count === 0;
  const label =
    `${status.name}: ${fmtNum(avail)} litres available out of ${fmtNum(cap)} capacity. ` +
    `Pending requests ask for ${fmtNum(demand)} litres.`;

  return (
    <section className={`reservoir level-${level}`} aria-labelledby="res-title">
      <div className="reservoir-head">
        <div>
          <h1 id="res-title" className="res-name">{status.name}</h1>
          <p className="res-sub">{idle ? "No requests are waiting for water." : SCARCITY_TEXT[level]}</p>
        </div>
        <dl className="res-figures">
          <div>
            <dt>Available now</dt>
            <dd>{fmtL(avail)}</dd>
          </div>
          <div>
            <dt>Requested ({status.pending_count} pending)</dt>
            <dd>{fmtL(demand)}</dd>
          </div>
          {status.forecast_today_litres != null && (
            <div>
              <dt>Forecast use today</dt>
              <dd>{fmtL(status.forecast_today_litres)}</dd>
            </div>
          )}
          <div className={short > 0 ? "short" : "enough"}>
            <dt>{short > 0 ? "Shortfall" : "Surplus"}</dt>
            <dd>{fmtL(short > 0 ? short : avail - demand)}</dd>
          </div>
        </dl>
      </div>

      <div className="gauge" role="img" aria-label={label}>
        <div className="gauge-track">
          <div className="gauge-fill" style={{ width: `${availPct}%` }} />
          {short > 0 && (
            <div className="gauge-short" style={{ left: `${availPct}%`, width: `${Math.max(0, demandPct - availPct)}%` }} />
          )}
          {demand > 0 && <div className="gauge-demand" style={{ left: `${demandPct}%` }} />}
        </div>
        <div className="gauge-scale">
          <span>0</span>
          <span>Capacity {fmtL(cap)}</span>
        </div>
      </div>
    </section>
  );
}
