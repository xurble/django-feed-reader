"""Tests for redirect URL validation and default FEEDS_SERVER derivation."""

import socket
from unittest.mock import patch

from django.test import SimpleTestCase

from feeds.url_safety import (
    derive_default_feeds_server,
    is_safe_http_redirect_target,
    resolve_feed_redirect_location,
    validate_http_redirect_target,
)


class DeriveDefaultFeedsServerTests(SimpleTestCase):
    def test_uses_https_for_first_dotted_host(self):
        self.assertEqual(
            derive_default_feeds_server(["localhost", "api.example.org"]),
            "https://api.example.org",
        )

    def test_unknown_when_no_dotted_host(self):
        self.assertEqual(derive_default_feeds_server(["localhost"]), "Unknown Server")


class ResolveFeedRedirectLocationTests(SimpleTestCase):
    def test_scheme_relative_location(self):
        resolved = resolve_feed_redirect_location(
            "//other.example/feed", "http://a.com/rss.xml"
        )
        self.assertEqual(resolved, "http://other.example/feed")

    def test_absolute_path(self):
        resolved = resolve_feed_redirect_location("/feed", "http://a.com/original.xml")
        self.assertEqual(resolved, "http://a.com/feed")


class IsSafeHttpRedirectTargetTests(SimpleTestCase):
    def test_allows_public_http_url(self):
        self.assertTrue(is_safe_http_redirect_target("http://new.feed.com/"))

    def test_allows_public_https_url(self):
        self.assertTrue(
            is_safe_http_redirect_target("https://feeds.example.org/news.atom")
        )

    def test_rejects_file_scheme(self):
        self.assertFalse(is_safe_http_redirect_target("file:///etc/passwd"))

    def test_rejects_loopback_ipv4(self):
        self.assertFalse(is_safe_http_redirect_target("http://127.0.0.1:8080/admin"))

    def test_rejects_private_ipv4(self):
        self.assertFalse(is_safe_http_redirect_target("http://192.168.1.1/"))

    def test_rejects_metadata_ip(self):
        self.assertFalse(
            is_safe_http_redirect_target("http://169.254.169.254/latest/meta-data")
        )

    def test_rejects_localhost_hostname(self):
        self.assertFalse(is_safe_http_redirect_target("http://localhost/feed"))

    def test_rejects_empty(self):
        self.assertFalse(is_safe_http_redirect_target(""))

    def test_rejects_backslash_authority_ambiguity(self):
        self.assertFalse(
            is_safe_http_redirect_target(
                "http://127.0.0.1\\@example.com/", resolve_hostname=True
            )
        )

    @patch("feeds.url_safety.socket.getaddrinfo")
    def test_dns_validation_allows_public_addresses(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80)),
            (
                socket.AF_INET6,
                socket.SOCK_STREAM,
                6,
                "",
                ("2606:2800:220:1:248:1893:25c8:1946", 80, 0, 0),
            ),
        ]

        safe, reason = validate_http_redirect_target(
            "http://public.example/feed", resolve_hostname=True
        )

        self.assertTrue(safe)
        self.assertEqual(reason, "")

    @patch("feeds.url_safety.socket.getaddrinfo")
    def test_dns_validation_rejects_any_private_address(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 80)),
        ]

        safe, reason = validate_http_redirect_target(
            "http://mixed.example/feed", resolve_hostname=True
        )

        self.assertFalse(safe)
        self.assertEqual(reason, "Unsafe redirect address")

    @patch("feeds.url_safety.socket.getaddrinfo", side_effect=socket.gaierror)
    def test_dns_validation_fails_closed(self, _mock_getaddrinfo):
        safe, reason = validate_http_redirect_target(
            "http://missing.example/feed", resolve_hostname=True
        )

        self.assertFalse(safe)
        self.assertEqual(reason, "Redirect hostname resolution failed")
