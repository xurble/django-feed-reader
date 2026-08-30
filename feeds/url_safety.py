"""Validate HTTP(S) redirect targets to reduce SSRF risk when following Location headers."""

import ipaddress
import socket
from typing import Tuple
from urllib.parse import urljoin, urlparse

__all__ = [
    "resolve_feed_redirect_location",
    "is_safe_http_redirect_target",
    "validate_http_redirect_target",
    "derive_default_feeds_server",
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


def validate_http_redirect_target(
    url: str, resolve_hostname: bool = False
) -> Tuple[bool, str]:
    """Validate a redirect URL and optionally all addresses returned by DNS."""
    invalid_result = (False, "Unsafe or invalid redirect URL")
    if not url or not isinstance(url, str):
        return invalid_result
    # Requests and urllib.parse disagree about backslashes in URL authorities.
    # Reject them before parsing so validation and connection cannot target
    # different hosts (for example, ``127.0.0.1\\@example.com``).
    if "\\" in url:
        return invalid_result
    try:
        parsed = urlparse(url)
    except (TypeError, ValueError):
        return invalid_result
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return invalid_result
    host = parsed.hostname
    if not host:
        return invalid_result
    host_lower = host.lower().rstrip(".")
    if host_lower in _BLOCKED_HOSTNAMES:
        return invalid_result
    if host_lower.endswith(".local") or host_lower.endswith(".localhost"):
        return invalid_result

    try:
        ipaddress.ip_address(host_lower)
    except ValueError:
        if not resolve_hostname:
            return (True, "")
    else:
        return (True, "") if _is_safe_ip_address(host_lower) else invalid_result

    try:
        port = parsed.port or (443 if scheme == "https" else 80)
        addresses = socket.getaddrinfo(
            host_lower,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except (OSError, ValueError):
        return (False, "Redirect hostname resolution failed")

    if not addresses:
        return (False, "Redirect hostname resolution failed")
    for address_info in addresses:
        try:
            address = address_info[4][0]
        except (IndexError, TypeError):
            return (False, "Redirect hostname resolution failed")
        if not _is_safe_ip_address(address):
            return (False, "Unsafe redirect address")

    return (True, "")


def is_safe_http_redirect_target(url: str, resolve_hostname: bool = False) -> bool:
    """Return whether a redirect target passes URL and optional DNS checks."""
    safe, _reason = validate_http_redirect_target(url, resolve_hostname)
    return safe
