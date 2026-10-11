import { useCallback, useEffect, useRef, useState } from "react";
import { api, hasToken, setToken } from "./api.js";
import Compare from "./Compare.jsx";
import Forecast from "./Forecast.jsx";
import History from "./History.jsx";
import Overview from "./Overview.jsx";
import Reservoir from "./Reservoir.jsx";
import Settings from "./Settings.jsx";

const TABS = [
  ["overview", "Overview"],
  ["compare", "Compare and allocate"],
  ["forecast", "Forecast and alerts"],
  ["history", "History"],
  ["settings", "Settings"],
];

// Shown only when the backend requires an admin token (JAL_ADMIN_TOKEN) for changes.
function Unlock({ notify }) {
  const [needed, setNeeded] = useState(false);
  const [unlocked, setUnlocked] = useState(hasToken());
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");
  const [error, setError] = useState(null);

  useEffect(() => {
    api.auth().then((a) => setNeeded(a.token_required)).catch(() => {});
  }, []);
  if (!needed) return null;

  if (unlocked) {
    return (
      <button type="button" className="lock" onClick={() => { setToken(""); setUnlocked(false); notify("Locked. Changes need the admin token again."); }}>
        Unlocked · Lock
      </button>
    );
  }
  if (!open) {
    return <button type="button" className="lock" onClick={() => setOpen(true)}>Locked · Unlock</button>;
  }
  return (
    <form
      className="unlock-form"
      onSubmit={async (e) => {
        e.preventDefault();
        try {
          await api.checkToken(value);
          setToken(value);
          setUnlocked(true);
          setOpen(false);
          setValue("");
          setError(null);
          notify("Unlocked: you can add requests and apply plans.");
        } catch (err) {
          setError(err.status === 401 ? "Wrong token." : err.message);
        }
      }}
    >
      <label>
        <span className="sr-only">Admin token</span>
        <input type="password" autoComplete="off" placeholder="Admin token" value={value} onChange={(e) => setValue(e.target.value)} autoFocus />
      </label>
      <button type="submit" className="lock">Unlock</button>
      {error && <span className="unlock-error" role="alert">{error}</span>}
    </form>
  );
}

export default function App() {
  const [tab, setTab] = useState("overview");
  const [status, setStatus] = useState(null);
  const [requests, setRequests] = useState([]);
  const [users, setUsers] = useState([]);
  const [online, setOnline] = useState(null);  // null = checking, true/false afterwards
  const [problem, setProblem] = useState(null);
  const [toast, setToast] = useState(null);
  const [version, setVersion] = useState(0);   // bumped whenever data changes, so History reloads
  const [alertCount, setAlertCount] = useState(0);
  const countAlerts = useCallback((list) => setAlertCount(list.filter((a) => a.severity !== "info").length), []);
  const toastTimer = useRef(null);

  const notify = useCallback((message, isError = false) => {
    setToast({ message, isError });
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 7000);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [s, r, u] = await Promise.all([api.status(), api.requests(), api.users()]);
      setStatus(s);
      setRequests(r);
      setUsers(u);
      setProblem(null);
      setOnline(true);
      setVersion((v) => v + 1);
      api.alerts().then(countAlerts).catch(() => {});
    } catch (e) {
      setProblem(e.message);
      setOnline(false);
    }
  }, [countAlerts]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Check the API and its database connection every 10 seconds; reload data when it comes back.
  const wasOnline = useRef(null);
  useEffect(() => {
    const check = async () => {
      try {
        await api.health();
        if (wasOnline.current === false) refresh();
        wasOnline.current = true;
        setOnline(true);
      } catch (e) {
        wasOnline.current = false;
        setOnline(false);
        setProblem(e.message);
      }
    };
    const id = setInterval(check, 10000);
    return () => clearInterval(id);
  }, [refresh]);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden="true">
            <path d="M16 3C16 3 6 14.5 6 20.5a10 10 0 0 0 20 0C26 14.5 16 3 16 3z" fill="currentColor" />
          </svg>
          <span className="brand-name">JalRakshak</span>
        </div>
        <nav className="tabs" role="tablist" aria-label="Sections">
          {TABS.map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="tab"
              id={`tab-${id}`}
              aria-selected={tab === id}
              aria-controls="panel"
              className={tab === id ? "tab active" : "tab"}
              onClick={() => setTab(id)}
            >
              {label}
              {id === "forecast" && alertCount > 0 && (
                <span className="count" aria-label={`${alertCount} alerts`}>{alertCount}</span>
              )}
            </button>
          ))}
        </nav>
        <Unlock notify={notify} />
        <div className={`conn ${online === true ? "up" : online === false ? "down" : ""}`} role="status">
          <span className="dot" />
          {online === null ? "Connecting…" : online ? "Connected to database" : "API unreachable"}
        </div>
      </header>

      {problem && online === false && (
        <div className="banner" role="alert">
          <strong>Cannot load data.</strong> {problem}
        </div>
      )}

      <main className="page">
        <Reservoir status={status} />
        <div id="panel" role="tabpanel" aria-labelledby={`tab-${tab}`} className="panel">
          {tab === "overview" && (
            <Overview status={status} requests={requests} users={users} onChanged={refresh} notify={notify} />
          )}
          {tab === "compare" && <Compare status={status} onApplied={refresh} notify={notify} />}
          {tab === "forecast" && <Forecast status={status} notify={notify} onAlertsChanged={countAlerts} />}
          {tab === "history" && <History refreshKey={version} />}
          {tab === "settings" && <Settings status={status} users={users} onChanged={refresh} notify={notify} />}
        </div>
      </main>

      <div className="toast-area" aria-live="polite">
        {toast && (
          <p className={toast.isError ? "toast toast-error" : "toast"}>
            {toast.message}
            <button type="button" className="toast-close" aria-label="Dismiss" onClick={() => setToast(null)}>×</button>
          </p>
        )}
      </div>
    </div>
  );
}
