import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import { fmtL } from "./format.js";

// Sources (reservoirs) and the network data the supply timetable needs: each source's daily release and pump
// capacity; each zone's source, inlet-main capacity, supply priority and map boundary (GeoJSON).
function SourceRow({ s, notify, onSaved }) {
  const [v, setV] = useState({ daily_release_litres: s.daily_release_litres ?? "", pump_capacity_lph: s.pump_capacity_lph ?? "" });
  return (
    <tr>
      <th scope="row"><span className="strong">{s.name}</span><span className="sub">storage {fmtL(s.available_litres)} of {fmtL(s.capacity_litres)}</span></th>
      <td><input aria-label={`${s.name} daily release`} className="num-input wide" type="number" min="0" step="any" value={v.daily_release_litres}
        onChange={(e) => setV({ ...v, daily_release_litres: e.target.value })} /></td>
      <td><input aria-label={`${s.name} pump capacity`} className="num-input wide" type="number" min="1" step="any" value={v.pump_capacity_lph}
        onChange={(e) => setV({ ...v, pump_capacity_lph: e.target.value })} /></td>
      <td><button type="button" className="btn btn-small" onClick={async () => {
        try {
          await api.reservoirNetwork(s.reservoir_id, {
            daily_release_litres: v.daily_release_litres === "" ? null : Number(v.daily_release_litres),
            pump_capacity_lph: v.pump_capacity_lph === "" ? null : Number(v.pump_capacity_lph) });
          notify(`${s.name} saved.`); onSaved();
        } catch (e) { notify(e.message, true); }
      }}>Save</button></td>
    </tr>
  );
}

function ZoneRow({ z, sources, notify, onSaved }) {
  const [v, setV] = useState({ reservoir_id: z.reservoir_id ?? "", inlet_capacity_lph: z.inlet_capacity_lph ?? "", supply_priority: z.supply_priority });
  const save = async (extra) => {
    try {
      await api.zoneNetwork(z.zone_id, {
        reservoir_id: v.reservoir_id === "" ? null : Number(v.reservoir_id),
        inlet_capacity_lph: v.inlet_capacity_lph === "" ? null : Number(v.inlet_capacity_lph),
        supply_priority: Number(v.supply_priority), ...extra });
      notify(`${z.name} saved.`); onSaved();
    } catch (e) { notify(e.message, true); }
  };
  return (
    <tr>
      <th scope="row"><span className="strong">{z.name}</span><span className="sub">{Number(z.population).toLocaleString()} people</span></th>
      <td><select aria-label={`${z.name} source`} value={v.reservoir_id} onChange={(e) => setV({ ...v, reservoir_id: e.target.value })}>
        <option value="">—</option>{sources.map((s) => <option key={s.reservoir_id} value={s.reservoir_id}>{s.name}</option>)}</select></td>
      <td><input aria-label={`${z.name} inlet capacity`} className="num-input wide" type="number" min="1" step="any" value={v.inlet_capacity_lph}
        onChange={(e) => setV({ ...v, inlet_capacity_lph: e.target.value })} /></td>
      <td><select aria-label={`${z.name} supply priority`} value={v.supply_priority} onChange={(e) => setV({ ...v, supply_priority: e.target.value })}>
        {[1, 2, 3, 4, 5].map((p) => <option key={p} value={p}>{p}</option>)}</select></td>
      <td className="small">{z.boundary ? "on map" : "none"}
        <label className="file-btn">Upload GeoJSON<input type="file" accept=".json,.geojson,application/geo+json" onChange={async (e) => {
          const file = e.target.files[0];
          if (!file) return;
          try {
            let geo = JSON.parse(await file.text());
            if (geo.type === "FeatureCollection") geo = geo.features[0];
            await save({ boundary: geo });
          } catch (err) { notify(err instanceof SyntaxError ? "That file is not valid JSON." : err.message, true); }
        }} /></label></td>
      <td><button type="button" className="btn btn-small" onClick={() => save({})}>Save</button></td>
    </tr>
  );
}

