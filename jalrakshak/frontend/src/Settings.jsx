import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import { USER_TYPE, fmtL, fmtNum, typeName } from "./format.js";
import { ConfirmButton, Empty, Priority } from "./ui.jsx";

const PRIORITIES = [
  [5, "5 – highest"], [4, "4 – high"], [3, "3 – normal"], [2, "2 – low"], [1, "1 – lowest"],
];

// Number inputs hold text while typing; convert only when saving.
const num = (v) => (v === "" || v === null || v === undefined ? NaN : Number(v));

function ReservoirForm({ status, onSaved, notify }) {
  const [name, setName] = useState("");
  const [capacity, setCapacity] = useState("");
  const [available, setAvailable] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  // Load the current values (and again after every change elsewhere, e.g. a plan was applied).
  useEffect(() => {
    if (!status) return;
    setName(status.name);
    setCapacity(String(status.capacity_litres));
    setAvailable(String(status.available_litres));
  }, [status]);

  if (!status) return null;
  const cap = num(capacity);
  const avail = num(available);
  const changed =
    name.trim() !== status.name || cap !== status.capacity_litres || avail !== status.available_litres;

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    if (!name.trim()) return setError("The reservoir needs a name.");
    if (!(cap > 0)) return setError("Capacity must be more than 0 L.");
    if (!(avail >= 0)) return setError("The water level cannot be negative.");
    if (avail > cap) {
      return setError(avail === status.available_litres
        ? `Capacity cannot be below the water already in the reservoir (${fmtL(avail)}). Lower "Water now" as well.`
        : `The water level (${fmtL(avail)}) cannot be more than the capacity (${fmtL(cap)}).`);
    }
    const data = {};
    if (name.trim() !== status.name) data.name = name.trim();
    if (cap !== status.capacity_litres) data.capacity_litres = cap;
    if (avail !== status.available_litres) data.available_litres = avail;
    setBusy(true);
    try {
      await api.updateReservoir(data);
      notify("Reservoir updated.");
      onSaved();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="form settings-res" onSubmit={submit} aria-labelledby="set-res">
      <h2 id="set-res">Reservoir</h2>
      <p className="muted">
        Changes apply to the next plan you compare or apply. The water level can never be more than the capacity.
      </p>
      <div className="form-row three">
        <label>
          <span>Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={100} required />
        </label>
        <label>
          <span>Capacity (L)</span>
          <input type="number" min="1" step="any" inputMode="decimal" value={capacity} onChange={(e) => setCapacity(e.target.value)} required />
        </label>
        <label>
          <span>Water now (L)</span>
          <input type="number" min="0" step="any" inputMode="decimal" value={available} onChange={(e) => setAvailable(e.target.value)} required />
        </label>
      </div>
      {cap > 0 && avail >= 0 && (
        <p className="note">{avail <= cap ? `${Math.round((avail / cap) * 100)}% full after saving.` : "More water than capacity."}</p>
      )}
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="form-actions">
        <button type="submit" className="btn btn-primary" disabled={busy || !changed}>
          {busy ? "Saving…" : "Save reservoir"}
        </button>
        {changed && (
          <button type="button" className="btn btn-quiet" onClick={() => {
            setName(status.name); setCapacity(String(status.capacity_litres)); setAvailable(String(status.available_litres)); setError(null);
          }}>
            Undo changes
          </button>
        )}
      </div>
    </form>
  );
}

