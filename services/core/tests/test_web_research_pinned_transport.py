from types import SimpleNamespace

import pytest
from httpcore._backends.anyio import AnyIOBackend

from app.web_research import pinned_transport


@pytest.mark.asyncio
async def test_network_backend_connects_to_validated_ip_not_hostname(monkeypatch):
    observed = {}
    target = SimpleNamespace(public_addresses=("93.184.216.34",))

    def fake_resolve(url):
        observed["validated_url"] = url
        return target

    async def fake_connect(self, **kwargs):
        observed["connect_kwargs"] = kwargs
        return "connected-stream"

    monkeypatch.setattr(pinned_transport, "validate_and_resolve_research_url", fake_resolve)
    monkeypatch.setattr(AnyIOBackend, "connect_tcp", fake_connect)

    backend = pinned_transport._PublicOnlyNetworkBackend()
    stream = await backend.connect_tcp("example.com", 443, timeout=2.0)

    assert stream == "connected-stream"
    assert observed["validated_url"] == "https://example.com/"
    assert observed["connect_kwargs"]["host"] == "93.184.216.34"
    assert observed["connect_kwargs"]["port"] == 443


@pytest.mark.asyncio
async def test_network_backend_revalidates_each_new_connection(monkeypatch):
    calls = []
    target = SimpleNamespace(public_addresses=("93.184.216.34",))

    def fake_resolve(url):
        calls.append(url)
        return target

    async def fake_connect(self, **kwargs):
        return "connected-stream"

    monkeypatch.setattr(pinned_transport, "validate_and_resolve_research_url", fake_resolve)
    monkeypatch.setattr(AnyIOBackend, "connect_tcp", fake_connect)

    backend = pinned_transport._PublicOnlyNetworkBackend()
    await backend.connect_tcp("example.com", 443)
    await backend.connect_tcp("example.com", 443)

    assert calls == ["https://example.com/", "https://example.com/"]
