import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import {
  ISSUE_CATEGORY, SCARCITY_TEXT, URGENCY, algoName, fmtDateTime, fmtL, fmtMin, fmtPct, typeName,
} from "./format.js";
import { useT } from "./i18n.js";
import { IssueBadge } from "./Issues.jsx";
import { ConsentBanner } from "./Privacy.jsx";
import Profile from "./Profile.jsx";
import Shell, { useHashTab } from "./Shell.jsx";
import { ConfirmButton, Empty, Priority, StatusBadge } from "./ui.jsx";

const IDS = ["home", "requests", "issues", "profile"];

// ---------------------------------------------------------------- home
function SupplyCard() {
  const t = useT();
  const [s, setS] = useState(undefined);
  const [policy, setPolicy] = useState(null);
  useEffect(() => {
    api.mySupply().then((r) => setS(r.supply)).catch(() => setS(null));
    api.activePolicy().then(setPolicy).catch(() => {});
  }, []);
  const z = s && s.zone;
  return (
    <section className="card-plain" aria-labelledby="h-supply">
      <h2 id="h-supply">{t("Piped supply to your zone")}</h2>
      {s === undefined && <p className="muted">{t("Loading…")}</p>}
      {s === null && <p className="muted">{t("No supply timetable has been published yet.")}</p>}
      {s && (
        <>
          <p className="small muted">{t("Timetable for {d}", { d: s.for_date })}</p>
          {z && z.blocks.length > 0 ? (
            <>
              <ul className="supply-blocks">
                {z.blocks.map((b) => <li key={b.start_minute}><strong>{b.start}–{b.end}</strong></li>)}
              </ul>
              <p className={z.meets_norm ? "res-note level-ok" : "res-note level-moderate"}>
                {t("about {n} litres per person (norm {m})", { n: Math.round(z.lpcd_delivered), m: Math.round(s.lpcd_norm) })}
              </p>
            </>
          ) : <p className="muted">{t("No piped supply in this timetable.")}</p>}
        </>
      )}
      {policy && (
        <p className="note">{t("Water policy")}: {policy.title} ({t("version {v}", { v: policy.version })}){policy.reference ? ` · ${policy.reference}` : ""}</p>
      )}
    </section>
  );
}

function Home({ data, goTo }) {
  const t = useT();
  if (!data) return <p className="muted">{t("Loading…")}</p>;
  const { organisation: org, reservoir: res, requests: rq } = data;
  const fill = Math.max(0, Math.min(100, res.fill_ratio * 100));
  return (
    <div className="stack">
      <section className="hello">
        <div>
          <p className="eyebrow">{typeName(org.user_type)} · {org.zone_name}</p>
          <h1>{org.name}</h1>
          <p className="muted">{t("Priority")} <Priority value={org.priority} /> {org.priority} {t("of 5")}, {t("set by the water department.")}</p>
        </div>
        <div className="form-actions">
          <button type="button" className="btn btn-primary" onClick={() => goTo("requests")}>{t("Request water")}</button>
          <button type="button" className="btn" onClick={() => goTo("issues")}>{t("Report a problem")}</button>
        </div>
      </section>

      <div className="tiles">
        <div className="tile"><span className="t-label">{t("Waiting for a decision")}</span><span className="t-value">{rq.pending}</span>
          <span className="t-sub">{t("of at most {n} at a time", { n: data.max_pending })}</span></div>
        <div className="tile"><span className="t-label">{t("Water received")}</span><span className="t-value">{fmtL(data.litres_received)}</span>
          <span className="t-sub">{t("of {x} asked in decided requests", { x: fmtL(data.litres_requested_decided) })}</span></div>
        <div className="tile"><span className="t-label">{t("Share delivered")}</span>
          <span className="t-value">{data.fulfilment == null ? "–" : fmtPct(data.fulfilment)}</span>
          <span className="t-sub">{t("{a} full · {b} partly · {c} not served", { a: rq.approved, b: rq.partial, c: rq.rejected })}</span></div>
        <div className="tile"><span className="t-label">{t("Open issues")}</span><span className="t-value">{data.open_issues}</span>
          <span className="t-sub">{t("{n} unread notifications", { n: data.unread_notifications })}</span></div>
      </div>

      <div className="two-col">
        <section className="card-plain" aria-labelledby="h-res">
          <h2 id="h-res">{res.name}</h2>
          <p className={`res-note level-${res.scarcity}`}>{t(SCARCITY_TEXT[res.scarcity])}</p>
          <div className="mini-gauge" role="img" aria-label={`${Math.round(fill)}% full`}>
            <div style={{ width: `${fill}%` }} />
          </div>
          <dl className="kv">
            <div><dt>{t("Available now")}</dt><dd>{fmtL(res.available_litres)} / {fmtL(res.capacity_litres)}</dd></div>
            <div><dt>{t("Waiting requests (whole city)")}</dt><dd>{res.pending_count} · {fmtL(res.pending_demand_litres)}</dd></div>
            {res.forecast_today_litres != null && <div><dt>{t("Expected use today")}</dt><dd>{fmtL(res.forecast_today_litres)}</dd></div>}
          </dl>
        </section>
        <section className="card-plain" aria-labelledby="h-alerts">
          <h2 id="h-alerts">{t("Alerts for your area")}</h2>
          {data.alerts.length === 0 ? (
            <p className="muted">{t("No alerts for {zone} right now.", { zone: org.zone_name })}</p>
          ) : (
            <ul className="mini-alerts">
              {data.alerts.map((a, i) => (
                <li key={a.alert_id ?? `l${i}`} className={`sev-line sev-${a.severity}`}>{a.message}</li>
              ))}
            </ul>
          )}
        </section>
      </div>
      <SupplyCard />
    </div>
  );
}

