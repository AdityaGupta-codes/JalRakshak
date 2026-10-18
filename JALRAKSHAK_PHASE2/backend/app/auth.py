"""Sign-in: password hashing, sessions, login throttling, sign-up with admin approval.

Security choices (each is a standard, explainable practice):
  * Passwords are stored as PBKDF2-SHA256 with a random 16-byte salt and 600,000 iterations (OWASP 2023),
    never in plain text. Checking uses a constant-time comparison.
  * A session token is 32 random bytes (secrets module). The database keeps only its SHA-256 hash, so a
    leaked database does not give anyone a working session. Sessions expire (SESSION_HOURS) and sign-out
    deletes them.
  * After MAX_FAILED_LOGINS wrong passwords the account is locked for LOCKOUT_MINUTES (slows guessing).
  * "Wrong email or password" is the same message for an unknown email and a wrong password, and an unknown
    email still costs one hash computation, so sign-in does not reveal which emails exist.
"""
import base64
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

from . import audit, config
from .errors import ConflictError, Forbidden, InvalidInput, NotFoundError, NotSignedIn, TooManyAttempts

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USER_TYPES = ("hospital", "domestic", "school", "industry", "commercial", "park")
DEFAULT_PRIORITY = {"hospital": 5, "school": 4, "domestic": 4, "commercial": 2, "industry": 2, "park": 1}
ACCOUNT_COLUMNS = ("a.account_id, a.email, a.full_name, a.phone, a.role, a.status, a.user_id, a.org_name, "
                   "a.org_type, a.org_zone_id, a.must_change_password, a.created_at, a.last_login_at, "
                   "a.mfa_enabled, a.consent_version, a.consent_at, a.language")


# ---------------------------------------------------------------- passwords
def _b64(raw):
    return base64.b64encode(raw).decode()


def hash_password(password, iterations=None):
    iterations = iterations or config.PASSWORD_ITERATIONS
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64(salt)}${_b64(digest)}"


def verify_password(password, stored):
    try:
        scheme, iterations, salt, digest = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        test = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iterations))
        return hmac.compare_digest(test, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


# Used when the email is unknown, so the response takes as long as a real check.
_DUMMY_HASH = None


def _dummy_hash():
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_hex(8))
    return _DUMMY_HASH


def check_password_rules(password):
    if not isinstance(password, str) or len(password) < 8:
        raise InvalidInput("the password must be at least 8 characters")
    if len(password) > 128:
        raise InvalidInput("the password must be at most 128 characters")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise InvalidInput("the password must contain at least one letter and one number")


def clean_email(email):
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 200:
        raise InvalidInput("enter a valid email address")
    return email


# ---------------------------------------------------------------- sessions
def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _create_session(cur, account_id):
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=config.SESSION_HOURS)
    cur.execute("INSERT INTO sessions (token_hash, account_id, expires_at) VALUES (%s, %s, %s)",
                (_token_hash(token), account_id, expires.isoformat()))
    return token, expires


def account_for_token(conn, token):
    """The active account behind a session token, or raise NotSignedIn."""
    if not token:
        raise NotSignedIn("sign in to continue")
    with conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {ACCOUNT_COLUMNS}, s.token_hash, u.name AS org_display_name, u.user_type, "
                        "u.priority, u.zone_id, z.name AS zone_name "
                        "FROM sessions s JOIN accounts a ON a.account_id = s.account_id "
                        "LEFT JOIN users u ON u.user_id = a.user_id LEFT JOIN zones z ON z.zone_id = u.zone_id "
                        "WHERE s.token_hash = %s AND s.expires_at > now()", (_token_hash(token),))
            row = cur.fetchone()
            if row is None:
                raise NotSignedIn("your session has expired, please sign in again")
            if row["status"] != "active":
                cur.execute("DELETE FROM sessions WHERE account_id = %s", (row["account_id"],))
                raise NotSignedIn("this account is no longer active")
            cur.execute("UPDATE sessions SET last_seen_at = now() WHERE token_hash = %s", (row["token_hash"],))
    row = dict(row)
    row.pop("token_hash", None)
    return row


