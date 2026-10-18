import { useState } from "react";
import { api } from "./api.js";
import { fmtL, fmtTime, typeName } from "./format.js";
import { ConfirmButton, Empty, Priority, StatusBadge } from "./ui.jsx";

const URGENCY = [
  [1, "1 – can wait"], [2, "2 – low"], [3, "3 – normal"], [4, "4 – high"], [5, "5 – critical"],
];
const FILTERS = [
  ["all", "All requests"], ["pending", "Pending"], ["approved", "Approved"],
  ["partial", "Partly served"], ["rejected", "Rejected"],
];

function RequestsTable({ rows }) {
  const [filter, setFilter] = useState("all");
  const shown = filter === "all" ? rows : rows.filter((r) => r.status === filter);

  return (
    <section aria-labelledby="req-title">
      <div className="section-head">
        <h2 id="req-title">Water requests</h2>
        <label className="inline-field">
          <span className="sr-only">Show</span>
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            {FILTERS.map(([v, l]) => (
              <option key={v} value={v}>{l}</option>
            ))}
          </select>
        </label>
      </div>
      {shown.length === 0 ? (
        <Empty title={rows.length === 0 ? "No requests yet." : "No requests match this filter."}>
          {rows.length === 0 && "Add one with the form, or reset the demo data to restore the sample requests."}
        </Empty>
      ) : (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th scope="col">#</th>
                <th scope="col">Requester</th>
                <th scope="col">Priority</th>
                <th scope="col" className="num">Litres</th>
                <th scope="col">Urgency</th>
                <th scope="col">Arrived</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => (
                <tr key={r.request_id}>
                  <td className="muted">{r.request_id}</td>
                  <td>
                    <span className="strong">{r.user_name}</span>
                    <span className="sub">{typeName(r.user_type)}</span>
                  </td>
                  <td><Priority value={r.priority} /></td>
                  <td className="num">{fmtL(r.requested_litres)}</td>
                  <td>{r.urgency} of 5</td>
                  <td>{fmtTime(r.arrival_time)}</td>
                  <td><StatusBadge status={r.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function NewRequestForm({ users, onAdded }) {
  const [userId, setUserId] = useState("");
  const [litres, setLitres] = useState("5000");
  const [urgency, setUrgency] = useState("3");
  const [deadline, setDeadline] = useState("240");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    if (!userId) return setError("Choose who is asking for water.");
    setBusy(true);
    try {
      const created = await api.createRequest({
        user_id: Number(userId),
        reservoir_id: 1,
        requested_litres: Number(litres),
        urgency: Number(urgency),
        deadline_in_minutes: Number(deadline),
      });
      onAdded(created);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="form" onSubmit={submit} aria-labelledby="new-title">
      <h2 id="new-title">Add a request</h2>
      <p className="muted small">On behalf of an organisation, e.g. a request received by phone. Organisations can also request water themselves in their own panel.</p>
      <label>
        <span>Requester</span>
        <select value={userId} onChange={(e) => setUserId(e.target.value)} required>
          <option value="">Choose…</option>
          {users.map((u) => (
            <option key={u.user_id} value={u.user_id}>
              {u.name} ({typeName(u.user_type)}, priority {u.priority})
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>Litres needed</span>
        <input type="number" min="1" step="any" inputMode="decimal" value={litres} onChange={(e) => setLitres(e.target.value)} required />
      </label>
      <div className="form-row">
        <label>
          <span>Urgency</span>
          <select value={urgency} onChange={(e) => setUrgency(e.target.value)}>
            {URGENCY.map(([v, l]) => (
              <option key={v} value={v}>{l}</option>
            ))}
          </select>
        </label>
        <label>
          <span>Needed within (min)</span>
          <input type="number" min="1" step="any" inputMode="decimal" value={deadline} onChange={(e) => setDeadline(e.target.value)} required />
        </label>
      </div>
      {error && <p className="form-error" role="alert">{error}</p>}
      <button type="submit" className="btn btn-primary" disabled={busy || users.length === 0}>
        {busy ? "Adding…" : "Add request"}
      </button>
    </form>
  );
}

function DemoReset({ status, onChanged, notify }) {
  const capacity = status ? status.capacity_litres : 200000;
  const [level, setLevel] = useState("");
  const fallback = Math.min(50000, capacity);
  const litres = level === "" ? fallback : Number(level);
  const valid = litres >= 0 && litres <= capacity;
  return (
    <div className="demo-box">
      <h2>Demo data</h2>
      <p>
        Applying a plan uses up water and closes the requests. Reset deletes the plan history, puts back the 8 sample
        requests (80,000 L) and refills the reservoir, so you can try another rule. Reservoir, zone and requester
        settings are kept.
      </p>
      <label className="inline-label">
        <span>Refill to (L)</span>
        <input type="number" min="0" max={capacity} step="any" inputMode="decimal" placeholder={String(fallback)}
          value={level} onChange={(e) => setLevel(e.target.value)} />
      </label>
      {!valid && <p className="form-error" role="alert">Choose between 0 and the capacity ({fmtL(capacity)}).</p>}
      <ConfirmButton
        label="Reset demo data"
        confirmLabel="Yes, reset"
        question={`Delete all plan history and refill to ${fmtL(litres)}?`}
        disabled={!valid}
        onConfirm={async () => {
          try {
            await api.reset(litres);
            notify("Demo data reset. All requests are pending again.");
            onChanged();
          } catch (err) {
            notify(err.message, true);
          }
        }}
      />
    </div>
  );
}

export default function Overview({ status, requests, users, onChanged, notify }) {
  return (
    <div className="overview">
      <div className="overview-main">
        <RequestsTable rows={requests} />
      </div>
      <aside className="overview-side">
        <NewRequestForm
          users={users}
          onAdded={(r) => {
            notify(`Request #${r.request_id} added.`);
            onChanged();
          }}
        />
        <DemoReset status={status} onChanged={onChanged} notify={notify} />
      </aside>
    </div>
  );
}
