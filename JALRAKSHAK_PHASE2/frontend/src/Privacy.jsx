import { useCallback, useEffect, useState } from "react";
import { api, saveJson } from "./api.js";
import { fmtDateTime } from "./format.js";
import { useT } from "./i18n.js";

// ---------------------------------------------------------------- privacy notice (DPDP Act)
export function PrivacyNotice({ onClose, onAccept }) {
  const t = useT();
  const [notice, setNotice] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => { api.privacyNotice().then(setNotice).catch((e) => setError(e.message)); }, []);
  useEffect(() => {
    const esc = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [onClose]);
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="pn-title">
        <h2 id="pn-title">{notice ? notice.title : t("Privacy notice")}</h2>
        {error && <p className="form-error">{error}</p>}
        {!notice && !error && <p className="muted">{t("Loading…")}</p>}
        {notice && (
          <>
            <p className="small muted">Version {notice.version}</p>
            {notice.sections.map(([h, body]) => (
              <section key={h}><h3>{h}</h3><p>{body}</p></section>
            ))}
          </>
        )}
        <div className="form-actions">
          {onAccept && <button type="button" className="btn btn-primary" onClick={onAccept}>{t("Accept")}</button>}
          <button type="button" className="btn" onClick={onClose}>{t("Close")}</button>
        </div>
      </div>
    </div>
  );
}

export function ConsentBanner({ me, notify, onMeChanged }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  if (!me.consent_required) return null;
  const accept = async () => {
    try { await api.consent(); setOpen(false); notify("Thank you."); onMeChanged(); } catch (e) { notify(e.message, true); }
  };
  return (
    <div className="banner banner-info" role="status">
      <span>{t("Please accept the updated privacy notice to keep using the portal.")}</span>
      <button type="button" className="btn btn-small" onClick={() => setOpen(true)}>{t("Read and accept")}</button>
      {open && <PrivacyNotice onClose={() => setOpen(false)} onAccept={accept} />}
    </div>
  );
}

// ---------------------------------------------------------------- my data & privacy
const KIND = { erasure: "Ask to erase my account", correction: "Ask for a correction", grievance: "Raise a grievance" };

