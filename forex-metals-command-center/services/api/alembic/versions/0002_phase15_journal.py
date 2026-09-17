"""Phase 15 journal: journal_entries, journal_outcomes

Revision ID: 0002_phase15
Revises: 0001_phase0
Create Date: 2026-09-14
"""

import sqlalchemy as sa

from alembic import op

revision = "0002_phase15"
down_revision = "0001_phase0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "journal_entries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("record", sa.JSON(), nullable=False),
        sa.Column("record_hash", sa.String(64), nullable=False),
        sa.Column("strategy_version", sa.String(32), nullable=False),
    )
    op.create_index("ix_journal_entries_created_at", "journal_entries", ["created_at"])
    op.create_index("ix_journal_entries_symbol", "journal_entries", ["symbol"])
    op.create_table(
        "journal_outcomes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "entry_id", sa.String(36), sa.ForeignKey("journal_entries.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("record", sa.JSON(), nullable=False),
        sa.Column("record_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("entry_id", "revision", name="uq_journal_outcome_revision"),
    )
    op.create_index("ix_journal_outcomes_entry_id", "journal_outcomes", ["entry_id"])


def downgrade() -> None:
    op.drop_index("ix_journal_outcomes_entry_id", table_name="journal_outcomes")
    op.drop_table("journal_outcomes")
    op.drop_index("ix_journal_entries_symbol", table_name="journal_entries")
    op.drop_index("ix_journal_entries_created_at", table_name="journal_entries")
    op.drop_table("journal_entries")
