"""Water allocation policy with maker-checker approval.

A policy fixes, for every class of organisation (hospital, school, ...), its priority and the share of its
request it is guaranteed when water is short; plus the per-person norm (litres per person per day), the
Water-aware weights, the default floor and the daily supply window.

Rules (each one is a government-office practice, not a technical need):
  * Exactly one policy is active ("approved") at a time; earlier ones are kept ("superseded") as history.
  * A change starts as a draft, is submitted ("pending"), and must be approved by a DIFFERENT administrator
    (maker-checker). REQUIRE_SECOND_APPROVER=0 allows self-approval for a one-person installation; such an
    approval is recorded as self-approved in the audit log.
  * Approving a policy re-sets every organisation's priority from its class, except organisations whose
    priority an administrator set by hand (users.priority_override).
"""
import json
from decimal import Decimal

from . import audit, config
from .errors import ConflictError, Forbidden, InvalidInput, NotFoundError

CLASSES = ("hospital", "domestic", "school", "industry", "commercial", "park")
WEIGHT_KEYS = ("priority", "urgency", "scarcity", "demand_impact", "fairness")
COLUMNS = ("policy_id, version, title, reference, status, class_priority, class_min_share, essential_classes, "
           "weights, guarantee_floor, lpcd, zone_min_share, tanker_trigger_lpcd, tanker_capacity_litres, "
           "supply_start_hour, supply_end_hour, notes, proposed_by, created_at, submitted_at, decided_by, "
           "decided_at, decision_note")
EDITABLE = ("title", "reference", "class_priority", "class_min_share", "essential_classes", "weights",
            "guarantee_floor", "lpcd", "zone_min_share", "tanker_trigger_lpcd", "tanker_capacity_litres",
            "supply_start_hour", "supply_end_hour", "notes")
JSON_FIELDS = ("class_priority", "class_min_share", "essential_classes", "weights")
P_COLUMNS = ", ".join("p." + c.strip() for c in COLUMNS.split(","))


def plain(v):
    """Decimals (from the database driver) inside JSON values become floats, so they can be re-serialised."""
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, dict):
        return {k: plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [plain(x) for x in v]
    return v


def _clean(p):
    """Convert DB row values (Decimal, JSON text) to plain Python types."""
    p = dict(p)
    for k in JSON_FIELDS:
        if isinstance(p.get(k), str):
            p[k] = json.loads(p[k])
        p[k] = plain(p.get(k))
    for k in ("guarantee_floor", "lpcd", "zone_min_share", "tanker_trigger_lpcd", "tanker_capacity_litres"):
        if p.get(k) is not None:
            p[k] = float(p[k])
    return p


def _view(cur, policy_id):
    cur.execute(f"SELECT {P_COLUMNS}, pa.email AS proposed_by_email, da.email AS decided_by_email "
                "FROM policies p LEFT JOIN accounts pa ON pa.account_id = p.proposed_by "
                "LEFT JOIN accounts da ON da.account_id = p.decided_by WHERE p.policy_id = %s", (policy_id,))
    row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"policy {policy_id} not found")
    return _clean(row)


def active(cur):
    """The approved policy, or None if the database has none (then config defaults are used)."""
    cur.execute(f"SELECT {COLUMNS} FROM policies WHERE status = 'approved' ORDER BY version DESC LIMIT 1")
    row = cur.fetchone()
    return _clean(row) if row else None


def scheduler_settings(cur):
    """(scheduler config, {class: min_share}, policy_id) from the active policy, or config defaults."""
    cfg = dict(config.DEFAULT_SCHEDULER_CONFIG)
    p = active(cur)
    if p is None:
        return cfg, {}, None
    cfg["weights"] = {k: float(p["weights"].get(k, cfg["weights"][k])) for k in WEIGHT_KEYS}
    cfg["guarantee_floor"] = float(p["guarantee_floor"])
    return cfg, {k: float(v) for k, v in p["class_min_share"].items()}, p["policy_id"]


