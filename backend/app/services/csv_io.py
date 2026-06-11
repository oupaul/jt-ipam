"""CSV 匯入/匯出（針對 IP Addresses）。

主動超越 phpIPAM 匯入的設計：
- header-driven：欄位順序不重要、列首必須是欄位名（小寫）
- BOM-tolerant：自動剝除 UTF-8 BOM
- 自動偵測 delimiter（csv.Sniffer，無法偵測時 fallback 為 ','）
- dry-run preview：不寫入 DB，回傳 N 筆範例 + 預期 inserted/skipped/error
- idempotent：已存在 (subnet_id, ip) 預設 skip 而非 error
- 逐列錯誤回報，不因為某列錯誤而整批失敗

OWASP A05：每列以 Pydantic schema 驗證；不接受 schema 外欄位。
"""

from __future__ import annotations

import csv
import io
import ipaddress
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.address import IPAddress
from app.models.device import Device as DeviceModel
from app.models.subnet import Subnet
from app.schemas.address import IPAddressCreate
from app.schemas.device import DeviceCreate

EXPORT_COLUMNS: list[str] = [
    "ip",
    "hostname",
    "mac",
    "state",
    "description",
    "owner",
    "switch_port",
    "note",
    "discovery_source",
    "last_seen_scanner",
    "effective_status",
]


