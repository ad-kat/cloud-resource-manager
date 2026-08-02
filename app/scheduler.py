"""
Policy enforcement engine — background scheduler that reconciles desired
vs actual resource state on a fixed interval.

Rules currently implemented:
  1. TTL breach       — resource has been active past its ttl_hours value → auto-stop
  2. Stuck provision  — resource stuck in 'provisioning' for > 10 min → flag it

This is the same pattern used by AWS Config rules and GCP Asset Inventory
policies, just without the managed service layer on top.
"""

import logging
import uuid
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import text

from app.database import SessionLocal

logger = logging.getLogger(__name__)

# anything stuck in provisioning longer than this is almost certainly broken
STUCK_PROVISIONING_MINUTES = 10


def _enforce_policies():
    db      = SessionLocal()
    flagged = 0
    now     = datetime.now(timezone.utc)

    try:
        rows = db.execute(
            text("""
                SELECT id, name, status, ttl_hours, created_at
                FROM resources
                WHERE status NOT IN ('deprovisioned')
            """)
        ).fetchall()

        for row in rows:
            r           = dict(row._mapping)
            resource_id = r["id"]

            created = r["created_at"]
            if isinstance(created, str):
                created = datetime.fromisoformat(created.replace(" ", "T"))
            if created.tzinfo is None:
                # sqlite gives naive datetimes — stamp them UTC
                created = created.replace(tzinfo=timezone.utc)

            age_hours = (now - created).total_seconds() / 3600

            # --- Rule 1: TTL breach ---
            # only enforce on active resources — stopped ones already aren't costing money
            if r["ttl_hours"] is not None and r["status"] == "active":
                if age_hours > r["ttl_hours"]:
                    db.execute(
                        text("""
                            UPDATE resources
                            SET status = 'stopped', updated_at = :now
                            WHERE id = :id AND status = 'active'
                        """),
                        {"now": now, "id": resource_id}
                    )
                    db.execute(
                        text("""
                            INSERT INTO events
                                (id, resource_id, action, actor, detail, occurred_at)
                            VALUES (:id, :rid, 'ttl_enforced', 'scheduler', :detail, :now)
                        """),
                        {
                            "id":     str(uuid.uuid4()),
                            "rid":    resource_id,
                            "detail": f"TTL of {r['ttl_hours']}h exceeded (age={round(age_hours, 1)}h). Auto-stopped.",
                            "now":    now,
                        }
                    )
                    logger.info(
                        "TTL enforced on %s (age=%.1fh ttl=%dh)",
                        resource_id, age_hours, r["ttl_hours"]
                    )
                    flagged += 1

            # --- Rule 2: Stuck in provisioning ---
            # a resource should never stay in 'provisioning' for more than a few minutes
            if r["status"] == "provisioning":
                age_minutes = age_hours * 60
                if age_minutes > STUCK_PROVISIONING_MINUTES:
                    # just log the event — don't auto-kill it, might need manual investigation
                    db.execute(
                        text("""
                            INSERT INTO events
                                (id, resource_id, action, actor, detail, occurred_at)
                            VALUES (:id, :rid, 'drift_detected', 'scheduler', :detail, :now)
                        """),
                        {
                            "id":     str(uuid.uuid4()),
                            "rid":    resource_id,
                            "detail": f"Stuck in provisioning for {round(age_minutes, 1)} minutes.",
                            "now":    now,
                        }
                    )
                    logger.warning(
                        "Drift detected: %s stuck in provisioning (%.1f min)",
                        resource_id, age_minutes
                    )
                    flagged += 1

        db.commit()

    except Exception:
        logger.exception("Policy enforcement run failed")
        db.rollback()
    finally:
        db.close()

    if flagged:
        logger.info("Policy enforcement complete — %d resource(s) flagged/updated.", flagged)


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    # every 5 minutes feels right — short enough to catch TTL breaches quickly,
    # long enough to not hammer the DB
    scheduler.add_job(_enforce_policies, "interval", minutes=5, id="policy_enforcement")
    scheduler.start()
    logger.info("Policy enforcement scheduler started (interval=5min).")
    return scheduler
