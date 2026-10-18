import { useEffect, useState } from "react";
import { api } from "./api.js";
import { USER_TYPE } from "./format.js";
import { useT } from "./i18n.js";
import { PrivacyNotice } from "./Privacy.jsx";
import { LangSwitch } from "./Shell.jsx";

function Brand() {
  return (
    <div className="auth-brand">
      <div className="auth-logo">
        <svg width="34" height="34" viewBox="0 0 32 32" aria-hidden="true">
          <path d="M16 3C16 3 6 14.5 6 20.5a10 10 0 0 0 20 0C26 14.5 16 3 16 3z" fill="currentColor" />
        </svg>
        <span>JalRakshak</span>
      </div>
      <h1>Fair water allocation for every part of the city.</h1>
      <p>
        Hospitals, schools, homes and industry request water here. The water department compares four allocation
        rules on the same water and applies the fairest plan.
      </p>
      <ul className="auth-points">
        <li>Request water and follow each request until it is decided</li>
        <li>Get notified as soon as a plan gives you water</li>
        <li>Report leaks and supply problems to the department</li>
      </ul>
    </div>
  );
}

function MfaStep({ challenge, onSignedIn, onBack }) {
  const t = useT();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  return (
    <form className="auth-form" onSubmit={async (e) => {
      e.preventDefault();
      setError(null);
      setBusy(true);
      try {
        onSignedIn(await api.mfaLogin(challenge, code.trim()));
      } catch (err) {
        setError(err.message);
        if (err.status === 401) onBack(err.message);
      } finally {
        setBusy(false);
      }
    }}>
      <h2>{t("Two-factor sign-in")}</h2>
      <p className="muted small">{t("Enter the 6-digit code from your authenticator app.")}</p>
      <label htmlFor="mfa-code"><span>{t("Code")}</span>
        <input id="mfa-code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9 ]{6,8}" maxLength={8}
          value={code} onChange={(e) => setCode(e.target.value)} required autoFocus />
      </label>
      {error && <p className="form-error" role="alert">{error}</p>}
      <button type="submit" className="btn btn-primary btn-wide" disabled={busy}>{busy ? "…" : t("Verify")}</button>
      <p className="auth-switch"><button type="button" className="link-btn" onClick={() => onBack(null)}>{t("Back")}</button></p>
    </form>
  );
}

function SignIn({ onSignedIn, onRegister, message }) {
  const t = useT();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [challenge, setChallenge] = useState(null);
  const [info, setInfo] = useState(message);
  if (challenge) {
    return <MfaStep challenge={challenge} onSignedIn={onSignedIn}
      onBack={(m) => { setChallenge(null); setPassword(""); setInfo(m); }} />;
  }
  return (
    <form className="auth-form" onSubmit={async (e) => {
      e.preventDefault();
      setError(null);
      setBusy(true);
      try {
        const res = await api.login(email, password);
        if (res.mfa_required) setChallenge(res.challenge);
        else onSignedIn(res);
      } catch (err) {
        setError(err.message);
      } finally {
        setBusy(false);
      }
    }}>
      <h2>{t("Sign in")}</h2>
      {info && <p className="form-info" role="status">{info}</p>}
      <label htmlFor="login-email"><span>{t("Email")}</span>
        <input id="login-email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
      </label>
      <label htmlFor="login-password"><span>{t("Password")}</span>
        <input id="login-password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
      </label>
      {error && <p className="form-error" role="alert">{error}</p>}
      <button type="submit" className="btn btn-primary btn-wide" disabled={busy}>{busy ? t("Signing in…") : t("Sign in")}</button>
      <p className="auth-switch"><button type="button" className="link-btn" onClick={onRegister}>{t("New organisation? Request an account")}</button></p>
      <details className="demo-hint">
        <summary>Demo sign-ins</summary>
        <p>Administrator: <code>admin@jalrakshak.local</code> / <code>Admin@1234</code> (asks for a new password the first time).</p>
        <p>Second officer (approves policy changes): <code>approver@jalrakshak.local</code> / <code>Demo@1234</code></p>
        <p>Organisations: <code>city.hospital@jalrakshak.local</code>, <code>town.park@jalrakshak.local</code> … / <code>Demo@1234</code></p>
      </details>
    </form>
  );
}

