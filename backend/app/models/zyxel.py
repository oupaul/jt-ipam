"""Zyxel 防火牆整合 model（Beta，實驗性）—— standalone（本機管理）ZLD 機種，SSH CLI，無實機可驗。

Zyxel USG FLEX / ATP / ZyWALL（ZLD 韌體）standalone 模式沒有對外開放的 REST API
（跟 FortiGate 不同），管理介面只有 Web GUI（表單）跟 SSH CLI。這裡改用 SSH 登入後
下 `show ...` 這類唯讀指令、解析純文字輸出。指令語法與欄位格式取自官方
《ZyWALL/USG(FLEX)/ATP/VPN Series CLI Reference Guide》，但沒有實機可以驗證輸出
格式在不同韌體版本下是否一致，比 FortiOS 的 JSON API 更容易因版本而跑掉 ——
所有欄位一律容錯解析，單一指令失敗或格式對不上不拖垮整輪同步。

密碼以 AES-GCM 加密（aad 綁實例 id）。SSH 用密碼登入，不做 host key pinning
（比照 cert_fetch.py 對任意客戶主機的既有作法：known_hosts=None）。
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


class ZyxelFirewall(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Zyxel 防火牆實例（standalone，SSH CLI）。"""

    __tablename__ = "zyxel_firewalls"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, default=22, nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)

    # 密碼：AES-GCM 雙欄加密（比照 Wazuh / AdGuard / Windows DHCP）
    password_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    password_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_interval_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    # 逐項同步開關 —— ARP/位址物件/政策格式在官方文件裡有明確範例輸出，較可信；
    # DHCP 租約沒有範例輸出可核對，預設關閉，待對著真機校正過再建議開啟。
    sync_arp: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_dhcp: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sync_policies: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sync_nat: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sync_addresses: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # 關聯子網路範圍（留空＝全域 IP 字串比對）；重疊網段時限定比對範圍
    scope_subnet_ids: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=True,
    )
    description: Mapped[str | None] = mapped_column(Text)


class ZyxelPolicy(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Zyxel Secure Policy（防火牆政策）唯讀鏡像（比照 fortigate_policies）。"""

    __tablename__ = "zyxel_policies"

    firewall_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("zyxel_firewalls.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    rule_number: Mapped[str] = mapped_column(String(32), nullable=False)   # secure-policy rule 序號
    name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str | None] = mapped_column(String(16))                 # yes/no（active）
    action: Mapped[str | None] = mapped_column(String(16))                 # allow / deny / reject
    from_zone: Mapped[str | None] = mapped_column(String(64))
    to_zone: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str | None] = mapped_column(Text)
    destination: Mapped[str | None] = mapped_column(Text)
    service: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)   # type: ignore[type-arg]
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("firewall_id", "rule_number", name="zyxel_policy_unique"),
    )


class ZyxelAddressObject(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Zyxel Address Object 唯讀鏡像（比照 fortigate_address_objects）。"""

    __tablename__ = "zyxel_address_objects"

    firewall_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("zyxel_firewalls.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    obj_type: Mapped[str | None] = mapped_column(String(32))   # HOST / RANGE / SUBNET / INTERFACE …
    value: Mapped[str | None] = mapped_column(Text)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("firewall_id", "name", name="zyxel_addr_unique"),
    )