// ---------------------------------------------------------------- requests
function NewRequest({ disabled, notify, onAdded }) {
  const t = useT();
  const [litres, setLitres] = useState("5000");
  const [urgency, setUrgency] = useState("3");
  const [within, setWithin] = useState("240");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  return (
    <form className="form card-plain" aria-labelledby="nr-title" onSubmit={async (e) => {
      e.preventDefault();
      setError(null);
      setBusy(true);
      try {
        const r = await api.createMyRequest({ requested_litres: Number(litres), urgency: Number(urgency),
          deadline_in_minutes: Number(within), note: note.trim() || null });
        notify(t("Request #{id} sent. You'll be notified when a plan decides it.", { id: r.request_id }));
        setNote("");
        onAdded();
      } catch (err) {
        setError(err.message);
      } finally {
        setBusy(false);
      }
    }}>
      <h2 id="nr-title">{t("Request water")}</h2>
      <label htmlFor="nr-litres"><span>{t("Litres needed")}</span>
        <input id="nr-litres" type="number" min="1" step="any" inputMode="decimal" value={litres} onChange={(e) => setLitres(e.target.value)} required />
      </label>
      <div className="form-row">
        <label htmlFor="nr-urg"><span>{t("Urgency")}</span>
          <select id="nr-urg" value={urgency} onChange={(e) => setUrgency(e.target.value)}>
            {URGENCY.map(([v, l]) => <option key={v} value={v}>{t(l)}</option>)}
          </select>
        </label>
        <label htmlFor="nr-within"><span>{t("Needed within (minutes)")}</span>
          <input id="nr-within" type="number" min="1" step="any" inputMode="decimal" value={within} onChange={(e) => setWithin(e.target.value)} required />
        </label>
      </div>
      <label htmlFor="nr-note"><span>{t("Note for the water department (optional)")}</span>
        <textarea id="nr-note" rows={2} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. tanker for the new ICU wing" />
      </label>
      {error && <p className="form-error" role="alert">{error}</p>}
      <button type="submit" className="btn btn-primary" disabled={busy || disabled}>{busy ? t("Sending…") : t("Send request")}</button>
      <p className="note">{t("Urgency and the time you need it by both raise your request's place in the Water-aware plan. Your organisation's priority is set by the department.")}</p>
    </form>
  );
}

