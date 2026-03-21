from django.test import TestCase

from feeds import utils as feeds_utils
from feeds import utils_internal
from feeds.utils_internal import fix_relative


class UtilsTest(TestCase):

    def test_fix_relative(self):

        url = "https://example.com/rss.xml"
        html = "<a href='/'><img src='/image.jpg'></a>"

        html = fix_relative(html, url)

        self.assertEqual(html, "<a href='https://example.com/'><img src='https://example.com/image.jpg'></a>")

    def test_fix_relative_protocol_relative_uses_https(self):
        url = "https://example.com/rss.xml"
        html = '<a href="//cdn.example.org/p"><img src="//cdn.example.org/i.jpg"></a>'
        html = fix_relative(html, url)
        self.assertIn("https://cdn.example.org", html)
        self.assertNotIn("http://cdn.example.org", html)

    def test_fix_relative_non_string_url_returns_html_unchanged(self):
        """Bad base URL must not crash sanitization; body is returned as-is."""
        html = "<p>x</p>"
        self.assertEqual(fix_relative(html, None), html)

    def test_verify_https_public_module_reexports_internal(self):
        """Avoid duplicating FEEDS_VERIFY_HTTPS handling in two modules."""
        self.assertIs(feeds_utils.VERIFY_HTTPS, utils_internal.VERIFY_HTTPS)
