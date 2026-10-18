import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import AuditLog from "./AuditLog.jsx";
import { fmtDateTime, fmtL, fmtPct, typeName } from "./format.js";
import { Empty } from "./ui.jsx";

const pct = (v) => (v == null ? "–" : fmtPct(v));

function Kpis() {
  const [days, setDays] = useState(30);
  const [k, setK] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => { api.kpis(days).then(setK).catch((e) => setError(e.message)); }, [days]);
  if (error) return <p className="form-error">{error}</p>;
  if (!k) return <p className="muted">Loading…</p>;
  const issues = Object.entries(k.issues_by_category);
  return (
    <section aria-labelledby="kpi-title">
      <div className="section-head"><h3 id="kpi-title">Pilot indicators</h3>
        <select aria-label="Period" value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {[7, 30, 90, 365].map((d) => <option key={d} value={d}>Last {d} days</option>)}
        </select></div>
      <div className="tiles">
        <div className="tile"><span className="t-label">Plans applied</span><span className="t-value">{k.plans_applied}</span>
          <span className="t-sub">{k.shadow_plans} shadow plan(s) recorded</span></div>
        <div className="tile"><span className="t-label">Officer overrides</span><span className="t-value">{pct(k.override_rate)}</span>
          <span className="t-sub">{k.plans_overridden} of {k.plans_applied} plans changed by hand</span></div>
        <div className="tile"><span className="t-label">Essential demand delivered</span><span className="t-value">{pct(k.essential_share_delivered)}</span>
          <span className="t-sub">{k.essential_classes.map(typeName).join(", ")} · {k.essential_requests_left_empty} left with nothing</span></div>
        <div className="tile"><span className="t-label">Requests left with nothing</span><span className="t-value">{k.requests_left_empty}</span>
          <span className="t-sub">of {k.requests_decided} decided</span></div>
        <div className="tile"><span className="t-label">Complaints</span><span className="t-value">{issues.reduce((s, [, n]) => s + n, 0)}</span>
          <span className="t-sub">{issues.map(([c, n]) => `${c.replace("_", " ")} ${n}`).join(" · ") || "none"}; {k.issues_open} open{k.issue_resolution_hours != null ? `, ${k.issue_resolution_hours} h to resolve` : ""}</span></div>
        <div className="tile"><span className="t-label">Latest timetable</span>
          <span className="t-value">{k.latest_timetable ? `${Math.round(k.latest_timetable.min_lpcd)} L` : "–"}</span>
          <span className="t-sub">{k.latest_timetable ? `lowest per person on ${k.latest_timetable.for_date}; ${k.latest_timetable.zones_below_norm} zone(s) below norm` : "none published"}</span></div>
        <div className="tile"><span className="t-label">Tanker loads delivered</span><span className="t-value">{k.tanker_trips_delivered}</span>
          <span className="t-sub">{fmtL(k.tanker_litres_delivered)}</span></div>
        <div className="tile"><span className="t-label">Leak alerts</span><span className="t-value">{k.leak_alerts}</span>
          <span className="t-sub">detector and complaint clusters</span></div>
      </div>
      <p className="note">Policy in force: {k.active_policy ? `v${k.active_policy.version} ${k.active_policy.title}` : "none"}. For a pilot,
        compare these numbers with the period before the system was used (complaints, zones without supply, time per decision).</p>
    </section>
  );
}

