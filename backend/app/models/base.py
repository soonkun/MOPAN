from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Named constraints from day one: Alembic cannot reliably reference
# Postgres-generated names like `chunks_document_id_fkey` in a later
# op.drop_constraint / alter_column.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    # No prefix token: every CheckConstraint here is already named ck_<table>_<what>,
    # and a convention containing %(constraint_name)s re-applies itself, so the name
    # in Postgres would be ck_users_ck_users_role_valid.
    "ck": "%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
