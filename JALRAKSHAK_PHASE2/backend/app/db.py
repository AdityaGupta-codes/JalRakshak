import logging
import threading
import time

import psycopg2
from psycopg2.extras import RealDictCursor

from . import auth, config, migrations
from .errors import DatabaseUnavailable

log = logging.getLogger("jalrakshak")
_schema_ready = False
_schema_lock = threading.Lock()


def ensure_schema(conn):
    """Upgrade the database once per backend process (see db/migrate.sql). Cheap after the first time.
    `conn` must be an owner connection (connect(owner=True)) when APP_DB_USER is used."""
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        try:
            migrations.apply(conn)
        except migrations.SchemaProblem as exc:
            raise DatabaseUnavailable(str(exc))
        except psycopg2.Error as exc:
            raise DatabaseUnavailable(f"could not upgrade the database: {str(exc).strip()}. "
                                      + migrations.RESET_HINT)
        try:
            migrations.ensure_app_role(conn, config.APP_DB_USER, config.APP_DB_PASSWORD)
        except migrations.SchemaProblem as exc:
            raise DatabaseUnavailable(str(exc))
        except psycopg2.Error as exc:
            raise DatabaseUnavailable(f"could not set up the application database user: {str(exc).strip()}")
        try:
            created = auth.bootstrap(conn)
        except psycopg2.Error as exc:
            raise DatabaseUnavailable(f"could not create the first accounts: {str(exc).strip()}")
        try:
            approver = auth.ensure_demo_approver(conn)
        except psycopg2.Error as exc:
            raise DatabaseUnavailable(f"could not create the demo approver: {str(exc).strip()}")
        if approver:
            created.append(approver)
        if created:
            print("JalRakshak: first start, created sign-ins: " + ", ".join(created), flush=True)
        _schema_ready = True
        log.info("database schema is up to date")


def connect(owner=False):
    """Open a connection (rows come back as dicts). Retries a few times, then raises DatabaseUnavailable.

    owner=False and APP_DB_USER set: log in as the least-privilege application user (normal requests).
    owner=True: the DATABASE_URL user, which owns the schema (migrations, start-up tasks)."""
    last_error = None
    extra = {}
    if config.APP_DB_USER and not owner:
        extra = {"user": config.APP_DB_USER, "password": config.APP_DB_PASSWORD}
    for attempt in range(max(1, config.DB_CONNECT_RETRIES)):
        try:
            return psycopg2.connect(config.DATABASE_URL, cursor_factory=RealDictCursor,
                                    connect_timeout=config.DB_CONNECT_TIMEOUT_S, **extra)
        except psycopg2.OperationalError as exc:
            last_error = exc
            if attempt + 1 < config.DB_CONNECT_RETRIES:
                time.sleep(config.DB_CONNECT_DELAY_S)
    raise DatabaseUnavailable(f"cannot connect to the database: {str(last_error).strip()}")


def get_conn():
    """FastAPI dependency: one DB connection per request, always closed afterwards."""
    if not _schema_ready:
        if config.APP_DB_USER:
            owner = connect(owner=True)
            try:
                ensure_schema(owner)
            finally:
                owner.close()
        else:
            pre = connect()
            try:
                ensure_schema(pre)
            except Exception:
                pre.close()
                raise
            conn = pre  # reuse it for this request
            try:
                yield conn
            finally:
                conn.close()
            return
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
