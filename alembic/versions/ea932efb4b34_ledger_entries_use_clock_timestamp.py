"""ledger entries use clock_timestamp

Revision ID: ea932efb4b34
Revises: 1e6a2d5fd1ef
Create Date: 2026-10-20 05:26:01.603816

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ea932efb4b34'
down_revision: Union[str, Sequence[str], None] = '1e6a2d5fd1ef'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "ledger_entries", "created_at", server_default=sa.text("clock_timestamp()")
    )


def downgrade() -> None:
    op.alter_column("ledger_entries", "created_at", server_default=sa.text("now()"))