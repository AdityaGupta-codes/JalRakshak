import { useCallback, useEffect, useRef, useState } from "react";
import Accounts from "./Accounts.jsx";
import { api } from "./api.js";
import Compare from "./Compare.jsx";
import Compliance from "./Compliance.jsx";
import FieldData from "./FieldData.jsx";
import Forecast from "./Forecast.jsx";
import History from "./History.jsx";
import Issues from "./Issues.jsx";
import Overview from "./Overview.jsx";
import Policy from "./Policy.jsx";
import { ConsentBanner } from "./Privacy.jsx";
import Profile from "./Profile.jsx";
import Reservoir from "./Reservoir.jsx";
import Settings from "./Settings.jsx";
import Shell, { useHashTab } from "./Shell.jsx";
import Supply from "./Supply.jsx";

const IDS = ["overview", "compare", "supply", "forecast", "history", "policy", "accounts", "issues", "field",
  "compliance", "settings", "profile"];
const OPERATIONS = ["overview", "compare", "forecast", "history", "settings"];

export default function AdminApp({ me, notify, onSignOut, onMeChanged }) {
  const [tab, setTab] = useHashTab(IDS, "overview");
  const [status, setStatus] = useState(null);
  const [requests, setRequests] = useState([]);
  const [users, setUsers] = useState([]);
  const [online, setOnline] = useState(null);
  const [problem, setProblem] = useState(null);
  const [version, setVersion] = useState(0);   // bumped whenever data changes, so other views reload
  const [alertCount, setAlertCount] = useState(0);
  const [summary, setSummary] = useState({ pending_accounts: 0, open_issues: 0 });
  const countAlerts = useCallback((list) => setAlertCount(list.filter((a) => a.severity !== "info").length), []);

  const loadSummary = useCallback(() => api.adminSummary().then(setSummary).catch(() => {}), []);

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
      loadSummary();
    } catch (e) {
      if (e.status === 401) return;  // signed out: Root shows the sign-in page
      setProblem(e.message);
      setOnline(false);
    }
  }, [countAlerts, loadSummary]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Check the API and its database every 15 seconds; reload data when it comes back. Badges refresh too.
  const wasOnline = useRef(null);
  useEffect(() => {
    const check = async () => {
      try {
        await api.health();
        if (wasOnline.current === false) refresh();
        wasOnline.current = true;
        setOnline(true);
        loadSummary();
      } catch (e) {
        wasOnline.current = false;
        setOnline(false);
        setProblem(e.message);
      }
    };
    const id = setInterval(check, 15000);
    return () => clearInterval(id);
  }, [refresh, loadSummary]);

  const tabs = [
    ["overview", "Overview"],
    ["compare", "Allocate"],
    ["supply", "Supply"],
    ["forecast", "Forecast", alertCount],
    ["history", "Records"],
    ["policy", "Policy"],
    ["accounts", "Accounts", summary.pending_accounts],
    ["issues", "Issues", summary.open_issues],
    ["field", "Field data"],
    ["compliance", "Compliance"],
    ["settings", "Settings"],
  ];

  const banner = (
    <>
      {problem && online === false && <div className="banner" role="alert"><strong>Cannot load data.</strong> {problem}</div>}
      <ConsentBanner me={me} notify={notify} onMeChanged={onMeChanged} />
    </>
  );

  return (
    <Shell me={me} tabs={tabs} tab={tab} setTab={setTab} onSignOut={onSignOut} notify={notify} banner={banner}
      refreshKey={version} profileTab="profile">
      {OPERATIONS.includes(tab) && <Reservoir status={status} />}
      <div className={OPERATIONS.includes(tab) ? "panel" : ""}>
        {tab === "overview" && (
          <Overview status={status} requests={requests} users={users} onChanged={refresh} notify={notify} />
        )}
        {tab === "compare" && <Compare status={status} onApplied={refresh} notify={notify} />}
        {tab === "forecast" && <Forecast status={status} notify={notify} onAlertsChanged={countAlerts} />}
        {tab === "history" && <History refreshKey={version} />}
        {tab === "accounts" && <Accounts me={me} users={users} notify={notify} onChanged={() => { loadSummary(); refresh(); }} />}
        {tab === "issues" && <Issues notify={notify} onChanged={loadSummary} />}
        {tab === "supply" && <Supply notify={notify} />}
        {tab === "policy" && <Policy me={me} notify={notify} onChanged={refresh} />}
        {tab === "field" && <FieldData notify={notify} onChanged={refresh} />}
        {tab === "compliance" && <Compliance notify={notify} />}
        {tab === "settings" && <Settings status={status} users={users} onChanged={refresh} notify={notify} />}
        {tab === "profile" && <Profile me={me} notify={notify} onMeChanged={onMeChanged} />}
      </div>
    </Shell>
  );
}