def _csv_safe(value: str) -> str:
    """CSV 公式注入防護（OWASP A03）：使用者可控字串若以 = + - @ 或 tab/CR 開頭，
    在 Excel / Google Sheets 開啟時會被當公式執行。前置單引號讓它被當純文字。"""
    if value and value[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def export_addresses_csv(rows: Iterable[IPAddress]) -> str:
    """將 IPAddress rows 序列化為 CSV 字串。"""
    buf = io.StringIO()
    # 寫入 BOM 讓 Excel 直接認 UTF-8
    buf.write("﻿")
    writer = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({
            "ip": str(r.ip).split("/")[0],
            "hostname": _csv_safe(r.hostname or ""),
            "mac": str(r.mac) if r.mac else "",
            "state": r.state,
            "description": _csv_safe(r.description or ""),
            "owner": _csv_safe(r.owner or ""),
            "switch_port": _csv_safe(r.switch_port or ""),
            "note": _csv_safe(r.note or ""),
            "discovery_source": r.discovery_source,
            "last_seen_scanner": r.last_seen_scanner.isoformat() if r.last_seen_scanner else "",
            "effective_status": r.effective_status or "",
        })
    return buf.getvalue()


@dataclass
class ImportRowError:
    line_number: int
    raw: dict[str, Any]
    error: str

    def to_dict(self) -> dict[str, Any]:
        return {"line_number": self.line_number, "raw": self.raw, "error": self.error}


@dataclass
class ImportResult:
    inserted: int
    updated: int          # upsert 模式下更新已存在記錄的筆數
    skipped: int          # 已存在 (subnet, ip)，且非 upsert 模式
    errored: int
    errors: list[ImportRowError]
    preview: list[dict[str, Any]]   # 前 5 筆「即將寫入」的列（dry-run + 真寫入皆回）

    def to_dict(self) -> dict[str, Any]:
        return {
            "inserted": self.inserted,
            "updated": self.updated,
            "skipped": self.skipped,
            "errored": self.errored,
            "errors": [e.to_dict() for e in self.errors[:50]],  # cap report
            "preview": self.preview,
        }


def _strip_bom(text: str) -> str:
    return text.lstrip("﻿")


def _detect_dialect(sample: str) -> csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")  # type: ignore[return-value]
    except csv.Error:
        # fallback：預設 excel
        return csv.excel  # type: ignore[return-value]


_INPUT_COLS = {
    "ip", "hostname", "mac", "state", "description", "owner", "switch_port", "note",
}

# 中文欄位別名 → 英文標準名（匯入時自動正規化）
_COL_ALIASES: dict[str, str] = {
    "主機名稱": "hostname",
    "狀態":    "state",
    "說明":    "description",
    "擁有者":  "owner",
    "交換器位置": "switch_port",
    "備註":    "note",
}


async def import_addresses_csv(
    session: AsyncSession,
    *,
    subnet: Subnet,
    csv_text: str,
    dry_run: bool = False,
    update_existing: bool = False,
) -> ImportResult:
    """匯入到指定 subnet。

    - header 必須含 `ip`
    - 多餘欄位忽略；不在 _INPUT_COLS 的 header 列入錯誤訊息（不阻擋）
    - update_existing=False（預設）：已存在的 (subnet_id, ip) 視為 skip（idempotent）
    - update_existing=True：已存在的記錄以 CSV 中非空欄位更新（hostname/mac/state/
      description/owner/switch_port/note）；discovery_source 等掃描欄位不動
    """
    text = _strip_bom(csv_text)
    if not text.strip():
        return ImportResult(inserted=0, updated=0, skipped=0, errored=0, errors=[], preview=[])

    sample = text[:4096]
    dialect = _detect_dialect(sample)
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)

    if not reader.fieldnames or "ip" not in [c.strip().lower() for c in reader.fieldnames]:
        return ImportResult(
            inserted=0, updated=0, skipped=0, errored=1,
            errors=[ImportRowError(line_number=1, raw={"fieldnames": reader.fieldnames or []},
                                   error="Required header 'ip' not found")],
            preview=[],
        )

    # 預先抓 subnet 內既有 IP；upsert 模式需要完整物件以便更新
    existing_q = await session.execute(
        select(IPAddress).where(IPAddress.subnet_id == subnet.id)
    )
    existing_objs: dict[str, IPAddress] = {
        str(r.ip).split("/")[0]: r for r in existing_q.scalars().all()
    }
    existing = set(existing_objs.keys())

    cidr = str(subnet.cidr)
    net = ipaddress.ip_network(cidr, strict=False)

    inserted = 0
    updated = 0
    skipped = 0
    errored = 0
    errors: list[ImportRowError] = []
    preview: list[dict[str, Any]] = []

    for line_no, raw in enumerate(reader, start=2):  # header 是第 1 行
        # 正規化 key（小寫 + strip）
        row_raw = {(k or "").strip(): (v.strip() if isinstance(v, str) else v) for k, v in raw.items()}
        # 中文欄位別名正規化（先套別名，再 lower）
        row = {(_COL_ALIASES.get(k, k).lower()): v for k, v in row_raw.items()}
        # 拒絕未識別欄位（A03）— 但只報告，不阻止此列
        unknown = [k for k in row if k not in _INPUT_COLS and k != ""]
        if unknown:
            errors.append(ImportRowError(line_no, dict(row), f"Ignored unknown columns: {unknown}"))
            # 但允許繼續處理已知欄位

        ip = row.get("ip") or ""
        if not ip:
            errored += 1
            errors.append(ImportRowError(line_no, dict(row), "Missing ip"))
            continue

        # 範圍檢查
        try:
            ip_addr = ipaddress.ip_address(ip)
        except ValueError as exc:
            errored += 1
            errors.append(ImportRowError(line_no, dict(row), f"Invalid IP: {exc}"))
            continue
        if ip_addr not in net:
            errored += 1
            errors.append(ImportRowError(line_no, dict(row),
                                         f"{ip} not in subnet {cidr}"))
            continue

        # Pydantic 驗證（hostname / MAC / state 等）— 在存在性檢查前跑，upsert 也需要驗證過的值
        try:
            payload = IPAddressCreate(
                subnet_id=subnet.id,
                ip=ip,
                hostname=row.get("hostname") or None,
                mac=row.get("mac") or None,
                state=row.get("state") or "active",
                description=row.get("description") or None,
                owner=row.get("owner") or None,
                switch_port=row.get("switch_port") or None,
                note=row.get("note") or None,
            )
        except ValidationError as exc:
            errored += 1
            errors.append(ImportRowError(line_no, dict(row), f"Validation: {exc.errors()[0]['msg']}"))
            continue

        if ip in existing:
            if not update_existing:
                skipped += 1
                continue
            # upsert：只以 CSV 中明確填寫的非空欄位更新，空值不覆蓋既有資料
            if not dry_run:
                obj = existing_objs[ip]
                if row.get("hostname"): obj.hostname = payload.hostname
                if row.get("mac"): obj.mac = payload.mac
                if row.get("state"): obj.state = payload.state
                if row.get("description"): obj.description = payload.description
                if row.get("owner"): obj.owner = payload.owner
                if row.get("switch_port"): obj.switch_port = payload.switch_port
                if row.get("note"): obj.note = payload.note
            updated += 1
            continue

        if len(preview) < 5:
            preview.append(payload.model_dump(mode="json"))

        if not dry_run:
            obj = IPAddress(
                subnet_id=subnet.id,
                ip=payload.ip,
                hostname=payload.hostname,
                mac=payload.mac,
                state=payload.state,
                description=payload.description,
                owner=payload.owner,
                switch_port=payload.switch_port,
                note=payload.note,
                discovery_source="manual",
            )
            session.add(obj)
            existing.add(ip)

        inserted += 1

    if not dry_run and (inserted > 0 or updated > 0):
        await session.commit()

    return ImportResult(
        inserted=0 if dry_run else inserted,  # dry_run 不算 inserted
        updated=0 if dry_run else updated,
        skipped=skipped,
        errored=errored,
        errors=errors,
        preview=preview,
    )


# ─────────────────── 裝置 CSV 匯入 ───────────────────

