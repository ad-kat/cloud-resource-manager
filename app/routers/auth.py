"""
auth.py — API key management

Yes, we're rolling our own auth. No, it's not OAuth2 with PKCE and
a dance routine. It's API keys. Finance teams use curl. This is fine.

Keys are stored hashed in the db so if someone dumps the table they
still can't use your keys. Tiny win but still a win.
"""

import hashlib
import logging
import os
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()

# fallback master key from env — useful for bootstrapping and tests
# in prod you'd rotate this and never commit it. you know the drill.
MASTER_KEY = os.getenv("MASTER_API_KEY", "dev-master-key-change-me-please")


def _hash_key(raw: str) -> str:
    # sha256 is fine for api keys — they're random enough that rainbow
    # tables are useless. bcrypt would be overkill here.
    return hashlib.sha256(raw.encode()).hexdigest()


def verify_api_key(x_api_key: str = Header(..., description="Your API key")):
    """
    FastAPI dependency — slap this on any endpoint you want protected.
    Usage: def my_endpoint(db: Session = Depends(get_db), _=Depends(verify_api_key))

    Returns nothing useful, raises 401 if the key is bad.
    That's the whole job.
    """
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header. Did you forget something?",
        )

    # master key check — bypasses db lookup, useful for admin ops
    if x_api_key == MASTER_KEY:
        return {"key_id": "master", "name": "master"}

    # otherwise look it up in the db
    # we hash before lookup so the raw key never touches the db
    hashed = _hash_key(x_api_key)

    # we don't have access to db here directly (it's a dependency param),
    # so we do a quick engine-level lookup. a bit ugly but works fine.
    from app.database import engine
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM api_keys WHERE key_hash = :h AND is_active = true"),
            {"h": hashed}
        ).fetchone()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API key.",
        )

    return dict(row._mapping)


@router.post("/keys", status_code=201, summary="Generate a new API key")
def create_api_key(name: str, db: Session = Depends(get_db)):
    """
    Creates a new API key. Returns the raw key ONCE — we don't store it,
    only the hash. So copy it now or forever hold your peace.
    """
    raw_key   = f"crm_{secrets.token_urlsafe(32)}"
    key_hash  = _hash_key(raw_key)
    key_id    = str(uuid.uuid4())
    now       = datetime.now(timezone.utc)

    db.execute(
        text("""
            INSERT INTO api_keys (id, name, key_hash, is_active, created_at)
            VALUES (:id, :name, :hash, true, :now)
        """),
        {"id": key_id, "name": name, "hash": key_hash, "now": now}
    )
    db.commit()

    logger.info("Created API key '%s' (id=%s)", name, key_id)

    return {
        "key_id":  key_id,
        "name":    name,
        "api_key": raw_key,   # only time this is ever shown — store it somewhere safe
        "warning": "This is the only time the raw key is shown. Save it.",
    }


@router.get("/keys", summary="List API keys (names only, no raw keys obviously)")
def list_api_keys(db: Session = Depends(get_db)):
    rows = db.execute(
        text("SELECT id, name, is_active, created_at FROM api_keys ORDER BY created_at DESC")
    ).fetchall()
    return {"keys": [dict(r._mapping) for r in rows]}


@router.delete("/keys/{key_id}", summary="Revoke an API key")
def revoke_api_key(key_id: str, db: Session = Depends(get_db)):
    result = db.execute(
        text("UPDATE api_keys SET is_active = false WHERE id = :id"),
        {"id": key_id}
    )
    db.commit()

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail=f"Key '{key_id}' not found.")

    return {"message": f"Key '{key_id}' revoked.", "key_id": key_id}
