from importlib import reload

from django.conf import settings
from django.utils import timezone
import requests_mock

from feeds.models import Source
from feeds.utils import read_feed
from feeds import utils
from feeds import utils_internal

from .base import BaseTest, NullOutput, BASE_URL


@requests_mock.Mocker()
class JSONFeedTest(BaseTest):

    def test_simple_json(self, mock):

        self._populate_mock(mock, status=200, test_file="json_simple_two_entry.json", content_type="application/json")

        ls = timezone.now()

        src = Source(name="test1", feed_url=BASE_URL, interval=0, last_success=ls, last_change=ls)
        src.save()

        # Read the feed once to get the 1 post  and the etag
        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 200)
        self.assertEqual(src.posts.count(), 2)  # got the one post
        self.assertEqual(src.interval, 60)
        self.assertEqual(src.etag, "an-etag")
        self.assertNotEqual(src.last_success, ls)
        self.assertNotEqual(src.last_change, ls)

    def test_save_json(self, mock):

        settings.FEEDS_SAVE_JSON = True

        # to pick up the settings change
        reload(utils)
        reload(utils_internal)

        self._populate_mock(mock, status=200, test_file="json_simple_two_entry.json", content_type="application/json")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()
        self.assertEqual(src.json["title"], src.name)

        post = src.posts.all()[0]
        self.assertEqual(post.json["url"], post.link)

    def test_sanitize_1(self, mock):

        self._populate_mock(mock, status=200, test_file="json_simple_two_entry.json", content_type="application/json")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        # Read the feed once to get the 1 post  and the etag
        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 200)
        p = src.posts.all()[0]

        self.assertFalse("<script>" in p.body)

    def test_sanitize_2(self, mock):
        """
            Another test that the sanitization is going on.  This time we have
            stolen a test case from the feedparser libarary
        """

        self._populate_mock(mock, status=200, test_file="sanitizer_bad_comment.json", content_type="application/json")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        # read the feed to update the name
        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 200)
        self.assertEqual(src.name, "safe")

    def test_podcast(self, mock):

        self._populate_mock(mock, status=200, test_file="podcast.json", content_type="application/json")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        # read the feed to update the name
        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 200)

        post = src.posts.all()[0]

        self.assertEqual(post.enclosures.count(), 1)

    def test_expired_json_feed(self, mock):
        """parse_feed_json must return a 2-tuple for expired feeds."""

        self._populate_mock(mock, status=200, test_file="json_expired.json", content_type="application/json")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.last_result, "This feed has expired")
        self.assertEqual(src.interval, 60 * 24)  # capped to max by read_feed

    def test_json_feed_saves_name_and_icon(self, mock):
        """parse_feed_json must use correct field names in update_fields."""

        self._populate_mock(mock, status=200, test_file="json_simple_two_entry.json", content_type="application/json")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.name, "My Example Feed")
        self.assertEqual(src.site_url, "https://example.org/")
        self.assertEqual(src.image_url, "https://example.org/feed.png")
