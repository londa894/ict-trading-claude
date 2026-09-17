"""Phase 0 foundation: instruments, candles, data_quality_events

Revision ID: 0001_phase0
Revises:
Create Date: 2026-09-13
"""

import sqlalchemy as sa

from alembic import op

revision = "0001_phase0"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "instruments",
        sa.Column("symbol", sa.String(16), primary_key=True),
        sa.Column("asset_class", sa.String(16), nullable=False),
        sa.Column("base", sa.String(8), nullable=False),
        sa.Column("quote", sa.String(8), nullable=False),
        sa.Column("price_precision", sa.Integer(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("deeply_validated", sa.Boolean(), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=True),
    )
    op.create_table(
        "candles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(16), sa.ForeignKey("instruments.symbol"), nullable=False),
        sa.Column("timeframe", sa.String(4), nullable=False),
        sa.Column("open_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("close_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("is_closed", sa.Boolean(), nullable=False),
        sa.Column("data_quality", sa.String(16), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("symbol", "timeframe", "open_time", "source", name="uq_candle_identity"),
    )
    op.create_index("ix_candles_symbol", "candles", ["symbol"])
    op.create_index("ix_candles_open_time", "candles", ["open_time"])
    op.create_table(
        "data_quality_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("timeframe", sa.String(4), nullable=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(8), nullable=False),
        sa.Column("message", sa.String(512), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_data_quality_events_symbol", "data_quality_events", ["symbol"])


def downgrade() -> None:
    op.drop_index("ix_data_quality_events_symbol", table_name="data_quality_events")
    op.drop_table("data_quality_events")
    op.drop_index("ix_candles_open_time", table_name="candles")
    op.drop_index("ix_candles_symbol", table_name="candles")
    op.drop_table("candles")
    op.drop_table("instruments")
