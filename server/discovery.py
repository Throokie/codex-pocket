"""Bounded local-address discovery and optional LAN-only mDNS announcements.

No subnet scans, Tailscale peer queries, account changes or credential reads.
Discovery is a reachability hint; HTTPS identity/pairing remains authoritative.
"""
from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import inspect
import time
import copy
import json
import re
from dataclasses import dataclass, field
from .platform_support import capabilities
from typing import Awaitable, Callable

COMMAND_TIMEOUT = 2.0
MAX_OUTPUT = 1024 * 1024
LAN_V4 = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
ULA = ipaddress.ip_network("fc00::/7")
TS_V4 = ipaddress.ip_network("100.64.0.0/10")
TS_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
SERVICE = "_codexpocket._tcp.local."
# Conservative: only conventional physical Ethernet/Wi-Fi names; virtual/VPNs
# are excluded even when they have private addresses and a carrier.
PHYSICAL = re.compile(r"^(?:eth[0-9]+|en[a-zA-Z0-9]+|wl[a-zA-Z0-9]+)$")
BAD_KINDS = {"bridge", "veth", "tun", "tap", "wireguard", "dummy", "vxlan", "macvlan", "ipvlan", "bond", "vlan"}


@dataclass
class DiscoverySnapshot:
    addresses: list[dict] = field(default_factory=list)
    endpoints: list[dict] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


class DiscoveryCommandError(Exception):
    """Intentionally carries no raw command output or user data."""

    def __init__(self, kind: str):
        self.kind = kind
        super().__init__(kind)


async def run_local(argv: tuple[str, ...]) -> bytes:
    """Fixed argv callers only; cap allocation while reading, not after communicate."""
    process = None
    try:
        async with asyncio.timeout(COMMAND_TIMEOUT):
            process = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                limit=65536)
            assert process.stdout
            output = bytearray()
            while True:
                block = await process.stdout.read(min(65536, MAX_OUTPUT + 1 - len(output)))
                if not block:
                    break
                output.extend(block)
                if len(output) > MAX_OUTPUT:
                    raise DiscoveryCommandError("too_large")
            code = await process.wait()
            if code:
                raise DiscoveryCommandError("unavailable")
            return bytes(output)
    except FileNotFoundError:
        raise DiscoveryCommandError("missing") from None
    except TimeoutError:
        raise DiscoveryCommandError("timeout") from None
    except PermissionError:
        raise DiscoveryCommandError("unavailable") from None
    finally:
        if process and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()


def _lan(address: str) -> bool:
    try:
        value = ipaddress.ip_address(address)
        return (any(value in n for n in LAN_V4) if value.version == 4 else value in ULA)
    except ValueError:
        return False


def _tailscale(address: str) -> bool:
    try:
        value = ipaddress.ip_address(address)
        return value in (TS_V4 if value.version == 4 else TS_V6)
    except ValueError:
        return False


def _url(address: str, port: int) -> str:
    return f"https://{'[' + address + ']' if ':' in address else address}:{port}"


def parse_ip(value: object) -> list[dict]:
    if not isinstance(value, list):
        raise DiscoveryCommandError("invalid")
    result = {}
    for row in value[:256]:
        if not isinstance(row, dict):
            continue
        name = row.get("ifname", "")
        flags = row.get("flags", [])
        info = row.get("linkinfo") or {}
        if (not isinstance(name, str) or not PHYSICAL.fullmatch(name)
                or not isinstance(flags, list) or "UP" not in flags or "LOOPBACK" in flags
                or row.get("operstate") not in ("UP", "UNKNOWN")
                or row.get("link_type", "ether") != "ether"
                or not isinstance(info, dict) or info.get("info_kind") in BAD_KINDS):
            continue
        addresses = row.get("addr_info", [])
        if not isinstance(addresses, list):
            continue
        for entry in addresses[:64]:
            if not isinstance(entry, dict) or entry.get("scope") != "global":
                continue
            address = entry.get("local")
            if (not isinstance(address, str) or "%" in address or not _lan(address)
                    or entry.get("tentative") or entry.get("dadfailed") or entry.get("deprecated")
                    or entry.get("valid_life_time") == 0 or entry.get("preferred_life_time") == 0
                    or not isinstance(entry.get("flags", []), list)
                    or any(f in entry.get("flags", []) for f in ("tentative", "dadfailed", "deprecated"))):
                continue
            parsed = ipaddress.ip_address(address)
            result[(parsed.version, int(parsed), name)] = {
                "address": str(parsed), "kind": "lan", "interface": name,
                **({"interface_index": row["ifindex"]} if isinstance(row.get("ifindex"), int) and row["ifindex"] > 0 else {})}
    return [result[key] for key in sorted(result)][:64]


