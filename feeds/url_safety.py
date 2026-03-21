"""Validate HTTP(S) redirect targets to reduce SSRF risk when following Location headers."""

import ipaddress
from urllib.parse import urljoin, urlparse

__all__ = [
    "resolve_feed_redirect_location",
    "is_safe_http_redirect_target",
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


def is_safe_http_redirect_target(url: str) -> bool:
    """
    Return True if url is an absolute http(s) URL with a host that is not obviously
    private, loopback, link-local, or cloud metadata. Hostnames are not DNS-resolved.
    """
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
    except (TypeError, ValueError):
        return False
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return False
    host = parsed.hostname
    if not host:
        return False
    host_lower = host.lower().rstrip(".")
    if host_lower in _BLOCKED_HOSTNAMES:
        return False
    if host_lower.endswith(".local") or host_lower.endswith(".localhost"):
        return False

    # Strip IPv6 brackets for ipaddress
    host_for_ip = host_lower
    if host_for_ip.startswith("[") and host_for_ip.endswith("]"):
        host_for_ip = host_for_ip[1:-1]

    try:
        ip = ipaddress.ip_address(host_for_ip)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
        if ip == ipaddress.ip_address("169.254.169.254"):
            return False
    except ValueError:
        pass

    return True
