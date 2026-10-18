# Security: sign-in, roles and the audit trail

JalRakshak has two kinds of people. **Users** (role `requester`) belong to an organisation (a hospital, school,
colony …) and use the user panel. **Administrators** (role `admin`) run the water department's admin panel.
This page explains how each security feature works and why it was chosen. The code is in `backend/app/auth.py`,
`backend/app/main.py` (role checks) and `db/migrate.sql` (tables).

## Passwords

- Never stored. The database keeps `pbkdf2_sha256$<iterations>$<salt>$<hash>`: PBKDF2 with SHA-256, a random
  16-byte salt per password, and 600,000 iterations (the OWASP 2023 recommendation). The salt means two people
  with the same password get different hashes; the iterations make each guess slow (well under a second on a
  laptop, but millions of times slower than a single hash).
- Checked with a constant-time comparison (`hmac.compare_digest`), so response time does not leak how much matched.
- Rules: at least 8 characters with a letter and a number.
- Temporary passwords (from an admin creating an account or resetting one) are shown once and must be changed at
  the next sign-in. The first administrator must also change the default password at first sign-in.

## Sessions

- Signing in creates a 32-byte random token (`secrets.token_urlsafe`). The browser sends it as
  `Authorization: Bearer <token>`.
- The database stores only the SHA-256 of the token (`sessions.token_hash`). Someone who copies the database
  cannot use those values to sign in.
- Sessions expire after 12 hours (`SESSION_HOURS`). Signing out deletes the session. Changing your password signs
  out every other device. Disabling an account, changing its role, or resetting its password ends all its sessions
  immediately.
- Tokens in a header, not cookies, so the API is not open to cross-site request forgery. The token is kept in the
  browser's local storage; the trade-off (readable by scripts on the same page if the page were ever compromised)
  is acceptable here because the app loads no third-party scripts.

## Guessing and account discovery

- Five wrong passwords lock the account for 5 minutes (`MAX_FAILED_LOGINS`, `LOCKOUT_MINUTES`); the answer is
  HTTP 429 with how long to wait.
- "Wrong email or password" is the same message for an unknown email and a wrong password, and an unknown email
  still costs one password-hash computation, so sign-in cannot be used to find out which emails have accounts.
- Every failed sign-in is written to the audit log.

## Roles and access control

Every endpoint declares who may call it, through FastAPI dependencies:

| Dependency | Lets through |
|---|---|
| `current_account` | Any active, signed-in account (otherwise 401) |
| `admin_account` | Role `admin` only (otherwise 403) |
| `requester_account` | Role `requester` with an organisation (otherwise 403) |

The user panel's queries are always filtered by the caller's own organisation (`WHERE user_id = me.user_id`).
Asking for another organisation's request answers 404, not 403, so it does not even confirm the request exists.

Guard rails for administrators: you cannot disable or demote yourself, and the system refuses to leave no active
administrator.

## Sign-up and approval

A new organisation registers with status `pending`. It cannot sign in until an administrator approves it, choosing
its priority (or linking it to an organisation that already exists). Rejected and disabled accounts cannot sign in.
Administrators are notified of every new sign-up.

## Audit trail

`audit_log` records who did what and when: sign-ins (and failures), plans applied, requests created or cancelled,
issues, account changes, reservoir and settings changes, alert handling, demo resets.

- Each entry is written **in the same transaction** as the change it describes: both are saved, or neither is.
- The table is **append-only**: a PostgreSQL trigger (`audit_log_append_only`) raises an error on any UPDATE or
  DELETE, so history cannot be quietly rewritten, even by the application.

## Other measures

- All SQL uses parameters (`%s`), never string building, so input cannot change a query (SQL injection).
- Every request body is validated by Pydantic models (types, lengths, ranges) before any code runs.
- Responses carry `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and `Cache-Control: no-store`.
- Allowed web origins are configurable (`CORS_ORIGINS`).

## Limits (honest list)

- No email sending, so there is no "forgot password" by email: an administrator resets it.
- No two-factor authentication.
- The demo sign-ins share one password; turn them off for anything beyond a demo (`JAL_DEMO_ACCOUNTS=0`).
- Run behind HTTPS (for example a reverse proxy) before putting it on a real network; Docker Compose here serves
  plain HTTP on localhost.