export default function NetworkSettings({ notify, onChanged }) {
  const [sources, setSources] = useState(null);
  const [zones, setZones] = useState([]);
  const [n, setN] = useState({ name: "", capacity_litres: "", available_litres: "", daily_release_litres: "", pump_capacity_lph: "" });
  const load = useCallback(async () => {
    try { const [s, z] = await Promise.all([api.resources(), api.zones()]); setSources(s); setZones(z); }
    catch (e) { notify(e.message, true); }
  }, [notify]);
  useEffect(() => { load(); }, [load]);
  const saved = () => { load(); onChanged(); };
  if (!sources) return null;
  return (
    <section aria-labelledby="set-net">
      <h2 id="set-net">Supply network</h2>
      <p className="muted">Used by the daily supply timetable. Daily release: water a source can send into the network today.
        Pump capacity and inlet capacity are hydraulic limits in litres per hour. Supply priority (1–5) decides which zones
        get extra water and the earliest hours when the source cannot meet every zone's demand.</p>
      <div className="table-wrap">
        <table className="data">
          <thead><tr><th scope="col">Source</th><th scope="col">Daily release (L)</th><th scope="col">Pump capacity (L/h)</th><th scope="col"><span className="sr-only">Save</span></th></tr></thead>
          <tbody>{sources.map((s) => <SourceRow key={`${s.reservoir_id}-${s.daily_release_litres}-${s.pump_capacity_lph}`} s={s} notify={notify} onSaved={saved} />)}</tbody>
        </table>
      </div>
      <form className="inline-form" aria-label="Add a source reservoir" onSubmit={async (e) => {
        e.preventDefault();
        const num = (x) => (x === "" ? null : Number(x));
        try {
          await api.createReservoir({ name: n.name, capacity_litres: Number(n.capacity_litres), available_litres: num(n.available_litres) || 0,
            daily_release_litres: num(n.daily_release_litres), pump_capacity_lph: num(n.pump_capacity_lph) });
          notify(`${n.name} added.`); setN({ name: "", capacity_litres: "", available_litres: "", daily_release_litres: "", pump_capacity_lph: "" }); saved();
        } catch (err) { notify(err.message, true); }
      }}>
        <input aria-label="Name" placeholder="New source name" value={n.name} onChange={(e) => setN({ ...n, name: e.target.value })} required />
        <input aria-label="Capacity" type="number" min="1" placeholder="Capacity (L)" value={n.capacity_litres} onChange={(e) => setN({ ...n, capacity_litres: e.target.value })} required />
        <input aria-label="Water now" type="number" min="0" placeholder="Water now (L)" value={n.available_litres} onChange={(e) => setN({ ...n, available_litres: e.target.value })} />
        <input aria-label="Daily release" type="number" min="0" placeholder="Daily release (L)" value={n.daily_release_litres} onChange={(e) => setN({ ...n, daily_release_litres: e.target.value })} />
        <input aria-label="Pump capacity" type="number" min="1" placeholder="Pumps (L/h)" value={n.pump_capacity_lph} onChange={(e) => setN({ ...n, pump_capacity_lph: e.target.value })} />
        <button type="submit" className="btn">Add source</button>
      </form>
      <div className="table-wrap">
        <table className="data">
          <thead><tr><th scope="col">Zone</th><th scope="col">Fed from</th><th scope="col">Inlet capacity (L/h)</th><th scope="col">Supply priority</th><th scope="col">Map boundary</th><th scope="col"><span className="sr-only">Save</span></th></tr></thead>
          <tbody>{zones.map((z) => <ZoneRow key={`${z.zone_id}-${z.reservoir_id}-${z.inlet_capacity_lph}-${z.supply_priority}-${!!z.boundary}`} z={z} sources={sources} notify={notify} onSaved={saved} />)}</tbody>
        </table>
      </div>
    </section>
  );
}
