"""Palo Alto（PAN-OS）整合（Beta）：實例 / 安全政策 / 位址物件三張表。

比照 FortiGate 的形狀，差別在 PAN-OS 的規則沒有數字 id（名稱就是識別），
另外多存一個 `api_version` —— REST URI 裡的版本段綁 PAN-OS 版本，寫死會在別的版本
整批失敗，留空時由 `show system info` 推導。

這個 revision 取代了本地在 0104_paloalto 建的舊版三張表（帳密 + 單一 vsys 欄位）；
upgrade 先整批砍掉舊表再照新 schema 建，不做欄位對欄位遷移 —— 認證模式從「存帳密、
每次同步換發一次性 key」改成「由管理員在 PAN-OS 上手動產生 API key、我們只存 key」，
兩邊本來就對不上，且此整合仍是 Beta，資料重新同步即可補回。

Revision ID: 0135_paloalto
Revises: 0134_cert_expiry_warn_days
Create Date: 2026-09-01
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0135_paloalto"
down_revision: str | None = "0134_cert_expiry_warn_days"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 砍掉 0104_paloalto 建的舊 schema（帳密欄位、單一 vsys）——
    # 新 schema 換成 API key 認證 + 多 vsys，欄位對不上，直接整批換掉。
    op.execute("DELETE FROM nat_translations WHERE source_origin LIKE 'paloalto:%'")
    op.drop_table("paloalto_address_objects")
    op.drop_table("paloalto_policies")
    op.drop_table("paloalto_firewalls")

    op.create_table(
        "paloalto_firewalls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("api_url", sa.Text(), nullable=False),
        sa.Column("api_key_enc", sa.LargeBinary(), nullable=False),
        sa.Column("api_key_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("verify_tls", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("api_version", sa.String(16)),
        sa.Column("vsys_list", postgresql.ARRAY(sa.String(64))),
        sa.Column("sync_interval_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("sync_dhcp", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sync_arp", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sync_policies", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sync_nat", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sync_addresses", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("scope_subnet_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True))),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )

    op.create_table(
        "paloalto_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("firewall_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("paloalto_firewalls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vsys", sa.String(64), nullable=False, server_default="vsys1"),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("position", sa.Integer()),
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
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("firewall_id", "vsys", "name", name="paloalto_policy_unique"),
    )
    op.create_index("ix_paloalto_policies_firewall_id", "paloalto_policies", ["firewall_id"])

    op.create_table(
        "paloalto_address_objects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("firewall_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("paloalto_firewalls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vsys", sa.String(64), nullable=False, server_default="vsys1"),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("obj_type", sa.String(32)),
        sa.Column("kind", sa.String(16), nullable=False, server_default="address"),
        sa.Column("value", sa.Text()),
        sa.Column("members", postgresql.JSONB()),
        sa.Column("description", sa.Text()),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("firewall_id", "vsys", "name", "kind", name="paloalto_addr_unique"),
    )
    op.create_index("ix_paloalto_address_objects_firewall_id",
                    "paloalto_address_objects", ["firewall_id"])


def downgrade() -> None:
    op.drop_index("ix_paloalto_address_objects_firewall_id",
                  table_name="paloalto_address_objects")
    op.drop_table("paloalto_address_objects")
    op.drop_index("ix_paloalto_policies_firewall_id", table_name="paloalto_policies")
    op.drop_table("paloalto_policies")
    op.drop_table("paloalto_firewalls")
