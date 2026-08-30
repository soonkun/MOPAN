from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

# No strip_whitespace: an admin's own leading blank line or trailing newline is
# their formatting and goes to the model as written. The emptiness check is NOT
# here - a Pydantic failure is a 422 and the requirement is a Korean 400 - it is
# in the router, where the message can be written for the person who typed it.
# The ceiling exists so a paste accident cannot store megabytes into a column
# that is read on every answer; 20k characters is far past any usable system
# prompt (the shipped one is ~1.5k). The ceiling that actually binds is the
# router's token check against MANDATORY_TOKEN_ALLOWANCE, which is the unit the
# budget is made of; this one is the crash barrier behind it.
PromptText = Annotated[str, StringConstraints(max_length=20000)]


class PromptVersionCreate(BaseModel):
    """Body of POST /api/prompts/{name}/versions. An edit is an INSERT: there is
    no field here for a version number, because the server assigns it."""

    text: PromptText


class PromptCreate(BaseModel):
    """Body of POST /api/prompts - a NEW prompt name, at version 1.

    Slice 4's agents are what made this necessary. An agent picks a prompt from
    the store, and until now the store had exactly the two names the migrations
    seeded and no way to add a third: an agent could only ever answer with the
    deployment's own system prompt, which is the field the whole feature is
    about. `POST /api/prompts/{name}/versions` deliberately 404s on an unknown
    name - a typo must not silently fork the answer prompt - so creating one is
    its own endpoint rather than a relaxation of that rule.

    The name is a KEY, not a label: `messages.prompt_name` records it, the agents
    table references it, and `get_prompt` looks it up. So it is constrained to
    the shape the two built-in names already have rather than left free-form;
    the human-readable part is the agent's own name.
    """

    name: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]*$")
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
    # What this template costs in cl100k tokens, and the ceiling a save is
    # refused above. Both are on the response rather than in the client, because
    # the browser cannot count cl100k tokens and the ceiling is a backend
    # constant (app/chat/prompt.py:MANDATORY_TOKEN_ALLOWANCE) that a hard-coded
    # copy in the TSX would silently outlive.
    tokens: int
    token_limit: int