function ZoneRow({ zone, onChanged, notify }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(zone.name);
  const [pop, setPop] = useState(String(zone.population));
  const [error, setError] = useState(null);

  const save = async () => {
    setError(null);
    if (!name.trim()) return setError("Name is required.");
    if (!(Number.isInteger(num(pop)) && num(pop) > 0)) return setError("Population must be a whole number above 0.");
    try {
      await api.updateZone(zone.zone_id, { name: name.trim(), population: num(pop) });
      notify(`Zone "${name.trim()}" saved.`);
      setEditing(false);
      onChanged();
    } catch (err) {
      setError(err.message);
    }
  };

  if (!editing) {
    return (
      <tr>
        <td className="strong">{zone.name}</td>
        <td className="num">{fmtNum(zone.population)}</td>
        <td className="num">{zone.user_count}</td>
        <td className="row-actions">
          <button type="button" className="btn btn-small" onClick={() => { setName(zone.name); setPop(String(zone.population)); setEditing(true); }}>Edit</button>
          <ConfirmButton
            label="Delete" confirmLabel="Yes, delete" className="btn btn-small"
            question={`Delete ${zone.name}?`}
            disabled={zone.user_count > 0}
            onConfirm={async () => {
              try {
                await api.deleteZone(zone.zone_id);
                notify(`Zone "${zone.name}" deleted.`);
                onChanged();
              } catch (err) {
                notify(err.message, true);
              }
            }}
          />
        </td>
      </tr>
    );
  }
  return (
    <tr className="editing">
      <td>
        <label><span className="sr-only">Zone name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={100} />
        </label>
        {error && <p className="form-error" role="alert">{error}</p>}
      </td>
      <td className="num">
        <label><span className="sr-only">Population</span>
          <input type="number" min="1" step="1" inputMode="numeric" value={pop} onChange={(e) => setPop(e.target.value)} />
        </label>
      </td>
      <td className="num">{zone.user_count}</td>
      <td className="row-actions">
        <button type="button" className="btn btn-small btn-primary" onClick={save}>Save</button>
        <button type="button" className="btn btn-small btn-quiet" onClick={() => { setEditing(false); setError(null); }}>Cancel</button>
      </td>
    </tr>
  );
}

function AddZone({ onChanged, notify }) {
  const [name, setName] = useState("");
  const [pop, setPop] = useState("");
  const [error, setError] = useState(null);
  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    if (!name.trim()) return setError("Name is required.");
    if (!(Number.isInteger(num(pop)) && num(pop) > 0)) return setError("Population must be a whole number above 0.");
    try {
      await api.createZone({ name: name.trim(), population: num(pop) });
      notify(`Zone "${name.trim()}" added.`);
      setName("");
      setPop("");
      onChanged();
    } catch (err) {
      setError(err.message);
    }
  };
  return (
    <form className="add-row" onSubmit={submit} aria-label="Add a zone">
      <label><span>New zone</span><input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. East Zone" maxLength={100} /></label>
      <label><span>Population</span><input type="number" min="1" step="1" inputMode="numeric" value={pop} onChange={(e) => setPop(e.target.value)} placeholder="e.g. 25000" /></label>
      <button type="submit" className="btn">Add zone</button>
      {error && <p className="form-error" role="alert">{error}</p>}
    </form>
  );
}