def parse_tailscale(value: object) -> tuple[list[dict], str | None, bool]:
    if not isinstance(value, dict):
        raise DiscoveryCommandError("invalid")
    if value.get("BackendState") != "Running":
        return [], None, False
    own = value.get("Self")
    if not isinstance(own, dict):
        raise DiscoveryCommandError("invalid")
    values = own.get("TailscaleIPs", [])
    if not isinstance(values, list):
        raise DiscoveryCommandError("invalid")
    addresses = {}
    for text in values[:16]:
        if isinstance(text, str) and "%" not in text and _tailscale(text):
            parsed = ipaddress.ip_address(text)
            addresses[(parsed.version, int(parsed))] = {"address": str(parsed), "kind": "tailscale"}
    name = own.get("DNSName", "")
    dns = None
    if isinstance(name, str) and len(name) <= 254:
        name = name.rstrip(".").lower()
        if (name.endswith(".ts.net") and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in name.split("."))):
            dns = name
    return [addresses[key] for key in sorted(addresses)], dns, True


class AddressDiscovery:
    def __init__(self, tls_port: int = 32851,
                 runner: Callable[[tuple[str, ...]], Awaitable[bytes]] | None = None,
                 clock: Callable[[], float] = time.monotonic, cache_ttl: float = 120,
                 platform: str | None = None):
        if type(tls_port) is not int or not 1 <= tls_port <= 65535:
            raise ValueError("invalid TLS port")
        self.tls_port, self.runner = tls_port, runner or run_local
        self.platform = platform
        self._lock = asyncio.Lock()
        self._clock, self._cache_ttl = clock, min(300, max(0, cache_ttl))
        self._cache: dict[str, tuple[float, object]] = {}

    async def _read(self, argv: tuple[str, ...], label: str) -> tuple[object | None, str | None]:
        try:
            async with asyncio.timeout(COMMAND_TIMEOUT + .2):
                raw = await self.runner(argv)
            if not isinstance(raw, bytes) or len(raw) > MAX_OUTPUT:
                raise DiscoveryCommandError("too_large")
            return json.loads(raw), None
        except Exception as error:
            kind = error.kind if isinstance(error, DiscoveryCommandError) else "timeout" if isinstance(error, TimeoutError) else "unavailable" if isinstance(error, (PermissionError, OSError)) else "invalid"
            descriptions = {"missing": "工具未安装", "timeout": "读取超时", "too_large": "返回内容过大", "unavailable": "暂时无法读取", "invalid": "返回格式暂不支持"}
            return None, f"{label}：{descriptions.get(kind, '暂时无法读取')}；保留手动线路设置。"

    async def snapshot(self) -> DiscoverySnapshot:
        async with self._lock:
            if capabilities(self.platform)['lan_discovery'] == 'linux-ip':
                (ip, ip_error), (ts, ts_error) = await asyncio.gather(
                    self._read(("ip", "-j", "address"), "本机局域网"),
                    self._read(("tailscale", "status", "--json", "--peers=false"), "Tailscale"))
            else:
                ip = None
                ip_error = '本平台自动局域网发现尚在适配；可在电脑管理页手动添加线路。'
                ts, ts_error = await self._read(("tailscale", "status", "--json", "--peers=false"), "Tailscale")
            diagnostics = [e for e in (ip_error, ts_error) if e]
            lan, tail, dns = [], [], None
            if ip is not None:
                try: lan = parse_ip(ip)
                except DiscoveryCommandError:
                    ip_error = "invalid"
                    diagnostics.append("本机局域网：返回格式暂不支持；保留手动线路设置。")
            if ts is not None:
                try:
                    tail, dns, running = parse_tailscale(ts)
                    if not running: diagnostics.append("Tailscale 尚未运行；可继续使用局域网或手动线路。")
                except DiscoveryCommandError:
                    ts_error = "invalid"
                    diagnostics.append("Tailscale：返回格式暂不支持；保留手动线路设置。")
            # A transient command failure does not immediately erase working routes.
            # Cache only sanitized projections, and never mask authoritative down/empty.
            for source, good, data in (("lan", ip is not None and not ip_error, lan),
                                       ("tailscale", ts is not None and not ts_error, (tail, dns))):
                if good:
                    self._cache[source] = (self._clock(), copy.deepcopy(data))
                elif source in self._cache:
                    at, old = self._cache[source]
                    if self._clock() - at <= self._cache_ttl:
                        if source == "lan": lan = copy.deepcopy(old)
                        else: tail, dns = copy.deepcopy(old)
                        diagnostics.append(("本机局域网" if source == "lan" else "Tailscale") + "：暂用最近成功地址，仍需确认监听与身份。")
            candidates = []
            if dns:
                candidates.append((f"https://{dns}:{self.tls_port}", "Tailscale 名称", "tailscale"))
            candidates += [(_url(row["address"], self.tls_port), "局域网", "lan") for row in lan[:1]]
            candidates += [(_url(row["address"], self.tls_port), "Tailscale 地址", "tailscale") for row in tail]
            candidates += [(_url(row["address"], self.tls_port), "局域网", "lan") for row in lan[1:]]
            endpoints = {}
            for url, label, kind in candidates:
                endpoints.setdefault(url, {"url": url, "label": label, "kind": kind, "tls": "pinned", "managed": True, "source": "auto"})
            addresses = list({row["address"]: row for row in lan + tail}.values())
            return DiscoverySnapshot(addresses, list(endpoints.values())[:6], diagnostics)


