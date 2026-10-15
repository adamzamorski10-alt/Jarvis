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


@pytest.mark.asyncio
async def test_network_backend_fails_closed_when_url_validation_rejects_destination(monkeypatch):
    attempted_connections = []

    def reject_url(url):
        raise pinned_transport.ResearchURLPolicyError("non_public_destination")

    async def fake_connect(self, **kwargs):
        attempted_connections.append(kwargs)
        return "must-not-connect"

    monkeypatch.setattr(pinned_transport, "validate_and_resolve_research_url", reject_url)
    monkeypatch.setattr(AnyIOBackend, "connect_tcp", fake_connect)

    backend = pinned_transport._PublicOnlyNetworkBackend()
    with pytest.raises(pinned_transport.ResearchURLPolicyError, match="non_public_destination"):
        await backend.connect_tcp("example.com", 443)

    assert attempted_connections == []


@pytest.mark.asyncio
async def test_network_backend_tries_only_validated_addresses_after_connect_failure(monkeypatch):
    attempted_hosts = []
    target = SimpleNamespace(public_addresses=("93.184.216.34", "93.184.216.35"))

    def fake_resolve(url):
        return target

    async def fake_connect(self, **kwargs):
        attempted_hosts.append(kwargs["host"])
        if kwargs["host"] == "93.184.216.34":
            raise pinned_transport.httpcore.ConnectError("simulated connect failure")
        return "connected-stream"

    monkeypatch.setattr(pinned_transport, "validate_and_resolve_research_url", fake_resolve)
    monkeypatch.setattr(AnyIOBackend, "connect_tcp", fake_connect)

    backend = pinned_transport._PublicOnlyNetworkBackend()
    stream = await backend.connect_tcp("example.com", 443)

    assert stream == "connected-stream"
    assert attempted_hosts == ["93.184.216.34", "93.184.216.35"]


class FakeCoreStream:
    async def __aiter__(self):
        yield b"ok"

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_http_transport_preserves_origin_hostname_and_host_header():
    captured = {}

    class FakePool:
        async def handle_async_request(self, request):
            captured["host"] = request.url.host
            captured["headers"] = dict(request.headers)
            return pinned_transport.httpcore.Response(
                status=200,
                headers=[(b"content-type", b"text/plain")],
                stream=FakeCoreStream(),
                extensions={},
            )

        async def aclose(self):
            return None

    transport = pinned_transport.PinnedPublicHTTPTransport()
    transport._pool = FakePool()
    request = pinned_transport.httpx.Request("GET", "https://example.com/path?q=1")

    response = await transport.handle_async_request(request)
    try:
        # HTTPCore must receive the original DNS hostname, not the pinned socket IP.
        # It uses the origin hostname for TLS server_hostname/certificate validation.
        assert captured["host"] == b"example.com"
        # HTTPX constructs Host from the original request URL before this adapter
        # passes the request headers through to HTTPCore.
        assert captured["headers"][b"host"] == b"example.com"
    finally:
        await response.aclose()
        await transport.aclose()


@pytest.mark.asyncio
async def test_network_backend_fails_closed_if_dns_rebinds_before_new_connection(monkeypatch):
    resolutions = 0
    attempted_hosts = []

    def rebind_between_connections(url):
        nonlocal resolutions
        resolutions += 1
        if resolutions == 1:
            return SimpleNamespace(public_addresses=("93.184.216.34",))
        raise pinned_transport.ResearchURLPolicyError("non_public_destination")

    async def fake_connect(self, **kwargs):
        attempted_hosts.append(kwargs["host"])
        return "connected-stream"

    monkeypatch.setattr(
        pinned_transport,
        "validate_and_resolve_research_url",
        rebind_between_connections,
    )
    monkeypatch.setattr(AnyIOBackend, "connect_tcp", fake_connect)

    backend = pinned_transport._PublicOnlyNetworkBackend()
    await backend.connect_tcp("example.com", 443)
    with pytest.raises(pinned_transport.ResearchURLPolicyError, match="non_public_destination"):
        await backend.connect_tcp("example.com", 443)

    assert resolutions == 2
    assert attempted_hosts == ["93.184.216.34"]
