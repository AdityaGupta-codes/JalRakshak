"""Two-factor sign-in with an authenticator app (TOTP, RFC 6238), using only the standard library.

Enrolment: the server makes a random secret, the person adds it to an authenticator app (Google
Authenticator, Microsoft Authenticator, ...) by scanning the otpauth:// link as a QR code or typing the key,
then confirms with one 6-digit code. Sign-in: after the password is correct, the server asks for the current
code (a short-lived challenge, 5 attempts, 5 minutes).

The secret is stored in accounts.mfa_secret. (A production system would additionally encrypt it with a key
held outside the database, e.g. in a key-management service.)
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from . import audit, config
from .errors import ConflictError, Forbidden, InvalidInput

STEP = 30      # seconds per code
DIGITS = 6
WINDOW = 1     # also accept the previous / next code (clock drift)


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret):
    s = secret.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def code_at(secret, counter):
    digest = hmac.new(_key(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def verify(secret, code, now=None):
    if not secret or not code:
        return False
    code = str(code).strip().replace(" ", "")
    if not code.isdigit() or len(code) != DIGITS:
        return False
    counter = int((now if now is not None else time.time()) // STEP)
    return any(hmac.compare_digest(code_at(secret, counter + d), code) for d in range(-WINDOW, WINDOW + 1))


def otpauth_uri(secret, email):
    label = quote(f"{config.MFA_ISSUER}:{email}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(config.MFA_ISSUER)}&digits={DIGITS}&period={STEP}"


def begin(conn, me):
    """Create (or replace) an unconfirmed secret. Returns the key to put in the authenticator app."""
    secret = new_secret()
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT mfa_enabled FROM accounts WHERE account_id = %s FOR UPDATE", (me["account_id"],))
            if cur.fetchone()["mfa_enabled"]:
                raise ConflictError("two-factor sign-in is already on; turn it off first to change the device")
            cur.execute("UPDATE accounts SET mfa_secret = %s WHERE account_id = %s", (secret, me["account_id"]))
    return {"secret": secret, "otpauth_uri": otpauth_uri(secret, me["email"]), "issuer": config.MFA_ISSUER,
            "digits": DIGITS, "period": STEP}


def confirm(conn, me, code):
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT mfa_secret, mfa_enabled FROM accounts WHERE account_id = %s FOR UPDATE", (me["account_id"],))
            row = cur.fetchone()
            if row["mfa_enabled"]:
                raise ConflictError("two-factor sign-in is already on")
            if not row["mfa_secret"]:
                raise ConflictError("start the set-up first")
            if not verify(row["mfa_secret"], code):
                raise InvalidInput("that code is not right; check the time on your phone and try the newest code")
            cur.execute("UPDATE accounts SET mfa_enabled = TRUE WHERE account_id = %s", (me["account_id"],))
            audit.write(cur, me, "mfa.enable", "account", me["account_id"])
    return {"mfa_enabled": True}


def disable(conn, me, code):
    if me["role"] == "admin" and config.REQUIRE_ADMIN_MFA:
        raise Forbidden("administrators must keep two-factor sign-in on")
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT mfa_secret, mfa_enabled FROM accounts WHERE account_id = %s FOR UPDATE", (me["account_id"],))
            row = cur.fetchone()
            if not row["mfa_enabled"]:
                raise ConflictError("two-factor sign-in is not on")
            if not verify(row["mfa_secret"], code):
                raise InvalidInput("that code is not right")
            cur.execute("UPDATE accounts SET mfa_enabled = FALSE, mfa_secret = NULL WHERE account_id = %s", (me["account_id"],))
            audit.write(cur, me, "mfa.disable", "account", me["account_id"])
    return {"mfa_enabled": False}
