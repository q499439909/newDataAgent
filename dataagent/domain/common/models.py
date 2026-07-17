from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


class DomainError(ValueError):
    """Raised when a domain invariant is violated."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VersionedModel(DomainModel):
    id: str
    version: int = Field(ge=1)
    parent_version_id: str | None = None
    created_by: str
    change_reason: str
    created_at: datetime = Field(default_factory=utc_now)
    immutable: bool = True
