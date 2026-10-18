import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import { fmtDateTime } from "./format.js";
import { Empty } from "./ui.jsx";

const GROUPS = [
  ["", "Everything"],
  ["login", "Sign-ins"],
  ["plan.", "Plans applied"],
  ["account.", "Accounts"],
  ["request.", "Requests"],
  ["issue.", "Issues"],
  ["reservoir.", "Reservoir"],
  ["zone.", "Zones"],
  ["organisation.", "Organisations"],
  ["alert.", "Alerts"],
  ["demo.", "Demo resets"],
];

const ACTION_TEXT = {
  login: "Signed in", "login.failed": "Failed sign-in", logout: "Signed out",
  "plan.apply": "Applied a plan", "demo.reset": "Reset the demo data",
  "account.register": "Signed up", "account.approve": "Approved a sign-up", "account.reject": "Rejected a sign-up",
  "account.create": "Created an account", "account.update": "Changed an account", "account.password_reset": "Reset a password",
  "account.password_change": "Changed own password", "account.profile_update": "Updated own profile",
  "account.bootstrap_admin": "First administrator created", "account.bootstrap_demo": "Demo accounts created",
  "request.create": "Created a request", "request.cancel": "Cancelled a request",
  "issue.create": "Reported an issue", "issue.update": "Updated an issue",
  "reservoir.update": "Changed the reservoir", "zone.create": "Added a zone", "zone.update": "Changed a zone",
  "zone.delete": "Deleted a zone", "organisation.create": "Added an organisation",
  "organisation.update": "Changed an organisation", "organisation.delete": "Deleted an organisation",
  "alert.resolve": "Marked an alert as handled",
};

function details(d) {
  if (!d || typeof d !== "object") return "";
  return Object.entries(d)
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `${k.replace(/_/g, " ")}: ${typeof v === "object" ? JSON.stringify(v) : v}`)
    .join(" · ");
}

export default function AuditLog() {
  const [group, setGroup] = useState("");
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const load = useCallback(async () => {
    try { setRows(await api.audit(group)); setError(null); } catch (e) { setError(e.message); }
  }, [group]);
  useEffect(() => { load(); }, [load]);

  return (
    <section aria-labelledby="aud-title">
      <div className="section-head">
        <h2 id="aud-title">Audit log</h2>
        <div className="form-actions">
          <label className="inline-field"><span className="sr-only">Show</span>
            <select value={group} onChange={(e) => setGroup(e.target.value)}>
              {GROUPS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
          <button type="button" className="btn" onClick={load}>Refresh</button>
        </div>
      </div>
      <p className="lede">Every sign-in and every change, newest first. Entries are written in the same database transaction as the change itself and cannot be edited or deleted.</p>
      {error && <p className="form-error" role="alert">{error}</p>}
      {rows && rows.length === 0 && <Empty title="No entries." />}
      {rows && rows.length > 0 && (
        <div className="table-wrap">
          <table className="data audit">
            <thead><tr><th scope="col">When</th><th scope="col">Who</th><th scope="col">What</th><th scope="col">Details</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.audit_id} className={r.action === "login.failed" ? "warn-row" : ""}>
                  <td className="small nowrap">{fmtDateTime(r.created_at)}</td>
                  <td className="small">{r.actor}</td>
                  <td>
                    <span>{ACTION_TEXT[r.action] || r.action}</span>
                    {r.entity && <span className="sub">{r.entity}{r.entity_id ? ` #${r.entity_id}` : ""}</span>}
                  </td>
                  <td className="small detail-cell">{details(r.details)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
