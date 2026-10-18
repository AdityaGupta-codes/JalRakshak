"""Audit trail: who did what, when. Rows are written inside the same transaction as the change they
describe, so a change and its record are saved together or not at all. The table is append-only (trigger)."""
import json


def write(cur, actor, action, entity=None, entity_id=None, details=None):
    """actor: the signed-in account dict (or None for the system)."""
    cur.execute(
        "INSERT INTO audit_log (account_id, actor, action, entity, entity_id, details) "
        "VALUES (%s, %s, %s, %s, %s, %s::jsonb)",
        (actor["account_id"] if actor else None, actor["email"] if actor else "system", action, entity,
         None if entity_id is None else str(entity_id), json.dumps(details or {}, default=str)))


def notify(cur, account_id, kind, title, body):
    cur.execute("INSERT INTO notifications (account_id, kind, title, body) VALUES (%s, %s, %s, %s)",
                (account_id, kind, title, body))


def notify_admins(cur, kind, title, body):
    cur.execute("INSERT INTO notifications (account_id, kind, title, body) "
                "SELECT account_id, %s, %s, %s FROM accounts WHERE role = 'admin' AND status = 'active'",
                (kind, title, body))


def notify_organisation(cur, user_id, kind, title, body):
    """Every active account that belongs to this organisation."""
    cur.execute("INSERT INTO notifications (account_id, kind, title, body) "
                "SELECT account_id, %s, %s, %s FROM accounts WHERE user_id = %s AND status = 'active'",
                (kind, title, body, user_id))
