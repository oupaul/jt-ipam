"""Palo Alto Networks（PAN-OS）同步服務（Beta，實驗性）—— **全程唯讀**。

跟 FortiGate 一樣是官方 API，但 PAN-OS 分兩套：
- **REST API**（`/restapi/<ver>/...`，JSON）：設定物件 —— 位址物件、政策、NAT。
  只打 GET。認證用 `X-PAN-KEY` 標頭。
- **XML op API**（`/api/?type=op&cmd=...`）：唯讀操作性資料，如 ARP 表。
  只送 `<show>...</show>` 這類指令。key 當查詢參數。

認證：帳密呼叫 `type=keygen` 換一次性 API key（PAN-OS 沒有 FortiGate 那種
使用者自建長效 token 的 GUI 流程），每次同步重新換發，不落地保存 key。

**無實機可驗**（除了 ARP 有查到真實範例輸出，位址物件／政策／NAT 是照官方
REST API 文件範例寫的），所有解析一律容錯，單一端點失敗不拖垮整輪同步 ——
跟 FortiGate / Zyxel service 的原則一致。DHCP 租約、VPN 狀態沒有查到足夠
可信的範例格式，這版**先不做**，避免憑空亂猜。
"""

from __future__ import annotations

import ipaddress
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.safe_http import UnsafeOutboundURL, safe_request
from app.core.security import decrypt_secret, encrypt_secret
from app.models.address import IPAddress
from app.models.paloalto import PaloAltoAddressObject, PaloAltoFirewall, PaloAltoPolicy
from app.services.hostname import apply_observation

REST_VERSION = "v11.0"
EP_ADDRESSES = f"/restapi/{REST_VERSION}/Objects/Addresses"
EP_SECURITY_RULES = f"/restapi/{REST_VERSION}/Policies/SecurityRules"
EP_NAT_RULES = f"/restapi/{REST_VERSION}/Policies/NATRules"

_DIAG_TIMEOUT = 10.0


class PaloAltoError(Exception):
    pass


# ─────────────────── 認證 ───────────────────
def _aad(fw_id: uuid.UUID) -> bytes:
    return f"paloalto_firewall:{fw_id}:password".encode()


def encrypt_password(fw_id: uuid.UUID, raw: str) -> tuple[bytes, bytes]:
    return encrypt_secret(raw, aad=_aad(fw_id))


def _decrypt_password(fw: PaloAltoFirewall) -> str:
    return decrypt_secret(fw.password_enc, fw.password_nonce, aad=_aad(fw.id)).decode("utf-8")


