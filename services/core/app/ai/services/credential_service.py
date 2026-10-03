"""Local credential storage helpers.

Windows uses DPAPI. On Unix-like systems, credentials use a per-user local key
file (0600) plus authenticated AES-GCM encryption. Existing unix-v1 values
remain decryptable for backwards compatibility; all newly encrypted values use
unix-v2 and the standard AEAD primitive.
"""
import base64
import ctypes
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import sys
from ctypes import wintypes

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CredentialStoreError(RuntimeError):
    pass


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob_from_bytes(data: bytes) -> tuple[DATA_BLOB, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    blob = DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    return blob, buffer


def _bytes_from_blob(blob: DATA_BLOB) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def _unix_key_path() -> Path:
    override = os.environ.get("AI_OPERATOR_CREDENTIAL_KEY_PATH")
    return Path(override).expanduser() if override else Path.home() / ".ai_operator" / "credential.key"


def _load_unix_key() -> bytes:
    path = _unix_key_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            fd = None
        if fd is not None:
            try:
                key = secrets.token_bytes(32)
                os.write(fd, key)
                return key
            finally:
                os.close(fd)
        key = path.read_bytes()
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        if len(key) != 32:
            raise CredentialStoreError("Credential key file is invalid")
        return key
    except OSError as exc:
        raise CredentialStoreError("Could not access local credential key") from exc


# Legacy unix-v1 helpers are retained only so existing installations can
# decrypt credentials written before the AES-GCM migration. New credentials
# never use this construction.
def _legacy_keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest())
        counter += 1
    return bytes(out[:length])


def _legacy_unix_decrypt(value: str) -> str:
    try:
        raw = base64.b64decode(value.removeprefix("unix-v1:"), validate=True)
        if len(raw) < 48:
            raise ValueError("ciphertext too short")
        nonce, ciphertext, tag = raw[:16], raw[16:-32], raw[-32:]
        key = _load_unix_key()
        expected = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            raise ValueError("authentication failed")
        plaintext = bytes(a ^ b for a, b in zip(ciphertext, _legacy_keystream(key, nonce, len(ciphertext))))
        return plaintext.decode("utf-8")
    except (ValueError, UnicodeDecodeError, OSError) as exc:
        raise CredentialStoreError("Could not decrypt securely stored credential") from exc


def _unix_encrypt(secret: str) -> str:
    key = _load_unix_key()
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(key).encrypt(nonce, secret.encode("utf-8"), b"ai-operator-credential-v2")
    return "unix-v2:" + base64.b64encode(nonce + ciphertext).decode("ascii")


def _unix_decrypt(value: str) -> str:
    if value.startswith("unix-v1:"):
        return _legacy_unix_decrypt(value)

    try:
        raw = base64.b64decode(value.removeprefix("unix-v2:"), validate=True)
        if len(raw) < 28:
            raise ValueError("ciphertext too short")
        nonce, ciphertext = raw[:12], raw[12:]
        key = _load_unix_key()
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, b"ai-operator-credential-v2")
        return plaintext.decode("utf-8")
    except (ValueError, UnicodeDecodeError, OSError) as exc:
        raise CredentialStoreError("Could not decrypt securely stored credential") from exc


class CredentialService:
    def encrypt(self, secret: str) -> str:
        if not secret:
            return ""
        if sys.platform != "win32":
            return _unix_encrypt(secret)

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        input_blob, _buffer = _blob_from_bytes(secret.encode("utf-8"))
        output_blob = DATA_BLOB()
        ok = crypt32.CryptProtectData(
            ctypes.byref(input_blob), "AI Operator Credential", None, None, None, 0, ctypes.byref(output_blob)
        )
        if not ok:
            raise CredentialStoreError("Could not encrypt credential with Windows DPAPI")
        try:
            return base64.b64encode(_bytes_from_blob(output_blob)).decode("ascii")
        finally:
            kernel32.LocalFree(output_blob.pbData)

    def decrypt(self, encrypted: str | None) -> str | None:
        if not encrypted:
            return None
        if sys.platform != "win32":
            return _unix_decrypt(encrypted)

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        data = base64.b64decode(encrypted.encode("ascii"))
        input_blob, _buffer = _blob_from_bytes(data)
        output_blob = DATA_BLOB()
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(input_blob), None, None, None, None, 0, ctypes.byref(output_blob)
        )
        if not ok:
            raise CredentialStoreError("Could not decrypt credential with Windows DPAPI")
        try:
            return _bytes_from_blob(output_blob).decode("utf-8")
        finally:
            kernel32.LocalFree(output_blob.pbData)

    def hint(self, encrypted: str | None) -> str | None:
        secret = self.decrypt(encrypted)
        if not secret:
            return None
        return f"****{secret[-4:]}" if len(secret) >= 4 else "****"
