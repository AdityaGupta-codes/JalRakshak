import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import { USER_TYPE, fmtDateTime, typeName } from "./format.js";
import { ConfirmButton, Empty, Priority } from "./ui.jsx";

const PRIORITIES = [5, 4, 3, 2, 1];
const DEFAULT_PRIORITY = { hospital: 5, school: 4, domestic: 4, commercial: 2, industry: 2, park: 1 };
const STATUS_LABEL = { pending: "Waiting for approval", active: "Active", disabled: "Disabled", rejected: "Rejected" };

// Shown once after a password is created or reset: the admin passes it on; it must be changed at first sign-in.
function TempPassword({ value, email, onClose }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="temp-pass" role="status">
      <div>
        <strong>Temporary password for {email}</strong>
        <p className="small">Give it to the person securely. It is shown only now, and they must change it when they sign in.</p>
      </div>
      <code className="big-code" id="temp-pass-value">{value}</code>
      <div className="form-actions">
        <button type="button" className="btn" onClick={async () => {
          try { await navigator.clipboard.writeText(value); setCopied(true); } catch {
            const r = document.createRange();
            r.selectNodeContents(document.getElementById("temp-pass-value"));
            window.getSelection().removeAllRanges();
            window.getSelection().addRange(r);
          }
        }}>{copied ? "Copied" : "Copy"}</button>
        <button type="button" className="btn btn-quiet" onClick={onClose}>Done</button>
      </div>
    </div>
  );
}