function UserFields({ value, onChange, zones }) {
  const set = (k) => (e) => onChange({ ...value, [k]: e.target.value });
  return (
    <>
      <label><span>Name</span><input value={value.name} onChange={set("name")} maxLength={100} /></label>
      <label><span>Type</span>
        <select value={value.user_type} onChange={set("user_type")}>
          {Object.entries(USER_TYPE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </label>
      <label><span>Priority</span>
        <select value={value.priority} onChange={set("priority")}>
          {PRIORITIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </label>
      <label><span>Zone</span>
        <select value={value.zone_id} onChange={set("zone_id")}>
          {zones.map((z) => <option key={z.zone_id} value={z.zone_id}>{z.name}</option>)}
        </select>
      </label>
    </>
  );
}

const userPayload = (v) => ({ name: v.name.trim(), user_type: v.user_type, priority: Number(v.priority), zone_id: Number(v.zone_id) });

function UserRow({ user, zones, onChanged, notify }) {
  const [editing, setEditing] = useState(null);
  const [error, setError] = useState(null);

  if (!editing) {
    return (
      <tr>
        <td><span className="strong">{user.name}</span></td>
        <td>{typeName(user.user_type)}</td>
        <td><Priority value={user.priority} /></td>
        <td>{user.zone_name}</td>
        <td className="row-actions">
          <button type="button" className="btn btn-small" onClick={() => setEditing({ ...user, name: user.name })}>Edit</button>
          <ConfirmButton
            label="Delete" confirmLabel="Yes, delete" className="btn btn-small"
            question={`Delete ${user.name}?`}
            onConfirm={async () => {
              try {
                await api.deleteUser(user.user_id);
                notify(`"${user.name}" deleted.`);
                onChanged();
              } catch (err) {
                notify(err.message, true);
              }
            }}
          />
        </td>
      </tr>
    );
  }
  return (
    <tr className="editing">
      <td colSpan={5}>
        <div className="edit-grid">
          <UserFields value={editing} onChange={setEditing} zones={zones} />
          <div className="row-actions">
            <button type="button" className="btn btn-small btn-primary" onClick={async () => {
              setError(null);
              if (!editing.name.trim()) return setError("Name is required.");
              try {
                await api.updateUser(user.user_id, userPayload(editing));
                notify(`"${editing.name.trim()}" saved.`);
                setEditing(null);
                onChanged();
              } catch (err) {
                setError(err.message);
              }
            }}>Save</button>
            <button type="button" className="btn btn-small btn-quiet" onClick={() => { setEditing(null); setError(null); }}>Cancel</button>
          </div>
        </div>
        {error && <p className="form-error" role="alert">{error}</p>}
      </td>
    </tr>
  );
}

function AddUser({ zones, onChanged, notify }) {
  const blank = { name: "", user_type: "domestic", priority: 3, zone_id: zones[0] ? zones[0].zone_id : "" };
  const [value, setValue] = useState(blank);
  const [error, setError] = useState(null);
  return (
    <form className="add-row" aria-label="Add a requester" onSubmit={async (e) => {
      e.preventDefault();
      setError(null);
      if (!value.name.trim()) return setError("Name is required.");
      if (!value.zone_id) return setError("Add a zone first.");
      try {
        await api.createUser(userPayload(value));
        notify(`"${value.name.trim()}" added. It can now ask for water on the Overview tab.`);
        setValue({ ...blank, zone_id: value.zone_id });
        onChanged();
      } catch (err) {
        setError(err.message);
      }
    }}>
      <UserFields value={value} onChange={setValue} zones={zones} />
      <button type="submit" className="btn">Add requester</button>
      {error && <p className="form-error" role="alert">{error}</p>}
    </form>
  );
}

export default function Settings({ status, users, onChanged, notify }) {
  const [zones, setZones] = useState(null);
  const [error, setError] = useState(null);

  const loadZones = useCallback(async () => {
    try {
      setZones(await api.zones());
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, []);
  useEffect(() => {
    loadZones();
  }, [loadZones, users]);

  const changed = () => {
    onChanged();
    loadZones();
  };

  return (
    <div className="settings">
      <ReservoirForm status={status} onSaved={changed} notify={notify} />

      <section aria-labelledby="set-zones">
        <h2 id="set-zones">Zones</h2>
        <p className="muted">
          Population is used by the Water-aware rule (zones with more people score higher). A zone with requesters cannot be deleted.
          New zones get consumption history and forecasts the next time the data-science job runs.
        </p>
        {error && <p className="form-error" role="alert">{error}</p>}
        {zones && (
          <>
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th scope="col">Zone</th>
                    <th scope="col" className="num">Population</th>
                    <th scope="col" className="num">Requesters</th>
                    <th scope="col"><span className="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody>
                  {zones.map((z) => <ZoneRow key={`${z.zone_id}-${z.name}-${z.population}-${z.user_count}`} zone={z} onChanged={changed} notify={notify} />)}
                </tbody>
              </table>
            </div>
            <AddZone onChanged={changed} notify={notify} />
          </>
        )}
      </section>

      <section aria-labelledby="set-users">
        <h2 id="set-users">Requesters</h2>
        <p className="muted">
          Priority 5 is most important. Changes apply to pending requests the next time plans are compared; plans already
          applied keep what they recorded. A requester with requests on record cannot be deleted, but can be renamed.
        </p>
        {users.length === 0 ? (
          <Empty title="No requesters yet." />
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th scope="col">Name</th>
                  <th scope="col">Type</th>
                  <th scope="col">Priority</th>
                  <th scope="col">Zone</th>
                  <th scope="col"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <UserRow key={`${u.user_id}-${u.name}-${u.priority}-${u.zone_id}-${u.user_type}`} user={u} zones={zones || []} onChanged={changed} notify={notify} />
                ))}
              </tbody>
            </table>
          </div>
        )}
        {zones && <AddUser zones={zones} onChanged={changed} notify={notify} />}
      </section>
    </div>
  );
}
