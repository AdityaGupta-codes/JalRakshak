// All communication with the FastAPI backend lives here.
const BASE = ((window.JAL_CONFIG && window.JAL_CONFIG.apiBase) || "http://localhost:8000").replace(/\/$/, "");
const KEY = "jal-session";

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

// ---- session token (sent as "Authorization: Bearer <token>") ----
let token = "";
try {
  token = localStorage.getItem(KEY) || "";
} catch {
  /* storage blocked: keep it in memory for this tab */
}
export function setSession(value) {
  token = value || "";
  try {
    if (token) localStorage.setItem(KEY, token);
    else localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}
export const hasSession = () => Boolean(token);

function describeDetail(detail) {
  if (typeof detail === "string") return detail.charAt(0).toUpperCase() + detail.slice(1);
  if (Array.isArray(detail)) {  // FastAPI validation errors: [{loc: [...], msg: "..."}]
    return detail.map((d) => `${(d.loc || []).slice(1).join(".") || "value"}: ${d.msg}`).join("; ");
  }
  return "The server rejected the request.";
}

async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(BASE + path, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(options.headers || {}),
      },
    });
  } catch {
    throw new ApiError(`Cannot reach the API at ${BASE}. Start the backend (docker compose up) and reload.`, 0);
  }
  let body = null;
  try {
    body = await res.json();
  } catch {
    /* empty or non-JSON body */
  }
  if (!res.ok) {
    // Session expired or revoked: let the app return to the sign-in screen.
    if (res.status === 401 && token && !path.startsWith("/auth/login")) {
      setSession("");
      window.dispatchEvent(new CustomEvent("jal:signed-out", { detail: describeDetail(body && body.detail) }));
    }
    throw new ApiError(describeDetail(body && body.detail), res.status);
  }
  return body;
}

