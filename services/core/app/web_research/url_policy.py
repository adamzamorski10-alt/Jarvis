"""URL and DNS preflight policy for public research sources.

IMPORTANT: resolution here is only a preflight decision. A caller MUST pin the
actual socket connection to one of the returned addresses while preserving the
original hostname for HTTP Host and TLS certificate/SNI validation. Using these
checks followed by an ordinary hostname-based HTTP request is vulnerable to DNS
rebinding and is not an approved fetch path.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit, urlunsplit


class ResearchURLPolicyError(ValueError):
    """A research URL or its resolved destination violates network policy."""


@dataclass(frozen=True)
class ResolvedResearchTarget:
    original_url: str
    scheme: str
    hostname: str
    port: int
    request_target: str
    public_addresses: tuple[str, ...]


Resolver = Callable[[str, int], list[str] | tuple[str, ...]]


def _system_resolver(hostname: str, port: int) -> list[str]:
    answers = socket.getaddrinfo(
        hostname,
        port,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    )
    return [answer[4][0] for answer in answers]


def validate_and_resolve_research_url(
    url: str,
    *,
    resolver: Resolver | None = None,
) -> ResolvedResearchTarget:
    """Validate URL syntax and reject any DNS answer that is not globally routable.

    This function deliberately rejects IP literals and non-default ports.
    It returns all validated DNS addresses so a future fetch transport can pin
    the connection. It does not itself open a socket or make a request.
    """
    if not isinstance(url, str) or not url or len(url) > 2048:
        raise ResearchURLPolicyError("invalid_url_length")
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
        raise ResearchURLPolicyError("invalid_url_characters")

    try:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        hostname = parts.hostname
        explicit_port = parts.port
    except ValueError as exc:
        raise ResearchURLPolicyError("malformed_url") from exc

    if scheme not in {"http", "https"} or not parts.netloc or not hostname:
        raise ResearchURLPolicyError("unsupported_url")
    if parts.username is not None or parts.password is not None:
        raise ResearchURLPolicyError("url_userinfo_forbidden")
    if "%" in hostname:
        raise ResearchURLPolicyError("scoped_or_escaped_hostname_forbidden")

    hostname = hostname.rstrip(".").lower()
    if not hostname:
        raise ResearchURLPolicyError("missing_hostname")

    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ResearchURLPolicyError("ip_literal_forbidden")

    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ResearchURLPolicyError("invalid_hostname") from exc
    if len(ascii_hostname) > 253 or any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or re.fullmatch(r"[a-z0-9-]+", label) is None
        for label in ascii_hostname.split(".")
    ):
        raise ResearchURLPolicyError("invalid_hostname")

    default_port = 443 if scheme == "https" else 80
    port = explicit_port if explicit_port is not None else default_port
    if port != default_port:
        raise ResearchURLPolicyError("non_default_port_forbidden")

    try:
        raw_addresses = (resolver or _system_resolver)(ascii_hostname, port)
    except (OSError, socket.gaierror) as exc:
        raise ResearchURLPolicyError("dns_resolution_failed") from exc
    if not raw_addresses:
        raise ResearchURLPolicyError("dns_no_addresses")

    addresses: list[str] = []
    for raw_address in raw_addresses:
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError as exc:
            raise ResearchURLPolicyError("dns_returned_invalid_address") from exc
        if not address.is_global or address.is_multicast or address.is_unspecified:
            raise ResearchURLPolicyError("non_public_destination")
        normalized = str(address)
        if normalized not in addresses:
            addresses.append(normalized)

    path = parts.path or "/"
    request_target = urlunsplit(("", "", path, parts.query, ""))
    canonical_url = urlunsplit((scheme, ascii_hostname, path, parts.query, ""))
    return ResolvedResearchTarget(
        original_url=canonical_url,
        scheme=scheme,
        hostname=ascii_hostname,
        port=port,
        request_target=request_target,
        public_addresses=tuple(addresses),
    )


__all__ = [
    "ResearchURLPolicyError",
    "ResolvedResearchTarget",
    "validate_and_resolve_research_url",
]
