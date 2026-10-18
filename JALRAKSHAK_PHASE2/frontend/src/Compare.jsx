import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import { ALGOS, METRICS, algoName, fmtL, fmtMin, typeName } from "./format.js";
import { ConfirmButton, Empty, Priority } from "./ui.jsx";

// Which columns hold the best value for this measure? (none when all algorithms tie)
function bestColumns(metric, results) {
  const vals = results.map((r) => (r && r.metrics ? Number(r.metrics[metric.key]) : null));
  const valid = vals.filter((v) => v !== null);
  if (valid.length < 2) return new Set();
  const best = metric.better === "high" ? Math.max(...valid) : Math.min(...valid);
  const worst = metric.better === "high" ? Math.min(...valid) : Math.max(...valid);
  if (Math.abs(best - worst) < 1e-6) return new Set();
  const winners = new Set();
  vals.forEach((v, i) => {
    if (v !== null && Math.abs(v - best) < 1e-6) winners.add(i);
  });
  return winners;
}

function AllocationCell({ alloc, requested }) {
  if (!alloc) return <td className="muted">–</td>;
  const pct = requested > 0 ? Math.min(100, (alloc.allocated_litres / requested) * 100) : 0;
  const none = alloc.status === "rejected";
  const details = [];
  if (alloc.wait_time != null) details.push(`waits ${fmtMin(alloc.wait_time)}`);
  if (alloc.score != null) details.push(`score ${Number(alloc.score).toFixed(2)}`);
  return (
    <td className="cell">
      <div
        className={`bar bar-${alloc.status}`}
        role="img"
        aria-label={none ? "No water" : `${Math.round(pct)}% of the request`}
      >
        <span style={{ width: `${pct}%` }} />
      </div>
      <div className="cell-main">
        <span className={none ? "strong none" : "strong"}>{none ? "No water" : fmtL(alloc.allocated_litres)}</span>
        {!none && <span className="muted"> {Math.round(pct)}%</span>}
      </div>
      {details.length > 0 && <div className="sub">{details.join(", ")}</div>}
    </td>
  );
}

// Officer override: start from one rule's plan, change litres, give a reason, apply. Or record the plan
// in shadow mode (pilot): nothing changes, the recommendation is kept for later comparison.
function OverridePanel({ data, results, onApplied, notify }) {
  const usable = ALGOS.filter((a, i) => results[i] && !results[i].error);
  const [base, setBase] = useState(usable.length ? usable[usable.length - 1].id : "");
  const [edits, setEdits] = useState({});
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState(null);
  const plan = results[ALGOS.findIndex((a) => a.id === base)];
  useEffect(() => { setEdits({}); }, [base, data]);
  if (!plan || plan.error) return null;
  const planned = Object.fromEntries(plan.allocations.map((a) => [a.request_id, a.allocated_litres]));
  const value = (id) => (edits[id] !== undefined ? edits[id] : planned[id]);
  const changed = Object.entries(edits).filter(([id, v]) => v !== "" && Math.abs(Number(v) - planned[id]) > 0.004);
  const total = data.requests.reduce((s, r) => s + (Number(value(r.request_id)) || 0), 0);
  const over = total > data.available_litres + 0.004;
  const apply = async () => {
    setError(null);
    const overrides = Object.fromEntries(changed.map(([id, v]) => [id, Number(v)]));
    try {
      const res = await api.commit(base, overrides, reason.trim());
      notify(`${algoName(base)} applied${changed.length ? ` with ${changed.length} change(s) by the officer` : ""} (run #${res.run_id}). The decision record is in History.`);
      setEdits({}); setReason("");
      await onApplied();
    } catch (e) { setError(e.message); }
  };
  return (
    <section className="card-plain override" aria-labelledby="ov-title">
      <h2 id="ov-title">Change the plan before applying (officer override)</h2>
      <p className="small muted">Start from a rule's plan, change any quantity, and give the reason. The reason, the original
        and the final litres go into the decision record and the audit log, and the organisations are told the
        quantity was set by an officer.</p>
      <label htmlFor="ov-base"><span>Start from</span>
        <select id="ov-base" value={base} onChange={(e) => setBase(e.target.value)}>
          {usable.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
      </label>
      <div className="table-wrap">
        <table className="data">
          <thead><tr><th scope="col">Request</th><th scope="col" className="num">Asked</th><th scope="col" className="num">Plan</th><th scope="col" className="num">Final litres</th></tr></thead>
          <tbody>
            {data.requests.map((r) => {
              const v = value(r.request_id);
              const diff = edits[r.request_id] !== undefined && Math.abs(Number(v) - planned[r.request_id]) > 0.004;
              return (
                <tr key={r.request_id} className={diff ? "changed" : ""}>
                  <th scope="row"><span className="strong">{r.user_name}</span><span className="sub">{typeName(r.user_type)}</span></th>
                  <td className="num">{fmtL(r.requested_litres)}</td>
                  <td className="num">{fmtL(planned[r.request_id])}</td>
                  <td className="num">
                    <input aria-label={`Final litres for ${r.user_name}`} className="num-input" type="number" min="0"
                      max={r.requested_litres} step="any" value={v}
                      onChange={(e) => setEdits({ ...edits, [r.request_id]: e.target.value })} />
                  </td>
                </tr>
              );
            })}
          </tbody>
          <tfoot><tr><th scope="row">Total</th><td /><td className="num">{fmtL(data.available_litres - plan.remaining_litres)}</td>
            <td className={over ? "num none" : "num strong"}>{fmtL(total)} of {fmtL(data.available_litres)}</td></tr></tfoot>
        </table>
      </div>
      <label htmlFor="ov-reason"><span>Reason for the changes (goes into the decision record)</span>
        <textarea id="ov-reason" rows={2} maxLength={1000} value={reason} onChange={(e) => setReason(e.target.value)}
          placeholder="e.g. Dialysis unit added today; Collector's order no. …" />
      </label>
      {over && <p className="form-error">The plan gives out more water than is available.</p>}
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="form-actions">
        <ConfirmButton label={changed.length ? `Apply with ${changed.length} change(s)` : `Apply ${algoName(base)}`}
          confirmLabel="Yes, apply" className="btn btn-primary" disabled={over || (changed.length > 0 && reason.trim().length < 10)}
          question="Apply this plan? This takes the water out of the reservoir." onConfirm={apply} />
      </div>
      <details className="shadow-box">
        <summary>Pilot (shadow) mode: record the recommendation without applying it</summary>
        <p className="small muted">Used while the department still decides outside the system: the recommended plan is
          stored for later comparison; water and requests do not change.</p>
        <input aria-label="Note" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} placeholder="Note, e.g. pilot week 2" />
        <button type="button" className="btn" onClick={async () => {
          try { const r = await api.shadow(base, note); notify(`Shadow plan #${r.run_id} recorded (nothing changed).`); setNote(""); }
          catch (e) { notify(e.message, true); }
        }}>Record {algoName(base)} as a shadow plan</button>
      </details>
    </section>
  );
}