function Ops({ notify }) {
  const [o, setO] = useState(null);
  const load = useCallback(() => api.ops().then(setO).catch((e) => notify(e.message, true)), [notify]);
  useEffect(() => { load(); }, [load]);
  if (!o) return <p className="muted">Loading…</p>;
  const ok = (b) => <span className={b ? "tag tag-ok" : "tag tag-short"}>{b ? "yes" : "no"}</span>;
  return (
    <section aria-labelledby="ops-title">
      <h3 id="ops-title">Operations and security status</h3>
      <dl className="kv two">
        <div><dt>Database version</dt><dd>{o.schema_version} (upgraded {fmtDateTime(o.schema_upgraded_at)}) · {(o.database_bytes / 1e6).toFixed(1)} MB</dd></div>
        <div><dt>API logs in as restricted DB user</dt><dd>{ok(o.least_privilege_db_user)} <span className="muted">({o.database_user})</span></dd></div>
        <div><dt>Administrators with two-factor sign-in</dt><dd>{o.admins_with_2fa} of {o.admins} · required: {ok(o.admin_2fa_required)}</dd></div>
        <div><dt>Maker-checker for policy</dt><dd>{ok(o.maker_checker)}</dd></div>
        <div><dt>Sensors</dt><dd>{o.devices} registered · {o.devices_offline} offline</dd></div>
        <div><dt>Last forecasting run</dt><dd>{o.last_ml_run ? fmtDateTime(o.last_ml_run) : "never"}</dd></div>
        <div><dt>Last data-retention run</dt><dd>{o.last_retention_run ? fmtDateTime(o.last_retention_run) : "never"}
          {" "}<button type="button" className="btn btn-small" onClick={async () => {
            try { const r = await api.runRetention(); notify(`Retention: ${Object.entries(r).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(", ")}.`); load(); }
            catch (e) { notify(e.message, true); }
          }}>Run now</button></dd></div>
        <div><dt>Rate limits</dt><dd>{o.rate_limits.sign_in_per_minute} sign-ins / {o.rate_limits.api_per_minute} requests per minute per address</dd></div>
        <div><dt>Open incidents · privacy requests</dt><dd>{o.open_incidents} · {o.open_privacy_requests}</dd></div>
      </dl>
      <h4>Backups</h4>
      {!o.backups.configured ? <p className="form-info">The backup service is not connected to this API (it runs with docker compose).</p>
        : o.backups.files.length === 0 ? <p className="form-info">No backup files yet.</p> : (
          <ul className="plain-list small">
            {o.backups.files.map((b) => <li key={b.name}>{b.name} · {(b.bytes / 1e6).toFixed(2)} MB · {fmtDateTime(b.modified)}</li>)}
          </ul>
        )}
    </section>
  );
}

