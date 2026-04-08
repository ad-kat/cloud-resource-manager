import logging
import os
import threading

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

logger = logging.getLogger(__name__)

# pull DSN from env, fall back to a local default so plain `uvicorn app.main:app`
# still works without docker if you have postgres running locally
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/resources"
)

# pool_pre_ping drops connections that went stale while sitting idle —
# without this you get random "server closed the connection" errors after
# the container has been idle for a while
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    with engine.connect() as conn:
        # main resource table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS resources (
                id            TEXT          PRIMARY KEY,
                name          TEXT          NOT NULL,
                type          TEXT          NOT NULL,
                status        TEXT          NOT NULL DEFAULT 'active',
                owner         TEXT          NOT NULL,
                policy_tags   TEXT          NOT NULL DEFAULT '{}',
                region        TEXT          NOT NULL DEFAULT 'us-east-1',
                cost_per_hr   NUMERIC(10,4) NOT NULL DEFAULT 0.0,
                ttl_hours     INTEGER,
                created_at    TIMESTAMPTZ   NOT NULL,
                updated_at    TIMESTAMPTZ   NOT NULL
            )
        """))

        # append-only audit log — rows here are never deleted, only inserted
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS events (
                id          TEXT        PRIMARY KEY,
                resource_id TEXT        NOT NULL,
                action      TEXT        NOT NULL,
                actor       TEXT        NOT NULL,
                detail      TEXT,
                occurred_at TIMESTAMPTZ NOT NULL
            )
        """))

        # budget config per cost-centre — upserted on conflict so you can
        # update a limit without worrying about duplication
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS budget_alerts (
                id              TEXT          PRIMARY KEY,
                cost_centre     TEXT          NOT NULL UNIQUE,
                monthly_limit   NUMERIC(12,2) NOT NULL,
                alert_threshold NUMERIC(5,2)  NOT NULL DEFAULT 0.80,
                created_at      TIMESTAMPTZ   NOT NULL,
                updated_at      TIMESTAMPTZ   NOT NULL
            )
        """))

        # api keys — we store hashes only, never the raw key
        # is_active lets us revoke without deleting (audit trail and all that)
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id          TEXT        PRIMARY KEY,
                name        TEXT        NOT NULL,
                key_hash    TEXT        NOT NULL UNIQUE,
                is_active   BOOLEAN     NOT NULL DEFAULT true,
                created_at  TIMESTAMPTZ NOT NULL
            )
        """))

        conn.commit()
    logger.info("Database tables ready.")

    # kick off a background pricing refresh so rates are warm before first request
    # doing it in a thread so we don't block startup — fallback rates cover the gap
    from app.pricing import refresh_rates
    t = threading.Thread(target=refresh_rates, daemon=True)
    t.start()
    logger.info("AWS pricing refresh started in background.")
