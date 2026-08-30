import socket
from datetime import timedelta
from importlib import reload
from unittest.mock import patch

import requests
import requests_mock
from django.conf import settings
from django.utils import timezone

from feeds import utils
from feeds.models import Source
from feeds.utils import read_feed

from .base import BASE_URL, BaseTest, NullOutput


@requests_mock.Mocker()
class HTTPStuffTest(BaseTest):
    def setUp(self):
        super().setUp()
        dns_patcher = patch("feeds.url_safety.socket.getaddrinfo")
        self.mock_getaddrinfo = dns_patcher.start()
        self.addCleanup(dns_patcher.stop)
        self.mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))
        ]

    def test_etags(self, mock):

        self._populate_mock(
            mock,
            status=200,
            test_file="rss_xhtml_body.xml",
            content_type="application/xml+rss",
        )
        self._populate_mock(
            mock,
            status=304,
            test_file="empty_file.txt",
            content_type="application/xml+rss",
            etag="an-etag",
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        # Read the feed once to get the 1 post  and the etag
        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 200)
        self.assertEqual(src.posts.count(), 1)  # got the one post
        self.assertEqual(src.interval, 60)
        self.assertEqual(src.etag, "an-etag")

        # Read the feed again to get a 304 and a small increment to the interval
        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.posts.count(), 1)  # should have no more
        self.assertEqual(src.status_code, 304)
        self.assertEqual(src.interval, 70)
        self.assertTrue(src.live)

    def test_304_clears_etag_when_last_change_stale(self, mock):

        self._populate_mock(
            mock,
            status=200,
            test_file="rss_xhtml_body.xml",
            content_type="application/xml+rss",
        )
        self._populate_mock(
            mock,
            status=304,
            test_file="empty_file.txt",
            content_type="application/xml+rss",
            etag="an-etag",
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()
        self.assertEqual(src.etag, "an-etag")

        Source.objects.filter(pk=src.pk).update(
            last_change=timezone.now() - timedelta(days=8)
        )
        src.refresh_from_db()
        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 304)
        self.assertEqual(
            src.last_result, "Clearing etag/last modified due to lack of changes"
        )
        self.assertIn(src.etag, (None, ""))

    def test_fetch_network_error_records_failure(self, mock):

        mock.register_uri(
            "GET", BASE_URL, exc=requests.exceptions.ConnectTimeout("timed out")
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 0)
        self.assertTrue(src.last_result.startswith("Fetch error:"))

    def test_http_400_disables_feed(self, mock):

        self._populate_mock(
            mock, status=400, test_file="empty_file.txt", content_type="text/plain"
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 400)
        self.assertFalse(src.live)
        self.assertIn("400", src.last_result)

    def test_http_401_disables_feed(self, mock):

        self._populate_mock(
            mock, status=401, test_file="empty_file.txt", content_type="text/plain"
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 401)
        self.assertFalse(src.live)

    def test_http_429_disables_feed(self, mock):

        self._populate_mock(
            mock, status=429, test_file="empty_file.txt", content_type="text/plain"
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 429)
        self.assertFalse(src.live)

    def test_not_a_feed(self, mock):

        self._populate_mock(
            mock,
            status=200,
            test_file="spurious_text_file.txt",
            content_type="text/plain",
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 200)  # it returned a page, but not a  feed
        self.assertEqual(src.posts.count(), 0)  # can't have got any
        self.assertEqual(src.interval, 120)
        self.assertTrue(src.live)

    def test_permission_denied(self, mock):

        self._populate_mock(
            mock, status=403, test_file="empty_file.txt", content_type="text/plain"
        )

        ls = timezone.now()

        src = Source(name="test1", feed_url=BASE_URL, interval=0, last_success=ls)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 403)  # it returned a page, but not a  feed
        self.assertEqual(src.posts.count(), 0)  # can't have got any
        self.assertFalse(src.live)

    def test_cloudflared_standard(self, mock):

        settings.FEEDS_DRIPFEED_KEY = "Key"

        # to pick up the settings change
        reload(utils)

        self._populate_mock(
            mock,
            status=403,
            test_file="empty_file.txt",
            content_type="text/plain",
            is_cloudflare=True,
        )

        mock.register_uri(
            "PUT",
            "https://dripfeed.app/api/v1/feeds/",
            content=b"""{"feed": {"uuid": "aa48333e-c40d-47ac-8a46-a13352dd8505", "name": "Elephant", "source_url": "http://feed.com/", "status_code": 200, "last_polled": "2024-03-17T18:48:19Z", "next_poll": "2024-03-25T03:06:08.991Z", "content_type": "text/plain", "etag": "06b06eb5", "error_code": "not-feed", "last_result": "Server response was not a feed", "dripfeed_url": "https://dripfeed.app/feed/aa48333e-c40d-47ac-8a46-a13352dd8505/", "live": true}, "detail": "OK"}""",
        )

        ls = timezone.now()

        src = Source(name="test1", feed_url=BASE_URL, interval=0, last_success=ls)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 403)  # it returned a page, but not a  feed
        self.assertEqual(src.posts.count(), 0)  # can't have got any
        self.assertTrue(src.live)
        self.assertTrue(src.is_cloudflare)
        self.assertEqual(
            src.alt_url,
            "https://dripfeed.app/feed/aa48333e-c40d-47ac-8a46-a13352dd8505/",
        )

    def test_cloudflared_already_dripfed(self, mock):

        settings.FEEDS_DRIPFEED_KEY = "Key"

        # to pick up the settings change
        reload(utils)

        self._populate_mock(
            mock,
            status=403,
            test_file="empty_file.txt",
            content_type="text/plain",
            is_cloudflare=True,
        )

        mock.register_uri(
            "PUT",
            "https://dripfeed.app/api/v1/feeds/",
            status_code=400,
            content=b"""{"detail": "Already subscribed to this feed."}""",
        )
        mock.register_uri(
            "GET",
            "https://dripfeed.app/api/v1/feeds/",
            content=b"""{"feeds": [{"uuid": "aa48333e-c40d-47ac-8a46-a13352dd8505", "name": "Elephant", "source_url": "http://feed.com/", "status_code": 200, "last_polled": "2024-03-17T18:48:19Z", "next_poll": "2024-03-25T03:06:08.991Z", "content_type": "text/plain", "etag": "06b06eb5", "error_code": "not-feed", "last_result": "Server response was not a feed", "dripfeed_url": "https://dripfeed.app/feed/aa48333e-c40d-47ac-8a46-a13352dd8505/", "live": true}], "detail": "OK"}""",
        )

        ls = timezone.now()

        src = Source(name="test1", feed_url=BASE_URL, interval=0, last_success=ls)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 403)  # it returned a page, but not a  feed
        self.assertEqual(src.posts.count(), 0)  # can't have got any
        self.assertTrue(src.live)
        self.assertTrue(src.is_cloudflare)
        self.assertEqual(
            src.alt_url,
            "https://dripfeed.app/feed/aa48333e-c40d-47ac-8a46-a13352dd8505/",
        )

    def test_cloudflared_cant_dripfeed(self, mock):

        settings.FEEDS_DRIPFEED_KEY = "Key"

        # to pick up the settings change
        reload(utils)

        self._populate_mock(
            mock,
            status=403,
            test_file="empty_file.txt",
            content_type="text/plain",
            is_cloudflare=True,
        )

        mock.register_uri(
            "PUT",
            "https://dripfeed.app/api/v1/feeds/",
            status_code=403,
            content=b"""{"detail": "Maximum number of feeds reached."}""",
        )

        ls = timezone.now()

        src = Source(name="test1", feed_url=BASE_URL, interval=0, last_success=ls)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 403)  # it returned a page, but not a  feed
        self.assertEqual(src.posts.count(), 0)  # can't have got any
        self.assertTrue(src.live)
        self.assertTrue(src.is_cloudflare)
        self.assertIsNone(src.alt_url)
        self.assertEqual(
            src.last_result, "Failed add to Dripfeed: Maximum number of feeds reached."
        )

    def test_feed_gone(self, mock):

        self._populate_mock(
            mock, status=410, test_file="empty_file.txt", content_type="text/plain"
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 410)  # it returned a page, but not a  feed
        self.assertEqual(src.posts.count(), 0)  # can't have got any
        self.assertFalse(src.live)

    def test_feed_not_found(self, mock):

        self._populate_mock(
            mock, status=404, test_file="empty_file.txt", content_type="text/plain"
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 404)  # it returned a page, but not a  feed
        self.assertEqual(src.posts.count(), 0)  # can't have got any
        self.assertTrue(src.live)
        self.assertEqual(src.interval, 120)

    def test_temp_redirect_rejects_unsafe_location(self, mock):

        unsafe = "http://127.0.0.1/internal"
        self._populate_mock(
            mock,
            status=302,
            test_file="empty_file.txt",
            content_type="text/plain",
            headers={"Location": unsafe},
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.last_result, "Unsafe or invalid redirect URL")
        self.assertEqual(src.posts.count(), 0)
        self.assertEqual(src.feed_url, BASE_URL)

    def test_temp_redirect_rejects_unsafe_later_hop(self, mock):
        safe_url = "http://safe.example/feed"
        unsafe_url = "http://127.0.0.1/internal"
        self._populate_mock(
            mock,
            status=302,
            test_file="empty_file.txt",
            content_type="text/plain",
            headers={"Location": safe_url},
        )
        self._populate_mock(
            mock,
            status=302,
            test_file="empty_file.txt",
            content_type="text/plain",
            headers={"Location": unsafe_url},
            url=safe_url,
        )
        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(len(mock.request_history), 2)
        self.assertEqual(src.last_result, "Unsafe or invalid redirect URL")
        self.assertEqual(src.posts.count(), 0)

    def test_temp_redirect_rejects_hostname_resolving_private(self, mock):
        safe_looking_url = "http://internal.example/feed"
        self.mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 80))
        ]
        self._populate_mock(
            mock,
            status=302,
            test_file="empty_file.txt",
            content_type="text/plain",
            headers={"Location": safe_looking_url},
        )
        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(len(mock.request_history), 1)
        self.assertEqual(src.last_result, "Unsafe redirect address")

    def test_temp_redirect_dns_failure_stops_before_request(self, mock):
        safe_looking_url = "http://missing.example/feed"
        self.mock_getaddrinfo.side_effect = socket.gaierror
        self._populate_mock(
            mock,
            status=302,
            test_file="empty_file.txt",
            content_type="text/plain",
            headers={"Location": safe_looking_url},
        )
        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(len(mock.request_history), 1)
        self.assertEqual(src.last_result, "Redirect hostname resolution failed")

    def test_perm_redirect_rejects_unsafe_location(self, mock):

        unsafe = "http://127.0.0.1/internal"
        self._populate_mock(
            mock,
            status=301,
            test_file="empty_file.txt",
            content_type="text/plain",
            headers={"Location": unsafe},
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.last_result, "Unsafe or invalid redirect URL")
        self.assertEqual(src.feed_url, BASE_URL)

    def test_perm_redirect_rejects_hostname_resolving_private(self, mock):
        safe_looking_url = "http://internal.example/feed"
        self.mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.2", 80))
        ]
        self._populate_mock(
            mock,
            status=301,
            test_file="empty_file.txt",
            content_type="text/plain",
            headers={"Location": safe_looking_url},
        )
        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.last_result, "Unsafe redirect address")
        self.assertEqual(src.feed_url, BASE_URL)

    def test_empty_response_body(self, mock):

        ret_headers = {"Content-Type": "application/rss+xml", "etag": "e"}
        mock.register_uri(
            "GET", BASE_URL, status_code=200, content=b"", headers=ret_headers
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 200)
        self.assertEqual(src.last_result, "Empty feed response")
        self.assertEqual(src.posts.count(), 0)

    def test_invalid_utf8_json_body(self, mock):

        ret_headers = {"Content-Type": "application/json", "etag": "e"}
        mock.register_uri(
            "GET", BASE_URL, status_code=200, content=b"{\xff", headers=ret_headers
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.last_result, "Feed body is not valid UTF-8")
        self.assertEqual(src.posts.count(), 0)

    def test_temp_redirect_relative_location(self, mock):

        resolved = "http://feed.com/second.xml"
        self._populate_mock(
            mock,
            status=302,
            test_file="empty_file.txt",
            content_type="text/plain",
            headers={"Location": "/second.xml"},
        )
        self._populate_mock(
            mock,
            status=200,
            test_file="rss_xhtml_body.xml",
            content_type="application/xml+rss",
            url=resolved,
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 200)
        self.assertEqual(src.posts.count(), 1)

    def test_temp_redirect_safe_multi_hop_relative_location(self, mock):
        first_url = "http://safe.example/first.xml"
        final_url = "http://safe.example/final.xml"
        self._populate_mock(
            mock,
            status=302,
            test_file="empty_file.txt",
            content_type="text/plain",
            headers={"Location": first_url},
        )
        self._populate_mock(
            mock,
            status=307,
            test_file="empty_file.txt",
            content_type="text/plain",
            headers={"Location": "/final.xml"},
            url=first_url,
        )
        self._populate_mock(
            mock,
            status=200,
            test_file="rss_xhtml_body.xml",
            content_type="application/xml+rss",
            url=final_url,
        )
        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(len(mock.request_history), 3)
        self.assertEqual(src.status_code, 200)
        self.assertEqual(src.posts.count(), 1)

    def test_temp_redirect_loop_stops_before_repeated_request(self, mock):
        first_url = "http://safe.example/first.xml"
        self._populate_mock(
            mock,
            status=302,
            test_file="empty_file.txt",
            content_type="text/plain",
            headers={"Location": first_url},
        )
        self._populate_mock(
            mock,
            status=302,
            test_file="empty_file.txt",
            content_type="text/plain",
            headers={"Location": BASE_URL},
            url=first_url,
        )
        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(len(mock.request_history), 2)
        self.assertEqual(src.last_result, "Redirect loop detected")

    def test_temp_redirect_hop_limit(self, mock):
        first_url = BASE_URL + "redirect/1"
        self._populate_mock(
            mock,
            status=302,
            test_file="empty_file.txt",
            content_type="text/plain",
            headers={"Location": first_url},
        )
        for hop in range(1, 11):
            self._populate_mock(
                mock,
                status=302,
                test_file="empty_file.txt",
                content_type="text/plain",
                headers={"Location": BASE_URL + f"redirect/{hop + 1}"},
                url=BASE_URL + f"redirect/{hop}",
            )
        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(len(mock.request_history), 11)
        self.assertEqual(src.last_result, "Redirect hop limit exceeded")

    def test_temp_redirect(self, mock):

        new_url = "http://new.feed.com/"
        self._populate_mock(
            mock,
            status=302,
            test_file="empty_file.txt",
            content_type="text/plain",
            headers={"Location": new_url},
        )
        self._populate_mock(
            mock,
            status=200,
            test_file="rss_xhtml_body.xml",
            content_type="application/xml+rss",
            url=new_url,
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        self.assertIsNone(src.last_302_start)

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 200)
        self.assertEqual(src.last_302_url, new_url)  # this is where  went
        self.assertIsNotNone(src.last_302_start)
        self.assertEqual(
            src.posts.count(), 1
        )  # after following redirect will have 1 post
        self.assertEqual(src.interval, 60)
        self.assertTrue(src.live)

        # do it all again -  shouldn't change
        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 200)  # it returned a page, but not a  feed
        self.assertEqual(src.last_302_url, new_url)  # this is where  went
        self.assertIsNotNone(src.last_302_start)
        self.assertEqual(
            src.posts.count(), 1
        )  # after following redirect will have 1 post
        self.assertEqual(src.interval, 80)
        self.assertTrue(src.live)

        # now we test making it permaent
        src.last_302_start = timezone.now() - timedelta(days=365)
        src.save()
        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 200)
        self.assertEqual(src.last_302_url, " ")
        self.assertIsNone(src.last_302_start)
        self.assertEqual(src.posts.count(), 1)
        self.assertEqual(src.interval, 100)
        self.assertEqual(src.feed_url, new_url)
        self.assertTrue(src.live)

    def test_perm_redirect(self, mock):

        new_url = "http://new.feed.com/"
        self._populate_mock(
            mock,
            status=301,
            test_file="empty_file.txt",
            content_type="text/plain",
            headers={"Location": new_url},
        )
        self._populate_mock(
            mock,
            status=200,
            test_file="rss_xhtml_body.xml",
            content_type="application/xml+rss",
            url=new_url,
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 301)
        self.assertEqual(src.interval, 60)
        self.assertEqual(src.feed_url, new_url)

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 200)
        self.assertEqual(src.posts.count(), 1)
        self.assertEqual(src.interval, 60)
        self.assertTrue(src.live)

    def test_server_error_1(self, mock):

        self._populate_mock(
            mock, status=500, test_file="empty_file.txt", content_type="text/plain"
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 500)  # error
        self.assertEqual(src.posts.count(), 0)  # can't have got any
        self.assertTrue(src.live)
        self.assertEqual(src.interval, 120)

    def test_server_error_2(self, mock):

        self._populate_mock(
            mock, status=503, test_file="empty_file.txt", content_type="text/plain"
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 503)  # error!
        self.assertEqual(src.posts.count(), 0)  # can't have got any
        self.assertTrue(src.live)
        self.assertEqual(src.interval, 120)
