import socket
import struct
import sys
import logging
from typing import Callable, Tuple, Any, Optional

from .models import NetworkAdapter

logger = logging.getLogger(__name__)

# Winsock constants
IP_UNICASTIF = 31       # Option on IPPROTO_IP
IPV6_UNICASTIF = 31     # Option on IPPROTO_IPV6

# Linux constants
SO_BINDTODEVICE = 25    # Option on SOL_SOCKET


class SocketBinder:
    """Manages low-level socket binding and OS-specific interface steering."""

    @staticmethod
    def apply_interface_binding(sock: socket.socket, adapter: NetworkAdapter) -> None:
        """
        Applies OS-specific interface-binding options to a raw socket before connection.
        - On Windows: Sets IP_UNICASTIF with the adapter's interface index.
        - On Linux: Sets SO_BINDTODEVICE with the adapter's interface name.
        """
        family = sock.family

        if sys.platform == "win32":
            if family == socket.AF_INET and adapter.if_index is not None:
                try:
                    # Winsock expects interface index in network byte order (big-endian)
                    if_index_bytes = struct.pack("!I", adapter.if_index)
                    sock.setsockopt(socket.IPPROTO_IP, IP_UNICASTIF, if_index_bytes)
                    logger.debug(
                        "Bound socket to Windows interface %s (IfIndex=%d) via IP_UNICASTIF",
                        adapter.name,
                        adapter.if_index,
                    )
                except OSError as exc:
                    logger.warning(
                        "Failed to set IP_UNICASTIF on Windows for adapter %s: %s",
                        adapter.name,
                        exc,
                    )
            elif family == socket.AF_INET6 and adapter.ipv6_if_index is not None:
                try:
                    if_index_bytes = struct.pack("!I", adapter.ipv6_if_index)
                    sock.setsockopt(socket.IPPROTO_IPV6, IPV6_UNICASTIF, if_index_bytes)
                    logger.debug(
                        "Bound socket to Windows IPv6 interface %s (IfIndex=%d) via IPV6_UNICASTIF",
                        adapter.name,
                        adapter.ipv6_if_index,
                    )
                except OSError as exc:
                    logger.warning(
                        "Failed to set IPV6_UNICASTIF on Windows for adapter %s: %s",
                        adapter.name,
                        exc,
                    )
        elif sys.platform.startswith("linux"):
            if hasattr(socket, "SO_BINDTODEVICE"):
                try:
                    device_bytes = adapter.name.encode() + b"\0"
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, device_bytes)
                    logger.debug(
                        "Bound socket to Linux interface %s via SO_BINDTODEVICE",
                        adapter.name,
                    )
                except PermissionError:
                    logger.warning(
                        "SO_BINDTODEVICE requires CAP_NET_RAW / root on Linux. "
                        "Falling back to source IP routing."
                    )
                except OSError as exc:
                    logger.warning(
                        "SO_BINDTODEVICE failed for adapter %s: %s",
                        adapter.name,
                        exc,
                    )

    @classmethod
    def create_socket_factory(
        cls, adapter: NetworkAdapter
    ) -> Callable[[Tuple[int, int, int, str, Any]], socket.socket]:
        """
        Creates a socket factory function compatible with aiohttp / aiohappyeyeballs.
        Takes an addr_info tuple (family, type, proto, canonname, sockaddr) and
        returns a non-blocking socket pre-configured with interface binding.
        """
        def factory(addr_info: Tuple[int, int, int, str, Any]) -> socket.socket:
            family, type_, proto, _canonname, _sockaddr = addr_info
            # Force AF_INET for IPv4 adapters to prevent unroutable IPv6 queries on dual-stack CDN hosts
            sock_family = socket.AF_INET if (adapter.ip and ":" not in adapter.ip) else family
            sock = socket.socket(sock_family, type_, proto)
            sock.setblocking(False)
            cls.apply_interface_binding(sock, adapter)
            return sock

        return factory

    @classmethod
    def create_bound_socket(
        cls, adapter: NetworkAdapter, family: int = socket.AF_INET
    ) -> socket.socket:
        """Creates a standalone socket bound to both the interface and the local IP."""
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.setblocking(False)
        cls.apply_interface_binding(sock, adapter)
        sock.bind((adapter.ip, 0))
        return sock
