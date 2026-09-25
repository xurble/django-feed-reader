"""Validate outbound feed URLs to reduce SSRF risk."""

import ipaddress
import socket
from typing import Tuple
from urllib.parse import urljoin, urlparse

from django.conf import settings

__all__ = [
    "derive_default_feeds_server",
    "is_safe_http_redirect_target",
    "resolve_feed_redirect_location",
    "validate_feed_request_target",
    "validate_http_redirect_target",
]

_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "metadata.google.internal",
    }
)


def derive_default_feeds_server(allowed_hosts):
    """Pick a default FEEDS_SERVER from ALLOWED_HOSTS (first entry containing a dot)."""
    server = "Unknown Server"
    for h in allowed_hosts:
        if "." in h:
            server = "https://" + h
            break
    return server


def resolve_feed_redirect_location(location: str, feed_url: str) -> str:
    """Resolve a Location header value against the current feed URL (RFC 3986 / urljoin)."""
    if location is None:
        return ""
    loc = location.strip()
    if not loc:
        return ""
    base = feed_url or ""
    if base and not base.endswith("/"):
        # urljoin needs a path segment for relative resolution; append "/" for bare origins
        parsed_base = urlparse(base)
        if not parsed_base.path:
            base = base + "/"
    return urljoin(base, loc)


def _is_safe_ip_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.is_global


def _validate_http_target(
    url: str, *, resolve_hostname: bool, allow_private_networks: bool
) -> Tuple[bool, str]:
    """Validate URL structure and, in strict mode, every resolved address."""
    if not url or not isinstance(url, str):
        return (False, "invalid")
    # Requests and urllib.parse disagree about backslashes in URL authorities.
    # Reject them before parsing so validation and connection cannot target
    # different hosts (for example, ``127.0.0.1\\@example.com``).
    if "\\" in url:
        return (False, "invalid")
    try:
        parsed = urlparse(url)
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except (TypeError, ValueError):
        return (False, "invalid")
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return (False, "invalid")
    host = parsed.hostname
    if not host:
        return (False, "invalid")

    if allow_private_networks:
        return (True, "")

    host_lower = host.lower().rstrip(".")
    if host_lower in _BLOCKED_HOSTNAMES:
        return (False, "invalid")
    if host_lower.endswith((".local", ".localhost")):
        return (False, "invalid")

    try:
        ipaddress.ip_address(host_lower)
    except ValueError:
        if not resolve_hostname:
            return (True, "")
    else:
        return (True, "") if _is_safe_ip_address(host_lower) else (False, "invalid")

    try:
        addresses = socket.getaddrinfo(
            host_lower,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except (OSError, ValueError):
        return (False, "resolution_failed")

    if not addresses:
        return (False, "resolution_failed")
    for address_info in addresses:
        try:
            address = address_info[4][0]
        except (IndexError, TypeError):
            return (False, "resolution_failed")
        if not _is_safe_ip_address(address):
            return (False, "unsafe_address")

    return (True, "")


def validate_http_redirect_target(
    url: str, resolve_hostname: bool = False
) -> Tuple[bool, str]:
    """Validate a redirect URL and optionally all addresses returned by DNS."""
    safe, reason = _validate_http_target(
        url,
        resolve_hostname=resolve_hostname,
        allow_private_networks=False,
    )
    failure_reasons = {
        "invalid": "Unsafe or invalid redirect URL",
        "resolution_failed": "Redirect hostname resolution failed",
        "unsafe_address": "Unsafe redirect address",
    }
    return (safe, failure_reasons.get(reason, ""))


def validate_feed_request_target(url: str) -> Tuple[bool, str]:
    """Apply the configured safety policy to any URL immediately before fetching."""
    allow_private_networks = bool(
        getattr(settings, "FEEDS_ALLOW_PRIVATE_NETWORKS", False)
    )
    safe, reason = _validate_http_target(
        url,
        resolve_hostname=not allow_private_networks,
        allow_private_networks=allow_private_networks,
    )
    failure_reasons = {
        "invalid": "Unsafe or invalid feed URL",
        "resolution_failed": "Feed hostname resolution failed",
        "unsafe_address": "Unsafe feed address",
    }
    return (safe, failure_reasons.get(reason, ""))


def is_safe_http_redirect_target(url: str, resolve_hostname: bool = False) -> bool:
    """Return whether a redirect target passes URL and optional DNS checks."""
    safe, _reason = validate_http_redirect_target(url, resolve_hostname)
    return safe
