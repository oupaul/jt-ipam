"""Palo Alto (PAN-OS) 整合（Beta，實驗性）：實例 / 政策 / 位址物件三張表

Revision ID: 0104_paloalto
Revises: 0103_zyxel
Create Date: 2026-08-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0104_paloalto"
down_revision: str | None = "0103_zyxel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paloalto_firewalls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("api_url", sa.Text(), nullable=False),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("password_enc", sa.LargeBinary(), nullable=False),
        sa.Column("password_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("vsys", sa.String(64), nullable=False, server_default="vsys1"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("verify_tls", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sync_interval_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("sync_arp", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sync_policies", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sync_nat", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sync_addresses", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("scope_subnet_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True))),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    op.create_table(
        "paloalto_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("firewall_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("paloalto_firewalls.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("vsys", sa.String(64), nullable=False, server_default="vsys1"),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("action", sa.String(16)),
        sa.Column("disabled", sa.Boolean()),
        sa.Column("from_zone", sa.Text()),
        sa.Column("to_zone", sa.Text()),
        sa.Column("source", sa.Text()),
        sa.Column("destination", sa.Text()),
        sa.Column("application", sa.Text()),
        sa.Column("service", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("raw", postgresql.JSONB()),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("firewall_id", "vsys", "name", name="paloalto_policy_unique"),
    )

    op.create_table(
        "paloalto_address_objects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("firewall_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("paloalto_firewalls.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("vsys", sa.String(64), nullable=False, server_default="vsys1"),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("obj_type", sa.String(32)),
        sa.Column("value", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("firewall_id", "vsys", "name", name="paloalto_addr_unique"),
    )


def downgrade() -> None:
    op.execute("DELETE FROM nat_translations WHERE source_origin LIKE 'paloalto:%'")
    op.drop_table("paloalto_address_objects")
    op.drop_table("paloalto_policies")
    op.drop_table("paloalto_firewalls")
