"""Palo Alto Networks（PAN-OS）防火牆整合 model（Beta，實驗性）。

跟 FortiGate 一樣有官方 REST API（設定物件：`/restapi/<ver>/Objects`、
`/restapi/<ver>/Policies`，JSON）+ 舊式 XML op API（唯讀操作性資料，如
`show arp all`），不像 Zyxel 得靠 SSH CLI 螢幕截字。認證方式：帳密呼叫
`type=keygen` 換一次性 API key（不像 FortiGate 是使用者自己在 GUI 產生
長效 token），每次同步重新換發，不落地保存 key 本身，只保存帳密（AES-GCM
加密，aad 綁實例 id）。

vsys（虛擬系統）：PAN-OS 即使沒開多 vsys 授權，預設也叫 "vsys1"（官方
REST API 範例就是這樣打），所以這裡採固定預設值 + 可覆寫欄位，不像
FortiGate VDOM 需要額外一次探索呼叫。

**全程唯讀**：REST 只打 GET；op API 只送 `show` 類指令。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PaloAltoFirewall(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Palo Alto 防火牆實例（PAN-OS REST + XML op API）。"""

    __tablename__ = "paloalto_firewalls"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    api_url: Mapped[str] = mapped_column(Text, nullable=False)   # 例：https://192.0.2.1

    username: Mapped[str] = mapped_column(String(255), nullable=False)
    # 密碼：AES-GCM 雙欄加密；每次同步用它換一次性 API key，不落地保存 key
    password_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    password_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    vsys: Mapped[str] = mapped_column(String(64), nullable=False, default="vsys1")

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_interval_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    # 逐項同步開關（ARP 有真實範例輸出核對過，預設開；其餘設定物件類預設關）
    sync_arp: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_policies: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sync_nat: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sync_addresses: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    scope_subnet_ids: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=True,
    )
    description: Mapped[str | None] = mapped_column(Text)


class PaloAltoPolicy(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """PAN-OS Security Policy Rule 唯讀鏡像（比照 fortigate_policies）。"""

    __tablename__ = "paloalto_policies"

    firewall_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("paloalto_firewalls.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    vsys: Mapped[str] = mapped_column(String(64), nullable=False, default="vsys1")
    name: Mapped[str] = mapped_column(String(255), nullable=False)   # PAN-OS 政策以 name 當唯一鍵，無獨立數字 id
    action: Mapped[str | None] = mapped_column(String(16))           # allow / deny / drop / reset-*
    disabled: Mapped[bool | None] = mapped_column(Boolean)
    from_zone: Mapped[str | None] = mapped_column(Text)
    to_zone: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    destination: Mapped[str | None] = mapped_column(Text)
    application: Mapped[str | None] = mapped_column(Text)
    service: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)   # type: ignore[type-arg]
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("firewall_id", "vsys", "name", name="paloalto_policy_unique"),
    )


class PaloAltoAddressObject(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """PAN-OS Address Object 唯讀鏡像（比照 fortigate_address_objects）。"""

    __tablename__ = "paloalto_address_objects"

    firewall_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("paloalto_firewalls.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    vsys: Mapped[str] = mapped_column(String(64), nullable=False, default="vsys1")
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    obj_type: Mapped[str | None] = mapped_column(String(32))   # ip-netmask / ip-range / fqdn
    value: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("firewall_id", "vsys", "name", name="paloalto_addr_unique"),
    )
