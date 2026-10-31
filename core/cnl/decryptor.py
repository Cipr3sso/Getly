import base64
import logging
import re
from typing import Optional, List

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.backends import default_backend

from .models import CNL2Payload, CNLDecryptedPackage

logger = logging.getLogger(__name__)

# Try importing quickjs and dukpy
try:
    import quickjs
    HAS_QUICKJS = True
except ImportError:
    HAS_QUICKJS = False

try:
    import dukpy
    HAS_DUKPY = True
except ImportError:
    HAS_DUKPY = False


class CNLDecryptionError(Exception):
    """Raised when CNL decryption fails."""
    pass


class CNLDecryptor:
    """
    Decodes and decrypts Click'n'Load (CNL2) payloads from Filecrypt and similar hosts.
    Uses QuickJS (with Dukpy fallback) for sandboxed JavaScript key evaluation,
    and AES-128-CBC for payload decryption.
    """

    @classmethod
    def evaluate_jk(cls, jk_code: str) -> str:
        """
        Executes the JavaScript code snippet in an isolated sandbox to extract
        the 32-character hexadecimal AES key.
        """
        if not HAS_QUICKJS and not HAS_DUKPY:
            raise CNLDecryptionError(
                "Neither 'quickjs' nor 'dukpy' runtime is installed. Cannot evaluate JS key."
            )

        # Standard JS wrapper that invokes f() or jk() or returns the evaluated expression
        harness = (
            f"{jk_code};\n"
            "var __cnl_key = '';\n"
            "if (typeof f === 'function') {\n"
            "    __cnl_key = f();\n"
            "} else if (typeof jk === 'function') {\n"
            "    __cnl_key = jk();\n"
            "}\n"
            "String(__cnl_key);"
        )

        key_str = None
        error_msg = ""

        # 1. Attempt with QuickJS
        if HAS_QUICKJS:
            try:
                ctx = quickjs.Context()
                res = ctx.eval(harness)
                if res is not None:
                    key_str = str(res).strip()
            except Exception as exc:
                logger.debug("QuickJS eval failed: %s. Trying Dukpy fallback...", exc)
                error_msg = f"QuickJS error: {exc}"

        # 2. Fallback to Dukpy
        if (not key_str or len(key_str) == 0) and HAS_DUKPY:
            try:
                res = dukpy.evaljs(harness)
                if res is not None:
                    key_str = str(res).strip()
            except Exception as exc:
                logger.debug("Dukpy eval failed: %s", exc)
                error_msg += f" | Dukpy error: {exc}"

        if not key_str:
            raise CNLDecryptionError(f"Failed to evaluate JS key from snippet. Details: {error_msg}")

        # Clean string: strip quotes or whitespace
        key_str = key_str.strip("'\" \r\n\t")

        # Validate that the extracted key is a valid hexadecimal string
        if not re.fullmatch(r"^[0-9a-fA-F]+$", key_str):
            raise CNLDecryptionError(
                f"Evaluated JS key is not a valid hex string: '{key_str}'"
            )

        return key_str

    @classmethod
    def decrypt(cls, payload: CNL2Payload) -> CNLDecryptedPackage:
        """
        Decrypts a CNL2Payload into a CNLDecryptedPackage containing the original URLs.
        """
        # 1. Evaluate the JavaScript to extract the hex AES key
        hex_key = cls.evaluate_jk(payload.jk)
        try:
            aes_key = bytes.fromhex(hex_key)
        except ValueError as exc:
            raise CNLDecryptionError(f"Invalid hex string for AES key: {exc}") from exc

        # 2. In standard CNL2, IV is identical to the key
        iv = aes_key

        # 3. Base64 decode the ciphertext
        try:
            # Handle URL-safe or standard Base64, with padding if truncated
            crypted_clean = payload.crypted.replace(" ", "+")
            missing_padding = len(crypted_clean) % 4
            if missing_padding:
                crypted_clean += "=" * (4 - missing_padding)
            ciphertext = base64.b64decode(crypted_clean)
        except Exception as exc:
            raise CNLDecryptionError(f"Base64 decoding failed for crypted payload: {exc}") from exc

        # 4. Decrypt via AES-CBC
        try:
            cipher = Cipher(algorithms.AES(aes_key), modes.CBC(iv), backend=default_backend())
            decryptor = cipher.decryptor()
            decrypted_padded = decryptor.update(ciphertext) + decryptor.finalize()
        except Exception as exc:
            raise CNLDecryptionError(f"AES decryption failed: {exc}") from exc

        # 5. Remove padding (PKCS7 or null padding)
        try:
            unpadder = padding.PKCS7(128).unpadder()
            decrypted_raw = unpadder.update(decrypted_padded) + unpadder.finalize()
        except Exception:
            # Graceful fallback: strip trailing null bytes and control chars
            decrypted_raw = decrypted_padded.rstrip(b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10")

        # 6. Parse URLs
        try:
            decrypted_text = decrypted_raw.decode("utf-8", errors="replace")
        except Exception as exc:
            raise CNLDecryptionError(f"UTF-8 decoding failed: {exc}") from exc

        raw_lines = re.split(r"[\r\n]+", decrypted_text)
        urls: List[str] = []
        for line in raw_lines:
            line_str = line.strip()
            if line_str and (line_str.startswith("http://") or line_str.startswith("https://") or "://" in line_str):
                urls.append(line_str)

        # 7. Parse passwords
        passwords: List[str] = []
        if payload.passwords:
            # Can be JSON array or newline/comma separated
            pw_clean = payload.passwords.strip()
            if pw_clean.startswith("[") and pw_clean.endswith("]"):
                import json
                try:
                    passwords = [str(p) for p in json.loads(pw_clean)]
                except Exception:
                    passwords = [pw_clean]
            else:
                passwords = [p.strip() for p in re.split(r"[\r\n,]+", pw_clean) if p.strip()]

        logger.info(
            "Successfully decrypted CNL2 container: package='%s', found %d URLs",
            payload.package,
            len(urls),
        )

        return CNLDecryptedPackage(
            package_name=payload.package,
            source_url=payload.source,
            passwords=passwords,
            urls=urls,
        )
