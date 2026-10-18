import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import { fmtDateTime, typeName } from "./format.js";
import { ConfirmButton } from "./ui.jsx";

const CLASSES = ["hospital", "domestic", "school", "industry", "commercial", "park"];
const WEIGHTS = [["priority", "Priority"], ["urgency", "Urgency"], ["scarcity", "Scarcity"],
  ["demand_impact", "People affected"], ["fairness", "Fairness (past share)"]];
const STATUS = { draft: "Draft", pending: "Waiting for approval", approved: "Active", rejected: "Rejected", superseded: "Superseded" };

function diffKeys(p, base) {
  if (!base) return new Set();
  const out = new Set();
  for (const k of ["title", "reference", "guarantee_floor", "lpcd", "zone_min_share", "tanker_trigger_lpcd",
    "tanker_capacity_litres", "supply_start_hour", "supply_end_hour"]) if (String(p[k]) !== String(base[k])) out.add(k);
  for (const c of CLASSES) {
    if (p.class_priority[c] !== base.class_priority[c]) out.add(`prio.${c}`);
    if (Number(p.class_min_share[c]) !== Number(base.class_min_share[c])) out.add(`share.${c}`);
  }
  for (const [k] of WEIGHTS) if (Number(p.weights[k]) !== Number(base.weights[k])) out.add(`w.${k}`);
  if (JSON.stringify([...p.essential_classes].sort()) !== JSON.stringify([...base.essential_classes].sort())) out.add("essential");
  return out;
}

