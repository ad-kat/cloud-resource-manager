"""
Pydantic models define the shape of data coming IN (requests) and going OUT
(responses).  FastAPI uses these automatically to:
  - Validate request bodies (wrong type → 422 error with a clear message)
  - Serialise response objects to JSON
  - Generate the /docs OpenAPI schema

Think of these as the Java POJOs / DTOs you may have used at your internship,
but with runtime validation baked in for free.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
import json

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enumerations — restrict field values to a known set
# ---------------------------------------------------------------------------

class ResourceType(str, Enum):
    """The kinds of cloud resources we can manage."""
    VM       = "vm"
    BUCKET   = "bucket"
    FUNCTION = "function"


class ResourceStatus(str, Enum):
    """Lifecycle states a resource can be in."""
    ACTIVE        = "active"
    STOPPED       = "stopped"
    DEPROVISIONED = "deprovisioned"


# ---------------------------------------------------------------------------
# Request models  (what the client sends to us)
# ---------------------------------------------------------------------------

class ResourceCreate(BaseModel):
    """
    Body expected when POST /resources is called to provision a new resource.

    Field() lets us attach metadata: description shows up in /docs, and
    examples make the Swagger UI pre-fill sensible values.
    """
    name: str = Field(
        ...,                        # '...' means the field is required
        min_length=1,
        max_length=100,
        description="Human-readable name for the resource",
        examples=["my-web-server"],
    )
    type: ResourceType = Field(
        ...,
        description="Kind of cloud resource: vm | bucket | function",
    )
    owner: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Owner identifier (email, username, team name)",
        examples=["alice@example.com"],
    )
    policy_tags: dict = Field(
        default_factory=dict,
        description="Arbitrary key-value governance tags (env, cost-centre, etc.)",
        examples=[{"env": "prod", "cost-centre": "eng-platform"}],
    )
    region: str = Field(
        default="us-east-1",
        description="Simulated deployment region",
        examples=["us-east-1"],
    )


class PolicyUpdate(BaseModel):
    """
    Body expected when PATCH /resources/{id}/policy is called.

    We only allow updating policy_tags and/or status — nothing else.
    All fields are Optional so the client can send just the ones they want
    to change (partial update pattern).
    """
    policy_tags: Optional[dict] = Field(
        default=None,
        description="Replace policy tags with this new set",
        examples=[{"env": "staging", "reviewed": "true"}],
    )
    status: Optional[ResourceStatus] = Field(
        default=None,
        description="Transition the resource to a new lifecycle state",
    )


# ---------------------------------------------------------------------------
# Response model  (what we send back to the client)
# ---------------------------------------------------------------------------

class ResourceResponse(BaseModel):
    """
    The full resource object returned by every endpoint.

    orm_mode (renamed model_config in Pydantic v2) lets FastAPI convert a
    sqlite3.Row (or any object) directly into this model without us manually
    building a dict first.
    """
    id:          str
    name:        str
    type:        ResourceType
    status:      ResourceStatus
    owner:       str
    policy_tags: dict
    region:      str
    created_at:  datetime
    updated_at:  datetime

    # Pydantic v2 config — allow constructing from ORM/dict-like objects
    model_config = {"from_attributes": True}

    @field_validator("policy_tags", mode="before")
    @classmethod
    def parse_policy_tags(cls, v):
        """
        SQLite stores policy_tags as a JSON string.
        This validator transparently deserialises it back to a dict
        so the rest of the code never has to think about it.
        """
        if isinstance(v, str):
            return json.loads(v)
        return v


# ---------------------------------------------------------------------------
# Generic response wrappers
# ---------------------------------------------------------------------------

class MessageResponse(BaseModel):
    """Used for simple confirmations (e.g. successful delete)."""
    message: str
    resource_id: str
