"""Helpers for tests that talk to a REAL PostgreSQL database.

Pick ONE of these (otherwise the database tests are skipped):

  JAL_TEST_DSN   a libpq connection string for a *test* database, used with the real psycopg2 driver
                 e.g.  "postgresql://postgres:jal123@localhost:5432/jalrakshak_test"
  JAL_TEST_PSQL  conninfo for the `psql` command-line client, e.g.
                 "host=/tmp/pgsock port=54329 user=postgres dbname=jalrakshak_test"
                 (a stand-in driver: it runs the same SQL on the same server through psql; used where
                 psycopg2 cannot be installed)

The test database is wiped and re-created from db/schema.sql + db/seed.sql before every test.
NEVER point these at a database whose contents you care about.
"""
import json
import os
import re
import subprocess
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_SQL = ROOT / "db" / "schema.sql"
SEED_SQL = ROOT / "db" / "seed.sql"
MIGRATE_SQL = ROOT / "db" / "migrate.sql"

DSN = os.environ.get("JAL_TEST_DSN")
PSQL_INFO = os.environ.get("JAL_TEST_PSQL")

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:  # pragma: no cover
    psycopg2 = None


def available():
    return bool((DSN and psycopg2) or PSQL_INFO)


# ---------------------------------------------------------------- psql-backed stand-in driver
class PsqlError(Exception):
    def __init__(self, pgcode, message):
        super().__init__(message)
        self.pgcode = pgcode


_ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d")


def _revive(value):
    if isinstance(value, str) and _ISO.match(value):
        return datetime.fromisoformat(value)
    return value


def _quote(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float, Decimal)):
        return repr(v) if not isinstance(v, Decimal) else str(v)
    if isinstance(v, (list, tuple)):  # psycopg2 sends a Python list as an SQL ARRAY
        return "ARRAY[" + ", ".join(_quote(x) for x in v) + "]" if v else "'{}'"
    return "'" + str(v).replace("'", "''") + "'"


class _PsqlCursor:
    def __init__(self, conn):
        self._c, self._rows = conn, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        if params:
            if sql.count("%s") != len(params):
                raise ValueError("placeholder / parameter count mismatch")
            sql = sql % tuple(_quote(p) for p in params)
        self._rows = self._c._execute(sql.strip().rstrip(";"))

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class PsqlConnection:
    """Just enough of the psycopg2 connection API for app/service.py: cursor(), `with conn:`, close()."""

    def __init__(self, conninfo):
        self._p = subprocess.Popen(["psql", "-X", "-q", "-A", "-t", "-v", "VERBOSITY=verbose", conninfo],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, bufsize=1)
        self._in_tx = False

    def _raw(self, statement):
        marker = "__END_" + uuid.uuid4().hex + "__"
        self._p.stdin.write(statement + ";\n\\echo " + marker + "\n")
        self._p.stdin.flush()
        lines = []
        for line in iter(self._p.stdout.readline, ""):
            if line.strip() == marker:
                break
            lines.append(line)
        text = "".join(lines)
        m = re.search(r"ERROR:\s+(\w{5}):\s*(.*)", text)
        if m:
            raise PsqlError(m.group(1), m.group(2).strip())
        return text

    def _execute(self, sql):
        if not self._in_tx:
            self._raw("BEGIN")
            self._in_tx = True
        head = sql.lstrip().upper()
        if head.startswith(("SELECT", "WITH")):
            text = self._raw(f"SELECT COALESCE(json_agg(t), '[]'::json) FROM ({sql}) t")
        elif "RETURNING" in head and head.startswith(("INSERT", "UPDATE", "DELETE")):
            text = self._raw(f"WITH q AS ({sql}) SELECT COALESCE(json_agg(q), '[]'::json) FROM q")
        else:
            self._raw(sql)
            return []
        rows = json.loads(text.strip(), parse_float=Decimal)
        return [{k: _revive(v) for k, v in row.items()} for row in rows]

    def cursor(self, *a, **kw):
        return _PsqlCursor(self)

    def commit(self):
        if self._in_tx:
            self._in_tx = False
            self._raw("COMMIT")

    def rollback(self):
        if self._in_tx:
            self._in_tx = False
            self._raw("ROLLBACK")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        (self.rollback if exc_type else self.commit)()
        return False

    def close(self):
        try:
            self._p.stdin.write("\\q\n")
            self._p.stdin.flush()
        except (BrokenPipeError, ValueError):
            pass
        self._p.wait(timeout=10)
        self._p.stdin.close()
        self._p.stdout.close()


# ---------------------------------------------------------------- public helpers
def open_conn():
    """A new connection to the test database (dict rows, psycopg2-style API)."""
    if DSN and psycopg2:
        return psycopg2.connect(DSN, cursor_factory=RealDictCursor)
    return PsqlConnection(PSQL_INFO)


def reset_database():
    """Drop and re-create all tables, load the seed data, then run the upgrade script (as Docker does)."""
    if DSN and psycopg2:
        conn = psycopg2.connect(DSN)
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(SCHEMA_SQL.read_text())
                cur.execute(SEED_SQL.read_text())
                cur.execute(MIGRATE_SQL.read_text())
        finally:
            conn.close()
    else:
        for f in (SCHEMA_SQL, SEED_SQL, MIGRATE_SQL):
            subprocess.run(["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-f", str(f), PSQL_INFO],
                           check=True, capture_output=True, text=True)
