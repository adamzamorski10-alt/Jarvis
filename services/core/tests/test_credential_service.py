import base64
import hashlib
import hmac
import os
import stat

import pytest

from app.ai.services.credential_service import (
    CredentialService,
    CredentialStoreError,
    _legacy_keystream,
)


@pytest.mark.skipif(os.name == "nt", reason="Unix-specific credential storage format")
def test_new_credentials_use_aes_gcm_and_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_OPERATOR_CREDENTIAL_KEY_PATH", str(tmp_path / "credential.key"))

    service = CredentialService()
    encrypted = service.encrypt("super-secret-value")

    assert encrypted.startswith("unix-v2:")
    assert service.decrypt(encrypted) == "super-secret-value"

    key_mode = stat.S_IMODE((tmp_path / "credential.key").stat().st_mode)
    assert key_mode == 0o600
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700


@pytest.mark.skipif(os.name == "nt", reason="Unix-specific credential storage format")
def test_aes_gcm_detects_tampering(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_OPERATOR_CREDENTIAL_KEY_PATH", str(tmp_path / "credential.key"))

    service = CredentialService()
    encrypted = service.encrypt("super-secret-value")
    raw = bytearray(base64.b64decode(encrypted.removeprefix("unix-v2:"), validate=True))
    raw[-1] ^= 1
    tampered = "unix-v2:" + base64.b64encode(raw).decode("ascii")

    with pytest.raises(CredentialStoreError):
        service.decrypt(tampered)


@pytest.mark.skipif(os.name == "nt", reason="Unix-specific credential storage format")
def test_legacy_unix_v1_remains_decryptable(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_OPERATOR_CREDENTIAL_KEY_PATH", str(tmp_path / "credential.key"))

    service = CredentialService()
    key = os.urandom(32)
    key_path = tmp_path / "credential.key"
    key_path.write_bytes(key)
    key_path.chmod(0o600)

    plaintext = b"legacy-secret"
    nonce = b"0123456789abcdef"
    ciphertext = bytes(
        a ^ b for a, b in zip(plaintext, _legacy_keystream(key, nonce, len(plaintext)))
    )
    tag = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
    legacy = "unix-v1:" + base64.b64encode(nonce + ciphertext + tag).decode("ascii")

    assert service.decrypt(legacy) == "legacy-secret"


@pytest.mark.skipif(os.name == "nt", reason="Unix-specific credential storage format")
def test_credential_hint_never_returns_full_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_OPERATOR_CREDENTIAL_KEY_PATH", str(tmp_path / "credential.key"))

    service = CredentialService()
    encrypted = service.encrypt("super-secret-value")

    assert service.hint(encrypted) == "****alue"
    assert "super-secret-value" not in service.hint(encrypted)
