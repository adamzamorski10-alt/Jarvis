import socket

import pytest

from app.web_research.url_policy import (
    ResearchURLPolicyError,
    validate_and_resolve_research_url,
)


PUBLIC_IP = "93.184.216.34"


def resolve_to(*addresses):
    return lambda hostname, port: list(addresses)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "//example.com/path",
        "https://user:pass@example.com/",
        "https://127.0.0.1/",
        "http://[::1]/",
        "https://example.com:8443/",
        "https://example.com/white space",
        "https://example.com:99999/",
    ],
)
def test_url_policy_rejects_unsafe_url_shapes(url):
    with pytest.raises(ResearchURLPolicyError):
        validate_and_resolve_research_url(url, resolver=resolve_to(PUBLIC_IP))


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.8",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "::1",
        "fe80::1",
        "fc00::1",
        "224.0.0.1",
        "0.0.0.0",
    ],
)
def test_url_policy_rejects_non_public_dns_destinations(address):
    with pytest.raises(ResearchURLPolicyError, match="non_public_destination"):
        validate_and_resolve_research_url(
            "https://example.com/path",
            resolver=resolve_to(address),
        )


def test_url_policy_rejects_mixed_public_and_private_dns_answers():
    with pytest.raises(ResearchURLPolicyError, match="non_public_destination"):
        validate_and_resolve_research_url(
            "https://example.com/",
            resolver=resolve_to(PUBLIC_IP, "10.0.0.1"),
        )


def test_url_policy_returns_pinned_candidate_addresses_and_request_target():
    target = validate_and_resolve_research_url(
        "https://Example.com/articles?q=one#ignored",
        resolver=resolve_to(PUBLIC_IP, PUBLIC_IP),
    )
    assert target.hostname == "example.com"
    assert target.port == 443
    assert target.request_target == "/articles?q=one"
    assert target.public_addresses == (PUBLIC_IP,)
    assert target.original_url == "https://example.com/articles?q=one"


def test_url_policy_fails_closed_when_dns_fails():
    def broken_resolver(hostname, port):
        raise socket.gaierror("temporary lookup failure")

    with pytest.raises(ResearchURLPolicyError, match="dns_resolution_failed"):
        validate_and_resolve_research_url("https://example.com/", resolver=broken_resolver)


def test_url_policy_fails_closed_when_dns_returns_no_addresses():
    with pytest.raises(ResearchURLPolicyError, match="dns_no_addresses"):
        validate_and_resolve_research_url("https://example.com/", resolver=resolve_to())