export default function Compare({ status, onApplied, notify }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const pending = status ? status.pending_count : null;

  const run = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.simulate(ALGOS.map((a) => a.id)));
    } catch (e) {
      setData(null);
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  // Re-run whenever the number of pending requests changes (applied, added or reset).
  useEffect(() => {
    run();
  }, [run, pending]);

  const apply = async (algorithm) => {
    try {
      const res = await api.commit(algorithm);
      const total = res.allocations.reduce((s, a) => s + a.allocated_litres, 0);
      notify(`${algoName(algorithm)} applied: ${fmtL(total)} delivered (run #${res.run_id}).`);
      await onApplied();
    } catch (e) {
      notify(e.message, true);
    }
  };

  const head = (
    <div className="section-head">
      <h2>Compare allocation rules</h2>
      <button type="button" className="btn" onClick={run} disabled={loading}>
        {loading ? "Running…" : "Run again"}
      </button>
    </div>
  );

  if (error) {
    return (
      <section>
        {head}
        <p className="form-error" role="alert">{error}</p>
      </section>
    );
  }
  if (!data) return <section>{head}<p className="muted">Running the four algorithms…</p></section>;
  if (data.pending_count === 0) {
    return (
      <section>
        {head}
        <Empty title="There are no pending requests to allocate.">
          Add requests on the Overview tab, or reset the demo data to restore the sample requests.
        </Empty>
      </section>
    );
  }

  const demand = data.requests.reduce((s, r) => s + r.requested_litres, 0);
  const byAlgo = Object.fromEntries(data.results.map((r) => [r.algorithm, r]));
  const results = ALGOS.map((a) => byAlgo[a.id] || null);
  const ok = (r) => r && !r.error;

  return (
    <section aria-busy={loading}>
      {head}
      <p className="lede">
        {data.pending_count} pending requests ask for {fmtL(demand)}, and {fmtL(data.available_litres)} is available.
        Each column shows what one rule would do with the same water. Nothing changes until you apply a plan.
      </p>

      <div className="table-wrap">
        <table className="data matrix">
          <caption className="sr-only">Comparison of the four allocation rules</caption>
          <thead>
            <tr>
              <th scope="col">Measure</th>
              {ALGOS.map((a, i) => (
                <th scope="col" key={a.id} className="algo-head">
                  <span className="algo-name">{a.name}</span>
                  <span className="sub">{a.blurb}</span>
                  {ok(results[i]) ? (
                    <ConfirmButton
                      label="Apply this plan"
                      confirmLabel="Yes, apply"
                      question={`Use ${a.name}? This takes the water out of the reservoir.`}
                      onConfirm={() => apply(a.id)}
                      className="btn btn-primary"
                    />
                  ) : (
                    <span className="sub none">{results[i] ? results[i].error : "No result"}</span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {METRICS.map((m) => {
              const winners = bestColumns(m, results);
              return (
                <tr key={m.key}>
                  <th scope="row">
                    <span className="strong">{m.label}</span>
                    <span className="sub">{m.hint}</span>
                  </th>
                  {results.map((r, i) => (
                    <td key={ALGOS[i].id} className={winners.has(i) ? "metric best" : "metric"}>
                      {ok(r) ? m.fmt(r.metrics[m.key]) : "–"}
                      {winners.has(i) && <span className="sr-only"> (best)</span>}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="note">Shaded cells mark the best result in each row. Rows where every rule ties are not shaded.</p>

      <h2 className="ledger-title">Who gets water under each rule</h2>
      <div className="table-wrap">
        <table className="data ledger">
          <caption className="sr-only">Litres each request receives under each rule</caption>
          <thead>
            <tr>
              <th scope="col">Request</th>
              {ALGOS.map((a) => (
                <th scope="col" key={a.id}>{a.name}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.requests.map((req) => (
              <tr key={req.request_id}>
                <th scope="row">
                  <span className="strong">{req.user_name}</span>
                  <span className="sub">
                    {typeName(req.user_type)}, asked for {fmtL(req.requested_litres)}
                  </span>
                  <Priority value={req.priority} />
                </th>
                {results.map((r, i) => (
                  <AllocationCell
                    key={ALGOS[i].id}
                    alloc={ok(r) ? r.allocations.find((a) => a.request_id === req.request_id) : null}
                    requested={req.requested_litres}
                  />
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <OverridePanel data={data} results={results} onApplied={onApplied} notify={notify} />
    </section>
  );
}
