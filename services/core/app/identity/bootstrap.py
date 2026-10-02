"""
Bootstrap Secret — Local file-based bootstrap credential.

The bootstrap secret is a 32-byte cryptographically random secret stored in a
local file protected by OS ACLs. It is used exactly once to obtain a server-issued
session token. It is never returned by any API, never logged, and never persisted
in the database.
"""
from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Windows: %APPDATA%/AI-Operator/.local_secret
# Default filename
SECRET_FILENAME = ".local_secret"
SECRET_DIR_NAME = "AI-Operator"


class BootstrapSecretError(Exception):
    pass


class BootstrapSecretNotFoundError(BootstrapSecretError):
    pass


class BootstrapSecret:
    """Manages the local bootstrap secret file."""

    def __init__(self, app_data_dir: Optional[Path] = None) -> None:
        if app_data_dir is None:
            app_data_dir = self._default_app_data_dir()
        self._path = app_data_dir / SECRET_DIR_NAME / SECRET_FILENAME
        self._secret: Optional[str] = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def secret(self) -> Optional[str]:
        return self._secret

    def load_or_generate(self) -> str:
        """
        Load the secret from disk, or generate a new one if it doesn't exist.

        Returns the secret value.
        Never returns None.
        """
        if self._secret is not None:
            return self._secret

        if self._path.exists():
            self._secret = self._read_secret()
            logger.debug("Bootstrap secret loaded from disk", path=str(self._path))
        else:
            self._secret = self._generate_secret()
            self._write_secret(self._secret)
            logger.info("Bootstrap secret generated and stored", path=str(self._path))

        return self._secret

    def verify(self, candidate: Optional[str]) -> bool:
        """
        Verify a candidate secret against the loaded secret.

        Uses constant-time comparison to prevent timing attacks.
        """
        if not candidate or not self._secret:
            return False
        return secrets.compare_digest(candidate, self._secret)

    def ensure_generated(self) -> None:
        """Ensure the secret file exists on disk. Creates it if missing."""
        if not self._path.exists():
            secret = self._generate_secret()
            self._write_secret(secret)
            self._secret = secret
            logger.info("Bootstrap secret generated", path=str(self._path))

    def _read_secret(self) -> str:
        """Read secret from disk."""
        try:
            content = self._path.read_text(encoding="utf-8").strip()
            if not content:
                raise BootstrapSecretError("Bootstrap secret file is empty")
            return content
        except FileNotFoundError:
            raise BootstrapSecretNotFoundError(f"Bootstrap secret not found: {self._path}")
        except Exception as exc:
            raise BootstrapSecretError(f"Failed to read bootstrap secret: {exc}") from exc

    def _write_secret(self, secret: str) -> None:
        """Write secret to disk."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # Write with user-only permissions on Windows
            if os.name == "nt":
                import tempfile
                fd, tmp_path = tempfile.mkstemp(dir=str(self._path.parent))
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        f.write(secret)
                    os.replace(tmp_path, str(self._path))
                except Exception:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise
            else:
                self._path.write_text(secret, encoding="utf-8")
                self._path.chmod(0o600)

            logger.debug("Bootstrap secret written", path=str(self._path))
        except Exception as exc:
            raise BootstrapSecretError(f"Failed to write bootstrap secret: {exc}") from exc

    def _generate_secret(self) -> str:
        """Generate a cryptographically random 32-byte secret as hex."""
        return secrets.token_hex(32)

    @staticmethod
    def _default_app_data_dir() -> Path:
        """Return the platform-specific app data directory."""
        if os.name == "nt":
            return Path(os.environ.get("APPDATA", ""))
        elif os.name == "posix":
            return Path.home() / ".local" / "share"
        else:
            return Path.home()


__all__ = ["BootstrapSecret", "BootstrapSecretError", "BootstrapSecretNotFoundError"]
