import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import { fmtDateTime, fmtL } from "./format.js";
import { Empty } from "./ui.jsx";

const KIND = { reservoir_level: "Reservoir level sensor", zone_flow: "Zone flow meter" };
const SOURCE = { synthetic: "Generated (demo)", meter: "Flow meters", import: "Imported meter data" };

function NewKey({ dev, onClose }) {
  return (
    <div className="form-info key-once" role="status">
      <p><strong>Device key for “{dev.name}”</strong>. Copy it now into the device or SCADA gateway; it is not shown again.</p>
      <p><code className="wrap">{dev.device_key}</code></p>
      <p className="small">The device sends readings to <code>POST /telemetry/readings</code> with the header <code>X-Device-Key</code>.
        To try it: <code>python scripts/scada_simulator.py --{dev.kind === "reservoir_level" ? "level" : "flow"}-key {dev.device_key.slice(0, 8)}…</code></p>
      <button type="button" className="btn btn-small" onClick={onClose}>I have copied it</button>
    </div>
  );
}

function Readings({ id }) {
  const [rows, setRows] = useState(null);
  useEffect(() => { api.deviceReadings(id).then(setRows).catch(() => setRows([])); }, [id]);
  if (!rows) return <p className="muted small">Loading…</p>;
  if (rows.length === 0) return <p className="muted small">No readings yet.</p>;
  return (
    <ul className="plain-list small">
      {rows.slice(0, 10).map((r) => <li key={r.measured_at}>{fmtDateTime(r.measured_at)}: {fmtL(r.value)}</li>)}
    </ul>
  );
}

