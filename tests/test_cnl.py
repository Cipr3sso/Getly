import base64
import pytest
import aiohttp
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.backends import default_backend

from core.cnl.models import CNL2Payload, CNLDecryptedPackage
from core.cnl.decryptor import CNLDecryptor, CNLDecryptionError
from core.cnl.server import CNLServer


def helper_encrypt_cnl2(hex_key: str, urls: list[str]) -> str:
    """Helper function to create a valid CNL2 ciphertext."""
    key = bytes.fromhex(hex_key)
    iv = key
    raw_bytes = "\r\n".join(urls).encode("utf-8")

    padder = padding.PKCS7(128).padder()
    padded_data = padder.update(raw_bytes) + padder.finalize()

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded_data) + encryptor.finalize()
    return base64.b64encode(ciphertext).decode("ascii")


def test_jk_evaluation_standard():
    jk_code = "function f() { return '0123456789abcdef0123456789abcdef'; }"
    key = CNLDecryptor.evaluate_jk(jk_code)
    assert key.lower() == "0123456789abcdef0123456789abcdef"


def test_jk_evaluation_obfuscated():
    jk_code = """
    var a = '0123456789';
    var b = 'abcdef';
    function f() {
        return a + b + a + b;
    }
    """
    key = CNLDecryptor.evaluate_jk(jk_code)
    assert key.lower() == "0123456789abcdef0123456789abcdef"


def test_jk_evaluation_jk_function():
    jk_code = "function jk() { return '11223344556677889900aabbccddeeff'; }"
    key = CNLDecryptor.evaluate_jk(jk_code)
    assert key.lower() == "11223344556677889900aabbccddeeff"


def test_decrypt_payload_success():
    hex_key = "0123456789abcdef0123456789abcdef"
    expected_urls = [
        "https://rapidgator.net/file/12345/part1.rar.html",
        "https://rapidgator.net/file/67890/part2.rar.html",
    ]
    crypted_b64 = helper_encrypt_cnl2(hex_key, expected_urls)

    payload = CNL2Payload(
        crypted=crypted_b64,
        jk=f"function f() {{ return '{hex_key}'; }}",
        package="TestPackage",
        passwords="secretpassword",
        source="https://filecrypt.co/Container/ABCDEF.html",
    )

    result = CNLDecryptor.decrypt(payload)
    assert isinstance(result, CNLDecryptedPackage)
    assert result.package_name == "TestPackage"
    assert result.source_url == "https://filecrypt.co/Container/ABCDEF.html"
    assert result.passwords == ["secretpassword"]
    assert result.urls == expected_urls


def test_decrypt_invalid_key_raises_error():
    payload = CNL2Payload(
        crypted="AAAA",
        jk="function f() { return 'INVALID_NOT_HEX'; }",
    )
    with pytest.raises(CNLDecryptionError):
        CNLDecryptor.decrypt(payload)


@pytest.mark.asyncio
async def test_cnl_server_endpoints():
    received_packages: list[CNLDecryptedPackage] = []

    async def on_received(pkg: CNLDecryptedPackage):
        received_packages.append(pkg)

    # Use a non-standard port for testing
    test_port = 19666
    server = CNLServer(host="127.0.0.1", port=test_port, on_package_received=on_received)

    async with server:
        assert server.is_running
        async with aiohttp.ClientSession() as client:
            # 1. Test GET /flash/check
            async with client.get(f"http://127.0.0.1:{test_port}/flash/check") as resp:
                assert resp.status == 200
                text = await resp.text()
                assert "success" in text
                assert resp.headers.get("Access-Control-Allow-Origin") == "*"

            # 2. Test OPTIONS preflight
            async with client.options(f"http://127.0.0.1:{test_port}/flash/addcrypted2") as resp:
                assert resp.status == 200
                assert resp.headers.get("Access-Control-Allow-Origin") == "*"
                assert "POST" in resp.headers.get("Access-Control-Allow-Methods", "")

            # 3. Test POST /flash/addcrypted2
            hex_key = "abcdef0123456789abcdef0123456789"
            urls = ["https://1fichier.com/?abcdef1234"]
            crypted_b64 = helper_encrypt_cnl2(hex_key, urls)

            post_data = {
                "crypted": crypted_b64,
                "jk": f"function f() {{ return '{hex_key}'; }}",
                "package": "MyArchivedAlbum",
                "passwords": "mypassword1, mypassword2",
            }
            async with client.post(f"http://127.0.0.1:{test_port}/flash/addcrypted2", data=post_data) as resp:
                assert resp.status == 200
                text = await resp.text()
                assert "success" in text

    # Verify callback was executed
    assert len(received_packages) == 1
    pkg = received_packages[0]
    assert pkg.package_name == "MyArchivedAlbum"
    assert pkg.urls == ["https://1fichier.com/?abcdef1234"]
    assert pkg.passwords == ["mypassword1", "mypassword2"]