def public_account(row):
    """What the browser is allowed to see about an account."""
    keep = ("account_id", "email", "full_name", "phone", "role", "status", "user_id", "must_change_password",
            "last_login_at", "org_display_name", "user_type", "priority", "zone_id", "zone_name", "mfa_enabled",
            "consent_at", "language")
    out = {k: row.get(k) for k in keep}
    out["mfa_enabled"] = bool(out.get("mfa_enabled"))
    # an administrator must set up two-factor sign-in before using the admin panel (REQUIRE_ADMIN_MFA=1)
    out["mfa_setup_required"] = bool(row.get("role") == "admin" and config.REQUIRE_ADMIN_MFA and not out["mfa_enabled"])
    # the person has not yet accepted the current privacy notice (DPDP Act)
    out["consent_required"] = row.get("consent_version") != config.PRIVACY_NOTICE_VERSION
    return out


# ---------------------------------------------------------------- sign in / out
def login(conn, email, password):
    try:
        email = clean_email(email)
    except InvalidInput:
        raise NotSignedIn("wrong email or password")
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT account_id, email, password_hash, status, failed_logins, locked_until, mfa_enabled, "
                        "locked_until > now() AS locked, "
                        "CEIL(EXTRACT(EPOCH FROM (locked_until - now())) / 60) AS minutes_left "
                        "FROM accounts WHERE lower(email) = %s FOR UPDATE", (email,))
            acc = cur.fetchone()
            if acc is None:
                verify_password(password or "", _dummy_hash())
                audit.write(cur, None, "login.failed", "account", None, {"email": email, "reason": "unknown email"})
                result = ("fail", None)
            elif acc["locked"]:
                result = ("locked", int(acc["minutes_left"] or 1))
            elif not verify_password(password or "", acc["password_hash"]):
                failed = int(acc["failed_logins"]) + 1
                if failed >= config.MAX_FAILED_LOGINS:
                    cur.execute("UPDATE accounts SET failed_logins = 0, "
                                "locked_until = now() + (%s * interval '1 minute') WHERE account_id = %s",
                                (config.LOCKOUT_MINUTES, acc["account_id"]))
                else:
                    cur.execute("UPDATE accounts SET failed_logins = %s WHERE account_id = %s",
                                (failed, acc["account_id"]))
                audit.write(cur, {"account_id": acc["account_id"], "email": acc["email"]}, "login.failed",
                            "account", acc["account_id"], {"attempt": failed})
                result = ("locked", int(config.LOCKOUT_MINUTES)) if failed >= config.MAX_FAILED_LOGINS else ("fail", None)
            elif acc["status"] != "active":
                result = (acc["status"], None)
            elif acc["mfa_enabled"]:
                # password is right; the second factor is checked by complete_mfa()
                cur.execute("UPDATE accounts SET failed_logins = 0, locked_until = NULL WHERE account_id = %s",
                            (acc["account_id"],))
                challenge = secrets.token_urlsafe(32)
                cur.execute("DELETE FROM login_challenges WHERE account_id = %s OR expires_at < now()", (acc["account_id"],))
                cur.execute("INSERT INTO login_challenges (token_hash, account_id, expires_at) "
                            "VALUES (%s, %s, now() + interval '5 minutes')", (_token_hash(challenge), acc["account_id"]))
                result = ("mfa", challenge)
            else:
                cur.execute("UPDATE accounts SET failed_logins = 0, locked_until = NULL, last_login_at = now() "
                            "WHERE account_id = %s", (acc["account_id"],))
                token, expires = _create_session(cur, acc["account_id"])
                audit.write(cur, {"account_id": acc["account_id"], "email": acc["email"]}, "login", "account",
                            acc["account_id"])
                result = ("ok", (token, expires))
    kind, value = result
    if kind == "fail":
        raise NotSignedIn("wrong email or password")
    if kind == "locked":
        raise TooManyAttempts(f"too many wrong passwords. Try again in {value} minute{'s' if value != 1 else ''}.")
    if kind == "pending":
        raise Forbidden("your sign-up is waiting for an administrator to approve it")
    if kind == "rejected":
        raise Forbidden("your sign-up was not approved. Contact the water department.")
    if kind == "disabled":
        raise Forbidden("this account has been disabled. Contact the water department.")
    if kind == "mfa":
        return {"mfa_required": True, "challenge": value,
                "message": "Enter the 6-digit code from your authenticator app."}
    token, expires = value
    account = account_for_token(conn, token)
    return {"token": token, "expires_at": expires.isoformat(), "account": public_account(account)}


