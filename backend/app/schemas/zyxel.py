"""Zyxel 防火牆整合 schemas（Beta，實驗性）。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import Field

from app.schemas.base import StrictModel


class ZyxelBase(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    host: Annotated[str, Field(min_length=1, max_length=255)]
    port: Annotated[int, Field(ge=1, le=65535)] = 22
    username: Annotated[str, Field(min_length=1, max_length=255)]
    enabled: bool = True
    sync_arp: bool = True
    sync_dhcp: bool = False
    sync_policies: bool = False
    sync_nat: bool = False
    sync_addresses: bool = False
    sync_interval_seconds: Annotated[int, Field(ge=30, le=86400)] = 300
    description: Annotated[str | None, Field(max_length=2048)] = None
    scope_subnet_ids: list[uuid.UUID] | None = None


class ZyxelCreate(ZyxelBase):
    password: Annotated[str, Field(min_length=1, max_length=512)]


class ZyxelUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    host: Annotated[str | None, Field(min_length=1, max_length=255)] = None
    port: Annotated[int | None, Field(ge=1, le=65535)] = None
    username: Annotated[str | None, Field(min_length=1, max_length=255)] = None
    password: Annotated[str | None, Field(min_length=1, max_length=512)] = None
    enabled: bool | None = None
    sync_arp: bool | None = None
    sync_dhcp: bool | None = None
    sync_policies: bool | None = None
    sync_nat: bool | None = None
    sync_addresses: bool | None = None
    sync_interval_seconds: Annotated[int | None, Field(ge=30, le=86400)] = None
    description: Annotated[str | None, Field(max_length=2048)] = None
    scope_subnet_ids: list[uuid.UUID] | None = None


class ZyxelRead(StrictModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    host: str
    port: int
    username: str
    enabled: bool
    sync_arp: bool
    sync_dhcp: bool
    sync_policies: bool
    sync_nat: bool
    sync_addresses: bool
    sync_interval_seconds: int
    description: str | None = None
    scope_subnet_ids: list[uuid.UUID] | None = None
    last_sync_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime
