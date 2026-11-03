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


@pytest.mark.asyncio
async def test_session_pool_refresh_adapters_excludes_virtual():
    pool = MultiInterfaceSessionPool()
    refreshed = pool.refresh_adapters()
    for ad in refreshed:
        assert not ad.is_loopback
        assert not ad.ip.startswith("169.254.")
        assert not ad.ip.startswith("127.")
        assert ad.is_up
        assert not InterfaceDetector._is_excluded_adapter(ad)


@pytest.mark.asyncio
async def test_session_pool_dynamic_vpn_adapter_addition(monkeypatch):
    """
    Verifies that when a VPN interface (like NordLynx) appears dynamically after boot,
    refresh_adapters() creates its ClientSession and does NOT throw KeyError: 'NordLynx'
    when get_primary_adapter_and_session() or get_next_adapter_and_session() is called.
    """
    initial_ad = NetworkAdapter(
        name="Wi-Fi 3",
        friendly_name="Wi-Fi",
        ip="192.168.1.89",
        is_up=True,
        gateway="192.168.1.1",
        is_primary=True,
        is_vpn=False,
    )
    vpn_ad = NetworkAdapter(
        name="NordLynx",
        friendly_name="NordLynx",
        ip="10.5.0.2",
        is_up=True,
        gateway="10.5.0.1",
        is_primary=False,
        is_vpn=True,
    )

    current_detected = [initial_ad]
    monkeypatch.setattr(InterfaceDetector, "get_active_internet_adapters", lambda: list(current_detected))

    pool = MultiInterfaceSessionPool(adapters=[initial_ad], vpn_shield_enabled=True)
    await pool.initialize()
    assert pool.is_initialized
    assert "Wi-Fi 3" in pool._sessions
    assert "NordLynx" not in pool._sessions

    # VPN connects while app is running
    current_detected.append(vpn_ad)
    refreshed = pool.refresh_adapters()

    assert any(a.name == "NordLynx" for a in refreshed)
    # VPN Shield is enabled, so active_adapters strictly isolates traffic to VPN!
    assert pool.active_adapters == [vpn_ad]

    # Verify session for NordLynx was dynamically created and does NOT throw KeyError
    primary_ad, primary_sess = pool.get_primary_adapter_and_session()
    assert primary_ad.name == "NordLynx"
    assert primary_sess is not None
    assert not primary_sess.closed

    next_ad, next_sess = pool.get_next_adapter_and_session()
    assert next_ad.name == "NordLynx"
    assert next_sess is not None

    named_sess = pool.get_session_by_name("NordLynx")
    assert named_sess is not None
    assert not named_sess.closed

    await pool.close()
    assert primary_sess.closed


@pytest.mark.asyncio
async def test_session_pool_lazy_session_creation():
    """Verifies _get_or_create_session creates session on-demand if missing from _sessions."""
    ad = NetworkAdapter(name="Ethernet Test", ip="192.168.1.200", is_up=True)
    pool = MultiInterfaceSessionPool(adapters=[ad])
    await pool.initialize()

    # Intentionally remove session to test lazy creation fallback
    del pool._sessions["Ethernet Test"]
    assert "Ethernet Test" not in pool._sessions

    sess = pool.get_session_by_name("Ethernet Test")
    assert sess is not None
    assert not sess.closed
    assert "Ethernet Test" in pool._sessions

    await pool.close()
    assert sess.closed

