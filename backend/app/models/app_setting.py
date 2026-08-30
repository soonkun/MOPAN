from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AppSetting(Base):
    """A runtime override for ONE `.env` value, keyed by its environment-variable
    name. Absent means "use the value the process booted with", so an EMPTY TABLE
    behaves exactly as the deployment did before this table existed - the same
    fallback rule `get_prompt` follows for the answer template.

    `value` is text for every key, parsed against
    `app/core/settings_store.py:RUNTIME_SAFE_SETTINGS`. A typed column per kind
    would be three nullable columns plus a discriminator to say which one is
    real; the spec table already knows each key's type and has to parse a form
    field anyway.

    No `updated_by` column. It would be the fourth foreign key in the schema and
    the first one with no good `ON DELETE`: CASCADE would silently revert an
    override because the admin who set it left the company, and RESTRICT would
    make that admin undeletable. Who changed what is in the
    `app_setting_changed` log line instead.
    """

    __tablename__ = "app_settings"

    # The env var name, e.g. RETRIEVAL_TOP_N. A natural key, so there is no
    # surrogate id and no second uniqueness rule to keep in step with it.
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