async def _keygen(fw: PaloAltoFirewall) -> str:
    """帳密換一次性 API key。POST 表單（不是 GET query）避免密碼進 URL/存取紀錄。"""
    from defusedxml.ElementTree import fromstring as _safe_xml_fromstring

    url = f"{fw.api_url.rstrip('/')}/api/"
    body = urlencode({"type": "keygen", "user": fw.username, "password": _decrypt_password(fw)})
    try:
        resp = await safe_request(
            "POST", url, content=body.encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15.0, verify=fw.verify_tls,
        )
    except UnsafeOutboundURL as exc:
        raise PaloAltoError(f"SSRF guard rejected URL: {exc}") from exc
    except httpx.HTTPError as exc:
        raise PaloAltoError(f"transport: {exc.__class__.__name__}") from exc
    if resp.status_code != 200:
        raise PaloAltoError(f"keygen HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        root = _safe_xml_fromstring(resp.text)   # defusedxml：擋 XXE / billion-laughs
    except Exception as exc:
        raise PaloAltoError(f"keygen 回應不是合法 XML：{exc}") from exc
    if root.get("status") != "success":
        msg = root.findtext(".//msg") or resp.text[:200]
        raise PaloAltoError(
            f"keygen 失敗（帳密錯誤，或該帳號沒有 API 存取權限）：{msg}",
        )
    key = root.findtext(".//key")
    if not key:
        raise PaloAltoError("keygen 回應找不到 <key>")
    return key


# ─────────────────── REST API（設定物件，JSON）───────────────────
async def _rest_get(fw: PaloAltoFirewall, key: str, path: str, *, timeout: float = 15.0) -> Any:
    url = f"{fw.api_url.rstrip('/')}{path}"
    params = {"location": "vsys", "vsys": fw.vsys}
    try:
        resp = await safe_request(
            "GET", url, headers={"X-PAN-KEY": key, "Accept": "application/json"},
            params=params, timeout=timeout, verify=fw.verify_tls,
        )
    except UnsafeOutboundURL as exc:
        raise PaloAltoError(f"SSRF guard rejected URL: {exc}") from exc
    except httpx.HTTPError as exc:
        raise PaloAltoError(f"transport: {exc.__class__.__name__}") from exc
    if resp.status_code == 403:
        raise PaloAltoError("403：該管理員帳號沒有這個資源的讀取權限")
    if resp.status_code != 200:
        raise PaloAltoError(f"PAN-OS REST GET {path}: {resp.status_code} {resp.text[:200]}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise PaloAltoError(f"回應不是 JSON（{path}）") from exc
    result = body.get("result") if isinstance(body, dict) else None
    entry = (result or {}).get("entry") if isinstance(result, dict) else None
    if entry is None:
        return []
    return entry if isinstance(entry, list) else [entry]


# ─────────────────── XML op API（唯讀操作性資料）───────────────────
async def _op_command(fw: PaloAltoFirewall, key: str, cmd_xml: str, *, timeout: float = 15.0) -> Any:
    from defusedxml.ElementTree import fromstring as _safe_xml_fromstring

    url = f"{fw.api_url.rstrip('/')}/api/"
    params = {"type": "op", "cmd": cmd_xml, "key": key}
    try:
        resp = await safe_request("GET", url, params=params, timeout=timeout, verify=fw.verify_tls)
    except UnsafeOutboundURL as exc:
        raise PaloAltoError(f"SSRF guard rejected URL: {exc}") from exc
    except httpx.HTTPError as exc:
        raise PaloAltoError(f"transport: {exc.__class__.__name__}") from exc
    if resp.status_code != 200:
        raise PaloAltoError(f"PAN-OS op {cmd_xml}: {resp.status_code} {resp.text[:200]}")
    try:
        root = _safe_xml_fromstring(resp.text)   # defusedxml：擋 XXE / billion-laughs
    except Exception as exc:
        raise PaloAltoError(f"op 回應不是合法 XML：{exc}") from exc
    if root.get("status") != "success":
        msg = root.findtext(".//msg") or resp.text[:200]
        raise PaloAltoError(f"op 指令失敗：{msg}")
    return root.find("result")


# ─────────────────── 小工具（容錯解析）───────────────────
def _valid_ip(v: object) -> str | None:
    if v is None:
        return None
    s = str(v).strip().split("/")[0]
    try:
        return str(ipaddress.ip_address(s))
    except ValueError:
        return None


def _norm_mac(v: object) -> str | None:
    if v is None:
        return None
    s = str(v).strip().lower().replace("-", ":")
    parts = s.split(":")
    if len(parts) == 6 and all(len(p) == 2 for p in parts):
        try:
            int(s.replace(":", ""), 16)
        except ValueError:
            return None
        return s
    return None


def _members(v: Any) -> str | None:
    """PAN-OS 的 from/to/source/destination/application/service 是 `{"member": [...]}` """
    if v is None:
        return None
    if isinstance(v, dict):
        m = v.get("member")
        if isinstance(m, list):
            return ", ".join(str(x) for x in m) or None
        if isinstance(m, str):
            return m or None
        return None
    if isinstance(v, str):
        return v or None
    return None


def _address_value(d: dict[str, Any]) -> tuple[str | None, str | None]:
    """回傳 (obj_type, value)：ip-netmask / ip-range / fqdn 三選一。"""
    if d.get("ip-netmask"):
        return "ip-netmask", str(d["ip-netmask"])
    if d.get("ip-range"):
        return "ip-range", str(d["ip-range"])
    if d.get("fqdn"):
        return "fqdn", str(d["fqdn"])
    if d.get("ip-wildcard"):
        return "ip-wildcard", str(d["ip-wildcard"])
    return None, None


# ─────────────────── 各項同步 ───────────────────
def _scope(fw: PaloAltoFirewall) -> list[uuid.UUID] | None:
    return list(fw.scope_subnet_ids) if fw.scope_subnet_ids else None


async def _stamp_ip_seen(
    session: AsyncSession, ip: str, *, mac: str | None = None, hostname: str | None = None,
    subnet_ids: list[uuid.UUID] | None = None,
) -> bool:
    stmt = select(IPAddress).where(IPAddress.ip == ip)
    if subnet_ids:
        stmt = stmt.where(IPAddress.subnet_id.in_(subnet_ids))
    ipa = (await session.execute(stmt.limit(1))).scalars().first()
    if ipa is None:
        return False
    ipa.last_seen_scanner = datetime.now(UTC)
    if mac:
        from app.services.arp_precedence import consider_mac
        await consider_mac(session, ip=ipa, mac=mac, source="paloalto")
    if hostname:
        await apply_observation(session, ip=ipa, source="paloalto", hostname=hostname)
    return True


async def sync_arp(session: AsyncSession, fw: PaloAltoFirewall, key: str) -> int:
    scope_ids = _scope(fw)
    result = await _op_command(fw, key, "<show><arp><entry name='all'/></arp></show>")
    matched = 0
    if result is None:
        return 0
    entries = result.findall(".//entry")
    for e in entries:
        ip = _valid_ip(e.findtext("ip"))
        if not ip:
            continue
        mac = _norm_mac(e.findtext("mac"))
        if await _stamp_ip_seen(session, ip, mac=mac, subnet_ids=scope_ids):
            matched += 1
    return matched


async def sync_addresses(session: AsyncSession, fw: PaloAltoFirewall, key: str) -> int:
    now = datetime.now(UTC)
    await session.execute(
        delete(PaloAltoAddressObject).where(PaloAltoAddressObject.firewall_id == fw.id))
    rows = await _rest_get(fw, key, EP_ADDRESSES)
    n = 0
    seen: set[str] = set()
    for d in rows:
        if not isinstance(d, dict):
            continue
        name = d.get("@name")
        if not name or name in seen:
            continue
        seen.add(name)
        obj_type, value = _address_value(d)
        session.add(PaloAltoAddressObject(
            firewall_id=fw.id, vsys=fw.vsys, name=str(name)[:255],
            obj_type=obj_type, value=value,
            description=(str(d.get("description")) if d.get("description") else None),
            last_sync_at=now,
        ))
        n += 1
    return n


async def sync_policies(session: AsyncSession, fw: PaloAltoFirewall, key: str) -> int:
    now = datetime.now(UTC)
    await session.execute(delete(PaloAltoPolicy).where(PaloAltoPolicy.firewall_id == fw.id))
    rows = await _rest_get(fw, key, EP_SECURITY_RULES)
    n = 0
    seen: set[str] = set()
    for d in rows:
        if not isinstance(d, dict):
            continue
        name = d.get("@name")
        if not name or name in seen:
            continue
        seen.add(name)
        disabled = d.get("disabled")
        session.add(PaloAltoPolicy(
            firewall_id=fw.id, vsys=fw.vsys, name=str(name)[:255],
            action=(str(d.get("action"))[:16] if d.get("action") else None),
            disabled=(str(disabled).lower() == "yes") if disabled is not None else None,
            from_zone=_members(d.get("from")), to_zone=_members(d.get("to")),
            source=_members(d.get("source")), destination=_members(d.get("destination")),
            application=_members(d.get("application")), service=_members(d.get("service")),
            description=(str(d.get("description")) if d.get("description") else None),
            raw=d, last_sync_at=now,
        ))
        n += 1
    return n


async def sync_nat(session: AsyncSession, fw: PaloAltoFirewall, key: str) -> int:
    """NAT rules → 共用 nat_translations（欄位對照未經真機驗證，容錯解析）。"""
    from app.models.nat import NATTranslation

    origin = f"paloalto:{fw.id}"
    await session.execute(delete(NATTranslation).where(NATTranslation.source_origin == origin))
    scope_ids = _scope(fw)
    rows = await _rest_get(fw, key, EP_NAT_RULES)
    n = 0
    for d in rows:
        if not isinstance(d, dict):
            continue
        name = d.get("@name")
        if not name:
            continue
        dst_xlate = d.get("destination-translation") or {}
        src_xlate = d.get("source-translation") or {}
        target_ip = _valid_ip(dst_xlate.get("translated-address")) if isinstance(dst_xlate, dict) else None
        dst_ip_id = None
        if target_ip:
            stmt = select(IPAddress.id).where(IPAddress.ip == target_ip)
            if scope_ids:
                stmt = stmt.where(IPAddress.subnet_id.in_(scope_ids))
            dst_ip_id = (await session.execute(stmt.limit(1))).scalars().first()
        is_dnat = bool(dst_xlate) and isinstance(dst_xlate, dict)
        mapped_port = dst_xlate.get("translated-port") if isinstance(dst_xlate, dict) else None
        session.add(NATTranslation(
            name=str(name)[:200],
            type="port_forward" if is_dnat else ("many_to_one" if src_xlate else "one_to_one"),
            protocol="any",
            dst_ip_id=dst_ip_id,
            dst_port=int(mapped_port) if isinstance(mapped_port, str) and mapped_port.isdigit() else None,
            description=(str(d.get("description")) if d.get("description") else None),
            source_origin=origin, external_id=str(name)[:200],
        ))
        n += 1
    return n


# ─────────────────── 連線診斷 / 整批同步 ───────────────────
async def diagnose(fw: PaloAltoFirewall) -> dict[str, Any]:
    """測試連線：先換 key，再逐端點回報通不通與筆數。"""
    key = await _keygen(fw)
    checks: list[dict[str, Any]] = []

    async def _probe_rest(label: str, path: str) -> None:
        try:
            rows = await _rest_get(fw, key, path, timeout=_DIAG_TIMEOUT)
            checks.append({"endpoint": label, "ok": True, "rows": len(rows)})
        except PaloAltoError as exc:
            checks.append({"endpoint": label, "ok": False, "error": str(exc)[:200]})

    async def _probe_arp() -> None:
        try:
            result = await _op_command(
                fw, key, "<show><arp><entry name='all'/></arp></show>", timeout=_DIAG_TIMEOUT)
            n = len(result.findall(".//entry")) if result is not None else 0
            checks.append({"endpoint": "arp", "ok": True, "rows": n})
        except PaloAltoError as exc:
            checks.append({"endpoint": "arp", "ok": False, "error": str(exc)[:200]})

    await _probe_arp()
    await _probe_rest("address", EP_ADDRESSES)
    await _probe_rest("security_rules", EP_SECURITY_RULES)
    await _probe_rest("nat_rules", EP_NAT_RULES)

    return {"vsys": fw.vsys, "checks": checks, "ok_count": sum(1 for c in checks if c["ok"])}


async def sync_instance(session: AsyncSession, fw: PaloAltoFirewall) -> dict[str, Any]:
    """跑此實例所有啟用的同步；設定 last_sync_at / last_error。單次 keygen 全程共用。"""
    key = await _keygen(fw)
    counts: dict[str, Any] = {}
    if fw.sync_arp:
        counts["arp"] = await sync_arp(session, fw, key)
    if fw.sync_addresses:
        counts["addresses"] = await sync_addresses(session, fw, key)
    if fw.sync_policies:
        counts["policies"] = await sync_policies(session, fw, key)
    if fw.sync_nat:
        counts["nat"] = await sync_nat(session, fw, key)
    fw.last_sync_at = datetime.now(UTC)
    fw.last_error = None
    return counts