_DEVICE_INPUT_COLS = {
    "name", "fqdn", "type", "vendor", "model", "serial", "description",
}

_DEVICE_COL_ALIASES: dict[str, str] = {
    "裝置名稱": "name",
    "名稱":    "name",
    "類型":    "type",
    "廠牌":    "vendor",
    "製造商":  "vendor",
    "型號":    "model",
    "序號":    "serial",
    "序列號":  "serial",
    "說明":    "description",
}


async def import_devices_csv(
    session: AsyncSession,
    *,
    csv_text: str,
    dry_run: bool = False,
    update_existing: bool = False,
) -> ImportResult:
    """匯入裝置 CSV。

    - header 必須含 `name`（裝置名稱，作為唯一鍵）
    - update_existing=False（預設）：已存在同名裝置視為 skip
    - update_existing=True：以 CSV 非空欄位更新既有裝置（fqdn/type/vendor/model/serial/description）
    - 支援欄位：name, fqdn, type, vendor, model, serial, description
    """
    text = _strip_bom(csv_text)
    if not text.strip():
        return ImportResult(inserted=0, updated=0, skipped=0, errored=0, errors=[], preview=[])

    sample = text[:4096]
    dialect = _detect_dialect(sample)
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)

    # 檢查 header 時也套別名（支援中文欄位）
    _norm_headers = [
        (_DEVICE_COL_ALIASES.get((c or "").strip(), (c or "").strip())).lower()
        for c in (reader.fieldnames or [])
    ]
    if not reader.fieldnames or "name" not in _norm_headers:
        return ImportResult(
            inserted=0, updated=0, skipped=0, errored=1,
            errors=[ImportRowError(line_number=1, raw={"fieldnames": reader.fieldnames or []},
                                   error="Required header 'name' (or '名稱') not found")],
            preview=[],
        )

    # 預先抓所有裝置名稱（用於 upsert 比對）
    existing_q = await session.execute(select(DeviceModel))
    existing_objs: dict[str, DeviceModel] = {r.name: r for r in existing_q.scalars().all()}
    existing = set(existing_objs.keys())

    inserted = 0
    updated = 0
    skipped = 0
    errored = 0
    errors: list[ImportRowError] = []
    preview: list[dict[str, Any]] = []

    for line_no, raw in enumerate(reader, start=2):
        # 正規化 key（小寫 + strip）
        row_raw = {(k or "").strip(): (v.strip() if isinstance(v, str) else v) for k, v in raw.items()}
        row = {(_DEVICE_COL_ALIASES.get(k, k).lower()): v for k, v in row_raw.items()}
        # 未識別欄位：報告但不阻擋
        unknown = [k for k in row if k not in _DEVICE_INPUT_COLS and k != ""]
        if unknown:
            errors.append(ImportRowError(line_no, dict(row), f"Ignored unknown columns: {unknown}"))

        name = row.get("name") or ""
        if not name:
            errored += 1
            errors.append(ImportRowError(line_no, dict(row), "Missing name"))
            continue

        # Pydantic 驗證（type enum / 長度等）
        try:
            payload = DeviceCreate(
                name=name,
                fqdn=row.get("fqdn") or None,
                type=row.get("type") or "other",
                vendor=row.get("vendor") or None,
                model=row.get("model") or None,
                serial=row.get("serial") or None,
                description=row.get("description") or None,
            )
        except Exception as exc:  # ValidationError
            errored += 1
            msg = getattr(exc, "errors", lambda: [{"msg": str(exc)}])()
            errors.append(ImportRowError(line_no, dict(row), f"Validation: {msg[0]['msg']}"))
            continue

        if name in existing:
            if not update_existing:
                skipped += 1
                continue
            # upsert：只以非空欄位更新
            if not dry_run:
                obj = existing_objs[name]
                if row.get("fqdn"):        obj.fqdn = payload.fqdn
                if row.get("type"):        obj.type = payload.type
                if row.get("vendor"):      obj.vendor = payload.vendor
                if row.get("model"):       obj.model = payload.model
                if row.get("serial"):      obj.serial = payload.serial
                if row.get("description"): obj.description = payload.description
            updated += 1
            continue

        if len(preview) < 5:
            preview.append(payload.model_dump(mode="json"))

        if not dry_run:
            obj = DeviceModel(
                name=payload.name,
                fqdn=payload.fqdn,
                type=payload.type,
                vendor=payload.vendor,
                model=payload.model,
                serial=payload.serial,
                description=payload.description,
            )
            session.add(obj)
            existing.add(name)

        inserted += 1

    if not dry_run and (inserted > 0 or updated > 0):
        await session.commit()

    return ImportResult(
        inserted=0 if dry_run else inserted,
        updated=0 if dry_run else updated,
        skipped=skipped,
        errored=errored,
        errors=errors,
        preview=preview,
    )
