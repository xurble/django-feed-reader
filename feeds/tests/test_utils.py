from django.test import TestCase

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
