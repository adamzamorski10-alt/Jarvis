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
            return SimpleNamespace(
                status=200,
                headers=[(b"content-type", b"text/plain")],
                stream=FakeCoreStream(),
                extensions={},
            )

        async def aclose(self):
            return None

    transport = pinned_transport.PinnedPublicHTTPTransport()
    transport._pool = FakePool()
    request = pinned_transport.httpx.Request("GET", "https://example.com/path?q=1", headers={"host": "example.com"})

    response = await transport.handle_async_request(request)
    try:
        # The adapter must preserve the original origin hostname in the HTTPCore URL.
        # This unit test does not itself exercise a TLS handshake or certificate check.
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


@pytest.mark.asyncio
async def test_httpcore_connection_passes_origin_hostname_to_tls_and_host_header(monkeypatch):
    import ssl

    observed = {}
    target = SimpleNamespace(public_addresses=("93.184.216.34",))

    def fake_resolve(url):
        assert url == "https://example.com/"
        return target

    class FakeTLSStream:
        def __init__(self):
            self._response = bytearray(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Length: 2\r\n"
                b"Connection: close\r\n"
                b"\r\nok"
            )
            self._written = bytearray()

        async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            observed["server_hostname"] = server_hostname
            observed["check_hostname"] = ssl_context.check_hostname
            observed["verify_mode"] = ssl_context.verify_mode
            return self

        async def write(self, buffer, timeout=None):
            self._written.extend(buffer)
            observed["request_bytes"] = bytes(self._written)

        async def read(self, max_bytes, timeout=None):
            if not self._response:
                return b""
            chunk = bytes(self._response[:max_bytes])
            del self._response[:max_bytes]
            return chunk

        async def aclose(self):
            return None

        def get_extra_info(self, info):
            return None

    stream = FakeTLSStream()

    async def fake_connect(self, **kwargs):
        observed["socket_host"] = kwargs["host"]
        observed["socket_port"] = kwargs["port"]
        return stream

    monkeypatch.setattr(pinned_transport, "validate_and_resolve_research_url", fake_resolve)
    monkeypatch.setattr(AnyIOBackend, "connect_tcp", fake_connect)

    transport = pinned_transport.PinnedPublicHTTPTransport()
    client = pinned_transport.httpx.AsyncClient(transport=transport, trust_env=False)
    try:
        response = await client.get("https://example.com/")
        assert response.status_code == 200
        assert response.text == "ok"
    finally:
        await client.aclose()

    assert observed["socket_host"] == "93.184.216.34"
    assert observed["socket_port"] == 443
    assert observed["server_hostname"] == "example.com"
    assert observed["check_hostname"] is True
    assert observed["verify_mode"] == ssl.CERT_REQUIRED
    assert b"host: example.com" in observed["request_bytes"].lower()
