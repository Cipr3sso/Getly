import sys
import socket
import logging
from typing import List, Optional
import psutil

from .models import NetworkAdapter

logger = logging.getLogger(__name__)

# Windows Constants for iphlpapi.GetAdaptersAddresses
GAA_FLAG_SKIP_ANYCAST = 0x0002
GAA_FLAG_SKIP_MULTICAST = 0x0004
GAA_FLAG_SKIP_DNS_SERVER = 0x0008
GAA_FLAG_INCLUDE_GATEWAYS = 0x0080
AF_INET = 2  # IPv4
IF_TYPE_ETHERNET_CSMACD = 6
IF_TYPE_IEEE80211 = 71
IF_OPER_STATUS_UP = 1


# Excluded virtual, container, or non-internet adapter name patterns
EXCLUDED_NAME_PATTERNS = [
    "vethernet",
    "vmware",
    "virtualbox",
    "loopback",
    "bluetooth",
    "default switch",
    "wsl",
]

# Patterns identifying VPN / Tunnel adapters
VPN_NAME_PATTERNS = [
    "nordlynx",
    "wireguard",
    "tun",
    "tap",
    "vpn",
    "mullvad",
    "proton",
]


class InterfaceDetector:
    """Detects and enumerates active local network interfaces across Windows and Linux."""

    @classmethod
    def is_vpn_adapter(
        cls,
        name: Optional[str] = None,
        friendly_name: Optional[str] = None,
        description: Optional[str] = None,
        ip: Optional[str] = None,
    ) -> bool:
        """Determines if an adapter is a VPN or encrypted tunnel interface."""
        combined = f"{name or ''} {friendly_name or ''} {description or ''}".lower()
        if any(pattern in combined for pattern in VPN_NAME_PATTERNS):
            return True
        if ip and ip.startswith("10.5.0."):
            return True
        return False

    @classmethod
    def detect_all_adapters(cls) -> List[NetworkAdapter]:
        """Detect all network adapters regardless of internet routability."""
        if sys.platform == "win32":
            adapters = cls._detect_windows_adapters()
            if adapters:
                return adapters
        return cls._detect_psutil_adapters()

    @staticmethod
    def get_system_primary_ip() -> Optional[str]:
        """
        Determines the system's primary IPv4 address used by the kernel routing table
        for outbound internet traffic (lowest metric route).
        Uses a connectionless UDP socket connect query (no traffic is sent).
        """
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return None

    @classmethod
    def _is_excluded_adapter(cls, adapter: NetworkAdapter) -> bool:
        """Checks if the adapter name, friendly_name, or description matches virtual patterns."""
        if getattr(adapter, "is_vpn", False):
            return False
        combined = f"{adapter.name or ''} {adapter.friendly_name or ''} {adapter.description or ''}".lower()
        if cls.is_vpn_adapter(adapter.name, adapter.friendly_name, adapter.description, adapter.ip):
            return False
        return any(pattern in combined for pattern in EXCLUDED_NAME_PATTERNS)

    @classmethod
    def get_active_internet_adapters(cls) -> List[NetworkAdapter]:
        """
        Returns active, operational physical or VPN network adapters valid for traffic.
        """
        all_adapters = cls.detect_all_adapters()
        primary_ip = cls.get_system_primary_ip()
        active: List[NetworkAdapter] = []

        for ad in all_adapters:
            # 1. State must be UP and not loopback
            if not ad.is_up or ad.is_loopback:
                continue

            # 2. IP must be valid and not APIPA
            if not ad.ip or ad.ip == "0.0.0.0" or ad.ip.startswith("169.254."):
                continue

            # 3. For non-VPN adapters, MUST have a valid Default Gateway
            if not ad.is_vpn:
                if not ad.gateway or ad.gateway.strip() in ("", "0.0.0.0"):
                    continue
                # 4. Exclude virtual/container interfaces
                if cls._is_excluded_adapter(ad):
                    continue

            # Mark primary adapter
            if primary_ip and ad.ip == primary_ip:
                ad.is_primary = True

            active.append(ad)

        # Sort: VPN interfaces or primary first, then speed
        active.sort(key=lambda a: (a.is_vpn, a.is_primary, a.speed_mbps or 0), reverse=True)
        return active

    @classmethod
    def _detect_windows_adapters(cls) -> List[NetworkAdapter]:
        """Native Windows detection via iphlpapi.GetAdaptersAddresses using ctypes."""
        import ctypes
        from ctypes import wintypes

        class SOCKET_ADDRESS(ctypes.Structure):
            _fields_ = [
                ("lpSockaddr", ctypes.c_void_p),
                ("iSockaddrLength", wintypes.INT),
            ]

        class sockaddr_in(ctypes.Structure):
            _fields_ = [
                ("sin_family", ctypes.c_short),
                ("sin_port", ctypes.c_ushort),
                ("sin_addr", ctypes.c_byte * 4),
                ("sin_zero", ctypes.c_char * 8),
            ]

        class IP_ADAPTER_UNICAST_ADDRESS(ctypes.Structure):
            pass

        IP_ADAPTER_UNICAST_ADDRESS._fields_ = [
            ("Length", wintypes.ULONG),
            ("Flags", wintypes.DWORD),
            ("Next", ctypes.POINTER(IP_ADAPTER_UNICAST_ADDRESS)),
            ("Address", SOCKET_ADDRESS),
        ]

        class IP_ADAPTER_GATEWAY_ADDRESS(ctypes.Structure):
            pass

        IP_ADAPTER_GATEWAY_ADDRESS._fields_ = [
            ("Length", wintypes.ULONG),
            ("Reserved", wintypes.DWORD),
            ("Next", ctypes.POINTER(IP_ADAPTER_GATEWAY_ADDRESS)),
            ("Address", SOCKET_ADDRESS),
        ]

        class IP_ADAPTER_ADDRESSES(ctypes.Structure):
            pass

        IP_ADAPTER_ADDRESSES._fields_ = [
            ("Length", wintypes.ULONG),
            ("IfIndex", wintypes.DWORD),
            ("Next", ctypes.POINTER(IP_ADAPTER_ADDRESSES)),
            ("AdapterName", ctypes.c_char_p),
            ("FirstUnicastAddress", ctypes.POINTER(IP_ADAPTER_UNICAST_ADDRESS)),
            ("FirstAnycastAddress", ctypes.c_void_p),
            ("FirstMulticastAddress", ctypes.c_void_p),
            ("FirstDnsServerAddress", ctypes.c_void_p),
            ("DnsSuffix", wintypes.LPWSTR),
            ("Description", wintypes.LPWSTR),
            ("FriendlyName", wintypes.LPWSTR),
            ("PhysicalAddress", ctypes.c_byte * 8),
            ("PhysicalAddressLength", wintypes.DWORD),
            ("Flags", wintypes.DWORD),
            ("Mtu", wintypes.DWORD),
            ("IfType", wintypes.DWORD),
            ("OperStatus", wintypes.DWORD),
            ("Ipv6IfIndex", wintypes.DWORD),
            ("ZoneIndices", wintypes.DWORD * 16),
            ("FirstPrefix", ctypes.c_void_p),
            ("TransmitLinkSpeed", ctypes.c_uint64),
            ("ReceiveLinkSpeed", ctypes.c_uint64),
            ("FirstWinsServerAddress", ctypes.c_void_p),
            ("FirstGatewayAddress", ctypes.POINTER(IP_ADAPTER_GATEWAY_ADDRESS)),
        ]

        iphlpapi = ctypes.windll.iphlpapi
        out_buf_len = wintypes.ULONG(16384)
        buf = ctypes.create_string_buffer(out_buf_len.value)
        flags = (
            GAA_FLAG_SKIP_ANYCAST
            | GAA_FLAG_SKIP_MULTICAST
            | GAA_FLAG_SKIP_DNS_SERVER
            | GAA_FLAG_INCLUDE_GATEWAYS
        )

        ret = iphlpapi.GetAdaptersAddresses(
            AF_INET, flags, None, buf, ctypes.byref(out_buf_len)
        )
        if ret == 111:  # ERROR_BUFFER_OVERFLOW
            buf = ctypes.create_string_buffer(out_buf_len.value)
            ret = iphlpapi.GetAdaptersAddresses(
                AF_INET, flags, None, buf, ctypes.byref(out_buf_len)
            )

        if ret != 0:
            logger.warning("GetAdaptersAddresses returned error code: %d", ret)
            return []

        adapters: List[NetworkAdapter] = []
        p_adapter = ctypes.cast(buf, ctypes.POINTER(IP_ADAPTER_ADDRESSES))

        while p_adapter:
            ad = p_adapter.contents
            friendly_name = ad.FriendlyName or ""
            desc = ad.Description or ""
            adapter_guid = ad.AdapterName.decode("latin-1", errors="ignore") if ad.AdapterName else ""
            if_index = int(ad.IfIndex)
            ipv6_if_index = int(ad.Ipv6IfIndex)
            is_up = bool(ad.OperStatus == IF_OPER_STATUS_UP)
            is_wireless = bool(ad.IfType == IF_TYPE_IEEE80211)
            speed_mbps = int(ad.ReceiveLinkSpeed / 1_000_000) if ad.ReceiveLinkSpeed else None

            # Extract Gateway IP
            gateway_ip: Optional[str] = None
            gw = ad.FirstGatewayAddress
            while gw:
                gw_sa_ptr = gw.contents.Address.lpSockaddr
                if gw_sa_ptr:
                    sa = ctypes.cast(gw_sa_ptr, ctypes.POINTER(sockaddr_in)).contents
                    gateway_ip = socket.inet_ntoa(bytes(sa.sin_addr))
                    break
                gw = gw.contents.Next

            # Extract Unicast IPv4 addresses
            unicast = ad.FirstUnicastAddress
            while unicast:
                sa_ptr = unicast.contents.Address.lpSockaddr
                if sa_ptr:
                    sa = ctypes.cast(sa_ptr, ctypes.POINTER(sockaddr_in)).contents
                    ip_str = socket.inet_ntoa(bytes(sa.sin_addr))
                    is_loopback = ip_str.startswith("127.")
                    is_vpn = cls.is_vpn_adapter(name=friendly_name or adapter_guid, friendly_name=friendly_name, description=desc, ip=ip_str)

                    adapters.append(
                        NetworkAdapter(
                            name=friendly_name or adapter_guid,
                            friendly_name=friendly_name,
                            description=desc,
                            ip=ip_str,
                            if_index=if_index,
                            ipv6_if_index=ipv6_if_index,
                            is_up=is_up,
                            speed_mbps=speed_mbps,
                            gateway=gateway_ip,
                            is_loopback=is_loopback,
                            is_wireless=is_wireless,
                            is_vpn=is_vpn,
                        )
                    )
                unicast = unicast.contents.Next

            p_adapter = ad.Next

        return adapters

    @classmethod
    def _get_linux_gateways(cls) -> dict:
        gateways = {}
        try:
            with open("/proc/net/route", "r") as f:
                for line in f.readlines()[1:]:
                    fields = line.strip().split()
                    if len(fields) >= 3 and fields[1] == "00000000":
                        ifname = fields[0]
                        gw_hex = fields[2]
                        gw_bytes = bytes.fromhex(gw_hex)
                        gw_ip = socket.inet_ntoa(gw_bytes[::-1])
                        gateways[ifname] = gw_ip
        except Exception:
            pass
        return gateways

    @classmethod
    def _detect_psutil_adapters(cls) -> List[NetworkAdapter]:
        """Fallback adapter detection using psutil for Linux / POSIX."""
        adapters: List[NetworkAdapter] = []
        stats = psutil.net_if_stats()
        addrs = psutil.net_if_addrs()
        linux_gws = cls._get_linux_gateways()

        for if_name, addr_list in addrs.items():
            if_stat = stats.get(if_name)
            is_up = if_stat.isup if if_stat else True
            speed_mbps = if_stat.speed if (if_stat and if_stat.speed > 0) else None
            gw = linux_gws.get(if_name)

            mac_address = None
            for addr in addr_list:
                if addr.family == psutil.AF_LINK:
                    mac_address = addr.address

            for addr in addr_list:
                if addr.family == socket.AF_INET:
                    ip_str = addr.address
                    is_loopback = ip_str.startswith("127.")
                    is_vpn = cls.is_vpn_adapter(name=if_name, friendly_name=if_name, ip=ip_str)
                    adapters.append(
                        NetworkAdapter(
                            name=if_name,
                            friendly_name=if_name,
                            ip=ip_str,
                            netmask=addr.netmask,
                            is_up=is_up,
                            speed_mbps=speed_mbps,
                            gateway=gw,
                            mac_address=mac_address,
                            is_loopback=is_loopback,
                            is_vpn=is_vpn,
                        )
                    )
        return adapters
