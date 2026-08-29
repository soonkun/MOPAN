import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, StringConstraints, field_validator

# strip_whitespace is what makes uq_collections_name mean something: without it
# "일반 " and "일반" are two different rows the admin cannot tell apart.
CollectionName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]


class CollectionCreate(BaseModel):
    name: CollectionName
    description: str | None = None


class CollectionUpdate(BaseModel):
    """PATCH body. The router dumps this with `exclude_unset=True`, so an omitted
    field means "leave it alone" and an explicit null means "clear it" - which is
    the only way to empty a description."""

    name: CollectionName | None = None
    description: str | None = None

    @field_validator("name")
    @classmethod
    def _reject_explicit_null(cls, value: str | None) -> str:
        # Validators do not run on defaults, so an ABSENT name never arrives here.
        # An explicit null does, and collections.name is NOT NULL - left alone it
        # reaches the database as an IntegrityError, which the router reports as
        # the duplicate-name 409 and so names the wrong cause.
        if value is None:
            raise ValueError("name must not be null")
        return value


class CollectionResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
