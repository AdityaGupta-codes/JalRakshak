import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { fmtDateTime } from "./format.js";
import { useLang, useT } from "./i18n.js";

export function LangSwitch() {
  const { lang, setLang } = useLang();
  return (
    <div className="lang-switch" role="group" aria-label="Language / भाषा">
      <button type="button" className={lang === "en" ? "on" : ""} aria-pressed={lang === "en"} onClick={() => setLang("en")} lang="en">EN</button>
      <button type="button" className={lang === "hi" ? "on" : ""} aria-pressed={lang === "hi"} onClick={() => setLang("hi")} lang="hi">हिं</button>
    </div>
  );
}

// Remembers the open tab in the address bar (#accounts), so a page reload keeps it.
export function useHashTab(ids, fallback) {
  const read = () => {
    const h = window.location.hash.replace(/^#/, "");
    return ids.includes(h) ? h : fallback;
  };
  const [tab, setTabState] = useState(read);
  useEffect(() => {
    const on = () => setTabState(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  });
  const setTab = useCallback((id) => {
    if (window.location.hash !== `#${id}`) window.location.hash = id;
    setTabState(id);
  }, []);
  return [tab, setTab];
}

function Bell({ notify, refreshKey }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [data, setData] = useState({ unread: 0, items: [] });
  const box = useRef(null);

  const load = useCallback(() => api.notifications().then(setData).catch(() => {}), []);
  useEffect(() => {
    load();
    const id = setInterval(load, 30000);
    return () => clearInterval(id);
  }, [load, refreshKey]);
  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => { if (box.current && !box.current.contains(e.target)) setOpen(false); };
    const esc = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [open]);

  return (
    <div className="menu-wrap" ref={box}>
      <button type="button" className="icon-btn" aria-haspopup="true" aria-expanded={open}
        aria-label={`${t("Notifications")}${data.unread ? `, ${data.unread} unread` : ""}`} onClick={() => setOpen((o) => !o)}>
        <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M12 22a2.5 2.5 0 0 0 2.45-2h-4.9A2.5 2.5 0 0 0 12 22zm7-6V11a7 7 0 0 0-5.5-6.84V3.5a1.5 1.5 0 0 0-3 0v.66A7 7 0 0 0 5 11v5l-2 2v1h18v-1z"/></svg>
        {data.unread > 0 && <span className="dot-count">{data.unread > 9 ? "9+" : data.unread}</span>}
      </button>
      {open && (
        <div className="dropdown notif-panel" role="dialog" aria-label="Notifications">
          <div className="dropdown-head">
            <strong>{t("Notifications")}</strong>
            {data.unread > 0 && (
              <button type="button" className="link-btn" onClick={async () => {
                try { await api.readAllNotifications(); load(); } catch (e) { notify(e.message, true); }
              }}>{t("Mark all as read")}</button>
            )}
          </div>
          {data.items.length === 0 ? (
            <p className="muted pad">{t("Nothing yet. You'll see plan decisions and replies here.")}</p>
          ) : (
            <ul className="notif-list">
              {data.items.slice(0, 15).map((n) => (
                <li key={n.notification_id} className={n.is_read ? "" : "unread"}>
                  <button type="button" onClick={async () => {
                    if (!n.is_read) { try { await api.readNotification(n.notification_id); load(); } catch { /* ignore */ } }
                  }}>
                    <span className="n-title">{n.title}</span>
                    <span className="n-body">{n.body}</span>
                    <span className="n-time">{fmtDateTime(n.created_at)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function AccountMenu({ me, onSignOut, onProfile }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const box = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => { if (box.current && !box.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  const initials = (me.full_name || me.email).split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();
  return (
    <div className="menu-wrap" ref={box}>
      <button type="button" className="avatar-btn" aria-haspopup="true" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <span className="avatar" aria-hidden="true">{initials}</span>
        <span className="who">
          <span className="who-name">{me.full_name}</span>
          <span className="who-role">{me.role === "admin" ? t("Administrator") : me.org_display_name}</span>
        </span>
      </button>
      {open && (
        <div className="dropdown account-panel" role="menu">
          <p className="pad small muted">{t("Signed in as")} <strong>{me.email}</strong></p>
          <button type="button" role="menuitem" className="menu-item" onClick={() => { setOpen(false); onProfile(); }}>{t("Profile and password")}</button>
          <button type="button" role="menuitem" className="menu-item danger" onClick={onSignOut}>{t("Sign out")}</button>
        </div>
      )}
    </div>
  );
}

export default function Shell({ me, tabs, tab, setTab, onSignOut, notify, children, banner, refreshKey, profileTab }) {
  const t = useT();
  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden="true">
            <path d="M16 3C16 3 6 14.5 6 20.5a10 10 0 0 0 20 0C26 14.5 16 3 16 3z" fill="currentColor" />
          </svg>
          <span className="brand-name">JalRakshak</span>
          <span className={`panel-tag ${me.role}`}>{me.role === "admin" ? t("Admin") : t("User")}</span>
        </div>
        <nav className="tabs" role="tablist" aria-label="Sections">
          {tabs.map(([id, label, badge]) => (
            <button key={id} type="button" role="tab" id={`tab-${id}`} aria-selected={tab === id} aria-controls="panel"
              className={tab === id ? "tab active" : "tab"} onClick={() => setTab(id)}>
              {label}
              {badge > 0 && <span className="count" aria-label={`${badge} need attention`}>{badge}</span>}
            </button>
          ))}
        </nav>
        <div className="top-actions">
          <LangSwitch />
          <Bell notify={notify} refreshKey={refreshKey} />
          <AccountMenu me={me} onSignOut={onSignOut} onProfile={() => setTab(profileTab)} />
        </div>
      </header>
      {banner}
      <main className="page">
        <div id="panel" role="tabpanel" aria-labelledby={`tab-${tab}`}>{children}</div>
      </main>
    </div>
  );
}
