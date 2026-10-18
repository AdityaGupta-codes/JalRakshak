import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import { fmtDateTime, fmtL } from "./format.js";
import { ConfirmButton, Empty } from "./ui.jsx";

const TT_STATUS = { draft: "Draft", published: "Published", withdrawn: "Withdrawn" };
const TRIP_STATUS = { planned: "Planned", dispatched: "On the way", delivered: "Delivered", cancelled: "Cancelled" };
const loadsText = (t) => `${t.loads} load${t.loads === 1 ? "" : "s"}`;
const today = () => new Date().toISOString().slice(0, 10);

// ---------------------------------------------------------------- zone map (GeoJSON drawn as SVG)
function rings(geo) {
  if (!geo) return [];
  const polys = geo.type === "Polygon" ? [geo.coordinates] : geo.coordinates;
  return polys.map((p) => p[0]);
}

export function ZoneMap({ zones }) {
  const shapes = zones.filter((z) => z.boundary);
  if (shapes.length === 0) {
    return <Empty title="No zone boundaries yet.">Upload each zone's boundary (GeoJSON) in Settings › Supply network.</Empty>;
  }
  const pts = shapes.flatMap((z) => rings(z.boundary).flat());
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const [minX, maxX, minY, maxY] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const W = 600, H = 400, pad = 20;
  const k = Math.min((W - 2 * pad) / (maxX - minX || 1), (H - 2 * pad) / (maxY - minY || 1));
  const px = (p) => [pad + (p[0] - minX) * k, H - pad - (p[1] - minY) * k];
  const status = (z) => (z.meets_norm == null ? "none" : z.meets_norm ? "ok" : "short");
  return (
    <figure className="zone-map">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Map of zones coloured by today's supply">
        {shapes.map((z) => {
          const rs = rings(z.boundary);
          const d = rs.map((r) => `M${r.map((p) => px(p).join(",")).join("L")}Z`).join(" ");
          const c = rs[0].map(px).reduce((a, p) => [a[0] + p[0] / rs[0].length, a[1] + p[1] / rs[0].length], [0, 0]);
          return (
            <g key={z.zone_id}>
              <path d={d} className={`zone zone-${status(z)}`}><title>{z.name}</title></path>
              <text x={c[0]} y={c[1]} className="zone-label" textAnchor="middle">{z.name}</text>
              <text x={c[0]} y={c[1] + 16} className="zone-sub" textAnchor="middle">
                {z.lpcd_delivered != null ? `${Math.round(z.lpcd_delivered)} L/person` : "no timetable"}
                {z.open_issues ? ` · ${z.open_issues} issue(s)` : ""}
              </text>
            </g>
          );
        })}
      </svg>
      <figcaption className="legend">
        <span className="key key-ok" /> meets the norm <span className="key key-short" /> below the norm
        <span className="key key-none" /> no published timetable
      </figcaption>
    </figure>
  );
}

// ---------------------------------------------------------------- timetable view
function Timeline({ zone, start, end }) {
  const span = 24 * 60;
  return (
    <div className="timeline" role="img" aria-label={zone.blocks.map((b) => `${b.start} to ${b.end}`).join(", ") || "no supply"}>
      <span className="tl-window" style={{ left: `${(start * 60 / span) * 100}%`, width: `${((end - start) * 60 / span) * 100}%` }} />
      {zone.blocks.map((b) => (
        <span key={b.start_minute} className="tl-block" title={`${b.start}–${b.end}: ${fmtL(b.litres)}`}
          style={{ left: `${(b.start_minute / span) * 100}%`, width: `${((b.end_minute - b.start_minute) / span) * 100}%` }} />
      ))}
    </div>
  );
}

