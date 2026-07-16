"""users.is_ops_admin — 運維管理員權限欄位

新增 is_ops_admin 布林欄位，允許使用者存取大部分管理功能（掃描代理、憑證代理、
LibreNMS、DNS、VLAN/VRF、NAT、防火牆、虛擬化等），但不含使用者管理、
系統設定、通知發送設定、版本資訊、系統紀錄等最高管理員專屬功能。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0097_user_is_ops_admin"
down_revision: str | None = "0096_device_port_name_len"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_ops_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "is_ops_admin")
