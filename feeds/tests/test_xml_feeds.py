from importlib import reload

from django.conf import settings
from django.utils import timezone
import requests_mock

from feeds.models import Source
from feeds.utils_internal import hash_body
from feeds.utils import read_feed
from feeds import utils
from feeds import utils_internal

from .base import BaseTest, NullOutput, BASE_URL


@requests_mock.Mocker()
class XMLFeedsTest(BaseTest):

    def test_item_without_enclosures_list(self, mock):
        """Entries without an enclosures key must not raise KeyError."""

        self._populate_mock(mock, status=200, test_file="rss_single_item_no_enclosure.xml", content_type="application/rss+xml")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 200)
        self.assertEqual(src.posts.count(), 1)
        self.assertEqual(src.posts.first().enclosures.count(), 0)

    def test_simple_xml(self, mock):

        self._populate_mock(mock, status=200, test_file="rss_xhtml_body.xml", content_type="application/rss+xml")

        ls = timezone.now()
        src = Source(name="test1", feed_url=BASE_URL, interval=0, last_success=ls, last_change=ls)
        src.save()

        # Read the feed once to get the 1 post  and the etag
        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 200)
        self.assertEqual(src.posts.count(), 1)  # got the one post
        self.assertEqual(src.interval, 60)
        self.assertEqual(src.etag, "an-etag")
        self.assertNotEqual(src.last_success, ls)
        self.assertNotEqual(src.last_change, ls)

    def test_podcast(self, mock):

        self._populate_mock(mock, status=200, test_file="podcast.xml", content_type="application/rss+xml")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        # Read the feed once to get the 1 post  and the etag
        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.description, 'SU: Three nerds discussing tech, Apple, programming, and loosely related matters.')

        self.assertEqual(src.posts.all()[0].enclosures.count(), 1)

        enc = src.posts.all()[0].enclosures.all()[0]

        self.assertEqual(enc.href, "http://traffic.libsyn.com/atpfm/atp238.mp3")

    def test_mastodon(self, mock):

        self._populate_mock(mock, status=200, test_file="mastodon.xml", content_type="application/rss+xml")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.description, 'Public posts from @xurble@toot.community')

        self.assertEqual(src.posts.all()[0].enclosures.count(), 1)

    def test_media_content(self, mock):

        self._populate_mock(mock, status=200, test_file="media_content.xml", content_type="application/rss+xml")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        post = src.posts.all()[0]
        self.assertEqual(post.enclosures.count(), 1)

        self.assertEqual(post.body, "<p>New job, new Mac.</p>")

        enc = post.enclosures.all()[0]

        self.assertEqual(enc.href, "https://static.toot.community/media_attachments/files/111/981/336/553/711/283/original/d83ded1af64141ba.jpeg")
        self.assertEqual(enc.description, "This is the alt text.")

    def test_keep_old_enclosure(self, mock):

        settings.FEEDS_KEEP_OLD_ENCLOSURES = True

        # to pick up the settings change
        reload(utils)
        reload(utils_internal)

        self._populate_mock(mock, status=200, test_file="media_content.xml", content_type="application/rss+xml")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())

        self._populate_mock(mock, status=200, test_file="media_content_changed.xml", content_type="application/rss+xml")

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        post = src.posts.all()[0]
        self.assertEqual(post.enclosures.count(), 2)

        self.assertEqual(post.current_enclosures.count(), 1)
        self.assertEqual(post.old_enclosures.count(), 1)

        enc = post.current_enclosures.all()[0]

        self.assertEqual(enc.href, "https://static.toot.community/media_attachments/files/111/981/336/553/711/283/original/d83ded1af64141ba_new.jpeg")

    def test_save_json(self, mock):

        settings.FEEDS_SAVE_JSON = True

        # to pick up the settings change
        reload(utils)
        reload(utils_internal)

        self._populate_mock(mock, status=200, test_file="media_content.xml", content_type="application/rss+xml")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()
        self.assertEqual(src.json["feed"]["link"], "https://toot.community/@xurble")

        post = src.posts.all()[0]
        self.assertEqual(post.json["summary"], post.body)

    def test_sanitize_1(self, mock):
        """Make sure feedparser's sanitization is running."""

        self._populate_mock(mock, status=200, test_file="rss_xhtml_body.xml", content_type="application/rss+xml")

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

        self._populate_mock(mock, status=200, test_file="sanitizer_bad_comment.xml", content_type="application/rss+xml")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        # read the feed to update the name
        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 200)
        self.assertEqual(src.name, "safe")

    def test_sanitize_attrs(self, mock):

        self._populate_mock(mock, status=200, test_file="sanitizer_img_attrs.xml", content_type="application/rss+xml")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        # read the feed to update the name
        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 200)

        body = src.posts.all()[0].body

        self.assertTrue("<img" in body)
        self.assertFalse("align=" in body)
        self.assertFalse("hspace=" in body)

    def create_source(self, mock, test_name, test_fn):
        self._populate_mock(mock, status=200,
                            test_file=test_fn,
                            content_type="application/rss+xml")
        src = Source(name=test_name, feed_url=BASE_URL, interval=0)
        src.save()
        # read the feed to update the name
        read_feed(src, output=NullOutput())
        src.refresh_from_db()
        self.assertEqual(src.status_code, 200)
        return src

    def test_catch_long_guid_short_url(self, mock):
        test_name = "long guid short url"
        src = self.create_source(mock, test_name, "long_guid_tests.xml")
        # post with long guid should have hash guid
        p = src.posts.get(title=test_name)
        self.assertEqual(p.guid, p.link)

    def test_catch_long_guid_long_url(self, mock):
        test_name = "long guid long url"
        src = self.create_source(mock, test_name, "long_guid_tests.xml")
        # post with long guid should have hash guid
        p = src.posts.get(title=test_name)
        self.assertEqual(p.guid, hash_body(p.body))
