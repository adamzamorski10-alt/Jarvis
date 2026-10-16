"""HTTPX transport that pins each TCP connection to a validated public IP.

The request URL and TLS server name remain the original hostname. DNS is
re-resolved inside the connection backend and the validated IP is passed directly
to the socket backend, preventing a second hostname lookup between validation
and connection. Environment proxies are not used by the fetcher.
"""
from __future__ import annotations

import asyncio
import ssl
from typing import Iterable

import httpcore
import httpx
from httpcore._backends.anyio import AnyIOBackend
from httpx._transports.default import map_httpcore_exceptions

from app.web_research.url_policy import (
    ResearchURLPolicyError,
    validate_and_resolve_research_url,
)


class _PublicOnlyNetworkBackend(AnyIOBackend):
    def __init__(self, *, max_concurrent_dns: int = 3) -> None:
        super().__init__()
        if max_concurrent_dns < 1:
            raise ValueError("max_concurrent_dns must be positive")
        self._dns_semaphore = asyncio.Semaphore(max_concurrent_dns)

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[tuple[int, int, int]] | None = None,
    ):
        if port == 443:
            scheme = "https"
        elif port == 80:
            scheme = "http"
        else:
            raise ResearchURLPolicyError("non_default_port_forbidden")

        # Bound resolver workers separately from sockets. Cancelling an await
        # of asyncio.to_thread does not stop its worker thread, so its slot is
        # released only when the worker actually finishes.
        await self._dns_semaphore.acquire()
        resolution_task = asyncio.create_task(
            asyncio.to_thread(
                validate_and_resolve_research_url,
                f"{scheme}://{host}/",
            )
        )
        try:
            target = await asyncio.shield(resolution_task)
        except BaseException:
            if resolution_task.done():
                self._dns_semaphore.release()
            else:
                def release_after_resolution(task: asyncio.Task) -> None:
                    try:
                        task.exception()
                    except asyncio.CancelledError:
                        pass
                    self._dns_semaphore.release()

                resolution_task.add_done_callback(release_after_resolution)
            raise
        else:
            self._dns_semaphore.release()

        last_error: Exception | None = None
        for address in target.public_addresses:
            try:
                return await super().connect_tcp(
                    host=address,
                    port=port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise ResearchURLPolicyError("dns_no_addresses")


class _ResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream) -> None:
        self._stream = stream

    async def __aiter__(self):
        async for chunk in self._stream:
            yield chunk

    async def aclose(self) -> None:
        await self._stream.aclose()


class PinnedPublicHTTPTransport(httpx.AsyncBaseTransport):
    """Minimal HTTPX adapter backed by httpcore's connection-pinning backend."""

    def __init__(self, *, max_connections: int = 3) -> None:
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            max_connections=max_connections,
            max_keepalive_connections=max_connections,
            keepalive_expiry=5.0,
            http1=True,
            http2=False,
            retries=0,
            network_backend=_PublicOnlyNetworkBackend(max_concurrent_dns=max_connections),
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        assert isinstance(request.stream, httpx.AsyncByteStream)
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        with map_httpcore_exceptions():
            response = await self._pool.handle_async_request(core_request)
        return httpx.Response(
            status_code=response.status,
            headers=response.headers,
            stream=_ResponseStream(response.stream),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()


__all__ = ["PinnedPublicHTTPTransport"]
