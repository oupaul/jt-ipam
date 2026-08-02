"""Zyxel 防火牆同步服務（Beta，實驗性）—— SSH CLI，**全程唯讀（只下 show 類指令）**。

跟 FortiGate 整合不同：standalone ZLD 機種沒有 REST API，只能走 SSH 登入後的互動式
CLI。連線細節：
- 密碼登入，`known_hosts=None`（比照 `app/services/cert_fetch.py` 對任意客戶主機的
  既有作法，不做 host key pinning）
- 開一個帶 PTY 的 shell，進 `configure terminal` 後逐一送出 `show ...` 指令，用
  行尾像 `xxx#` / `xxx(config)#` 這種 prompt 樣式判斷單一指令的輸出結束
- 指令語法與範例輸出格式取自官方 CLI Reference Guide，但**沒有實機可驗證**，
  不同韌體版本的欄位/樣式可能有落差 → 所有解析一律容錯，單一指令或單一列解析失敗
  不拖垮整輪同步（跟 FortiGate service 的原則一致）
- DHCP 租約（`show ip dhcp binding`）文件裡沒有範例輸出可核對，用跟 ARP 一樣的
  「逐行抓 IP + MAC」寬鬆解法，且預設關閉（sync_dhcp=False），上線前務必先對真機校正
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncssh
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_secret, encrypt_secret
from app.models.address import IPAddress
from app.models.zyxel import ZyxelAddressObject, ZyxelFirewall, ZyxelPolicy

_CONNECT_TIMEOUT = 15.0
_DIAG_TIMEOUT = 10.0

# 一行輸出的最尾端如果像 `hostname#` 或 `hostname(config)#`，視為 CLI 回到可輸入狀態。
_PROMPT_RE = re.compile(r"[^\r\n]{1,80}(\([\w.-]+\))?[#>]\s*$")
_IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_MAC_RE = re.compile(r"\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\b")


class ZyxelError(Exception):
    pass


# ─────────────────── 認證 ───────────────────
def _aad(fw_id: uuid.UUID) -> bytes:
    return f"zyxel_firewall:{fw_id}:password".encode()


def encrypt_password(fw_id: uuid.UUID, raw: str) -> tuple[bytes, bytes]:
    return encrypt_secret(raw, aad=_aad(fw_id))


def _decrypt_password(fw: ZyxelFirewall) -> str:
    return decrypt_secret(fw.password_enc, fw.password_nonce, aad=_aad(fw.id)).decode("utf-8")


# ─────────────────── SSH CLI 連線 ───────────────────
class _CLISession:
    """一次 SSH 連線裡的互動式 shell；逐指令送出、以 prompt 判斷輸出結束。"""

    def __init__(self, process: asyncssh.SSHClientProcess[str], timeout: float) -> None:
        self._proc = process
        self._timeout = timeout

    async def _read_until_prompt(self) -> str:
        buf = ""
        try:
            async with asyncio.timeout(self._timeout):
                while not _PROMPT_RE.search(buf.rstrip()):
                    chunk = await self._proc.stdout.read(65536)
                    if not chunk:
                        break
                    buf += chunk
        except TimeoutError as exc:
            raise ZyxelError(f"CLI 逾時（{self._timeout}s），未看到預期的 prompt") from exc
        return buf

    async def login_and_enter_config(self) -> None:
        await self._read_until_prompt()   # 吃掉登入 banner / 初始 prompt
        self._proc.stdin.write("configure terminal\n")
        out = await self._read_until_prompt()
        if "invalid" in out.lower() or "unknown command" in out.lower():
            raise ZyxelError(f"無法進入 configure terminal：{out.strip()[:200]}")

    async def run(self, command: str) -> str:
        """送一行指令，回傳去掉指令回音與結尾 prompt 的輸出本文。"""
        self._proc.stdin.write(command + "\n")
        raw = await self._read_until_prompt()
        lines = raw.splitlines()
        # 第一行通常是終端機回顯的指令本身；最後一行通常是下一個 prompt —— 兩者都不是資料
        if lines and command.strip() in lines[0]:
            lines = lines[1:]
        if lines and _PROMPT_RE.match(lines[-1].strip()):
            lines = lines[:-1]
        return "\n".join(lines)


async def _open_session(fw: ZyxelFirewall) -> tuple[asyncssh.SSHClientConnection, _CLISession]:
    try:
        conn = await asyncio.wait_for(
            asyncssh.connect(
                fw.host, port=fw.port, username=fw.username,
                password=_decrypt_password(fw),
                known_hosts=None,   # 比照 cert_fetch.py：任意客戶內網設備，不做 host key pinning
                preferred_auth=("password", "keyboard-interactive"),
            ),
            timeout=_CONNECT_TIMEOUT,
        )
    except TimeoutError as exc:
        raise ZyxelError(f"SSH connect timeout to {fw.host}:{fw.port}") from exc
    except asyncssh.PermissionDenied as exc:
        raise ZyxelError(f"SSH 認證被拒：請確認帳號密碼，且該帳號有 CLI 存取權限（{exc}）") from exc
    except (asyncssh.Error, OSError) as exc:
        raise ZyxelError(f"SSH connect failed: {exc.__class__.__name__}: {exc}") from exc

    try:
        process = await conn.create_process(term_type="vt100", encoding="utf-8")
    except asyncssh.Error as exc:
        conn.close()
        raise ZyxelError(f"無法開啟互動式 shell：{exc}") from exc

    session = _CLISession(process, timeout=_CONNECT_TIMEOUT)
    try:
        await session.login_and_enter_config()
    except ZyxelError:
        conn.close()
        raise
    return conn, session


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
    m = _MAC_RE.search(str(v))
    return m.group(0).lower() if m else None


def _trunc(v: str | None, limit: int | None = None) -> str | None:
    if not v:
        return None
    return v[:limit] if limit else v


def _parse_kv_blocks(text: str, header_re: re.Pattern[str]) -> list[dict[str, str]]:
    """把『區塊標頭行 + 縮排 key: value（可逗號分隔多組）』的輸出剖成一串 dict。

    secure-policy / ip virtual-server 兩種輸出都長這樣，共用同一個剖析器。
    """
    blocks: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in text.splitlines():
        m = header_re.match(line.strip())
        if m:
            if current is not None:
                blocks.append(current)
            current = {"_header": m.group(1).strip()}
            continue
        if current is None:
            continue
        for segment in line.split(","):
            if ":" not in segment:
                continue
            key, _, value = segment.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key:
                current[key] = value
    if current is not None:
        blocks.append(current)
    return blocks


def _parse_address_objects(text: str) -> list[dict[str, str]]:
    """`show address-object` 的表格輸出：Object name / Type / Address / Note / Ref.

    真機核對過（見 2026-08 對話紀錄）：表頭其實有 Note 這一欄（原先漏看，只當
    Object name / Type / Address / Ref. 四欄），Note 可能含空白、內容不定，
    但 Address 本身（HOST 的 IP、RANGE、SUBNET 的 CIDR、INTERFACE 名稱、FQDN）
    一定是不含空白的單一 token，所以只取第三欄當 value，其餘（含 Note、結尾
    Ref. 計數）一律略過，不因為 Note 有沒有內容而跑位。
    """
    out: list[dict[str, str]] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3 or set(parts[0]) == {"="}:
            continue
        if parts[0].lower() in ("object", "name"):   # 表頭
            continue
        name, obj_type, value = parts[0], parts[1], parts[2]
        out.append({"name": name, "type": obj_type, "value": value})
    return out


def _parse_arp_table(text: str) -> list[tuple[str, str | None]]:
    """`show arp-table`：逐行抓 IP，再嘗試在同一行找 MAC（欄位對不齊時仍可用）。"""
    out: list[tuple[str, str | None]] = []
    for line in text.splitlines():
        ip_m = _IP_RE.search(line)
        if not ip_m:
            continue
        ip = _valid_ip(ip_m.group(0))
        if not ip:
            continue
        out.append((ip, _norm_mac(line)))
    return out


# ─────────────────── 各項同步 ───────────────────
def _scope(fw: ZyxelFirewall) -> list[uuid.UUID] | None:
    return list(fw.scope_subnet_ids) if fw.scope_subnet_ids else None


async def _stamp_ip_seen(
    session: AsyncSession, ip: str, *, mac: str | None = None,
    subnet_ids: list[uuid.UUID] | None = None, dhcp: bool = False,
) -> bool:
    stmt = select(IPAddress).where(IPAddress.ip == ip)
    if subnet_ids:
        stmt = stmt.where(IPAddress.subnet_id.in_(subnet_ids))
    ipa = (await session.execute(stmt.limit(1))).scalars().first()
    if ipa is None:
        return False
    ipa.last_seen_scanner = datetime.now(UTC)
    if dhcp:
        ipa.in_dhcp_lease = True
    if mac:
        from app.services.arp_precedence import consider_mac
        await consider_mac(session, ip=ipa, mac=mac, source="zyxel")
    return True


async def sync_arp(session: AsyncSession, fw: ZyxelFirewall, cli: _CLISession) -> int:
    scope_ids = _scope(fw)
    out = await cli.run("show arp-table")
    matched = 0
    for ip, mac in _parse_arp_table(out):
        if await _stamp_ip_seen(session, ip, mac=mac, subnet_ids=scope_ids):
            matched += 1
    return matched


async def sync_dhcp_leases(session: AsyncSession, fw: ZyxelFirewall, cli: _CLISession) -> int:
    """未經真機驗證：跟 ARP 一樣用寬鬆的逐行 IP/MAC 抓取，預設關閉。"""
    from sqlalchemy import func
    from sqlalchemy import update as sa_update

    scope_ids = _scope(fw)
    out = await cli.run("show ip dhcp binding")
    matched = 0
    leased: set[str] = set()
    for line in out.splitlines():
        ip_m = _IP_RE.search(line)
        if not ip_m:
            continue
        ip = _valid_ip(ip_m.group(0))
        if not ip:
            continue
        leased.add(ip)
        if await _stamp_ip_seen(session, ip, mac=_norm_mac(line), subnet_ids=scope_ids, dhcp=True):
            matched += 1
    # 撤銷：只在有設 scope 時做，避免多來源在全域互相清掉標記（比照 FortiGate/OPNsense）
    if scope_ids:
        stmt = sa_update(IPAddress).where(
            IPAddress.subnet_id.in_(scope_ids), IPAddress.in_dhcp_lease.is_(True))
        if leased:
            stmt = stmt.where(func.host(IPAddress.ip).notin_(leased))
        await session.execute(stmt.values(in_dhcp_lease=False))
    return matched


async def sync_addresses(session: AsyncSession, fw: ZyxelFirewall, cli: _CLISession) -> int:
    now = datetime.now(UTC)
    await session.execute(delete(ZyxelAddressObject).where(ZyxelAddressObject.firewall_id == fw.id))
    out = await cli.run("show address-object")
    n = 0
    seen: set[str] = set()
    for row in _parse_address_objects(out):
        name = row["name"]
        if not name or name in seen:
            continue
        seen.add(name)
        session.add(ZyxelAddressObject(
            firewall_id=fw.id, name=name[:255], obj_type=row["type"][:32],
            value=row["value"] or None, last_sync_at=now,
        ))
        n += 1
    return n


_POLICY_HEADER_RE = re.compile(r"^secure-policy rule:\s*(.+)$")


async def sync_policies(session: AsyncSession, fw: ZyxelFirewall, cli: _CLISession) -> int:
    now = datetime.now(UTC)
    await session.execute(delete(ZyxelPolicy).where(ZyxelPolicy.firewall_id == fw.id))
    out = await cli.run("show secure-policy")
    n = 0
    seen: set[str] = set()
    for b in _parse_kv_blocks(out, _POLICY_HEADER_RE):
        rule_number = b.get("_header")
        if not rule_number or rule_number in seen:
            continue
        seen.add(rule_number)
        session.add(ZyxelPolicy(
            firewall_id=fw.id, rule_number=rule_number[:32],
            name=_trunc(b.get("name")), status=_trunc(b.get("status"), 16),
            action=_trunc(b.get("action"), 16),
            from_zone=_trunc(b.get("from"), 64), to_zone=_trunc(b.get("to"), 64),
            source=_trunc(b.get("source ip")), destination=_trunc(b.get("destination ip")),
            service=_trunc(b.get("service")), description=_trunc(b.get("description")),
            raw=b, last_sync_at=now,
        ))
        n += 1
    return n


_VS_HEADER_RE = re.compile(r"^virtual server:\s*(.+)$")


async def sync_nat(session: AsyncSession, fw: ZyxelFirewall, cli: _CLISession) -> int:
    """`show ip virtual-server`（port-forward / 1:1 NAT）→ 共用 nat_translations。"""
    from app.models.nat import NATTranslation

    origin = f"zyxel:{fw.id}"
    await session.execute(delete(NATTranslation).where(NATTranslation.source_origin == origin))
    scope_ids = _scope(fw)
    out = await cli.run("show ip virtual-server")
    n = 0
    for b in _parse_kv_blocks(out, _VS_HEADER_RE):
        name = b.get("_header")
        if not name:
            continue
        target_ip = _valid_ip(b.get("mapped ip"))
        dst_ip_id = None
        if target_ip:
            stmt = select(IPAddress.id).where(IPAddress.ip == target_ip)
            if scope_ids:
                stmt = stmt.where(IPAddress.subnet_id.in_(scope_ids))
            dst_ip_id = (await session.execute(stmt.limit(1))).scalars().first()
        is_1to1 = str(b.get("nat 1-1") or "").lower() in ("yes", "enable", "true")
        mapped_port = b.get("mapped start port")
        session.add(NATTranslation(
            name=str(name)[:200],
            type="one_to_one" if is_1to1 else "port_forward",
            protocol=(b.get("protocol type") or "any")[:8],
            dst_ip_id=dst_ip_id,
            dst_port=int(mapped_port) if mapped_port and mapped_port.isdigit() else None,
            src_port=int(b["original start port"]) if b.get("original start port", "").isdigit() else None,
            source_origin=origin, external_id=str(name)[:200],
        ))
        n += 1
    return n


# ─────────────────── 連線診斷 / 整批同步 ───────────────────
_DIAG_COMMANDS = (
    ("arp", "show arp-table"),
    ("dhcp_binding", "show ip dhcp binding"),
    ("address_object", "show address-object"),
    ("secure_policy", "show secure-policy"),
    ("virtual_server", "show ip virtual-server"),
)


async def diagnose(fw: ZyxelFirewall) -> dict[str, Any]:
    """測試連線：逐指令回報通不通與抓到幾個資料區塊/列（無實機開發，藉此對齊實際輸出格式）。"""
    conn, cli = await _open_session(fw)
    try:
        checks: list[dict[str, Any]] = []
        for label, cmd in _DIAG_COMMANDS:
            try:
                async with asyncio.timeout(_DIAG_TIMEOUT):
                    out = await cli.run(cmd)
                checks.append({"command": cmd, "label": label, "ok": True,
                                "sample": out.strip()[:500]})
            except (ZyxelError, TimeoutError) as exc:
                checks.append({"command": cmd, "label": label, "ok": False, "error": str(exc)[:200]})
        return {"host": fw.host, "checks": checks,
                "ok_count": sum(1 for c in checks if c["ok"])}
    finally:
        conn.close()


async def sync_instance(session: AsyncSession, fw: ZyxelFirewall) -> dict[str, Any]:
    """跑此實例所有啟用的同步；設定 last_sync_at / last_error。單一 SSH session 內全部跑完。"""
    conn, cli = await _open_session(fw)
    try:
        counts: dict[str, Any] = {}
        if fw.sync_arp:
            counts["arp"] = await sync_arp(session, fw, cli)
        if fw.sync_dhcp:
            counts["dhcp"] = await sync_dhcp_leases(session, fw, cli)
        if fw.sync_addresses:
            counts["addresses"] = await sync_addresses(session, fw, cli)
        if fw.sync_policies:
            counts["policies"] = await sync_policies(session, fw, cli)
        if fw.sync_nat:
            counts["nat"] = await sync_nat(session, fw, cli)
    finally:
        conn.close()
    fw.last_sync_at = datetime.now(UTC)
    fw.last_error = None
    return counts
