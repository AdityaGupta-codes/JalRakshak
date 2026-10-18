import { createRoot } from "react-dom/client";
import { api, hasSession } from "./api.js";
import { LangProvider } from "./i18n.js";
import Root from "./Root.jsx";
import "./styles.css";

// Remember the chosen language on the account too (when signed in), so it follows the person.
const saveLanguage = (lang) => { if (hasSession()) api.setLanguage(lang).catch(() => {}); };

createRoot(document.getElementById("root")).render(
  <LangProvider onChange={saveLanguage}>
    <Root />
  </LangProvider>,
);