function PolicyView({ p, base }) {
  const d = diffKeys(p, base);
  const c = (k) => (d.has(k) ? "changed" : "");
  return (
    <div className="policy-view">
      <dl className="kv">
        <div className={c("reference")}><dt>Government reference</dt><dd>{p.reference || "–"}</dd></div>
        <div className={c("lpcd")}><dt>Norm (litres per person per day)</dt><dd>{p.lpcd}</dd></div>
        <div className={c("supply_start_hour") || c("supply_end_hour")}><dt>Daily supply window</dt><dd>{p.supply_start_hour}:00 – {p.supply_end_hour}:00</dd></div>
        <div className={c("zone_min_share")}><dt>Each zone's guaranteed share (timetable)</dt><dd>{Math.round(p.zone_min_share * 100)}%</dd></div>
        <div className={c("tanker_trigger_lpcd")}><dt>Send tankers below</dt><dd>{p.tanker_trigger_lpcd} L per person · {Number(p.tanker_capacity_litres).toLocaleString()} L per tanker</dd></div>
        <div className={c("essential")}><dt>Essential classes (KPIs)</dt><dd>{p.essential_classes.map(typeName).join(", ")}</dd></div>
      </dl>
      <div className="table-wrap">
        <table className="data compact">
          <thead><tr><th scope="col">Class</th><th scope="col" className="num">Priority</th><th scope="col" className="num">Guaranteed share when short</th></tr></thead>
          <tbody>
            {CLASSES.map((k) => (
              <tr key={k}><th scope="row">{typeName(k)}</th>
                <td className={`num ${c(`prio.${k}`)}`}>{p.class_priority[k]}</td>
                <td className={`num ${c(`share.${k}`)}`}>{Math.round(p.class_min_share[k] * 100)}%</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="small muted">Water-aware weights: {WEIGHTS.map(([k, l]) => (
        <span key={k} className={c(`w.${k}`)}>{l} {Number(p.weights[k]).toFixed(2)}{k !== "fairness" ? " · " : ""}</span>))}
      </p>
      {p.notes && <p className="small">{p.notes}</p>}
    </div>
  );
}

function ProposeForm({ active, notify, onDone }) {
  const [f, setF] = useState(() => ({
    title: "", reference: active.reference || "", notes: "",
    lpcd: active.lpcd, zone_min_share: active.zone_min_share, guarantee_floor: active.guarantee_floor,
    tanker_trigger_lpcd: active.tanker_trigger_lpcd, tanker_capacity_litres: active.tanker_capacity_litres,
    supply_start_hour: active.supply_start_hour, supply_end_hour: active.supply_end_hour,
    class_priority: { ...active.class_priority }, class_min_share: { ...active.class_min_share },
    weights: { ...active.weights }, essential_classes: [...active.essential_classes],
  }));
  const [error, setError] = useState(null);
  const num = (k) => (e) => setF({ ...f, [k]: e.target.value });
  return (
    <form className="form card-plain" onSubmit={async (e) => {
      e.preventDefault();
      setError(null);
      const body = { ...f };
      for (const k of ["lpcd", "zone_min_share", "guarantee_floor", "tanker_trigger_lpcd", "tanker_capacity_litres",
        "supply_start_hour", "supply_end_hour"]) body[k] = Number(f[k]);
      body.class_priority = Object.fromEntries(CLASSES.map((c) => [c, Number(f.class_priority[c])]));
      body.class_min_share = Object.fromEntries(CLASSES.map((c) => [c, Number(f.class_min_share[c])]));
      body.weights = Object.fromEntries(WEIGHTS.map(([k]) => [k, Number(f.weights[k])]));
      try {
        const d = await api.draftPolicy(body);
        await api.submitPolicy(d.policy_id);
        notify(`Policy v${d.version} submitted. A different administrator must approve it.`);
        onDone();
      } catch (err) { setError(err.message); }
    }}>
      <h2>Propose a policy change</h2>
      <p className="small muted">Changes start from the active policy. After you submit, a <strong>different administrator</strong> must approve them (maker-checker).</p>
      <label htmlFor="pp-title"><span>Title</span><input id="pp-title" value={f.title} onChange={num("title")} required maxLength={200} placeholder="e.g. Summer scarcity order 2026" /></label>
      <label htmlFor="pp-ref"><span>Government order / reference</span><input id="pp-ref" value={f.reference} onChange={num("reference")} maxLength={500} placeholder="e.g. G.O. No. … dated …" /></label>
      <div className="table-wrap">
        <table className="data compact">
          <thead><tr><th scope="col">Class</th><th scope="col">Priority (1–5)</th><th scope="col">Guaranteed share (0–1)</th><th scope="col">Essential</th></tr></thead>
          <tbody>
            {CLASSES.map((c) => (
              <tr key={c}><th scope="row">{typeName(c)}</th>
                <td><input aria-label={`${c} priority`} className="num-input" type="number" min="1" max="5" step="1" value={f.class_priority[c]}
                  onChange={(e) => setF({ ...f, class_priority: { ...f.class_priority, [c]: e.target.value } })} /></td>
                <td><input aria-label={`${c} share`} className="num-input" type="number" min="0" max="1" step="0.05" value={f.class_min_share[c]}
                  onChange={(e) => setF({ ...f, class_min_share: { ...f.class_min_share, [c]: e.target.value } })} /></td>
                <td><input aria-label={`${c} essential`} type="checkbox" checked={f.essential_classes.includes(c)}
                  onChange={(e) => setF({ ...f, essential_classes: e.target.checked ? [...f.essential_classes, c] : f.essential_classes.filter((x) => x !== c) })} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="form-row">
        <label htmlFor="pp-lpcd"><span>Norm, litres per person per day</span><input id="pp-lpcd" type="number" min="1" step="any" value={f.lpcd} onChange={num("lpcd")} /></label>
        <label htmlFor="pp-zone"><span>Zone guaranteed share (0–1)</span><input id="pp-zone" type="number" min="0" max="1" step="0.05" value={f.zone_min_share} onChange={num("zone_min_share")} /></label>
      </div>
      <div className="form-row">
        <label htmlFor="pp-s"><span>Supply starts (hour)</span><input id="pp-s" type="number" min="0" max="23" value={f.supply_start_hour} onChange={num("supply_start_hour")} /></label>
        <label htmlFor="pp-e"><span>Supply ends (hour)</span><input id="pp-e" type="number" min="1" max="24" value={f.supply_end_hour} onChange={num("supply_end_hour")} /></label>
      </div>
      <div className="form-row">
        <label htmlFor="pp-tt"><span>Tankers below (L/person)</span><input id="pp-tt" type="number" min="0" step="any" value={f.tanker_trigger_lpcd} onChange={num("tanker_trigger_lpcd")} /></label>
        <label htmlFor="pp-tc"><span>Litres per tanker</span><input id="pp-tc" type="number" min="100" step="any" value={f.tanker_capacity_litres} onChange={num("tanker_capacity_litres")} /></label>
      </div>
      <fieldset><legend>Water-aware weights</legend>
        <div className="form-row wrap">
          {WEIGHTS.map(([k, l]) => (
            <label key={k} htmlFor={`pp-w-${k}`}><span>{l}</span><input id={`pp-w-${k}`} type="number" min="0" step="0.05" value={f.weights[k]}
              onChange={(e) => setF({ ...f, weights: { ...f.weights, [k]: e.target.value } })} /></label>
          ))}
        </div>
      </fieldset>
      <label htmlFor="pp-notes"><span>Notes</span><textarea id="pp-notes" rows={2} value={f.notes} onChange={num("notes")} maxLength={2000} /></label>
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="form-actions">
        <button type="submit" className="btn btn-primary">Submit for approval</button>
        <button type="button" className="btn btn-quiet" onClick={onDone}>Cancel</button>
      </div>
    </form>
  );
}

export default function Policy({ me, notify, onChanged }) {
  const [list, setList] = useState(null);
  const [error, setError] = useState(null);
  const [proposing, setProposing] = useState(false);
  const [notes, setNotes] = useState({});
  const load = useCallback(async () => {
    try { setList(await api.policies()); setError(null); } catch (e) { setError(e.message); }
  }, []);
  useEffect(() => { load(); }, [load]);
  if (error) return <p className="form-error" role="alert">{error}</p>;
  if (!list) return <p className="muted">Loading…</p>;
  const active = list.find((p) => p.status === "approved");
  const decide = async (p, approve) => {
    try {
      await api.decidePolicy(p.policy_id, approve, notes[p.policy_id]);
      notify(approve ? `Policy v${p.version} is now active. Organisation priorities were updated from their class.` : `Policy v${p.version} rejected.`);
      load();
      onChanged();
    } catch (e) { notify(e.message, true); }
  };
  return (
    <div className="stack">
      <div className="section-head"><h2>Water policy</h2>
        {active && !proposing && <button type="button" className="btn btn-primary" onClick={() => setProposing(true)}>Propose a change</button>}
      </div>
      <p className="lede">The policy decides each class's priority and guaranteed share, the per-person norm, and the
        Water-aware weights. Every plan and timetable records the policy version it used.</p>
      {proposing && active && <ProposeForm active={active} notify={notify} onDone={() => { setProposing(false); load(); }} />}
      {active && (
        <section className="card-plain policy-active" aria-labelledby="pa-title">
          <h3 id="pa-title">Active: v{active.version} · {active.title}</h3>
          <p className="small muted">Approved {fmtDateTime(active.decided_at)}{active.decided_by_email ? ` by ${active.decided_by_email}` : ""}
            {active.proposed_by_email ? ` · proposed by ${active.proposed_by_email}` : ""}</p>
          <PolicyView p={active} />
        </section>
      )}
      {list.filter((p) => p.status === "pending" || p.status === "draft").map((p) => (
        <section key={p.policy_id} className="card-plain policy-pending">
          <h3>v{p.version} · {p.title} <span className="tag">{STATUS[p.status]}</span></h3>
          <p className="small muted">Proposed by {p.proposed_by_email || "–"} · {fmtDateTime(p.submitted_at || p.created_at)}. Highlighted values differ from the active policy.</p>
          <PolicyView p={p} base={active} />
          {p.status === "draft" && p.proposed_by === me.account_id && (
            <button type="button" className="btn" onClick={async () => { try { await api.submitPolicy(p.policy_id); load(); } catch (e) { notify(e.message, true); } }}>Submit for approval</button>
          )}
          {p.status === "pending" && (p.proposed_by === me.account_id ? (
            <p className="form-info">Waiting for a different administrator to approve (maker-checker).</p>
          ) : (
            <div className="decide">
              <input aria-label="Decision note" placeholder="Note (required to reject)" value={notes[p.policy_id] || ""}
                onChange={(e) => setNotes({ ...notes, [p.policy_id]: e.target.value })} />
              <ConfirmButton label="Approve and activate" confirmLabel="Yes, activate" className="btn btn-primary"
                question={`Activate v${p.version}? Organisation priorities change to their class values.`} onConfirm={() => decide(p, true)} />
              <button type="button" className="btn" onClick={() => decide(p, false)}>Reject</button>
            </div>
          ))}
        </section>
      ))}
      <section aria-labelledby="ph-title">
        <h3 id="ph-title">History</h3>
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th scope="col">Version</th><th scope="col">Title</th><th scope="col">Status</th><th scope="col">Proposed by</th><th scope="col">Decided by</th><th scope="col">Decided</th></tr></thead>
            <tbody>
              {list.map((p) => (
                <tr key={p.policy_id}><td>v{p.version}</td><td>{p.title}<span className="sub">{p.reference}</span></td>
                  <td><span className={`tag tag-${p.status}`}>{STATUS[p.status]}</span>{p.decision_note && <span className="sub">{p.decision_note}</span>}</td>
                  <td className="small">{p.proposed_by_email || "system"}</td><td className="small">{p.decided_by_email || "–"}</td>
                  <td className="small">{p.decided_at ? fmtDateTime(p.decided_at) : "–"}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