def validate(p):
    """Raise InvalidInput unless the policy values make sense. Returns the cleaned values."""
    out = dict(p)
    cp, ms = out.get("class_priority") or {}, out.get("class_min_share") or {}
    for c in CLASSES:
        if c not in cp or c not in ms:
            raise InvalidInput(f"the policy must give a priority and a guaranteed share for '{c}'")
        try:
            prio, share = int(cp[c]), float(ms[c])
        except (TypeError, ValueError):
            raise InvalidInput(f"'{c}': priority must be a whole number and share a number")
        if not 1 <= prio <= 5:
            raise InvalidInput(f"'{c}': priority must be between 1 and 5")
        if not 0 <= share <= 1:
            raise InvalidInput(f"'{c}': guaranteed share must be between 0 and 1")
        cp[c], ms[c] = prio, share
    out["class_priority"] = {c: cp[c] for c in CLASSES}
    out["class_min_share"] = {c: ms[c] for c in CLASSES}
    w = out.get("weights") or {}
    try:
        w = {k: float(w.get(k, 0)) for k in WEIGHT_KEYS}
    except (TypeError, ValueError):
        raise InvalidInput("weights must be numbers")
    if any(v < 0 for v in w.values()) or sum(w.values()) <= 0:
        raise InvalidInput("weights must not be negative and must not all be zero")
    out["weights"] = w
    ess = out.get("essential_classes") or []
    if not isinstance(ess, list) or any(c not in CLASSES for c in ess):
        raise InvalidInput("essential classes must be from: " + ", ".join(CLASSES))
    out["essential_classes"] = ess
    for k, lo, hi in (("guarantee_floor", 0, 1), ("zone_min_share", 0, 1), ("lpcd", 1, 1000),
                      ("tanker_trigger_lpcd", 0, 1000), ("tanker_capacity_litres", 100, 100000)):
        try:
            out[k] = float(out[k])
        except (TypeError, ValueError, KeyError):
            raise InvalidInput(f"{k} is required and must be a number")
        if not lo <= out[k] <= hi:
            raise InvalidInput(f"{k} must be between {lo} and {hi}")
    try:
        s, e = int(out["supply_start_hour"]), int(out["supply_end_hour"])
    except (TypeError, ValueError, KeyError):
        raise InvalidInput("the supply window hours are required")
    if not 0 <= s < e <= 24:
        raise InvalidInput("the supply window must satisfy 0 <= start hour < end hour <= 24")
    out["supply_start_hour"], out["supply_end_hour"] = s, e
    if not (out.get("title") or "").strip():
        raise InvalidInput("the policy needs a title")
    return out


def list_policies(conn):
    with conn.cursor() as cur:
        cur.execute(f"SELECT {P_COLUMNS}, pa.email AS proposed_by_email, da.email AS decided_by_email "
                    "FROM policies p LEFT JOIN accounts pa ON pa.account_id = p.proposed_by "
                    "LEFT JOIN accounts da ON da.account_id = p.decided_by ORDER BY p.version DESC")
        return [_clean(r) for r in cur.fetchall()]


def get_active(conn):
    with conn.cursor() as cur:
        p = active(cur)
    if p is None:
        raise NotFoundError("no approved policy")
    return p


def create_draft(conn, actor, changes):
    """New draft = the active policy with `changes` applied."""
    with conn:
        with conn.cursor() as cur:
            base = active(cur) or {}
            values = {k: base.get(k) for k in EDITABLE}
            for k, v in (changes or {}).items():
                if k in EDITABLE and v is not None:
                    values[k] = v
            if not changes or not changes.get("title"):
                values["title"] = f"Revision of: {base.get('title', 'policy')}"
            values = validate(values)
            cur.execute("SELECT COALESCE(MAX(version), 0) + 1 AS v FROM policies")
            version = cur.fetchone()["v"]
            cols = ", ".join(EDITABLE)
            ph = ", ".join(["%s::jsonb" if k in JSON_FIELDS else "%s" for k in EDITABLE])
            params = [json.dumps(values[k]) if k in JSON_FIELDS else values[k] for k in EDITABLE]
            cur.execute(f"INSERT INTO policies (version, status, proposed_by, {cols}) VALUES (%s, 'draft', %s, {ph}) "
                        "RETURNING policy_id", [version, actor["account_id"], *params])
            pid = cur.fetchone()["policy_id"]
            audit.write(cur, actor, "policy.draft", "policy", pid, {"version": version, "title": values["title"]})
            return _view(cur, pid)