function TimetableView({ tt, onPublish, onTankers }) {
  const r = tt.result;
  const m = r.metrics;
  return (
    <section className="card-plain" aria-labelledby={`tt-${tt.timetable_id}`}>
      <div className="section-head">
        <h3 id={`tt-${tt.timetable_id}`}>Timetable for {String(tt.for_date).slice(0, 10)} <span className={`tag tag-${tt.status}`}>{TT_STATUS[tt.status]}</span></h3>
        <div className="form-actions">
          {tt.status === "draft" && (
            <ConfirmButton label="Publish" confirmLabel="Yes, publish" className="btn btn-primary"
              question="Publish? Every organisation is told its zone's supply hours." onConfirm={onPublish} />
          )}
          <button type="button" className="btn" onClick={onTankers}>Plan tankers for short zones</button>
        </div>
      </div>
      <div className="tiles">
        <div className="tile"><span className="t-label">Demand</span><span className="t-value">{fmtL(m.total_demand_litres)}</span>
          <span className="t-sub">{r.demand_mode === "meter" ? "from metered use" : `${r.lpcd_norm} L/person norm`}</span></div>
        <div className="tile"><span className="t-label">Delivered</span><span className="t-value">{Math.round(m.served_ratio * 100)}%</span>
          <span className="t-sub">{fmtL(m.total_delivered_litres)}</span></div>
        <div className="tile"><span className="t-label">Lowest per person</span><span className="t-value">{Math.round(m.min_lpcd)} L</span>
          <span className="t-sub">{m.zones_below_norm} zone(s) below the norm</span></div>
        <div className="tile"><span className="t-label">Fairness between zones</span><span className="t-value">{Number(m.jain_fairness).toFixed(2)}</span>
          <span className="t-sub">Jain index</span></div>
      </div>
      <div className="table-wrap">
        <table className="data">
          <thead><tr><th scope="col">Zone</th><th scope="col">Supply hours (0–24 h)</th><th scope="col" className="num">Litres</th><th scope="col" className="num">Per person</th></tr></thead>
          <tbody>
            {r.zones.map((z) => (
              <tr key={z.zone_id}>
                <th scope="row"><span className="strong">{z.name}</span><span className="sub">{z.population.toLocaleString()} people · source #{z.reservoir_id}</span></th>
                <td className="cell"><Timeline zone={z} start={r.day_start_hour} end={r.day_end_hour} />
                  <span className="sub">{z.blocks.map((b) => `${b.start}–${b.end}`).join(", ") || "no piped supply"} · {z.supply_hours} h</span></td>
                <td className="num">{fmtL(z.delivered_litres)}<span className="sub">of {fmtL(z.demand_litres)}</span></td>
                <td className={z.meets_norm ? "num" : "num none"}>{Math.round(z.lpcd_delivered)} L</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {r.skipped_zones && r.skipped_zones.length > 0 && (
        <p className="form-info">Not planned: {r.skipped_zones.map((s) => `${s.name} (${s.reason})`).join("; ")}</p>
      )}
      <p className="note">Sources: {r.sources.map((s) => `#${s.reservoir_id} released ${fmtL(s.delivered_litres)} of ${fmtL(s.release_litres)}, pumps ${Math.round(s.pump_utilization * 100)}% busy`).join(" · ")}.
        Policy v{tt.policy_version}. Created by {tt.created_by_email || "–"} {fmtDateTime(tt.created_at)}{tt.published_by_email ? `; published by ${tt.published_by_email}` : ""}.</p>
    </section>
  );
}

// ---------------------------------------------------------------- tankers
function Tankers({ zones, notify, refreshKey }) {
  const [rows, setRows] = useState([]);
  const [f, setF] = useState({ zone_id: "", loads: "1", litres: "10000", vehicle: "" });
  const load = useCallback(() => api.tankers().then(setRows).catch((e) => notify(e.message, true)), [notify]);
  useEffect(() => { load(); }, [load, refreshKey]);
  const set = async (trip, status) => {
    try { await api.updateTanker(trip.trip_id, { status }); load(); } catch (e) { notify(e.message, true); }
  };
  return (
    <section aria-labelledby="tk-title">
      <h3 id="tk-title">Tanker orders</h3>
      <form className="inline-form" onSubmit={async (e) => {
        e.preventDefault();
        try { await api.createTanker({ zone_id: Number(f.zone_id), loads: Number(f.loads), litres: Number(f.loads) * Number(f.litres), vehicle: f.vehicle || null }); setF({ ...f, vehicle: "" }); load(); }
        catch (err) { notify(err.message, true); }
      }}>
        <select aria-label="Zone" value={f.zone_id} onChange={(e) => setF({ ...f, zone_id: e.target.value })} required>
          <option value="">Zone…</option>{zones.map((z) => <option key={z.zone_id} value={z.zone_id}>{z.name}</option>)}
        </select>
        <input aria-label="Loads" type="number" min="1" value={f.loads} onChange={(e) => setF({ ...f, loads: e.target.value })} />
        <span className="small muted">loads ×</span>
        <input aria-label="Litres per load" type="number" min="1" value={f.litres} onChange={(e) => setF({ ...f, litres: e.target.value })} />
        <span className="small muted">L</span>
        <input aria-label="Vehicle" placeholder="Vehicle (optional)" value={f.vehicle} onChange={(e) => setF({ ...f, vehicle: e.target.value })} />
        <button type="submit" className="btn">Add order</button>
      </form>
      {rows.length === 0 ? <p className="muted">No tanker orders.</p> : (
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th scope="col">#</th><th scope="col">Date</th><th scope="col">Zone</th><th scope="col" className="num">Litres</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {rows.map((t) => (
                <tr key={t.trip_id}>
                  <td className="muted">{t.trip_id}</td><td>{String(t.planned_for).slice(0, 10)}</td>
                  <td>{t.zone_name}<span className="sub">{t.origin === "timetable" ? "from timetable" : "added by hand"}{t.vehicle ? ` · ${t.vehicle}` : ""}</span></td>
                  <td className="num">{fmtL(t.litres)}<span className="sub">{loadsText(t)}</span></td>
                  <td><span className={`tag tag-${t.status}`}>{TRIP_STATUS[t.status]}</span></td>
                  <td className="row-actions">
                    {t.status === "planned" && <button type="button" className="btn btn-small" onClick={() => set(t, "dispatched")}>Dispatch</button>}
                    {t.status === "dispatched" && <button type="button" className="btn btn-small" onClick={() => set(t, "delivered")}>Delivered</button>}
                    {(t.status === "planned" || t.status === "dispatched") && <button type="button" className="btn btn-small btn-quiet" onClick={() => set(t, "cancelled")}>Cancel</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- the tab
export default function Supply({ notify }) {
  const [list, setList] = useState([]);
  const [current, setCurrent] = useState(null);
  const [sources, setSources] = useState([]);
  const [zones, setZones] = useState([]);
  const [f, setF] = useState({ for_date: today(), demand_mode: "norm", releases: {} });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [version, setVersion] = useState(0);
  const load = useCallback(async () => {
    try {
      const [l, s, z] = await Promise.all([api.timetables(), api.resources(), api.mapZones()]);
      setList(l); setSources(s); setZones(z);
      if (!current && l.length) setCurrent(await api.timetable(l[0].timetable_id));
    } catch (e) { setError(e.message); }
  }, [current]);
  useEffect(() => { load(); }, [load, version]);
  const open = async (id) => { try { setCurrent(await api.timetable(id)); } catch (e) { notify(e.message, true); } };

  return (
    <div className="stack">
      <div className="section-head"><h2>Daily zone supply timetable</h2></div>
      <p className="lede">Plans which zone gets piped water when, within each source's release for the day, its pump
        capacity and each zone's inlet main. Demand is the policy norm (litres per person per day) or real metered use.</p>
      {error && <p className="form-error" role="alert">{error}</p>}
      <form className="form card-plain" onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true); setError(null);
        const releases = Object.fromEntries(Object.entries(f.releases).filter(([, v]) => v !== "").map(([k, v]) => [k, Number(v)]));
        try {
          const tt = await api.buildTimetable({ for_date: f.for_date, demand_mode: f.demand_mode, releases });
          setCurrent(tt); setVersion((v) => v + 1);
          notify(`Draft timetable #${tt.timetable_id} computed. Review it, then publish.`);
        } catch (err) { setError(err.message); } finally { setBusy(false); }
      }}>
        <h3>Compute a timetable</h3>
        <div className="form-row">
          <label htmlFor="tt-date"><span>For date</span><input id="tt-date" type="date" value={f.for_date} onChange={(e) => setF({ ...f, for_date: e.target.value })} /></label>
          <label htmlFor="tt-mode"><span>Demand from</span>
            <select id="tt-mode" value={f.demand_mode} onChange={(e) => setF({ ...f, demand_mode: e.target.value })}>
              <option value="norm">Policy norm × population</option><option value="meter">Metered use (last 7 days)</option>
            </select></label>
        </div>
        <fieldset><legend>Today's release per source (leave empty to use the usual value)</legend>
          <div className="form-row wrap">
            {sources.map((s) => (
              <label key={s.reservoir_id} htmlFor={`rel-${s.reservoir_id}`}><span>{s.name}{s.daily_release_litres != null ? ` (usual ${fmtL(s.daily_release_litres)})` : " (not set)"}</span>
                <input id={`rel-${s.reservoir_id}`} type="number" min="0" step="any" value={f.releases[s.reservoir_id] ?? ""}
                  onChange={(e) => setF({ ...f, releases: { ...f.releases, [s.reservoir_id]: e.target.value } })} /></label>
            ))}
          </div>
        </fieldset>
        <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Computing…" : "Compute draft"}</button>
      </form>
      {current && (
        <TimetableView tt={current}
          onPublish={async () => { try { setCurrent(await api.publishTimetable(current.timetable_id)); setVersion((v) => v + 1); notify("Published. Organisations were notified of their supply hours."); } catch (e) { notify(e.message, true); } }}
          onTankers={async () => { try { const t = await api.planTankers(current.timetable_id); notify(t.length ? `Tanker orders for ${t.length} zone(s): ${t.map((x) => `${x.zone_name} ${loadsText(x)}`).join(", ")}.` : "No zone is below the tanker trigger (or orders already exist)."); setVersion((v) => v + 1); } catch (e) { notify(e.message, true); } }} />
      )}
      <section className="card-plain" aria-labelledby="map-title">
        <h3 id="map-title">Zones today</h3>
        <ZoneMap zones={zones} />
      </section>
      <Tankers zones={zones} notify={notify} refreshKey={version} />
      <section aria-labelledby="tl-title">
        <h3 id="tl-title">Timetables</h3>
        {list.length === 0 ? <p className="muted">None yet.</p> : (
          <ul className="plain-list">
            {list.map((t) => (
              <li key={t.timetable_id}>
                <button type="button" className="link-btn" onClick={() => open(t.timetable_id)}>#{t.timetable_id} · {String(t.for_date).slice(0, 10)}</button>
                {" "}<span className={`tag tag-${t.status}`}>{TT_STATUS[t.status]}</span>
                {t.metrics && <span className="muted small"> · {Math.round(t.metrics.served_ratio * 100)}% delivered, lowest {Math.round(t.metrics.min_lpcd)} L/person</span>}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
