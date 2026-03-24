"""
Database layer — SQLite via the standard `sqlite3` module.

Why SQLite?  Zero external dependencies, single file on disk, perfect for a
self-contained demo that runs inside Docker without a separate DB container.

Connection strategy: we open a new connection per request (thread-safe) and
close it when the request is done.  For a production service you would use
SQLAlchemy + connection pooling, but this keeps the code easy to follow.
"""

import sqlite3
import os
import logging

logger = logging.getLogger(__name__)

# The DB file lives at this path.  When running in Docker the working directory
# is /app, so the file will be /app/resources.db inside the container.
DB_PATH = os.getenv("DB_PATH", "resources.db")


def get_connection() -> sqlite3.Connection:
    """
    Open and return a SQLite connection.

    detect_types lets SQLite automatically convert stored TEXT back to Python
    datetime objects when we use the TIMESTAMP column type.
    row_factory = sqlite3.Row makes rows behave like dicts (col access by name).
    """
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row   # so we can do row["id"] instead of row[0]
    return conn


def init_db() -> None:
    """
    Create the resources table if it doesn't exist yet.

    Running this at startup is idempotent — safe to call every time the
    service starts without destroying existing data.
    """
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS resources (
                id          TEXT        PRIMARY KEY,
                name        TEXT        NOT NULL,
                type        TEXT        NOT NULL,
                status      TEXT        NOT NULL    DEFAULT 'active',
                owner       TEXT        NOT NULL,
                policy_tags TEXT        NOT NULL    DEFAULT '{}',
                region      TEXT        NOT NULL    DEFAULT 'us-east-1',
                created_at  TIMESTAMP   NOT NULL,
                updated_at  TIMESTAMP   NOT NULL
            )
            """
        )
        conn.commit()
        logger.info("Table 'resources' is ready.")
    finally:
        conn.close()
