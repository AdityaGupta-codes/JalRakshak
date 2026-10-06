import psycopg2
from psycopg2.extras import RealDictCursor

from . import config


def get_conn():
    """FastAPI dependency: one DB connection per request (rows come back as dicts)."""
    conn = psycopg2.connect(config.DATABASE_URL, cursor_factory=RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()
