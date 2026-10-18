import { useCallback, useEffect, useRef, useState } from "react";
import AdminApp from "./AdminApp.jsx";
import { api, hasSession, setSession } from "./api.js";
import { useLang } from "./i18n.js";
import Login, { ForcedPasswordChange } from "./Login.jsx";
import { ForcedMfaSetup } from "./Privacy.jsx";
import UserApp from "./UserApp.jsx";

// Decides what to show: the sign-in page, the forced password change, the admin panel or the user panel.
export default function Root() {
  const [me, setMe] = useState(null);
  const [checking, setChecking] = useState(hasSession());
  const [message, setMessage] = useState(null);
  const [toast, setToast] = useState(null);
  const timer = useRef(null);
  const { lang, setLang } = useLang();
  const langRef = useRef(lang);
  langRef.current = lang;

  const notify = useCallback((text, isError = false) => {
    setToast({ text, isError });
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setToast(null), 7000);
  }, []);

  const loadMe = useCallback(async () => {
    try {
      const m = await api.me();
      setMe(m);
    } catch (e) {
      if (e.status !== 401) setMessage(e.message);
      setMe(null);
    } finally {
      setChecking(false);
    }
  }, []);

  useEffect(() => {
    if (hasSession()) loadMe();
  }, [loadMe]);

  // Any request that finds the session expired sends us back to sign-in.
  useEffect(() => {
    const out = (e) => {
      setMe(null);
      setMessage(e.detail || "Please sign in again.");
    };
    window.addEventListener("jal:signed-out", out);
    return () => window.removeEventListener("jal:signed-out", out);
  }, []);

  const signOut = useCallback(async () => {
    try { await api.logout(); } catch { /* already signed out */ }
    setSession("");
    setMe(null);
    setMessage("You have signed out.");
    window.location.hash = "";
  }, []);

  const toastEl = (
    <div className="toast-area" aria-live="polite">
      {toast && (
        <p className={toast.isError ? "toast toast-error" : "toast"}>
          {toast.text}
          <button type="button" className="toast-close" aria-label="Dismiss" onClick={() => setToast(null)}>×</button>
        </p>
      )}
    </div>
  );

  let view;
  if (checking) {
    view = <div className="loading-screen" aria-busy="true">Loading…</div>;
  } else if (!me) {
    view = (
      <Login message={message} onSignedIn={(res) => {
        setSession(res.token);
        setMessage(null);
        setMe(res.account);
        if (res.account.language && res.account.language !== langRef.current) setLang(res.account.language);
      }} />
    );
  } else if (me.must_change_password) {
    view = <ForcedPasswordChange me={me} notify={notify} onDone={loadMe} onSignOut={signOut} />;
  } else if (me.mfa_setup_required) {
    view = <ForcedMfaSetup me={me} notify={notify} onDone={loadMe} onSignOut={signOut} />;
  } else if (me.role === "admin") {
    view = <AdminApp me={me} notify={notify} onSignOut={signOut} onMeChanged={loadMe} />;
  } else {
    view = <UserApp me={me} notify={notify} onSignOut={signOut} onMeChanged={loadMe} />;
  }
  return (
    <>
      {view}
      {toastEl}
    </>
  );
}
