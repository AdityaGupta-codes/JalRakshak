import logging
import threading
import time

import psycopg2
from psycopg2.extras import RealDictCursor

from . import config, migrations
from .errors import DatabaseUnavailable

log = logging.getLogger("jalrakshak")
_schema_ready = False
_schema_lock = threading.Lock()


def ensure_schema(conn):
    """Upgrade the database once per backend process (see db/migrate.sql). Cheap after the first time."""
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
        _schema_ready = True
        log.info("database schema is up to date")


def connect():
    """Open a connection (rows come back as dicts). Retries a few times, then raises DatabaseUnavailable."""
    last_error = None
    for attempt in range(max(1, config.DB_CONNECT_RETRIES)):
        try:
            return psycopg2.connect(config.DATABASE_URL, cursor_factory=RealDictCursor,
                                    connect_timeout=config.DB_CONNECT_TIMEOUT_S)
        except psycopg2.OperationalError as exc:
            last_error = exc
            if attempt + 1 < config.DB_CONNECT_RETRIES:
                time.sleep(config.DB_CONNECT_DELAY_S)
    raise DatabaseUnavailable(f"cannot connect to the database: {str(last_error).strip()}")


def get_conn():
    """FastAPI dependency: one DB connection per request, always closed afterwards."""
    conn = connect()
    try:
        ensure_schema(conn)
        yield conn
    finally:
        conn.close()
