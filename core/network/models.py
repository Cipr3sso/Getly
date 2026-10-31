from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class NetworkAdapter:
    """Represents a physical or virtual network interface on the host machine."""
    name: str
    ip: str
    netmask: Optional[str] = None
    if_index: Optional[int] = None
    ipv6_if_index: Optional[int] = None
    is_up: bool = True
    speed_mbps: Optional[int] = None
    friendly_name: Optional[str] = None
    description: Optional[str] = None
    gateway: Optional[str] = None
    mac_address: Optional[str] = None
    is_loopback: bool = False
    is_wireless: bool = False
    is_primary: bool = False
    is_vpn: bool = False

    def __repr__(self) -> str:
        status = "UP" if self.is_up else "DOWN"
        idx_info = f", if_index={self.if_index}" if self.if_index is not None else ""
        gw_info = f", gw={self.gateway}" if self.gateway else ""
        prim_info = ", PRIMARY" if self.is_primary else ""
        vpn_info = ", VPN" if self.is_vpn else ""
        return (
            f"<NetworkAdapter name='{self.name}' friendly='{self.friendly_name}' "
            f"ip='{self.ip}' status={status}{idx_info}{gw_info}{prim_info}{vpn_info}>"
        )
