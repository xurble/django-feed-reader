import os
from importlib import reload

import requests_mock
from django.conf import settings
from django.test import override_settings
from django.utils import timezone

from feeds import utils, utils_internal
from feeds.models import Source
from feeds.utils import read_feed
from feeds.utils_internal import hash_body

from .base import BASE_URL, TEST_FILES_FOLDER, BaseTest, NullOutput


def _atom_feed(entry_ids, next_url=None):
    next_link = ""
    if next_url is not None:
        next_link = f'<link href="{next_url}" rel="next"/>'
    entries = "".join(
        f"""
        <entry>
          <title>Item {entry_id}</title>
          <id>tag:example.org,2024:{entry_id}</id>
          <updated>2024-01-01T12:00:00Z</updated>
          <content type="html">Item {entry_id}</content>
        </entry>
        """
        for entry_id in entry_ids
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <title>Paged feed</title>
      <link href="{BASE_URL}" rel="self"/>
      {next_link}
      <updated>2024-01-01T12:00:00Z</updated>
      {entries}
    </feed>
    """.encode("utf-8")


@requests_mock.Mocker()
class XMLFeedsTest(BaseTest):
    def test_atom_follows_rel_next_on_first_parse(self, mock):
        """First full parse should follow atom:link[@rel='next'] and merge pages."""

        self._populate_mock(
            mock,
            status=200,
            test_file="atom_paged_1.xml",
            content_type="application/atom+xml",
        )
        content2 = open(
            os.path.join(TEST_FILES_FOLDER, "atom_paged_2.xml"), "rb"
        ).read()
        mock.register_uri(
            "GET",
            "http://feed.com/atom_paged_2.xml",
            status_code=200,
            content=content2,
            headers={"Content-Type": "application/atom+xml", "etag": "page2"},
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.posts.count(), 2)
        titles = {p.title for p in src.posts.all()}
        self.assertIn("First page item", titles)
        self.assertIn("Second page item", titles)

    def test_atom_stops_at_repeated_pagination_url(self, mock):
        mock.register_uri(
            "GET",
            BASE_URL,
            status_code=200,
            content=_atom_feed(["first"], next_url=BASE_URL),
            headers={"Content-Type": "application/atom+xml"},
        )
        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.posts.count(), 1)
        self.assertEqual(len(mock.request_history), 1)
        self.assertEqual(src.last_result, "Pagination stopped at repeated URL")

    def test_atom_stops_at_two_url_pagination_cycle(self, mock):
        second_url = BASE_URL + "page-2.xml"
        mock.register_uri(
            "GET",
            BASE_URL,
            status_code=200,
            content=_atom_feed(["first"], next_url=second_url),
            headers={"Content-Type": "application/atom+xml"},
        )
        mock.register_uri(
            "GET",
            second_url,
            status_code=200,
            content=_atom_feed(["second"], next_url=BASE_URL),
            headers={"Content-Type": "application/atom+xml"},
        )
        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.posts.count(), 2)
        self.assertEqual(len(mock.request_history), 2)
        self.assertEqual(src.last_result, "Pagination stopped at repeated URL")

    @override_settings(FEEDS_MAX_PAGINATION_PAGES=1)
    def test_atom_stops_at_pagination_page_limit(self, mock):
        second_url = BASE_URL + "page-2.xml"
        third_url = BASE_URL + "page-3.xml"
        mock.register_uri(
            "GET",
            BASE_URL,
            status_code=200,
            content=_atom_feed(["first"], next_url=second_url),
            headers={"Content-Type": "application/atom+xml"},
        )
        mock.register_uri(
            "GET",
            second_url,
            status_code=200,
            content=_atom_feed(["second"], next_url=third_url),
            headers={"Content-Type": "application/atom+xml"},
        )
        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.posts.count(), 2)
        self.assertEqual(len(mock.request_history), 2)
        self.assertEqual(src.last_result, "Pagination stopped at 1 page")

    @override_settings(FEEDS_MAX_PAGINATION_ENTRIES=2)
    def test_atom_stops_at_total_entry_limit(self, mock):
        second_url = BASE_URL + "page-2.xml"
        mock.register_uri(
            "GET",
            BASE_URL,
            status_code=200,
            content=_atom_feed(["first"], next_url=second_url),
            headers={"Content-Type": "application/atom+xml"},
        )
        mock.register_uri(
            "GET",
            second_url,
            status_code=200,
            content=_atom_feed(["second", "third"]),
            headers={"Content-Type": "application/atom+xml"},
        )
        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.posts.count(), 2)
        self.assertEqual(src.last_result, "Pagination stopped at 2 entries")

    def test_atom_stops_when_pagination_request_fails(self, mock):
        second_url = BASE_URL + "page-2.xml"
        mock.register_uri(
            "GET",
            BASE_URL,
            status_code=200,
            content=_atom_feed(["first"], next_url=second_url),
            headers={"Content-Type": "application/atom+xml"},
        )
        mock.register_uri("GET", second_url, status_code=503)
        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.posts.count(), 1)
        self.assertTrue(src.last_result.startswith("Pagination request failed:"))

    def test_atom_does_not_request_unsafe_pagination_url(self, mock):
        mock.register_uri(
            "GET",
            BASE_URL,
            status_code=200,
            content=_atom_feed(["first"], next_url="http://127.0.0.1/private"),
            headers={"Content-Type": "application/atom+xml"},
        )
        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.posts.count(), 1)
        self.assertEqual(len(mock.request_history), 1)
        self.assertEqual(src.last_result, "Pagination stopped at unsafe URL")

    def test_item_without_enclosures_list(self, mock):
        """Entries without an enclosures key must not raise KeyError."""

        self._populate_mock(
            mock,
            status=200,
            test_file="rss_single_item_no_enclosure.xml",
            content_type="application/rss+xml",
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 200)
        self.assertEqual(src.posts.count(), 1)
        self.assertEqual(src.posts.first().enclosures.count(), 0)

    def test_simple_xml(self, mock):

        self._populate_mock(
            mock,
            status=200,
            test_file="rss_xhtml_body.xml",
            content_type="application/rss+xml",
        )

        ls = timezone.now()
        src = Source(
            name="test1", feed_url=BASE_URL, interval=0, last_success=ls, last_change=ls
        )
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

        self._populate_mock(
            mock,
            status=200,
            test_file="podcast.xml",
            content_type="application/rss+xml",
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        # Read the feed once to get the 1 post  and the etag
        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(
            src.description,
            "SU: Three nerds discussing tech, Apple, programming, and loosely related matters.",
        )

        self.assertEqual(src.posts.all()[0].enclosures.count(), 1)

        enc = src.posts.all()[0].enclosures.all()[0]

        self.assertEqual(enc.href, "http://traffic.libsyn.com/atpfm/atp238.mp3")

    def test_mastodon(self, mock):

        self._populate_mock(
            mock,
            status=200,
            test_file="mastodon.xml",
            content_type="application/rss+xml",
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.description, "Public posts from @xurble@toot.community")

        self.assertEqual(src.posts.all()[0].enclosures.count(), 1)

    def test_media_content(self, mock):

        self._populate_mock(
            mock,
            status=200,
            test_file="media_content.xml",
            content_type="application/rss+xml",
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        post = src.posts.all()[0]
        self.assertEqual(post.enclosures.count(), 1)

        self.assertEqual(post.body, "<p>New job, new Mac.</p>")

        enc = post.enclosures.all()[0]

        self.assertEqual(
            enc.href,
            "https://static.toot.community/media_attachments/files/111/981/336/553/711/283/original/d83ded1af64141ba.jpeg",
        )
        self.assertEqual(enc.description, "This is the alt text.")

    def test_keep_old_enclosure(self, mock):

        settings.FEEDS_KEEP_OLD_ENCLOSURES = True

        # to pick up the settings change
        reload(utils)
        reload(utils_internal)

        self._populate_mock(
            mock,
            status=200,
            test_file="media_content.xml",
            content_type="application/rss+xml",
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())

        self._populate_mock(
            mock,
            status=200,
            test_file="media_content_changed.xml",
            content_type="application/rss+xml",
        )

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        post = src.posts.all()[0]
        self.assertEqual(post.enclosures.count(), 2)

        self.assertEqual(post.current_enclosures.count(), 1)
        self.assertEqual(post.old_enclosures.count(), 1)

        enc = post.current_enclosures.all()[0]

        self.assertEqual(
            enc.href,
            "https://static.toot.community/media_attachments/files/111/981/336/553/711/283/original/d83ded1af64141ba_new.jpeg",
        )

    def test_save_json(self, mock):

        settings.FEEDS_SAVE_JSON = True

        # to pick up the settings change
        reload(utils)
        reload(utils_internal)

        self._populate_mock(
            mock,
            status=200,
            test_file="media_content.xml",
            content_type="application/rss+xml",
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()
        self.assertEqual(src.json["feed"]["link"], "https://toot.community/@xurble")

        post = src.posts.all()[0]
        self.assertEqual(post.json["summary"], post.body)

    def test_sanitize_1(self, mock):
        """Make sure feedparser's sanitization is running."""

        self._populate_mock(
            mock,
            status=200,
            test_file="rss_xhtml_body.xml",
            content_type="application/rss+xml",
        )

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

        self._populate_mock(
            mock,
            status=200,
            test_file="sanitizer_bad_comment.xml",
            content_type="application/rss+xml",
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        # read the feed to update the name
        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 200)
        self.assertEqual(src.name, "safe")

    def test_sanitize_attrs(self, mock):

        self._populate_mock(
            mock,
            status=200,
            test_file="sanitizer_img_attrs.xml",
            content_type="application/rss+xml",
        )

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
        self._populate_mock(
            mock, status=200, test_file=test_fn, content_type="application/rss+xml"
        )
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
