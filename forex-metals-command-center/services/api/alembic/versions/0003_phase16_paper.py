"""Phase 16 paper trading: paper_sims, paper_events

Revision ID: 0003_phase16
Revises: 0002_phase15
Create Date: 2026-09-14
"""

import sqlalchemy as sa

from alembic import op

revision = "0003_phase16"
down_revision = "0002_phase15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paper_sims",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("record", sa.JSON(), nullable=False),
        sa.Column("record_hash", sa.String(64), nullable=False),
        sa.Column("strategy_version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("progress", sa.JSON(), nullable=False),
    )
    op.create_index("ix_paper_sims_created_at", "paper_sims", ["created_at"])
    op.create_index("ix_paper_sims_symbol", "paper_sims", ["symbol"])
    op.create_index("ix_paper_sims_status", "paper_sims", ["status"])
    op.create_table(
        "paper_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "sim_id", sa.String(36), sa.ForeignKey("paper_sims.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("record", sa.JSON(), nullable=False),
        sa.Column("record_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("sim_id", "seq", name="uq_paper_event_seq"),
    )
    op.create_index("ix_paper_events_sim_id", "paper_events", ["sim_id"])


def downgrade() -> None:
    op.drop_index("ix_paper_events_sim_id", table_name="paper_events")
    op.drop_table("paper_events")
    op.drop_index("ix_paper_sims_status", table_name="paper_sims")
    op.drop_index("ix_paper_sims_symbol", table_name="paper_sims")
    op.drop_index("ix_paper_sims_created_at", table_name="paper_sims")
    op.drop_table("paper_sims")
