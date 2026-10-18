import { useT } from "./i18n.js";
import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import { ISSUE_CATEGORY, ISSUE_STATUS, fmtDateTime } from "./format.js";
import { Empty } from "./ui.jsx";

export function IssueBadge({ status }) {
  const t = useT();
  return <span className={`badge issue-${status}`}>{t(ISSUE_STATUS[status] || status)}</span>;
}

function IssueCard({ issue, notify, onSaved }) {
  const [status, setStatus] = useState(issue.status);
  const [response, setResponse] = useState("");
  const [busy, setBusy] = useState(false);
  const changed = status !== issue.status || response.trim() !== "";
  return (
    <li className={`issue-card cat-${issue.category}`}>
      <div className="ic-head">
        <span className="cat-tag">{ISSUE_CATEGORY[issue.category]}</span>
        <IssueBadge status={issue.status} />
        <span className="muted small">#{issue.issue_id} · {fmtDateTime(issue.created_at)}</span>
      </div>
      <h3>{issue.title}</h3>
      <p>{issue.description}</p>
      <p className="small muted">
        {issue.reporter ? `${issue.reporter} (${issue.reporter_email})` : "Unknown reporter"}
        {issue.organisation ? ` · ${issue.organisation}` : ""}{issue.zone_name ? ` · ${issue.zone_name}` : ""}
        {issue.location ? ` · ${issue.location}` : ""}
      </p>
      {issue.admin_response && (
        <p className="response"><strong>Reply{issue.handled_by_name ? ` by ${issue.handled_by_name}` : ""}:</strong> {issue.admin_response}</p>
      )}
      <form className="ic-form" onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        try {
          await api.updateIssue(issue.issue_id, { status: status !== issue.status ? status : null, response: response.trim() || null });
          setResponse("");
          notify(`Issue #${issue.issue_id} updated. The reporter has been notified.`);
          onSaved();
        } catch (err) {
          notify(err.message, true);
        } finally {
          setBusy(false);
        }
      }}>
        <label htmlFor={`is-${issue.issue_id}`} className="inline-label"><span>Status</span>
          <select id={`is-${issue.issue_id}`} value={status} onChange={(e) => setStatus(e.target.value)}>
            {Object.entries(ISSUE_STATUS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <label htmlFor={`ir-${issue.issue_id}`} className="grow"><span className="sr-only">Reply</span>
          <textarea id={`ir-${issue.issue_id}`} rows={2} maxLength={2000} placeholder="Reply to the reporter (optional)"
            value={response} onChange={(e) => setResponse(e.target.value)} />
        </label>
        <button type="submit" className="btn btn-primary" disabled={!changed || busy}>{busy ? "Saving…" : "Save"}</button>
      </form>
    </li>
  );
}

export default function Issues({ notify, onChanged }) {
  const [rows, setRows] = useState(null);
  const [filter, setFilter] = useState("active");
  const [error, setError] = useState(null);
  const load = useCallback(async () => {
    try { setRows(await api.issues()); setError(null); } catch (e) { setError(e.message); }
  }, []);
  useEffect(() => { load(); }, [load]);

  if (error) return <p className="form-error" role="alert">{error}</p>;
  if (!rows) return <p className="muted">Loading issues…</p>;
  const shown = rows.filter((i) => filter === "all" || (filter === "active" ? ["open", "in_progress"].includes(i.status) : i.status === filter));
  const counts = rows.reduce((c, i) => ({ ...c, [i.status]: (c[i.status] || 0) + 1 }), {});

  return (
    <section aria-labelledby="iss-title" className="stack">
      <div className="section-head">
        <h2 id="iss-title">Issues reported by users</h2>
        <label className="inline-field"><span className="sr-only">Show</span>
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="active">Open and in progress ({(counts.open || 0) + (counts.in_progress || 0)})</option>
            <option value="resolved">Resolved ({counts.resolved || 0})</option>
            <option value="closed">Closed ({counts.closed || 0})</option>
            <option value="all">All ({rows.length})</option>
          </select>
        </label>
      </div>
      {shown.length === 0 ? (
        <Empty title={filter === "active" ? "No open issues." : "Nothing here."}>Leaks and supply problems reported by users appear here.</Empty>
      ) : (
        <ul className="issue-list">
          {shown.map((i) => <IssueCard key={`${i.issue_id}-${i.updated_at}`} issue={i} notify={notify} onSaved={() => { load(); onChanged(); }} />)}
        </ul>
      )}
    </section>
  );
}
