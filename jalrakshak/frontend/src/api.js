// All communication with the FastAPI backend lives here.
const BASE = ((window.JAL_CONFIG && window.JAL_CONFIG.apiBase) || "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

function describeDetail(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {  // FastAPI validation errors: [{loc: [...], msg: "..."}]
    return detail.map((d) => `${(d.loc || []).slice(1).join(".") || "value"}: ${d.msg}`).join("; ");
  }
  return "The server rejected the request.";
}

// Admin token (only needed when the backend sets JAL_ADMIN_TOKEN). Kept for this browser tab only.
let token = "";
try {
  token = sessionStorage.getItem("jal-admin-token") || "";
} catch {
  /* storage blocked: keep it in memory */
}
export function setToken(value) {
  token = value || "";
  try {
    if (token) sessionStorage.setItem("jal-admin-token", token);
    else sessionStorage.removeItem("jal-admin-token");
  } catch {
    /* ignore */
  }
}
export const hasToken = () => Boolean(token);

async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(BASE + path, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { "X-Admin-Token": token } : {}),
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
  if (!res.ok) throw new ApiError(describeDetail(body && body.detail), res.status);
  return body;
}

const post = (path, data) => request(path, { method: "POST", body: JSON.stringify(data) });
const patch = (path, data) => request(path, { method: "PATCH", body: JSON.stringify(data) });
const del = (path) => request(path, { method: "DELETE" });

export const api = {
  base: BASE,
  health: () => request("/health"),
  status: () => request("/status"),
  users: () => request("/users"),
  requests: (status) => request("/requests" + (status ? `?status=${encodeURIComponent(status)}` : "")),
  createRequest: (data) => post("/requests", data),
  simulate: (algorithms) => post("/allocate/simulate", { reservoir_id: 1, algorithms }),
  commit: (algorithm) => post("/allocate/commit", { reservoir_id: 1, algorithm }),
  runs: () => request("/runs"),
  run: (id) => request(`/runs/${id}`),
  alerts: () => request("/alerts"),
  resolveAlert: (id) => post(`/alerts/${id}/resolve`, {}),
  predictions: () => request("/predictions"),
  forecast: (days = 60) => request(`/forecast?history_days=${days}`),
  auth: () => request("/auth"),
  checkToken: (value) => request("/auth/check", { method: "POST", body: "{}", headers: { "X-Admin-Token": value } }),
  reset: (litres) => post("/demo/reset", { reservoir_id: 1, available_litres: litres }),
  updateReservoir: (data) => patch("/resources/1", data),
  zones: () => request("/zones"),
  createZone: (data) => post("/zones", data),
  updateZone: (id, data) => patch(`/zones/${id}`, data),
  deleteZone: (id) => del(`/zones/${id}`),
  createUser: (data) => post("/users", data),
  updateUser: (id, data) => patch(`/users/${id}`, data),
  deleteUser: (id) => del(`/users/${id}`),
};
