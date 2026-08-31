import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.rag.chunking import resolve

# strip_whitespace is what makes uq_collections_name mean something: without it
# "일반 " and "일반" are two different rows the admin cannot tell apart.
CollectionName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]


def _validated_chunking(value: dict | None) -> dict:
    """`{}` means "chunk my documents as prose", which is every collection until
    somebody says otherwise.

    Validated HERE, by the same `resolve` the worker calls, so that an
    uncompilable pattern or a misspelt preset is a 422 at the moment it is saved
    rather than a failed document at the next upload - and so that there is one
    reader of this shape rather than two that can drift.
    """
    if not value:
        return {}
    resolve(value)
    return value


class CollectionCreate(BaseModel):
    name: CollectionName
    description: str | None = None
    # e.g. {"strategy": "classification_table", "preset": "korean_ip_classification"}
    chunking: dict | None = Field(default_factory=dict)

    _check_chunking = field_validator("chunking")(staticmethod(_validated_chunking))


class CollectionUpdate(BaseModel):
    """PATCH body. The router dumps this with `exclude_unset=True`, so an omitted
    field means "leave it alone" and an explicit null means "clear it" - which is
    the only way to empty a description."""

    name: CollectionName | None = None
    description: str | None = None
    # An explicit null clears it back to "chunk as prose", the same way a null
    # description empties the description; collections.chunking is NOT NULL, so
    # the null is turned into {} here rather than reaching the database.
    chunking: dict | None = None

    _check_chunking = field_validator("chunking")(staticmethod(_validated_chunking))

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
    chunking: dict
    created_at: datetime

    model_config = {"from_attributes": True}
