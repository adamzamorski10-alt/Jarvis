"""Local credential storage helpers.

On Windows this uses DPAPI through CryptProtectData/CryptUnprotectData so the
Kimi API key is not stored as plain text in SQLite.
"""
import base64
import ctypes
from ctypes import wintypes
import sys


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


class CredentialService:
    def encrypt(self, secret: str) -> str:
        if not secret:
            return ""
        if sys.platform != "win32":
            raise CredentialStoreError("Secure credential storage is only implemented for Windows DPAPI")

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        input_blob, _buffer = _blob_from_bytes(secret.encode("utf-8"))
        output_blob = DATA_BLOB()
        ok = crypt32.CryptProtectData(
            ctypes.byref(input_blob),
            "AI Operator Kimi API Key",
            None,
            None,
            None,
            0,
            ctypes.byref(output_blob),
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
            raise CredentialStoreError("Secure credential storage is only implemented for Windows DPAPI")

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        data = base64.b64decode(encrypted.encode("ascii"))
        input_blob, _buffer = _blob_from_bytes(data)
        output_blob = DATA_BLOB()
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(input_blob),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(output_blob),
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