export function MyDataPanel({ me, notify }) {
  const t = useT();
  const [rows, setRows] = useState([]);
  const [kind, setKind] = useState("correction");
  const [details, setDetails] = useState("");
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(false);
  const load = useCallback(() => api.myPrivacyRequests().then(setRows).catch(() => {}), []);
  useEffect(() => { load(); }, [load]);
  return (
    <section className="card-plain" aria-labelledby="pd-title">
      <h2 id="pd-title">{t("My data & privacy")}</h2>
      <p className="small muted">
        Under the Digital Personal Data Protection Act you can get a copy of your data, ask for corrections or
        erasure, and raise a grievance. {me.consent_at ? `You accepted the privacy notice on ${fmtDateTime(me.consent_at)}.` : ""}
        {" "}<button type="button" className="link-btn" onClick={() => setNotice(true)}>{t("Privacy notice")}</button>
      </p>
      {notice && <PrivacyNotice onClose={() => setNotice(false)} />}
      <button type="button" className="btn" onClick={async () => {
        try { saveJson(await api.dataExport(), "my-jalrakshak-data.json"); notify("Your data was downloaded."); }
        catch (e) { notify(e.message, true); }
      }}>{t("Download my data")}</button>
      <form className="form" onSubmit={async (e) => {
        e.preventDefault();
        setError(null);
        try {
          await api.privacyRequest(kind, details.trim());
          notify("Request sent to the water department.");
          setDetails("");
          load();
        } catch (err) { setError(err.message); }
      }}>
        <label htmlFor="pd-kind"><span>{t("Details")}</span>
          <select id="pd-kind" value={kind} onChange={(e) => setKind(e.target.value)}>
            {Object.entries(KIND).map(([k, v]) => <option key={k} value={k}>{t(v)}</option>)}
          </select>
        </label>
        <textarea aria-label={t("Details")} rows={2} maxLength={2000} value={details} onChange={(e) => setDetails(e.target.value)}
          placeholder={kind === "erasure" ? "Optional: why" : "What should be corrected / what is the grievance?"} />
        {kind === "erasure" && <p className="note">Erasure closes your account. The department keeps its audit record of past actions, as the notice explains.</p>}
        {error && <p className="form-error" role="alert">{error}</p>}
        <button type="submit" className="btn btn-primary">{t("Send")}</button>
      </form>
      {rows.length > 0 && (
        <>
          <h3>{t("Your privacy requests")}</h3>
          <ul className="plain-list">
            {rows.map((r) => (
              <li key={r.request_id}><strong>{t(KIND[r.kind])}</strong> · {r.status} · {fmtDateTime(r.created_at)}
                {r.response && <span className="sub">{r.response}</span>}</li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- two-factor sign-in
function groupKey(k) { return (k || "").match(/.{1,4}/g)?.join(" ") || ""; }

export function MfaPanel({ me, notify, onMeChanged, forced }) {
  const t = useT();
  const [setup, setSetup] = useState(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState(null);
  const confirm = async (e) => {
    e.preventDefault();
    setError(null);
    try {
      if (me.mfa_enabled) { await api.mfaDisable(code.trim()); notify("Two-factor sign-in turned off."); }
      else { await api.mfaConfirm(code.trim()); notify("Two-factor sign-in is on."); }
      setSetup(null);
      setCode("");
      onMeChanged();
    } catch (err) { setError(err.message); }
  };
  return (
    <section className="card-plain" aria-labelledby="mfa-title">
      <h2 id="mfa-title">{t("Two-factor sign-in")}</h2>
      <p className={me.mfa_enabled ? "res-note level-ok" : "res-note level-moderate"}>
        {me.mfa_enabled ? t("Two-factor sign-in is on.") : t("Two-factor sign-in is off.")}
      </p>
      {forced && <p className="form-info">The water department requires administrators to use two-factor sign-in.</p>}
      {!me.mfa_enabled && !setup && (
        <button type="button" className="btn btn-primary" onClick={async () => {
          try { setSetup(await api.mfaSetup()); } catch (e) { notify(e.message, true); }
        }}>{t("Turn on two-factor sign-in")}</button>
      )}
      {setup && (
        <div className="mfa-setup">
          <p className="small">{t("Add this key to an authenticator app, then enter the code it shows.")}
            {" "}(Google Authenticator, Microsoft Authenticator, … : "Enter a setup key", time-based.)</p>
          <p className="mfa-key" aria-label="Setup key"><code>{groupKey(setup.secret)}</code></p>
          <details><summary className="small">otpauth link (for QR-code tools)</summary><code className="wrap">{setup.otpauth_uri}</code></details>
        </div>
      )}
      {(setup || me.mfa_enabled) && (
        <form className="form" onSubmit={confirm}>
          <label htmlFor="mfa-c"><span>{t("Code")}</span>
            <input id="mfa-c" inputMode="numeric" autoComplete="one-time-code" maxLength={8} value={code}
              onChange={(e) => setCode(e.target.value)} required />
          </label>
          {error && <p className="form-error" role="alert">{error}</p>}
          <button type="submit" className={me.mfa_enabled ? "btn" : "btn btn-primary"}>
            {me.mfa_enabled ? t("Turn off") : t("Verify")}
          </button>
        </form>
      )}
    </section>
  );
}

export function ForcedMfaSetup({ me, notify, onDone, onSignOut }) {
  return (
    <div className="auth-page single">
      <div className="auth-card">
        <MfaPanel me={me} notify={notify} onMeChanged={onDone} forced />
        <p className="auth-switch"><button type="button" className="link-btn" onClick={onSignOut}>Sign out</button></p>
      </div>
    </div>
  );
}