// Downloads a file from the API with the session token and saves it (reports, data export).
export async function download(path, filename, type) {
  let res;
  try {
    res = await fetch(BASE + path, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  } catch {
    throw new ApiError(`Cannot reach the API at ${BASE}.`, 0);
  }
  if (!res.ok) {
    let body = null;
    try { body = await res.json(); } catch { /* not JSON */ }
    throw new ApiError(describeDetail(body && body.detail), res.status);
  }
  const blob = type ? new Blob([await res.text()], { type }) : await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export function saveJson(data, filename) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

const post = (path, data) => request(path, { method: "POST", body: JSON.stringify(data || {}) });
const patch = (path, data) => request(path, { method: "PATCH", body: JSON.stringify(data) });
const del = (path) => request(path, { method: "DELETE" });

export const api = {
  base: BASE,
  health: () => request("/health"),

  // sign-in
  login: (email, password) => post("/auth/login", { email, password }),
  mfaLogin: (challenge, code) => post("/auth/mfa", { challenge, code }),
  privacyNotice: () => request("/privacy-notice"),
  register: (data) => post("/auth/register", data),
  me: () => request("/auth/me"),
  logout: () => post("/auth/logout"),
  changePassword: (current_password, new_password) => post("/auth/change-password", { current_password, new_password }),

  // shared
  status: () => request("/status"),
  zones: () => request("/zones"),
  notifications: () => request("/me/notifications"),
  readNotification: (id) => post(`/me/notifications/${id}/read`),
  readAllNotifications: () => post("/me/notifications/read-all"),
  updateProfile: (data) => patch("/me/profile", data),

  // user panel
  overview: () => request("/me/overview"),
  myRequests: () => request("/me/requests"),
  createMyRequest: (data) => post("/me/requests", data),
  cancelMyRequest: (id) => post(`/me/requests/${id}/cancel`),
  myIssues: () => request("/me/issues"),
  reportIssue: (data) => post("/me/issues", data),

  // admin: operations
  users: () => request("/users"),
  requests: (status) => request("/requests" + (status ? `?status=${encodeURIComponent(status)}` : "")),
  createRequest: (data) => post("/requests", data),
  simulate: (algorithms) => post("/allocate/simulate", { reservoir_id: 1, algorithms }),
  commit: (algorithm, overrides, reason) => post("/allocate/commit", { reservoir_id: 1, algorithm,
    overrides: overrides && Object.keys(overrides).length ? overrides : null, reason: reason || null }),
  shadow: (algorithm, note) => post("/allocate/shadow", { reservoir_id: 1, algorithm, note: note || null }),
  runs: () => request("/runs"),
  run: (id) => request(`/runs/${id}`),
  alerts: () => request("/alerts"),
  resolveAlert: (id) => post(`/alerts/${id}/resolve`, {}),
  predictions: () => request("/predictions"),
  forecast: (days = 60) => request(`/forecast?history_days=${days}`),
  reset: (litres) => post("/demo/reset", { reservoir_id: 1, available_litres: litres }),

  // admin: settings
  updateReservoir: (data) => patch("/resources/1", data),
  createZone: (data) => post("/zones", data),
  updateZone: (id, data) => patch(`/zones/${id}`, data),
  deleteZone: (id) => del(`/zones/${id}`),
  createUser: (data) => post("/users", data),
  updateUser: (id, data) => patch(`/users/${id}`, data),
  deleteUser: (id) => del(`/users/${id}`),

  // admin: people, issues, audit
  adminSummary: () => request("/admin/summary"),
  accounts: (status) => request("/admin/accounts" + (status ? `?status=${status}` : "")),
  createAccount: (data) => post("/admin/accounts", data),
  updateAccount: (id, data) => patch(`/admin/accounts/${id}`, data),
  approveAccount: (id, data) => post(`/admin/accounts/${id}/approve`, data),
  rejectAccount: (id, reason) => post(`/admin/accounts/${id}/reject`, { reason }),
  resetPassword: (id) => post(`/admin/accounts/${id}/reset-password`),
  issues: (status) => request("/admin/issues" + (status ? `?status=${status}` : "")),
  updateIssue: (id, data) => patch(`/admin/issues/${id}`, data),
  audit: (action) => request("/admin/audit?limit=300" + (action ? `&action=${encodeURIComponent(action)}` : "")),

  // ---- v2.0: everyone signed in
  mfaSetup: () => post("/me/mfa/setup"),
  mfaConfirm: (code) => post("/me/mfa/confirm", { code }),
  mfaDisable: (code) => post("/me/mfa/disable", { code }),
  consent: () => post("/me/consent"),
  dataExport: () => request("/me/data-export"),
  myPrivacyRequests: () => request("/me/privacy-requests"),
  privacyRequest: (kind, details) => post("/me/privacy-requests", { kind, details: details || null }),
  setLanguage: (language) => patch("/me/language", { language }),
  mySupply: () => request("/me/supply"),
  activePolicy: () => request("/policy/active"),
  mapZones: () => request("/map/zones"),

  // ---- v2.0: admin governance
  policies: () => request("/admin/policies"),
  draftPolicy: (data) => post("/admin/policies", data),
  editPolicy: (id, data) => patch(`/admin/policies/${id}`, data),
  submitPolicy: (id) => post(`/admin/policies/${id}/submit`),
  decidePolicy: (id, approve, note) => post(`/admin/policies/${id}/decide`, { approve, note: note || null }),
  kpis: (days = 30) => request(`/admin/kpis?days=${days}`),

  // ---- v2.0: network, timetable, tankers
  resources: () => request("/resources"),
  createReservoir: (data) => post("/resources", data),
  reservoirNetwork: (id, data) => patch(`/resources/${id}/network`, data),
  zoneNetwork: (id, data) => patch(`/zones/${id}/network`, data),
  timetables: () => request("/timetables"),
  buildTimetable: (data) => post("/timetables", data),
  timetable: (id) => request(`/timetables/${id}`),
  publishTimetable: (id) => post(`/timetables/${id}/publish`),
  planTankers: (id) => post(`/timetables/${id}/tankers`),
  tankers: () => request("/tankers"),
  createTanker: (data) => post("/tankers", data),
  updateTanker: (id, data) => patch(`/tankers/${id}`, data),

  // ---- v2.0: field data
  devices: () => request("/admin/devices"),
  createDevice: (data) => post("/admin/devices", data),
  setDeviceActive: (id, active) => patch(`/admin/devices/${id}`, { active }),
  deviceReadings: (id) => request(`/admin/devices/${id}/readings`),
  importConsumption: (csv) => post("/admin/import/consumption", { csv }),
  dataSources: () => request("/admin/data-sources"),

  // ---- v2.0: security, privacy, operations
  resetMfa: (id) => post(`/admin/accounts/${id}/reset-mfa`),
  privacyRequests: () => request("/admin/privacy-requests"),
  handlePrivacy: (id, status, response) => post(`/admin/privacy-requests/${id}`, { status, response }),
  incidents: () => request("/admin/incidents"),
  createIncident: (data) => post("/admin/incidents", data),
  updateIncident: (id, data) => patch(`/admin/incidents/${id}`, data),
  ops: () => request("/admin/ops"),
  runRetention: () => post("/admin/maintenance/retention"),
};