function Received({ r }) {
  const t = useT();
  if (r.allocated_litres == null) return <span className="muted">–</span>;
  const got = Number(r.allocated_litres), asked = Number(r.requested_litres);
  const pct = asked > 0 ? Math.min(100, (got / asked) * 100) : 0;
  return (
    <>
      <div className={`bar bar-${r.status}`}><span style={{ width: `${pct}%` }} /></div>
      <span className="strong">{got > 0 ? fmtL(got) : t("No water")}</span>{got > 0 && <span className="muted"> {Math.round(pct)}%</span>}
      <span className="sub">{algoName(r.algorithm)} plan · {fmtDateTime(r.decided_at)}{r.wait_minutes != null && got > 0 ? ` · starts after ${fmtMin(r.wait_minutes)}` : ""}</span>
    </>
  );
}

function MyRequests({ overview, notify, onChanged }) {
  const t = useT();
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const load = useCallback(async () => {
    try { setRows(await api.myRequests()); setError(null); } catch (e) { setError(e.message); }
  }, []);
  useEffect(() => { load(); }, [load]);
  const changed = () => { load(); onChanged(); };
  const full = overview && overview.requests.pending >= overview.max_pending;

  return (
    <div className="split">
      <div className="split-side">
        <NewRequest disabled={full} notify={notify} onAdded={changed} />
        {full && <p className="form-info">{t("You have {n} requests waiting, the most allowed. Cancel one or wait for the next plan.", { n: overview.max_pending })}</p>}
      </div>
      <section className="split-main" aria-labelledby="mr-title">
        <div className="section-head"><h2 id="mr-title">{t("Your requests")}</h2><button type="button" className="btn" onClick={load}>{t("Refresh")}</button></div>
        {error && <p className="form-error" role="alert">{error}</p>}
        {rows && rows.length === 0 && <Empty title={t("No requests yet.")}>{t("Use the form to ask for water.")}</Empty>}
        {rows && rows.length > 0 && (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr><th scope="col">#</th><th scope="col" className="num">{t("Asked")}</th><th scope="col">{t("Sent")}</th>
                  <th scope="col">{t("Status")}</th><th scope="col">{t("Received")}</th><th scope="col"><span className="sr-only">Actions</span></th></tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.request_id}>
                    <td className="muted">{r.request_id}</td>
                    <td className="num">{fmtL(r.requested_litres)}<span className="sub">{t("urgency {n}", { n: r.urgency })}</span></td>
                    <td className="small">{fmtDateTime(r.arrival_time)}{r.note && <span className="sub">“{r.note}”</span>}</td>
                    <td><StatusBadge status={r.status} /></td>
                    <td className="cell"><Received r={r} /></td>
                    <td className="row-actions">
                      {r.status === "pending" && (
                        <ConfirmButton label={t("Cancel")} confirmLabel={t("Yes, cancel")} className="btn btn-small" question={`${t("Cancel")} #${r.request_id}?`}
                          onConfirm={async () => {
                            try { await api.cancelMyRequest(r.request_id); notify(t("Request #{id} cancelled.", { id: r.request_id })); changed(); }
                            catch (e) { notify(e.message, true); }
                          }} />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------- issues
function MyIssues({ me, notify, onChanged }) {
  const t = useT();
  const [rows, setRows] = useState(null);
  const [f, setF] = useState({ category: "leak", title: "", location: "", description: "" });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try { setRows(await api.myIssues()); } catch (e) { setError(e.message); }
  }, []);
  useEffect(() => { load(); }, [load]);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });

  return (
    <div className="split">
      <div className="split-side">
        <form className="form card-plain" aria-labelledby="ri-title" onSubmit={async (e) => {
          e.preventDefault();
          setError(null);
          setBusy(true);
          try {
            const r = await api.reportIssue({ ...f, location: f.location.trim() || null });
            notify(t("Issue #{id} reported. The water department has been notified.", { id: r.issue_id }));
            setF({ category: f.category, title: "", location: "", description: "" });
            load();
            onChanged();
          } catch (err) {
            setError(err.message);
          } finally {
            setBusy(false);
          }
        }}>
          <h2 id="ri-title">{t("Report a problem")}</h2>
          <label htmlFor="ri-cat"><span>{t("What kind of problem?")}</span>
            <select id="ri-cat" value={f.category} onChange={set("category")}>
              {Object.entries(ISSUE_CATEGORY).map(([k, v]) => <option key={k} value={k}>{t(v)}</option>)}
            </select>
          </label>
          <label htmlFor="ri-title-in"><span>{t("Short title")}</span>
            <input id="ri-title-in" value={f.title} onChange={set("title")} maxLength={120} required placeholder="e.g. Pipe burst near gate 2" />
          </label>
          <label htmlFor="ri-loc"><span>{t("Where (optional)")}</span>
            <input id="ri-loc" value={f.location} onChange={set("location")} maxLength={200} placeholder={`e.g. MG Road, ${me.zone_name || "your zone"}`} />
          </label>
          <label htmlFor="ri-desc"><span>{t("What is happening?")}</span>
            <textarea id="ri-desc" rows={4} maxLength={2000} value={f.description} onChange={set("description")} required />
          </label>
          {error && <p className="form-error" role="alert">{error}</p>}
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? t("Sending…") : t("Send report")}</button>
        </form>
      </div>
      <section className="split-main" aria-labelledby="mi-title">
        <h2 id="mi-title">{t("Your reports")}</h2>
        {rows && rows.length === 0 && <Empty title={t("You haven't reported anything.")}>Leaks, no supply, low pressure or water quality problems go to the water department.</Empty>}
        {rows && rows.length > 0 && (
          <ul className="issue-list">
            {rows.map((i) => (
              <li key={i.issue_id} className={`issue-card cat-${i.category}`}>
                <div className="ic-head">
                  <span className="cat-tag">{t(ISSUE_CATEGORY[i.category])}</span>
                  <IssueBadge status={i.status} />
                  <span className="muted small">#{i.issue_id} · {fmtDateTime(i.created_at)}</span>
                </div>
                <h3>{i.title}</h3>
                <p>{i.description}</p>
                {i.location && <p className="small muted">{i.location}</p>}
                {i.admin_response ? (
                  <p className="response"><strong>{t("Water department:")}</strong> {i.admin_response}</p>
                ) : (
                  <p className="small muted">{t("No reply yet.")}</p>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------- the panel
export default function UserApp({ me, notify, onSignOut, onMeChanged }) {
  const t = useT();
  const [tab, setTab] = useHashTab(IDS, "home");
  const [overview, setOverview] = useState(null);
  const [problem, setProblem] = useState(null);
  const [version, setVersion] = useState(0);

  const load = useCallback(async () => {
    try {
      setOverview(await api.overview());
      setProblem(null);
      setVersion((v) => v + 1);
    } catch (e) {
      if (e.status !== 401) setProblem(e.message);
    }
  }, []);
  useEffect(() => {
    load();
    const id = setInterval(load, 30000);
    return () => clearInterval(id);
  }, [load]);

  const tabs = [
    ["home", t("Home")],
    ["requests", t("My requests"), overview ? overview.requests.pending : 0],
    ["issues", t("Report a problem")],
  ];
  const banner = (
    <>
      {problem && <div className="banner" role="alert"><strong>{t("Cannot load data.")}</strong> {problem}</div>}
      <ConsentBanner me={me} notify={notify} onMeChanged={onMeChanged} />
    </>
  );

  return (
    <Shell me={me} tabs={tabs} tab={tab} setTab={setTab} onSignOut={onSignOut} notify={notify} banner={banner}
      refreshKey={version} profileTab="profile">
      {tab === "home" && <Home data={overview} goTo={setTab} />}
      {tab === "requests" && <MyRequests overview={overview} notify={notify} onChanged={load} />}
      {tab === "issues" && <MyIssues me={me} notify={notify} onChanged={load} />}
      {tab === "profile" && <Profile me={me} notify={notify} onMeChanged={onMeChanged} />}
    </Shell>
  );
}
