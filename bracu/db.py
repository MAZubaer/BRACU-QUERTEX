"""One reusable Postgres connection per server instance.

Serverless instances are short-lived but handle many requests while warm,
so a single connection (guarded by a lock; queries take milliseconds) is
reused, and reopened automatically if Neon closed it (for example after the
database scaled to zero).
"""
import threading

import psycopg

from . import config

_conn = None
_lock = threading.Lock()


def connect(url=None):
    url = url or config.DATABASE_URL
    if not url:
        raise RuntimeError('DATABASE_URL is not set (see .env.example)')
    # prepare_threshold=None: no server-side prepared statements, which is
    # what connection poolers such as Neon's "-pooler" endpoint expect.
    return psycopg.connect(url, autocommit=True, prepare_threshold=None, connect_timeout=15)


def run(fn):
    """Run fn(cursor) and return its result. Reconnects and retries once if
    the connection was dropped. Keep fn short: other requests wait for it."""
    global _conn
    with _lock:
        for attempt in (1, 2):
            try:
                if _conn is None or _conn.closed:
                    _conn = connect()
                with _conn.cursor() as cur:
                    return fn(cur)
            except psycopg.OperationalError:
                try:
                    if _conn is not None:
                        _conn.close()
                finally:
                    _conn = None
                if attempt == 2:
                    raise


def fetchall(sql, params=None):
    def q(cur):
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else []
    return run(q)


def fetchone(sql, params=None):
    rows = fetchall(sql, params)
    return rows[0] if rows else None
