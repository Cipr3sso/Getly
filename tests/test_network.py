import pytest
import socket
import sys
from core.network.models import NetworkAdapter
from core.network.interface_detector import InterfaceDetector
from core.network.socket_binder import SocketBinder
from core.network.session_pool import MultiInterfaceSessionPool


def test_interface_detector_detects_adapters():
    adapters = InterfaceDetector.detect_all_adapters()
    assert isinstance(adapters, list)
    assert len(adapters) > 0

    # There must be at least one adapter with a valid IP
    ips = [a.ip for a in adapters if a.ip]
    assert len(ips) > 0


def test_active_internet_adapters():
    active = InterfaceDetector.get_active_internet_adapters()
    assert isinstance(active, list)
    for ad in active:
        assert not ad.is_loopback
        assert not ad.ip.startswith("169.254.")
        assert ad.is_up
        assert ad.gateway is not None
        assert ad.gateway != "0.0.0.0"
        assert not InterfaceDetector._is_excluded_adapter(ad)


def test_strict_virtual_and_gateway_filtering():
    # Verify exclusion patterns
    virtual_names = [
        "vEthernet (Default Switch)",
        "VMware Network Adapter VMnet1",
        "VirtualBox Host-Only Ethernet Adapter",
        "Bluetooth Device (Personal Area Network)",
        "Loopback Pseudo-Interface 1",
        "WSL (Hyper-V firewall)",
    ]
    for vname in virtual_names:
        fake_ad = NetworkAdapter(name=vname, ip="192.168.10.5", is_up=True, gateway="192.168.10.1")
        assert InterfaceDetector._is_excluded_adapter(fake_ad)

    # Valid physical adapters
    valid_ad = NetworkAdapter(name="Wi-Fi 3", ip="192.168.1.89", is_up=True, gateway="192.168.1.1")
    assert not InterfaceDetector._is_excluded_adapter(valid_ad)

    # Test filtering logic with mock adapters
    adapters = [
        NetworkAdapter(name="VMware Adapter", ip="192.168.1.10", is_up=True, gateway="192.168.1.1"),
        NetworkAdapter(name="Ethernet 2", ip="192.168.2.10", is_up=True, gateway=None),
        NetworkAdapter(name="Ethernet 3", ip="192.168.3.10", is_up=True, gateway="0.0.0.0"),
        NetworkAdapter(name="Down Adapter", ip="192.168.4.10", is_up=False, gateway="192.168.4.1"),
        NetworkAdapter(name="Wi-Fi", ip="192.168.1.50", is_up=True, gateway="192.168.1.1"),
        NetworkAdapter(name="Cellular Tethering", ip="192.168.43.100", is_up=True, gateway="192.168.43.1"),
    ]

    # Manual application of the filter
    filtered = [
        ad for ad in adapters
        if ad.is_up
        and not ad.is_loopback
        and ad.ip and not ad.ip.startswith("169.254.")
        and ad.gateway and ad.gateway.strip() not in ("", "0.0.0.0")
        and not InterfaceDetector._is_excluded_adapter(ad)
    ]
    assert len(filtered) == 2
    names = [a.name for a in filtered]
    assert "Wi-Fi" in names
    assert "Cellular Tethering" in names


def test_socket_binder_factory():
    dummy_adapter = NetworkAdapter(
        name="test_eth",
        friendly_name="Test Adapter",
        ip="127.0.0.1",
        if_index=1,
    )
    factory = SocketBinder.create_socket_factory(dummy_adapter)
    addr_info = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 80))
    sock = factory(addr_info)
    assert isinstance(sock, socket.socket)
    sock.close()


@pytest.mark.asyncio
async def test_session_pool_lifecycle():
    pool = MultiInterfaceSessionPool()
    await pool.initialize()
    assert pool.is_initialized
    assert len(pool.active_adapters) > 0

    adapter, session = pool.get_next_adapter_and_session()
    assert adapter is not None
    assert session is not None
    assert not session.closed
    # Ensure IPv4 is strictly forced on connectors
    assert session.connector._family == socket.AF_INET

    # Test primary adapter lookup
    primary_ad, primary_sess = pool.get_primary_adapter_and_session()
    assert primary_ad is not None
    assert primary_sess is not None

    # Test round-robin
    adapter2, session2 = pool.get_next_adapter_and_session()
    assert adapter2 is not None
    assert session2 is not None

    await pool.close()
    assert not pool.is_initialized
    assert session.closed
