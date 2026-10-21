"""add immutability triggers

Revision ID: 54226fc33396
Revises: 6facce5a2e5b
Create Date: 2026-10-21 09:43:26.182287

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.db.immutability import (
    CREATE_FUNCTIONS,
    CREATE_TRIGGERS,
    DROP_TRIGGERS,
)



# revision identifiers, used by Alembic.
revision: str = '54226fc33396'
down_revision: Union[str, Sequence[str], None] = '6facce5a2e5b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(CREATE_FUNCTIONS)
    op.execute(CREATE_TRIGGERS)


def downgrade() -> None:
    op.execute(DROP_TRIGGERS)