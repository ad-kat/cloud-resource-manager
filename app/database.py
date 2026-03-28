import sqlite3
import os
import logging

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "resources.db")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
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
        # Audit log — append-only, never delete rows from this table
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id          TEXT        PRIMARY KEY,
                resource_id TEXT        NOT NULL,
                action      TEXT        NOT NULL,
                actor       TEXT        NOT NULL,
                detail      TEXT,
                occurred_at TIMESTAMP   NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()
