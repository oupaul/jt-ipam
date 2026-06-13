"""憑證派送 schemas（管理面;agent 協定的 schema 放 endpoint 內）。

機敏:cert_pem/chain 可回(公開資訊),**私鑰一律不回傳**(schema 根本沒有 key 欄位)。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import Field

from app.schemas.base import StrictModel


class CertificateCreate(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    description: Annotated[str | None, Field(max_length=1024)] = None


class CertificateUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None


class SelfSignedRequest(StrictModel):
    """產生自簽憑證:自訂名稱(CN) + SAN + 效期天數。"""
    common_name: Annotated[str, Field(min_length=1, max_length=253)]
    sans: list[Annotated[str, Field(max_length=253)]] = Field(default_factory=list)
    days: Annotated[int, Field(ge=1, le=3650)] = 365


class CertVersionRead(StrictModel):
    id: uuid.UUID
    fingerprint_sha256: str
    serial: str | None
    subject: str | None
    issuer: str | None
    not_before: datetime | None
    not_after: datetime
    domains: list[str] | None
    is_current: bool
    uploaded_by: uuid.UUID | None
    created_at: datetime


class CertificateRead(StrictModel):
    id: uuid.UUID
    name: str
    description: str | None
    domains: list[str] | None
    created_at: datetime
    updated_at: datetime
    # 由端點計算填入（目前版本摘要 + 統計）
    current_fingerprint: str | None = None
    current_not_after: datetime | None = None
    current_days_remaining: int | None = None
    version_count: int = 0


# ─────────────────── Cert Agents ───────────────────

class CertAgentCreate(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    description: Annotated[str | None, Field(max_length=1024)] = None
    enabled: bool = True
    # 此 agent 可取的 certificate id 清單(deny-by-default;空＝不可取)
    scope_cert_ids: list[uuid.UUID] = Field(default_factory=list)


class CertAgentUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None
    enabled: bool | None = None
    scope_cert_ids: list[uuid.UUID] | None = None


class CertAgentRead(StrictModel):
    id: uuid.UUID
    name: str
    description: str | None
    enabled: bool
    scope_cert_ids: list[uuid.UUID] | None
    last_seen_at: datetime | None
    last_source_ip: str | None
    agent_version: str | None
    reported: list[dict[str, Any]] | None
    has_key: bool = False
    created_at: datetime
    updated_at: datetime


class CertAgentCreated(CertAgentRead):
    """建立 / 輪替 key 時一次性回傳明文 enrollment key。"""
    enroll_key: str