function Register({ onDone, onSignIn }) {
  const [zones, setZones] = useState([]);
  const [f, setF] = useState({ full_name: "", email: "", phone: "", password: "", confirm: "",
    organisation_name: "", organisation_type: "domestic", zone_id: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [consent, setConsent] = useState(false);
  const [showNotice, setShowNotice] = useState(false);
  const t = useT();
  useEffect(() => { api.zones().then(setZones).catch((e) => setError(e.message)); }, []);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });

  return (
    <form className="auth-form" onSubmit={async (e) => {
      e.preventDefault();
      setError(null);
      if (f.password !== f.confirm) return setError("The two passwords are different.");
      if (!/[A-Za-z]/.test(f.password) || !/\d/.test(f.password) || f.password.length < 8) {
        return setError("Use at least 8 characters with at least one letter and one number.");
      }
      if (!f.zone_id) return setError("Choose your zone.");
      if (!consent) return setError("Please read and accept the privacy notice.");
      setBusy(true);
      try {
        const { confirm, ...data } = f;
        const res = await api.register({ ...data, zone_id: Number(f.zone_id), phone: f.phone || null, consent: true });
        onDone(res.message);
      } catch (err) {
        setError(err.message);
      } finally {
        setBusy(false);
      }
    }}>
      <h2>Create an account</h2>
      <p className="muted small">For organisations that request water. The water department checks each sign-up and sets your priority.</p>
      <fieldset>
        <legend>Your organisation</legend>
        <label htmlFor="reg-org"><span>Organisation name</span>
          <input id="reg-org" value={f.organisation_name} onChange={set("organisation_name")} maxLength={100} required placeholder="e.g. Sunrise Public School" />
        </label>
        <div className="form-row">
          <label htmlFor="reg-type"><span>Type</span>
            <select id="reg-type" value={f.organisation_type} onChange={set("organisation_type")}>
              {Object.entries(USER_TYPE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
          <label htmlFor="reg-zone"><span>Zone</span>
            <select id="reg-zone" value={f.zone_id} onChange={set("zone_id")} required>
              <option value="">Choose…</option>
              {zones.map((z) => <option key={z.zone_id} value={z.zone_id}>{z.name}</option>)}
            </select>
          </label>
        </div>
      </fieldset>
      <fieldset>
        <legend>You</legend>
        <label htmlFor="reg-name"><span>Full name</span>
          <input id="reg-name" value={f.full_name} onChange={set("full_name")} autoComplete="name" maxLength={100} required />
        </label>
        <div className="form-row">
          <label htmlFor="reg-email"><span>Email</span>
            <input id="reg-email" type="email" value={f.email} onChange={set("email")} autoComplete="email" required />
          </label>
          <label htmlFor="reg-phone"><span>Phone (optional)</span>
            <input id="reg-phone" type="tel" value={f.phone} onChange={set("phone")} autoComplete="tel" maxLength={30} />
          </label>
        </div>
        <div className="form-row">
          <label htmlFor="reg-pass"><span>Password</span>
            <input id="reg-pass" type="password" value={f.password} onChange={set("password")} autoComplete="new-password" required />
          </label>
          <label htmlFor="reg-pass2"><span>Repeat password</span>
            <input id="reg-pass2" type="password" value={f.confirm} onChange={set("confirm")} autoComplete="new-password" required />
          </label>
        </div>
        <p className="note">At least 8 characters, with a letter and a number.</p>
      </fieldset>
      <label className="check" htmlFor="reg-consent">
        <input id="reg-consent" type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
        <span>{t("I have read and accept the privacy notice")} (<button type="button" className="link-btn"
          onClick={() => setShowNotice(true)}>{t("Privacy notice")}</button>)</span>
      </label>
      {showNotice && <PrivacyNotice onClose={() => setShowNotice(false)} />}
      {error && <p className="form-error" role="alert">{error}</p>}
      <button type="submit" className="btn btn-primary btn-wide" disabled={busy}>{busy ? "Sending…" : "Create account"}</button>
      <p className="auth-switch">Already have an account? <button type="button" className="link-btn" onClick={onSignIn}>Sign in</button></p>
    </form>
  );
}

export default function Login({ onSignedIn, message: initial }) {
  const [mode, setMode] = useState("signin");
  const [message, setMessage] = useState(initial);
  return (
    <div className="auth-page">
      <div className="auth-lang"><LangSwitch /></div>
      <Brand />
      <div className="auth-card">
        {mode === "signin" ? (
          <SignIn onSignedIn={onSignedIn} message={message} onRegister={() => { setMessage(null); setMode("register"); }} />
        ) : (
          <Register onSignIn={() => setMode("signin")} onDone={(m) => { setMessage(m); setMode("signin"); }} />
        )}
      </div>
    </div>
  );
}

export function ChangePasswordForm({ notify, forced, onDone }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  return (
    <form className="form" onSubmit={async (e) => {
      e.preventDefault();
      setError(null);
      if (next !== again) return setError("The two new passwords are different.");
      setBusy(true);
      try {
        await api.changePassword(current, next);
        setCurrent(""); setNext(""); setAgain("");
        notify("Password changed. Other devices have been signed out.");
        if (onDone) onDone();
      } catch (err) {
        setError(err.message);
      } finally {
        setBusy(false);
      }
    }}>
      {!forced && <h2>Change password</h2>}
      <label htmlFor="cp-current"><span>{forced ? "Temporary password" : "Current password"}</span>
        <input id="cp-current" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required />
      </label>
      <label htmlFor="cp-new"><span>New password</span>
        <input id="cp-new" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} required />
      </label>
      <label htmlFor="cp-again"><span>Repeat new password</span>
        <input id="cp-again" type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} required />
      </label>
      <p className="note">At least 8 characters, with a letter and a number.</p>
      {error && <p className="form-error" role="alert">{error}</p>}
      <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Saving…" : "Change password"}</button>
    </form>
  );
}

export function ForcedPasswordChange({ me, notify, onDone, onSignOut }) {
  return (
    <div className="auth-page single">
      <div className="auth-card">
        <h2>Choose a new password</h2>
        <p className="muted">
          Welcome, {me.full_name}. You signed in with a temporary password. Choose your own before you continue.
        </p>
        <ChangePasswordForm notify={notify} forced onDone={onDone} />
        <p className="auth-switch"><button type="button" className="link-btn" onClick={onSignOut}>Sign out</button></p>
      </div>
    </div>
  );
}
