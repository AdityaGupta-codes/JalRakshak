import { Fragment, useCallback, useEffect, useState } from "react";
import { api, download } from "./api.js";
import { algoName, fmtDateTime, fmtL, fmtMin, fmtPct, typeName } from "./format.js";
import { Empty } from "./ui.jsx";

function RunDetail({ id }) {
  const [run, setRun] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    api.run(id).then(setRun).catch((e) => setError(e.message));
  }, [id]);

  if (error) return <p className="form-error">{error}</p>;
  if (!run) return <p className="muted">Loading…</p>;
  const changed = new Set((run.overrides || []).map((o) => o.request_id));
  const get = (fmt) => download(`/runs/${id}/report?format=${fmt}`, `plan-${id}-record.${fmt}`,
    fmt === "csv" ? "text/csv" : "text/html").catch((e) => setError(e.message));
  return (
    <>
    <div className="run-meta">
      <span>Policy v{run.policy_version ?? "–"}{run.policy_reference ? ` · ${run.policy_reference}` : ""}</span>
      <span>By {run.created_by_email || "system"}</span>
      {run.override_reason && <span className="override-note">Officer override: “{run.override_reason}”</span>}
      <span className="run-actions">
        <button type="button" className="btn btn-small" onClick={() => get("html")}>Decision record (print / PDF)</button>
        <button type="button" className="btn btn-small" onClick={() => get("csv")}>CSV</button>
      </span>
    </div>
    <table className="data nested">
      <thead>
        <tr>
          <th scope="col">Requester</th>
          <th scope="col" className="num">Asked for</th>
          <th scope="col" className="num">Received</th>
          <th scope="col" className="num">Waited</th>
          <th scope="col" className="num">Score</th>
        </tr>
      </thead>
      <tbody>
        {run.allocations.map((a) => (
          <tr key={a.request_id}>
            <td>
              <span className="strong">{a.user_name}</span>
              <span className="sub">{typeName(a.user_type)}</span>
            </td>
            <td className="num">{fmtL(a.requested_litres)}</td>
            <td className="num">{Number(a.allocated_litres) > 0 ? fmtL(a.allocated_litres) : <span className="none">None</span>}</td>
            <td className="num">{a.wait_minutes != null ? fmtMin(a.wait_minutes) : "–"}</td>
            <td className="num">{a.score != null ? Number(a.score).toFixed(2) : "–"}</td>
          </tr>
        ))}
      </tbody>
    </table>
    {changed.size > 0 && <p className="note">{changed.size} quantity(ies) were set by the officer; the record shows plan and final litres.</p>}
    </>
  );
}

function Runs({ runs }) {
  const [open, setOpen] = useState(null);
  if (runs.length === 0) {
    return (
      <Empty title="No plans have been applied yet.">
        Compare the algorithms and apply one. Each applied plan is stored in the database and listed here.
      </Empty>
    );
  }
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th scope="col">Run</th>
            <th scope="col">Rule</th>
            <th scope="col">Applied</th>
            <th scope="col" className="num">Water before</th>
            <th scope="col" className="num">Water after</th>
            <th scope="col" className="num">Fairness</th>
            <th scope="col" className="num">Delivered</th>
            <th scope="col"><span className="sr-only">Details</span></th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <Fragment key={r.run_id}>
              <tr>
                <td className="muted">#{r.run_id}</td>
                <td className="strong">{algoName(r.algorithm)}
                  {r.scenario === "shadow" && <span className="tag tag-shadow">shadow</span>}
                  {r.overridden && <span className="tag tag-override">override</span>}
                  {r.policy_id && <span className="sub">policy #{r.policy_id}</span>}</td>
                <td>{fmtDateTime(r.created_at)}</td>
                <td className="num">{fmtL(r.available_before)}</td>
                <td className="num">{fmtL(r.available_after)}</td>
                <td className="num">{r.metrics ? Number(r.metrics.jain_fairness).toFixed(2) : "–"}</td>
                <td className="num">{r.metrics ? fmtPct(r.metrics.satisfaction_ratio) : "–"}</td>
                <td>
                  <button
                    type="button"
                    className="btn btn-quiet"
                    aria-expanded={open === r.run_id}
                    onClick={() => setOpen(open === r.run_id ? null : r.run_id)}
                  >
                    {open === r.run_id ? "Hide" : "Details"}
                  </button>
                </td>
              </tr>
              {open === r.run_id && (
                <tr className="detail-row">
                  <td colSpan={8}><RunDetail id={r.run_id} /></td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function History({ refreshKey }) {
  const [runs, setRuns] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      setRuns(await api.runs());
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, []);
  useEffect(() => {
    load();
  }, [load, refreshKey]);

  if (error) return <p className="form-error" role="alert">{error}</p>;
  if (!runs) return <p className="muted">Loading…</p>;

  return (
    <div className="history">
      <section aria-labelledby="runs-title">
        <div className="section-head"><h2 id="runs-title">Plans and decision records</h2></div>
        <p className="lede">Every applied plan (and every shadow plan recorded during a pilot), newest first. Open one to see
          what each organisation received and to download its decision record for review meetings, audits and RTI replies.</p>
        <Runs runs={runs} />
      </section>
    </div>
  );
}