export default function FieldData({ notify, onChanged }) {
  const [devices, setDevices] = useState(null);
  const [sources, setSources] = useState([]);
  const [reservoirs, setReservoirs] = useState([]);
  const [zones, setZones] = useState([]);
  const [f, setF] = useState({ name: "", kind: "reservoir_level", reservoir_id: "1", zone_id: "" });
  const [newKey, setNewKey] = useState(null);
  const [open, setOpen] = useState(null);
  const [csv, setCsv] = useState("");
  const [error, setError] = useState(null);
  const load = useCallback(async () => {
    try {
      const [d, s, r, z] = await Promise.all([api.devices(), api.dataSources(), api.resources(), api.zones()]);
      setDevices(d); setSources(s); setReservoirs(r); setZones(z);
    } catch (e) { setError(e.message); }
  }, []);
  useEffect(() => { load(); }, [load]);

  return (
    <div className="stack">
      <div className="section-head"><h2>Field data</h2><button type="button" className="btn" onClick={load}>Refresh</button></div>
      <p className="lede">Readings from SCADA level sensors and zone flow meters, and imported meter data. A level sensor's
        reading becomes the reservoir's water level; flow-meter readings build each zone's daily consumption, which the
        forecast and the timetable use instead of generated data. A sensor that stops reporting raises an alert, and
        staff can keep entering values by hand in Settings.</p>
      {error && <p className="form-error" role="alert">{error}</p>}

      <section className="card-plain" aria-labelledby="dev-title">
        <h3 id="dev-title">Sensors and meters</h3>
        {newKey && <NewKey dev={newKey} onClose={() => setNewKey(null)} />}
        {devices && devices.length === 0 && <Empty title="No devices registered.">Add a level sensor or flow meter below.</Empty>}
        {devices && devices.length > 0 && (
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th scope="col">Device</th><th scope="col">Measures</th><th scope="col">Last reading</th><th scope="col">State</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {devices.map((d) => (
                  <tr key={d.device_id}>
                    <th scope="row"><span className="strong">{d.name}</span><span className="sub">{KIND[d.kind]}</span>
                      {open === d.device_id && <Readings id={d.device_id} />}</th>
                    <td>{d.reservoir_name || d.zone_name}</td>
                    <td>{d.last_seen_at ? `${fmtL(d.last_value)} · ${fmtDateTime(d.last_seen_at)}` : "never"}</td>
                    <td>{!d.active ? <span className="tag">disabled</span> : d.stale ? <span className="tag tag-short">offline</span> : <span className="tag tag-ok">reporting</span>}</td>
                    <td className="row-actions">
                      <button type="button" className="btn btn-small btn-quiet" onClick={() => setOpen(open === d.device_id ? null : d.device_id)}>{open === d.device_id ? "Hide" : "Readings"}</button>
                      <button type="button" className="btn btn-small" onClick={async () => {
                        try { await api.setDeviceActive(d.device_id, !d.active); load(); } catch (e) { notify(e.message, true); }
                      }}>{d.active ? "Disable" : "Enable"}</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <form className="inline-form" onSubmit={async (e) => {
          e.preventDefault();
          try {
            const dev = await api.createDevice({ name: f.name, kind: f.kind,
              reservoir_id: f.kind === "reservoir_level" ? Number(f.reservoir_id) : null,
              zone_id: f.kind === "zone_flow" ? Number(f.zone_id) : null });
            setNewKey(dev); setF({ ...f, name: "" }); load();
          } catch (err) { notify(err.message, true); }
        }}>
          <input aria-label="Device name" placeholder="Name, e.g. Main reservoir level" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required />
          <select aria-label="Kind" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
            {Object.entries(KIND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          {f.kind === "reservoir_level" ? (
            <select aria-label="Reservoir" value={f.reservoir_id} onChange={(e) => setF({ ...f, reservoir_id: e.target.value })}>
              {reservoirs.map((r) => <option key={r.reservoir_id} value={r.reservoir_id}>{r.name}</option>)}
            </select>
          ) : (
            <select aria-label="Zone" value={f.zone_id} onChange={(e) => setF({ ...f, zone_id: e.target.value })} required>
              <option value="">Zone…</option>{zones.map((z) => <option key={z.zone_id} value={z.zone_id}>{z.name}</option>)}
            </select>
          )}
          <button type="submit" className="btn btn-primary">Register device</button>
        </form>
      </section>

      <section className="card-plain" aria-labelledby="imp-title">
        <h3 id="imp-title">Import meter data (CSV)</h3>
        <p className="small muted">Columns <code>zone_id</code> (or <code>zone</code> name), <code>date</code> (YYYY-MM-DD), <code>litres</code>, one row per zone per day.
          The whole file is rejected if any line is wrong. Existing days for the same zone are replaced.</p>
        <input type="file" accept=".csv,text/csv" aria-label="CSV file" onChange={async (e) => {
          const file = e.target.files[0];
          if (file) setCsv(await file.text());
        }} />
        <textarea aria-label="CSV text" rows={4} value={csv} onChange={(e) => setCsv(e.target.value)} placeholder={"zone,date,litres\nNorth Zone,2026-10-01,5200000"} />
        <button type="button" className="btn btn-primary" disabled={!csv.trim()} onClick={async () => {
          try { const r = await api.importConsumption(csv); notify(`${r.rows} rows imported.`); setCsv(""); load(); onChanged(); }
          catch (e) { notify(e.message, true); }
        }}>Import</button>
      </section>

      <section aria-labelledby="ds-title">
        <h3 id="ds-title">Consumption data held</h3>
        {sources.length === 0 ? <p className="muted">None yet.</p> : (
          <ul className="plain-list">
            {sources.map((s) => <li key={s.source}><strong>{SOURCE[s.source] || s.source}</strong>: {Number(s.days).toLocaleString()} zone-days, {String(s.first).slice(0, 10)} to {String(s.last).slice(0, 10)}</li>)}
          </ul>
        )}
        <p className="note">The forecasting job uses a zone's real data once it has at least 120 days of it; until then that zone uses generated demo data, which is labelled as such.</p>
      </section>
    </div>
  );
}