function PendingCard({ acc, users, notify, onChanged }) {
  const [mode, setMode] = useState("new");
  const [priority, setPriority] = useState(DEFAULT_PRIORITY[acc.org_type] || 3);
  const [linkTo, setLinkTo] = useState("");
  const [busy, setBusy] = useState(false);
  const approve = async () => {
    setBusy(true);
    try {
      await api.approveAccount(acc.account_id, mode === "new" ? { priority: Number(priority) } : { user_id: Number(linkTo) });
      notify(`${acc.full_name} approved. They can sign in now.`);
      onChanged();
    } catch (e) {
      notify(e.message, true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <li className="pending-card">
      <div className="pc-main">
        <span className="strong">{acc.org_name}</span>
        <span className="sub">{typeName(acc.org_type)} · {acc.org_zone_name || "no zone"}</span>
        <span className="small">{acc.full_name} · {acc.email}{acc.phone ? ` · ${acc.phone}` : ""}</span>
        <span className="sub">Signed up {fmtDateTime(acc.created_at)}</span>
      </div>
      <div className="pc-decide">
        <div className="seg-mini" role="radiogroup" aria-label="Organisation">
          <label><input type="radio" name={`m-${acc.account_id}`} checked={mode === "new"} onChange={() => setMode("new")} /> New organisation</label>
          <label><input type="radio" name={`m-${acc.account_id}`} checked={mode === "link"} onChange={() => setMode("link")} /> Existing one</label>
        </div>
        {mode === "new" ? (
          <label htmlFor={`p-${acc.account_id}`} className="inline-label"><span>Priority</span>
            <select id={`p-${acc.account_id}`} value={priority} onChange={(e) => setPriority(e.target.value)}>
              {PRIORITIES.map((p) => <option key={p} value={p}>{p}{p === 5 ? " (highest)" : p === 1 ? " (lowest)" : ""}</option>)}
            </select>
          </label>
        ) : (
          <label htmlFor={`l-${acc.account_id}`} className="inline-label"><span>Organisation</span>
            <select id={`l-${acc.account_id}`} value={linkTo} onChange={(e) => setLinkTo(e.target.value)}>
              <option value="">Choose…</option>
              {users.map((u) => <option key={u.user_id} value={u.user_id}>{u.name}</option>)}
            </select>
          </label>
        )}
        <div className="form-actions">
          <button type="button" className="btn btn-primary" disabled={busy || (mode === "link" && !linkTo)} onClick={approve}>Approve</button>
          <ConfirmButton label="Reject" confirmLabel="Yes, reject" question={`Reject ${acc.org_name}?`}
            onConfirm={async () => {
              try { await api.rejectAccount(acc.account_id); notify("Sign-up rejected."); onChanged(); } catch (e) { notify(e.message, true); }
            }} />
        </div>
      </div>
    </li>
  );
}

function CreateAccount({ users, notify, onCreated }) {
  const [f, setF] = useState({ email: "", full_name: "", role: "requester", user_id: "" });
  const [error, setError] = useState(null);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  return (
    <form className="add-row" aria-label="Create an account" onSubmit={async (e) => {
      e.preventDefault();
      setError(null);
      if (f.role === "requester" && !f.user_id) return setError("Choose the organisation.");
      try {
        const res = await api.createAccount({ ...f, user_id: f.role === "requester" ? Number(f.user_id) : null });
        setF({ email: "", full_name: "", role: f.role, user_id: "" });
        onCreated(res);
      } catch (err) {
        setError(err.message);
      }
    }}>
      <label htmlFor="ca-name"><span>Name</span><input id="ca-name" value={f.full_name} onChange={set("full_name")} required maxLength={100} /></label>
      <label htmlFor="ca-email"><span>Email</span><input id="ca-email" type="email" value={f.email} onChange={set("email")} required /></label>
      <label htmlFor="ca-role"><span>Role</span>
        <select id="ca-role" value={f.role} onChange={set("role")}>
          <option value="requester">User (requests water)</option>
          <option value="admin">Administrator</option>
        </select>
      </label>
      {f.role === "requester" && (
        <label htmlFor="ca-org"><span>Organisation</span>
          <select id="ca-org" value={f.user_id} onChange={set("user_id")}>
            <option value="">Choose…</option>
            {users.map((u) => <option key={u.user_id} value={u.user_id}>{u.name}</option>)}
          </select>
        </label>
      )}
      <button type="submit" className="btn">Create account</button>
      {error && <p className="form-error" role="alert">{error}</p>}
    </form>
  );
}

export default function Accounts({ me, users, notify, onChanged }) {
  const [rows, setRows] = useState(null);
  const [filter, setFilter] = useState("all");
  const [temp, setTemp] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try { setRows(await api.accounts()); setError(null); } catch (e) { setError(e.message); }
  }, []);
  useEffect(() => { load(); }, [load]);
  const changed = () => { load(); onChanged(); };

  if (error) return <p className="form-error" role="alert">{error}</p>;
  if (!rows) return <p className="muted">Loading accounts…</p>;
  const pending = rows.filter((a) => a.status === "pending");
  const others = rows.filter((a) => a.status !== "pending" && (filter === "all" || a.role === filter || a.status === filter));

  const act = async (fn, okText) => {
    try { await fn(); notify(okText); changed(); } catch (e) { notify(e.message, true); }
  };

  return (
    <div className="stack">
      <section aria-labelledby="acc-pending">
        <div className="section-head"><h2 id="acc-pending">Sign-ups waiting for approval</h2></div>
        {pending.length === 0 ? (
          <Empty title="No sign-ups are waiting." />
        ) : (
          <ul className="pending-list">
            {pending.map((a) => <PendingCard key={a.account_id} acc={a} users={users} notify={notify} onChanged={changed} />)}
          </ul>
        )}
      </section>

      {temp && <TempPassword value={temp.password} email={temp.email} onClose={() => setTemp(null)} />}

      <section aria-labelledby="acc-all">
        <div className="section-head">
          <h2 id="acc-all">All accounts</h2>
          <label className="inline-field"><span className="sr-only">Show</span>
            <select value={filter} onChange={(e) => setFilter(e.target.value)}>
              <option value="all">Everyone</option>
              <option value="admin">Administrators</option>
              <option value="requester">Users</option>
              <option value="disabled">Disabled</option>
              <option value="rejected">Rejected</option>
            </select>
          </label>
        </div>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr><th scope="col">Person</th><th scope="col">Role</th><th scope="col">Organisation</th>
                <th scope="col">Status</th><th scope="col">Last sign-in</th><th scope="col"><span className="sr-only">Actions</span></th></tr>
            </thead>
            <tbody>
              {others.map((a) => (
                <tr key={a.account_id}>
                  <td><span className="strong">{a.full_name}</span><span className="sub">{a.email}</span></td>
                  <td>{a.role === "admin" ? "Administrator" : "User"}</td>
                  <td>
                    {a.organisation ? <><span>{a.organisation}</span><span className="sub">{typeName(a.user_type)} · {a.zone_name}</span><Priority value={a.priority} /></> : <span className="muted">–</span>}
                  </td>
                  <td>
                    <span className={`badge acc-${a.status}`}>{STATUS_LABEL[a.status]}</span>
                    {a.locked && <span className="sub">Locked after wrong passwords</span>}
                    {a.must_change_password && a.status === "active" && <span className="sub">Must change password</span>}
                    {a.mfa_enabled && <span className="sub">Two-factor sign-in on</span>}
                  </td>
                  <td className="small">{a.last_login_at ? fmtDateTime(a.last_login_at) : <span className="muted">Never</span>}</td>
                  <td className="row-actions">
                    {a.account_id !== me.account_id && a.status === "active" && (
                      <ConfirmButton label="Disable" confirmLabel="Yes, disable" className="btn btn-small"
                        question={`Disable ${a.full_name}? They are signed out at once.`}
                        onConfirm={() => act(() => api.updateAccount(a.account_id, { status: "disabled" }), "Account disabled.")} />
                    )}
                    {a.status === "disabled" && (
                      <button type="button" className="btn btn-small" onClick={() => act(() => api.updateAccount(a.account_id, { status: "active" }), "Account enabled.")}>Enable</button>
                    )}
                    {a.status !== "rejected" && (
                      <ConfirmButton label="Reset password" confirmLabel="Yes, reset" className="btn btn-small"
                        question="Create a temporary password?"
                        onConfirm={async () => {
                          try { const r = await api.resetPassword(a.account_id); setTemp({ password: r.temporary_password, email: a.email }); changed(); }
                          catch (e) { notify(e.message, true); }
                        }} />
                    )}
                    {a.mfa_enabled && a.account_id !== me.account_id && (
                      <ConfirmButton label="Reset 2FA" confirmLabel="Yes, reset" className="btn btn-small"
                        question="Lost phone? Turn off two-factor sign-in so they can set it up again."
                        onConfirm={() => act(() => api.resetMfa(a.account_id), "Two-factor sign-in reset.")} />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section aria-labelledby="acc-new">
        <h2 id="acc-new">Create an account</h2>
        <p className="muted small">For someone who should not wait for a sign-up, or for another administrator. A temporary password is shown once.</p>
        <CreateAccount users={users} notify={notify} onCreated={(res) => {
          setTemp({ password: res.temporary_password, email: res.email });
          notify(`Account created for ${res.full_name}.`);
          changed();
        }} />
      </section>
      <p className="note">Organisation types: {Object.values(USER_TYPE).join(", ")}. Change an organisation's priority in Settings.</p>
    </div>
  );
}