def complete_mfa(conn, challenge, code):
    """Second sign-in step: the challenge from login() plus the current authenticator code."""
    from . import mfa  # local import: mfa imports config only
    if not challenge:
        raise NotSignedIn("sign in again")
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT c.account_id, c.attempts, c.expires_at > now() AS valid, a.email, a.mfa_secret, a.status "
                        "FROM login_challenges c JOIN accounts a ON a.account_id = c.account_id "
                        "WHERE c.token_hash = %s FOR UPDATE OF c", (_token_hash(challenge),))
            row = cur.fetchone()
            if row is None or not row["valid"] or row["attempts"] >= 5 or row["status"] != "active":
                if row is not None:
                    cur.execute("DELETE FROM login_challenges WHERE token_hash = %s", (_token_hash(challenge),))
                result = ("expired", None)
            elif not mfa.verify(row["mfa_secret"], code):
                cur.execute("UPDATE login_challenges SET attempts = attempts + 1 WHERE token_hash = %s",
                            (_token_hash(challenge),))
                audit.write(cur, {"account_id": row["account_id"], "email": row["email"]}, "login.mfa_failed",
                            "account", row["account_id"], {"attempt": row["attempts"] + 1})
                result = ("wrong", None)
            else:
                cur.execute("DELETE FROM login_challenges WHERE token_hash = %s", (_token_hash(challenge),))
                cur.execute("UPDATE accounts SET last_login_at = now() WHERE account_id = %s", (row["account_id"],))
                token, expires = _create_session(cur, row["account_id"])
                audit.write(cur, {"account_id": row["account_id"], "email": row["email"]}, "login", "account",
                            row["account_id"], {"mfa": True})
                result = ("ok", (token, expires))
    kind, value = result
    if kind == "expired":
        raise NotSignedIn("the sign-in step expired or had too many wrong codes; sign in again")
    if kind == "wrong":
        raise InvalidInput("that code is not right; use the newest code from your app")
    token, expires = value
    return {"token": token, "expires_at": expires.isoformat(), "account": public_account(account_for_token(conn, token))}


def logout(conn, token, actor):
    with conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sessions WHERE token_hash = %s", (_token_hash(token),))
            audit.write(cur, actor, "logout", "account", actor["account_id"])
    return {"signed_out": True}


def register(conn, full_name, email, password, phone, org_name, org_type, zone_id, consent=False):
    """A new organisation signs up. The account waits for an admin to approve it.
    consent: the person accepted the privacy notice (DPDP Act); sign-up is refused without it."""
    if not consent:
        raise InvalidInput("please read and accept the privacy notice to sign up")
    email = clean_email(email)
    check_password_rules(password)
    full_name, org_name = (full_name or "").strip(), (org_name or "").strip()
    if not full_name or not org_name:
        raise InvalidInput("your name and your organisation's name are required")
    if org_type not in USER_TYPES:
        raise InvalidInput(f"type must be one of {', '.join(USER_TYPES)}")
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS x FROM accounts WHERE lower(email) = %s", (email,))
            if cur.fetchone():
                raise ConflictError("an account with this email already exists")
            cur.execute("SELECT zone_id FROM zones WHERE zone_id = %s", (zone_id,))
            if cur.fetchone() is None:
                raise NotFoundError(f"zone {zone_id} not found")
            cur.execute("INSERT INTO accounts (email, password_hash, full_name, phone, role, status, org_name, "
                        "org_type, org_zone_id, consent_at, consent_version) "
                        "VALUES (%s, %s, %s, %s, 'requester', 'pending', %s, %s, %s, now(), %s) "
                        "RETURNING account_id", (email, hash_password(password), full_name, (phone or "").strip() or None,
                                                 org_name, org_type, zone_id, config.PRIVACY_NOTICE_VERSION))
            account_id = cur.fetchone()["account_id"]
            audit.write(cur, {"account_id": account_id, "email": email}, "account.register", "account", account_id,
                        {"organisation": org_name, "type": org_type, "zone_id": zone_id})
            audit.notify_admins(cur, "account", "New sign-up waiting for approval",
                                f"{full_name} ({email}) registered {org_name} as {org_type}.")
    return {"account_id": account_id, "status": "pending",
            "message": "Thank you. An administrator will review your sign-up; you can sign in once it is approved."}


