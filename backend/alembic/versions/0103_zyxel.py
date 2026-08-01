"""Zyxel 防火牆整合（Beta，實驗性）：實例 / 政策 / 位址物件三張表

Standalone ZLD 機種沒有 REST API，走 SSH CLI（見 app/services/zyxel.py 開頭說明）。
無實機可驗，欄位長度採保守值。

Revision ID: 0103_zyxel
Revises: 0102_librenms_links
Create Date: 2026-08-01
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0103_zyxel"
down_revision: str | None = "0102_librenms_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "zyxel_firewalls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False, server_default="22"),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("password_enc", sa.LargeBinary(), nullable=False),
        sa.Column("password_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sync_interval_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("sync_arp", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sync_dhcp", sa.Boolean(), nullable=False, server_default=sa.false()),
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
        "zyxel_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("firewall_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("zyxel_firewalls.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("rule_number", sa.String(32), nullable=False),
        sa.Column("name", sa.String(255)),
        sa.Column("status", sa.String(16)),
        sa.Column("action", sa.String(16)),
        sa.Column("from_zone", sa.String(64)),
        sa.Column("to_zone", sa.String(64)),
        sa.Column("source", sa.Text()),
        sa.Column("destination", sa.Text()),
        sa.Column("service", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("raw", postgresql.JSONB()),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("firewall_id", "rule_number", name="zyxel_policy_unique"),
    )

    op.create_table(
        "zyxel_address_objects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("firewall_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("zyxel_firewalls.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("obj_type", sa.String(32)),
        sa.Column("value", sa.Text()),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("firewall_id", "name", name="zyxel_addr_unique"),
    )


def downgrade() -> None:
    op.execute("DELETE FROM nat_translations WHERE source_origin LIKE 'zyxel:%'")
    op.drop_table("zyxel_address_objects")
    op.drop_table("zyxel_policies")
    op.drop_table("zyxel_firewalls")
