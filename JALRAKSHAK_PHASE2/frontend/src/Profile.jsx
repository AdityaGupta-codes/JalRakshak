import { useState } from "react";
import { api } from "./api.js";
import { fmtDateTime, typeName } from "./format.js";
import { useT } from "./i18n.js";
import { ChangePasswordForm } from "./Login.jsx";
import { MfaPanel, MyDataPanel } from "./Privacy.jsx";
import { Priority } from "./ui.jsx";

export default function Profile({ me, notify, onMeChanged }) {
  const t = useT();
  const [name, setName] = useState(me.full_name || "");
  const [phone, setPhone] = useState(me.phone || "");
  const [error, setError] = useState(null);
  return (
    <div className="profile-grid">
      <section className="card-plain" aria-labelledby="pf-title">
        <h2 id="pf-title">{t("Your profile")}</h2>
        <dl className="kv">
          <div><dt>{t("Email")}</dt><dd>{me.email}</dd></div>
          <div><dt>{t("Role")}</dt><dd>{me.role === "admin" ? t("Administrator") : t("User")}</dd></div>
          {me.org_display_name && (
            <div><dt>{t("Organisation")}</dt><dd>{me.org_display_name}<span className="sub">{typeName(me.user_type)} · {me.zone_name}</span><Priority value={me.priority} /></dd></div>
          )}
          <div><dt>{t("Last sign-in")}</dt><dd>{me.last_login_at ? fmtDateTime(me.last_login_at) : "–"}</dd></div>
        </dl>
        <form className="form" onSubmit={async (e) => {
          e.preventDefault();
          setError(null);
          try {
            await api.updateProfile({ full_name: name.trim(), phone: phone.trim() });
            notify(t("Profile saved."));
            onMeChanged();
          } catch (err) {
            setError(err.message);
          }
        }}>
          <label htmlFor="pf-name"><span>{t("Full name")}</span>
            <input id="pf-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={100} required autoComplete="name" />
          </label>
          <label htmlFor="pf-phone"><span>{t("Phone")}</span>
            <input id="pf-phone" type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} maxLength={30} autoComplete="tel" />
          </label>
          {error && <p className="form-error" role="alert">{error}</p>}
          <button type="submit" className="btn btn-primary">{t("Save profile")}</button>
        </form>
        {me.role === "requester" && (
          <p className="note">Your organisation's name, type, zone and priority are set by the water department.</p>
        )}
      </section>
      <section className="card-plain">
        <ChangePasswordForm notify={notify} />
      </section>
      <MfaPanel me={me} notify={notify} onMeChanged={onMeChanged} />
      <MyDataPanel me={me} notify={notify} />
    </div>
  );
}