def update_draft(conn, actor, policy_id, changes):
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, proposed_by FROM policies WHERE policy_id = %s FOR UPDATE", (policy_id,))
            row = cur.fetchone()
            if row is None:
                raise NotFoundError(f"policy {policy_id} not found")
            if row["status"] != "draft":
                raise ConflictError("only a draft can be edited (create a new draft instead)")
            current = _view(cur, policy_id)
            values = {k: current[k] for k in EDITABLE}
            for k, v in (changes or {}).items():
                if k in EDITABLE and v is not None:
                    values[k] = v
            values = validate(values)
            sets = ", ".join(f"{k} = %s::jsonb" if k in JSON_FIELDS else f"{k} = %s" for k in EDITABLE)
            params = [json.dumps(values[k]) if k in JSON_FIELDS else values[k] for k in EDITABLE]
            cur.execute(f"UPDATE policies SET {sets} WHERE policy_id = %s", [*params, policy_id])
            audit.write(cur, actor, "policy.edit", "policy", policy_id, {"fields": sorted((changes or {}).keys())})
            return _view(cur, policy_id)


def submit(conn, actor, policy_id):
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, version, title FROM policies WHERE policy_id = %s FOR UPDATE", (policy_id,))
            row = cur.fetchone()
            if row is None:
                raise NotFoundError(f"policy {policy_id} not found")
            if row["status"] != "draft":
                raise ConflictError("only a draft can be submitted for approval")
            cur.execute("UPDATE policies SET status = 'pending', submitted_at = now() WHERE policy_id = %s", (policy_id,))
            audit.notify_admins(cur, "policy", f"Policy v{row['version']} waits for approval",
                                f"\"{row['title']}\" was submitted by {actor['email']}. A different officer must approve it.")
            audit.write(cur, actor, "policy.submit", "policy", policy_id, {"version": row["version"]})
            return _view(cur, policy_id)


def decide(conn, actor, policy_id, approve, note=None):
    """Approve (activate) or reject a pending policy. The approver must not be the proposer."""
    note = (note or "").strip() or None
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM policies WHERE policy_id = %s FOR UPDATE", (policy_id,))
            p = cur.fetchone()
            if p is None:
                raise NotFoundError(f"policy {policy_id} not found")
            if p["status"] != "pending":
                raise ConflictError("only a policy waiting for approval can be approved or rejected")
            self_check = p["proposed_by"] == actor["account_id"]
            if self_check and config.REQUIRE_SECOND_APPROVER:
                raise Forbidden("maker-checker: a policy must be approved by a different administrator "
                                "than the one who proposed it")
            if not approve:
                if not note:
                    raise InvalidInput("give a reason for rejecting the policy")
                cur.execute("UPDATE policies SET status = 'rejected', decided_by = %s, decided_at = now(), "
                            "decision_note = %s WHERE policy_id = %s", (actor["account_id"], note, policy_id))
                audit.write(cur, actor, "policy.reject", "policy", policy_id, {"version": p["version"], "note": note})
                if p["proposed_by"]:
                    audit.notify(cur, p["proposed_by"], "policy", f"Policy v{p['version']} was not approved", note)
                return _view(cur, policy_id)

            # lock the active policy too, so two approvals cannot both activate
            cur.execute("SELECT policy_id FROM policies WHERE status = 'approved' FOR UPDATE")
            cur.execute("UPDATE policies SET status = 'superseded' WHERE status = 'approved'")
            cur.execute("UPDATE policies SET status = 'approved', decided_by = %s, decided_at = now(), "
                        "decision_note = %s WHERE policy_id = %s", (actor["account_id"], note, policy_id))
            changed = apply_class_priorities(cur, _clean(p))
            audit.write(cur, actor, "policy.approve", "policy", policy_id,
                        {"version": p["version"], "self_approved": self_check, "organisations_updated": changed})
            audit.notify_admins(cur, "policy", f"Policy v{p['version']} is now active",
                                f"\"{p['title']}\" was approved by {actor['email']}. {changed} organisation(s) got a new priority.")
            return _view(cur, policy_id)


def apply_class_priorities(cur, p):
    """Set each organisation's priority from its class, except hand-set ones. Returns how many changed."""
    changed = 0
    for cls, prio in p["class_priority"].items():
        cur.execute("UPDATE users SET priority = %s WHERE user_type = %s AND NOT priority_override "
                    "AND priority <> %s RETURNING user_id", (int(prio), cls, int(prio)))
        changed += len(cur.fetchall())
    return changed


def class_priority(cur, user_type, fallback=3):
    p = active(cur)
    if p is None:
        return fallback
    return int(p["class_priority"].get(user_type, fallback))
