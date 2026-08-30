import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class McpServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=1, max_length=1000)
    # HTTP only. There is no "stdio" here and there is not meant to be: it would
    # mean this container spawning binaries an admin named through a web form.
    auth_kind: Literal["none", "bearer"] = "none"
    # WRITE-ONLY. It appears in this model and in McpServerUpdate and nowhere
    # else - no response model carries it, which is what makes "never returned"
    # a property of the types rather than of whoever writes the next endpoint.
    auth_token: str | None = Field(default=None, max_length=4000)

    @field_validator("name", "base_url")
    @classmethod
    def _stripped(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("값을 입력해 주세요.")
        return stripped


class McpServerUpdate(BaseModel):
    """PATCH semantics: an OMITTED field is left alone.

    There is no way to blank the token by sending an empty string, and that is
    deliberate - `auth_kind="none"` is the one way to clear it, so "I did not
    send the token because I do not know it" can never be read as "delete it".
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = Field(default=None, min_length=1, max_length=1000)
    auth_kind: Literal["none", "bearer"] | None = None
    auth_token: str | None = Field(default=None, max_length=4000)
    enabled: bool | None = None

    @field_validator("name", "base_url")
    @classmethod
    def _stripped(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("값을 입력해 주세요.")
        return stripped


class McpToolUpdate(BaseModel):
    risk_level: Literal["read", "write", "destructive"] | None = None
    enabled: bool | None = None


class McpToolResponse(BaseModel):
    id: uuid.UUID
    server_id: uuid.UUID
    name: str
    description: str | None
    input_schema: dict
    risk_level: str
    # False on a tool the server stopped listing (a tombstone) as well as on one
    # an admin turned off. The screen tells them apart by `discovered_at`.
    enabled: bool
    discovered_at: datetime

    model_config = {"from_attributes": True}


class McpServerResponse(BaseModel):
    """Note what is NOT here: `auth_token`. The list endpoint reports whether one
    is set, never the value."""

    id: uuid.UUID
    name: str
    base_url: str
    auth_kind: str
    has_auth_token: bool
    enabled: bool
    created_by_email: str | None
    created_at: datetime
    updated_at: datetime
    tools: list[McpToolResponse] = []
    # Set when registration or a re-discovery could not reach the server. The row
    # still exists - an admin who mistyped a URL should be able to fix it rather
    # than register it again from scratch - so this is how the screen says the
    # tool list is empty because the call failed, not because the server has no
    # tools.
    discovery_error: str | None = None


class McpToolOption(BaseModel):
    """GET /api/mcp/tools - what the composer's tool picker lists.

    Deliberately narrower than McpToolResponse: no `base_url`, nothing about the
    server beyond its name. Any authenticated user may read it, the way
    GET /api/models is readable, because it discloses only what a user would
    learn by picking a tool and being answered.
    """

    id: uuid.UUID
    server_name: str
    name: str
    description: str | None
    input_schema: dict
    risk_level: str
