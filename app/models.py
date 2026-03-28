from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
import json

from pydantic import BaseModel, Field, field_validator


class ResourceType(str, Enum):
    VM       = "vm"
    BUCKET   = "bucket"
    FUNCTION = "function"
    DATABASE = "database"   # added after realising functions need persistent state too


class ResourceStatus(str, Enum):
    PROVISIONING  = "provisioning"   # async gap between request and ready
    ACTIVE        = "active"
    STOPPED       = "stopped"
    DEPROVISIONED = "deprovisioned"


class ResourceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, examples=["my-web-server"])
    type: ResourceType
    owner: str = Field(..., min_length=1, max_length=100, examples=["alice@example.com"])
    policy_tags: dict = Field(
        default_factory=dict,
        description="Governance tags — 'env' and 'cost-centre' are required by policy",
        examples=[{"env": "prod", "cost-centre": "eng-platform"}],
    )
    region: str = Field(default="us-east-1", examples=["us-east-1"])


class PolicyUpdate(BaseModel):
    # Partial update — only send the fields you want to change
    policy_tags: Optional[dict] = Field(default=None)
    status: Optional[ResourceStatus] = Field(default=None)


class ResourceResponse(BaseModel):
    id:          str
    name:        str
    type:        ResourceType
    status:      ResourceStatus
    owner:       str
    policy_tags: dict
    region:      str
    created_at:  datetime
    updated_at:  datetime

    model_config = {"from_attributes": True}

    @field_validator("policy_tags", mode="before")
    @classmethod
    def parse_policy_tags(cls, v):
        # SQLite returns JSON strings; deserialise transparently
        if isinstance(v, str):
            return json.loads(v)
        return v


class MessageResponse(BaseModel):
    message: str
    resource_id: str