def change_password(conn, actor, current, new, keep_token=None):
    check_password_rules(new)
    if current == new:
        raise InvalidInput("choose a password different from the current one")
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT password_hash FROM accounts WHERE account_id = %s FOR UPDATE", (actor["account_id"],))
            row = cur.fetchone()
            if row is None or not verify_password(current or "", row["password_hash"]):
                raise InvalidInput("the current password is not correct")
            cur.execute("UPDATE accounts SET password_hash = %s, must_change_password = FALSE WHERE account_id = %s",
                        (hash_password(new), actor["account_id"]))
            # sign out every other device
            cur.execute("DELETE FROM sessions WHERE account_id = %s AND token_hash <> %s",
                        (actor["account_id"], _token_hash(keep_token or "")))
            audit.write(cur, actor, "account.password_change", "account", actor["account_id"])
    return {"changed": True}


# ---------------------------------------------------------------- first start
DEMO_APPROVER_EMAIL = "approver@jalrakshak.local"


def ensure_demo_approver(conn):
    """Demo installs get a second administrator, so maker-checker approvals can be shown. Idempotent."""
    if not config.DEMO_ACCOUNTS:
        return None
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(724003) AS locked")
            cur.execute("SELECT 1 AS x FROM accounts WHERE lower(email) = %s", (DEMO_APPROVER_EMAIL,))
            if cur.fetchone():
                return None
            cur.execute("INSERT INTO accounts (email, password_hash, full_name, role, status) "
                        "VALUES (%s, %s, 'Second officer (demo approver)', 'admin', 'active') RETURNING account_id",
                        (DEMO_APPROVER_EMAIL, hash_password(config.DEMO_PASSWORD)))
            aid = cur.fetchone()["account_id"]
            audit.write(cur, None, "account.bootstrap_approver", "account", aid, {"email": DEMO_APPROVER_EMAIL})
    return DEMO_APPROVER_EMAIL


def _demo_email(name):
    slug = re.sub(r"[^a-z0-9]+", ".", name.lower()).strip(".")
    return f"{slug}@jalrakshak.local"


def bootstrap(conn):
    """On first start (no admin yet): create the first admin and, if enabled, demo sign-ins for every
    organisation. Returns a list of what was created (for the start-up log)."""
    created = []
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(724002) AS locked")
            cur.execute("SELECT COUNT(*) AS n FROM accounts WHERE role = 'admin'")
            if cur.fetchone()["n"]:
                return created
            cur.execute("INSERT INTO accounts (email, password_hash, full_name, role, status, must_change_password) "
                        "VALUES (%s, %s, 'Administrator', 'admin', 'active', %s) RETURNING account_id",
                        (config.ADMIN_EMAIL.lower(), hash_password(config.ADMIN_PASSWORD),
                         config.FORCE_ADMIN_PASSWORD_CHANGE))
            admin_id = cur.fetchone()["account_id"]
            audit.write(cur, None, "account.bootstrap_admin", "account", admin_id, {"email": config.ADMIN_EMAIL})
            created.append(config.ADMIN_EMAIL)
            if config.DEMO_ACCOUNTS:
                cur.execute("SELECT u.user_id, u.name FROM users u WHERE NOT EXISTS "
                            "(SELECT 1 FROM accounts a WHERE a.user_id = u.user_id) ORDER BY u.user_id")
                demo_hash = hash_password(config.DEMO_PASSWORD)  # one hash, reused: demo accounts only
                for u in cur.fetchall():
                    email = _demo_email(u["name"])
                    cur.execute("INSERT INTO accounts (email, password_hash, full_name, role, status, user_id) "
                                "VALUES (%s, %s, %s, 'requester', 'active', %s) ON CONFLICT DO NOTHING",
                                (email, demo_hash, f"{u['name']} (demo)", u["user_id"]))
                    created.append(email)
                audit.write(cur, None, "account.bootstrap_demo", "account", None, {"count": len(created) - 1})
    return created