class MDNSAdvertiser:
    def __init__(self, *, zeroconf_factory=None, info_factory=None):
        self._factory, self._info_factory = zeroconf_factory, info_factory
        self._zc, self._info, self._signature = None, None, None
        self._lock = asyncio.Lock()

    async def _close(self) -> list[str]:
        zc, info = self._zc, self._info
        self._zc = self._info = self._signature = None
        errors = []
        if zc:
            if info:
                try:
                    async with asyncio.timeout(2):
                        pending = await zc.async_unregister_service(info)
                        if inspect.isawaitable(pending): await pending
                except Exception: errors.append("局域网发现：旧广播注销未确认。")
            try:
                async with asyncio.timeout(2): await zc.async_close()
            except Exception: errors.append("局域网发现：广播连接关闭未确认。")
        return errors

    async def close(self) -> list[str]:
        async with self._lock:
            return await self._close()

    async def update(self, server_id: str, addresses: list[dict], tls_port: int,
                     enabled: bool = True) -> list[str]:
        async with self._lock:
            if not enabled:
                return await self._close()
            if not isinstance(server_id, str) or not re.fullmatch(r"[0-9a-f]{64}", server_id) or type(tls_port) is not int or not 1 <= tls_port <= 65535:
                return await self._close() + ["局域网发现：服务身份或端口无效，未广播。"]
            # Caller supplies only successfully bound addresses. Recheck LAN and physical
            # interface, never default to all interfaces or include Tailscale multicast.
            lan = [row for row in addresses if isinstance(row, dict) and row.get("kind") == "lan"
                   and isinstance(row.get("address"), str) and _lan(row["address"])
                   and isinstance(row.get("interface"), str) and PHYSICAL.fullmatch(row["interface"])]
            by_ip = {row["address"]: row for row in lan}
            lan = [by_ip[key] for key in sorted(by_ip)]
            if not lan:
                return await self._close()
            packed = [ipaddress.ip_address(row["address"]).packed for row in lan]
            interfaces = list(dict.fromkeys(row["address"] if ":" not in row["address"] else row.get("interface_index") for row in lan))
            interfaces = [value for value in interfaces if value is not None]
            if not interfaces:
                return ["局域网发现：缺少可用接口编号，未广播。"]
            signature = (server_id, tuple(row["address"] for row in lan), tuple(interfaces), tls_port)
            if signature == self._signature:
                return []
            diagnostics = await self._close()
            try:
                if self._factory is None:
                    from zeroconf import IPVersion
                    from zeroconf.asyncio import AsyncZeroconf, AsyncServiceInfo
                    factory, info_factory = AsyncZeroconf, AsyncServiceInfo
                    options = {"interfaces": interfaces, "ip_version": IPVersion.All}
                else:
                    factory, info_factory = self._factory, self._info_factory
                    options = {"interfaces": interfaces}
                self._zc = factory(**options)
                name = f"Codex-Pocket-{server_id[:8]}"
                self._info = info_factory(SERVICE, name + "." + SERVICE,
                    addresses=packed, port=tls_port, server=name.lower() + ".local.",
                    properties={"id": server_id, "v": "1", "tlsport": str(tls_port)}, host_ttl=30, other_ttl=30)
                async with asyncio.timeout(2):
                    pending = await self._zc.async_register_service(self._info, allow_name_change=False)
                    if inspect.isawaitable(pending): await pending
                self._signature = signature
            except asyncio.CancelledError:
                await self._close()
                raise
            except Exception:
                diagnostics += ["局域网发现：广播暂不可用，可继续扫码或手动连接。"]
                diagnostics += await self._close()
            return diagnostics
