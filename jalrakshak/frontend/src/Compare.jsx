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
    </section>
  );
}
