"""
Identity Provider — OS-specific user identity derivation.

Windows-first implementation. Other platforms are explicitly unsupported in V1.
"""
from __future__ import annotations

import hashlib
import os
import platform
from abc import ABC, abstractmethod
from typing import Optional

from app.identity.models import UserIdentity


class IdentityProviderError(Exception):
    """Base identity provider error."""
    pass


class UnsupportedPlatformError(IdentityProviderError):
    """Raised when the platform is not supported."""
    pass


class IdentityProvider(ABC):
    """Abstract identity provider."""

    @abstractmethod
    def derive(self) -> UserIdentity:
        """Derive the local user identity."""
        raise NotImplementedError


class WindowsIdentityProvider(IdentityProvider):
    """
    Derives user identity from Windows SIDs.

    Uses machine SID + user SID to form a stable identifier that does not
    change when the display username changes.
    """

    def derive(self) -> UserIdentity:
        if platform.system() != "Windows":
            raise UnsupportedPlatformError(
                f"WindowsIdentityProvider requires Windows, got {platform.system()}"
            )

        machine_sid = self._get_machine_sid()
        user_sid = self._get_user_sid()

        composite = f"{machine_sid}:{user_sid}"
        user_id_hash = hashlib.sha256(composite.encode()).hexdigest()[:16]

        os_username = os.environ.get("USERNAME", "unknown")

        return UserIdentity(
            user_id=f"local_user_{user_id_hash}",
            display_name=os_username,
            os_username=os_username,
            machine_id=hashlib.sha256(machine_sid.encode()).hexdigest()[:16],
        )

    def _get_machine_sid(self) -> str:
        """Get the machine SID from the registry."""
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
                0,
                winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
            ) as key:
                sid, _ = winreg.QueryValueEx(key, "MachineGuid")
                return sid
        except Exception:
            # Fallback: use hostname-based identifier
            import socket
            return socket.gethostname()

    def _get_user_sid(self) -> str:
        """Get the current user's SID from the process token."""
        try:
            import ctypes
            from ctypes import wintypes

            advapi32 = ctypes.windll.advapi32
            kernel32 = ctypes.windll.kernel32

            token_handle = wintypes.HANDLE()
            if not advapi32.OpenProcessToken(
                kernel32.GetCurrentProcess(),
                0x0008,  # TOKEN_QUERY
                ctypes.byref(token_handle),
            ):
                raise ctypes.WinError()

            try:
                sid_buffer = ctypes.create_string_buffer(256)
                sid_buffer_size = wintypes.DWORD(ctypes.sizeof(sid_buffer))
                token_user = ctypes.c_void_p()

                if not advapi32.GetTokenInformation(
                    token_handle,
                    1,  # TokenUser
                    sid_buffer,
                    sid_buffer_size,
                    ctypes.byref(sid_buffer_size),
                ):
                    raise ctypes.WinError()

                # Parse the token user structure to get SID
                token_user_ptr = ctypes.cast(sid_buffer, ctypes.POINTER(ctypes.c_void_p))
                sid_ptr = ctypes.cast(token_user_ptr[0], ctypes.POINTER(ctypes.c_byte))

                # Convert SID to string
                convert_sid = kernel32.ConvertSidToStringSidW
                string_sid = wintypes.LPWSTR()
                if not convert_sid(sid_ptr, ctypes.byref(string_sid)):
                    raise ctypes.WinError()

                try:
                    return string_sid.value or "unknown_sid"
                finally:
                    kernel32.LocalFree(string_sid)
            finally:
                kernel32.CloseHandle(token_handle)
        except Exception:
            # Fallback: use username + domain
            import getpass
            return getpass.getuser()


class UnixIdentityProvider(IdentityProvider):
    """Derive a stable local identity on Linux/macOS without OS-specific APIs."""

    def derive(self) -> UserIdentity:
        import getpass
        import socket

        os_username = getpass.getuser() or os.environ.get("USER") or "unknown"
        machine_name = socket.gethostname() or "unknown-machine"
        composite = f"{machine_name}:{os_username}"
        identity_hash = hashlib.sha256(composite.encode("utf-8")).hexdigest()[:16]

        return UserIdentity(
            user_id=f"local_user_{identity_hash}",
            display_name=os_username,
            os_username=os_username,
            machine_id=hashlib.sha256(machine_name.encode("utf-8")).hexdigest()[:16],
        )


def get_identity_provider() -> IdentityProvider:
    """Return the appropriate identity provider for the current platform."""
    system = platform.system()
    if system == "Windows":
        return WindowsIdentityProvider()
    elif system in ("Linux", "Darwin"):
        return UnixIdentityProvider()
    else:
        raise UnsupportedPlatformError(f"Unsupported platform: {system}")


__all__ = [
    "IdentityProvider",
    "IdentityProviderError",
    "UnsupportedPlatformError",
    "WindowsIdentityProvider",
    "UnixIdentityProvider",
    "get_identity_provider",
]