function PrivacyRequests({ notify }) {
  const [rows, setRows] = useState(null);
  const [resp, setResp] = useState({});
  const load = useCallback(() => api.privacyRequests().then(setRows).catch((e) => notify(e.message, true)), [notify]);
  useEffect(() => { load(); }, [load]);
  const act = async (r, status) => {
    try { await api.handlePrivacy(r.request_id, status, resp[r.request_id] || ""); notify(`Request #${r.request_id} ${status}.`); load(); }
    catch (e) { notify(e.message, true); }
  };
  if (!rows) return <p className="muted">Loading…</p>;
  return (
    <section aria-labelledby="pr-title">
      <h3 id="pr-title">Data-principal requests (DPDP Act)</h3>
      {rows.length === 0 ? <Empty title="No requests.">Users ask for corrections, erasure or raise grievances from their profile.</Empty> : (
        <ul className="issue-list">
          {rows.map((r) => (
            <li key={r.request_id} className="issue-card">
              <div className="ic-head"><span className="cat-tag">{r.kind}</span><span className={`tag tag-${r.status}`}>{r.status}</span>
                <span className="muted small">#{r.request_id} · {r.email} · {fmtDateTime(r.created_at)}</span></div>
              {r.details && <p>{r.details}</p>}
              {r.status === "open" ? (
                <div className="decide">
                  <input aria-label="Response" placeholder="Response to the person (required)" value={resp[r.request_id] || ""}
                    onChange={(e) => setResp({ ...resp, [r.request_id]: e.target.value })} />
                  <button type="button" className="btn btn-primary" onClick={() => act(r, "completed")}>
                    {r.kind === "erasure" ? "Erase account" : "Mark done"}</button>
                  <button type="button" className="btn" onClick={() => act(r, "rejected")}>Reject</button>
                </div>
              ) : <p className="small muted">{r.response} · {fmtDateTime(r.handled_at)}</p>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Incidents({ notify }) {
  const [rows, setRows] = useState(null);
  const [f, setF] = useState({ title: "", description: "", severity: "medium", personal_data_affected: false });
  const load = useCallback(() => api.incidents().then(setRows).catch((e) => notify(e.message, true)), [notify]);
  useEffect(() => { load(); }, [load]);
  const due = (iso, done) => {
    if (done) return <span className="tag tag-ok">reported {fmtDateTime(done)}</span>;
    const late = new Date(iso) < new Date();
    return <span className={late ? "tag tag-short" : "tag"}>{late ? "overdue since" : "due"} {fmtDateTime(iso)}</span>;
  };
  return (
    <section aria-labelledby="inc-title">
      <h3 id="inc-title">Security incident register</h3>
      <p className="small muted">CERT-In: report specified cyber incidents within 6 hours of noticing them. Personal-data breach:
        inform the Data Protection Board without delay, with a detailed report within 72 hours. This register tracks the deadlines; it does not file the reports.</p>
      <form className="form card-plain" onSubmit={async (e) => {
        e.preventDefault();
        try { await api.createIncident(f); setF({ title: "", description: "", severity: "medium", personal_data_affected: false }); load(); }
        catch (err) { notify(err.message, true); }
      }}>
        <input aria-label="Title" placeholder="What happened (short)" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} required />
        <textarea aria-label="Description" rows={2} placeholder="Details, systems affected, how it was noticed" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} required />
        <div className="form-row">
          <select aria-label="Severity" value={f.severity} onChange={(e) => setF({ ...f, severity: e.target.value })}>
            {["low", "medium", "high", "critical"].map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <label className="check"><input type="checkbox" checked={f.personal_data_affected} onChange={(e) => setF({ ...f, personal_data_affected: e.target.checked })} /><span>Personal data affected</span></label>
        </div>
        <button type="submit" className="btn btn-primary">Record incident</button>
      </form>
      {rows && rows.length > 0 && (
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th scope="col">Incident</th><th scope="col">Status</th><th scope="col">CERT-In</th><th scope="col">Data Protection Board</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.incident_id}>
                  <th scope="row"><span className="strong">{r.title}</span><span className="sub">{r.severity} · detected {fmtDateTime(r.detected_at)}</span></th>
                  <td><span className={`tag tag-${r.status}`}>{r.status}</span></td>
                  <td>{due(r.cert_in_due, r.reported_cert_in_at)}</td>
                  <td>{r.personal_data_affected ? due(r.board_due, r.reported_board_at) : <span className="muted">n/a</span>}</td>
                  <td className="row-actions">
                    {!r.reported_cert_in_at && <button type="button" className="btn btn-small" onClick={() => api.updateIncident(r.incident_id, { reported_cert_in: true }).then(load)}>CERT-In reported</button>}
                    {r.personal_data_affected && !r.reported_board_at && <button type="button" className="btn btn-small" onClick={() => api.updateIncident(r.incident_id, { reported_board: true }).then(load)}>Board informed</button>}
                    {r.status !== "closed" && <button type="button" className="btn btn-small btn-quiet" onClick={() => api.updateIncident(r.incident_id, { status: r.status === "open" ? "contained" : "closed" }).then(load)}>{r.status === "open" ? "Contained" : "Close"}</button>}
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

const SECTIONS = [["kpis", "Pilot indicators"], ["ops", "Operations"], ["privacy", "Privacy requests"],
  ["incidents", "Incidents"], ["audit", "Audit log"]];

export default function Compliance({ notify }) {
  const [sec, setSec] = useState("kpis");
  return (
    <div className="stack">
      <div className="subtabs" role="tablist" aria-label="Compliance sections">
        {SECTIONS.map(([id, label]) => (
          <button key={id} type="button" role="tab" aria-selected={sec === id} className={sec === id ? "subtab active" : "subtab"} onClick={() => setSec(id)}>{label}</button>
        ))}
      </div>
      {sec === "kpis" && <Kpis />}
      {sec === "ops" && <Ops notify={notify} />}
      {sec === "privacy" && <PrivacyRequests notify={notify} />}
      {sec === "incidents" && <Incidents notify={notify} />}
      {sec === "audit" && <AuditLog />}
    </div>
  );
}
