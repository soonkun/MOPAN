from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, StringConstraints

# No strip_whitespace: an admin's own leading blank line or trailing newline is
# their formatting and goes to the model as written. The emptiness check is NOT
# here - a Pydantic failure is a 422 and the requirement is a Korean 400 - it is
# in the router, where the message can be written for the person who typed it.
# The ceiling exists so a paste accident cannot store megabytes into a column
# that is read on every answer; 20k characters is far past any usable system
# prompt (the shipped one is ~1.1k) and well under the token budget.
PromptText = Annotated[str, StringConstraints(max_length=20000)]


class PromptVersionCreate(BaseModel):
    """Body of POST /api/prompts/{name}/versions. An edit is an INSERT: there is
    no field here for a version number, because the server assigns it."""

    text: PromptText


class PromptVersionResponse(BaseModel):
    id: str
    version: str
    text: str
    is_active: bool
    # NULL for the row migration 0004 seeded, which predates every user account.
    # The screen renders that as 시스템.
    created_by_email: str | None
    created_at: datetime


class PromptResponse(BaseModel):
    """One row per prompt NAME, carrying the text that is live right now.

    `text` is the active version's - the preview the admin edits and the exact
    string get_prompt hands the model on the next question."""

    name: str
    version: str
    text: str
    version_count: int
    updated_at: datetime
