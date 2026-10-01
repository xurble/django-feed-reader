import hashlib
import json
from importlib import reload

import requests_mock
from django.conf import settings
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from feeds import utils, utils_internal
from feeds.models import Enclosure, Post, Source
from feeds.utils import read_feed

from .base import BASE_URL, BaseTest, NullOutput


@requests_mock.Mocker()
class JSONFeedTest(BaseTest):
    def test_guid_lookup_uses_digest_index_without_loading_history(self, mock):
        src = Source.objects.create(feed_url=BASE_URL)
        matched = Post.objects.create(
            source=src, index=1, guid="known", title="Known", created=timezone.now()
        )
        Post.objects.bulk_create([
            Post(source=src, index=i + 2, guid=f"history-{i}", created=timezone.now())
            for i in range(100)
        ])

        with CaptureQueriesContext(connection) as queries:
            found = utils_internal._posts_by_guid_lookup(src, {"known"})

        self.assertEqual(list(found), ["known"])
        self.assertEqual(found["known"].pk, matched.pk)
        post_reads = [q["sql"] for q in queries if
                      'FROM "feeds_post"' in q["sql"] and q["sql"].lstrip().startswith("SELECT")]
        self.assertEqual(len(post_reads), 1)
        self.assertIn('"guid_digest" IN', post_reads[0])
        self.assertNotIn('"guid" IN', post_reads[0])
        if connection.vendor == "sqlite":
            plan = Post.objects.filter(
                source=src, guid_digest=matched.guid_digest
            ).explain()
            self.assertIn("source_id=? AND guid_digest=?", plan)

    def test_guid_lookup_recovers_missing_and_stale_digests(self, mock):
        src = Source.objects.create(feed_url=BASE_URL)
        missing = Post.objects.bulk_create([
            Post(source=src, index=1, guid="missing", created=timezone.now())
        ])[0]
        stale = Post.objects.create(
            source=src, index=2, guid="stale", created=timezone.now()
        )
        Post.objects.filter(pk=stale.pk).update(guid_digest="0" * 64)

        found = utils_internal._posts_by_guid_lookup(src, {"missing", "stale"})

        self.assertEqual({guid: post.pk for guid, post in found.items()}, {
            "missing": missing.pk, "stale": stale.pk,
        })

    def test_guid_fallback_uses_guid_index_and_filters_other_sources(self, mock):
        src = Source.objects.create(feed_url=BASE_URL)
        other = Source.objects.create(feed_url=BASE_URL + "other")
        legacy = Post.objects.bulk_create([
            Post(source=src, index=1, guid="legacy", created=timezone.now())
        ])[0]
        Enclosure.objects.create(post=legacy, href=BASE_URL + "media", type="audio/mpeg")
        Post.objects.bulk_create([
            Post(source=other, index=1, guid="legacy", created=timezone.now()),
            Post(source=other, index=2, guid="other-only", created=timezone.now()),
            *[
                Post(source=src, index=i + 2, guid=f"history-{i}", created=timezone.now())
                for i in range(100)
            ],
        ])

        with CaptureQueriesContext(connection) as queries:
            found = utils_internal._posts_by_guid_lookup(
                src, {"legacy", "other-only", "brand-new"}
            )

        self.assertEqual({guid: post.pk for guid, post in found.items()}, {"legacy": legacy.pk})
        self.assertEqual(
            [enclosure.href for enclosure in found["legacy"].enclosures.all()],
            [BASE_URL + "media"],
        )
        post_reads = [q["sql"] for q in queries if
                      'FROM "feeds_post"' in q["sql"] and q["sql"].lstrip().startswith("SELECT")]
        self.assertEqual(len(post_reads), 3)  # digest, narrow GUID probe, matching PK hydration
        enclosure_reads = [q for q in queries if
                           'FROM "feeds_enclosure"' in q["sql"] and q["sql"].lstrip().startswith("SELECT")]
        self.assertEqual(len(enclosure_reads), 1)
        self.assertIn('"guid" IN', post_reads[1])
        self.assertNotIn('"source_id" =', post_reads[1])
        self.assertNotIn('"index"', post_reads[1])
        if connection.vendor == "sqlite":
            plan = Post.objects.filter(guid__in=["brand-new"]).order_by().values_list(
                "pk", "source_id", "guid"
            ).explain()
            self.assertIn("USING INDEX", plan)
            self.assertIn("(guid=?)", plan)

    def test_guid_lookup_batches_large_incoming_sets(self, mock):
        src = Source.objects.create(feed_url=BASE_URL)
        incoming = {f"new-{i}" for i in range(801)}

        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(utils_internal._posts_by_guid_lookup(src, incoming), {})

        post_reads = [q for q in queries if
                      'FROM "feeds_post"' in q["sql"] and q["sql"].lstrip().startswith("SELECT")]
        self.assertEqual(len(post_reads), 6)  # Three digest and three GUID probes

    def test_guid_lookup_rejects_mismatched_digest_candidate(self, mock):
        src = Source.objects.create(feed_url=BASE_URL)
        post = Post.objects.create(
            source=src, index=1, guid="different", created=timezone.now()
        )
        Post.objects.filter(pk=post.pk).update(
            guid_digest=hashlib.sha256(b"target").hexdigest()
        )

        self.assertEqual(utils_internal._posts_by_guid_lookup(src, {"target"}), {})

    def test_duplicate_new_guid_reloads_enclosures_for_later_entry(self, mock):
        src = Source.objects.create(feed_url=BASE_URL)
        feed = {"title": "Feed", "items": [
            {"id": "duplicate", "attachments": [{"url": "http://feed.com/b"}]},
            {"id": "duplicate", "attachments": [{"url": "http://feed.com/a"}]},
        ]}

        self.assertEqual(
            utils_internal.parse_feed_json(src, json.dumps(feed), NullOutput()),
            (True, True),
        )
        self.assertEqual(
            list(src.posts.get().current_enclosures.values_list("href", flat=True)),
            ["http://feed.com/b"],
        )

    def test_refresh_loads_only_incoming_posts_and_enclosures(self, mock):
        src = Source.objects.create(feed_url=BASE_URL)
        matched = Post.objects.create(
            source=src, index=1, guid="known", title="Old",
            body="", created=timezone.now(),
        )
        unrelated = Post.objects.create(
            source=src, index=2, guid="unrelated-history", title="Old",
            body="", created=timezone.now(),
        )
        for post in (matched, unrelated):
            Enclosure.objects.create(post=post, href=f"http://feed.com/{post.pk}", type="audio/mpeg")
        Post.objects.bulk_create([
            Post(source=src, index=i + 3, guid=f"historical-{i}",
                 title="Historical", body="", created=timezone.now())
            for i in range(40)
        ])
        feed = {"title": "Feed", "home_page_url": BASE_URL, "items": [
            {"id": "known", "title": "Known", "content_text": "Known"},
            {"id": "new", "title": "New", "content_text": "New"},
        ]}

        with CaptureQueriesContext(connection) as queries:
            ok, changed = utils_internal.parse_feed_json(src, json.dumps(feed), NullOutput())

        self.assertEqual((ok, changed), (True, True))
        post_reads = [q["sql"] for q in queries if
                      'FROM "feeds_post"' in q["sql"] and q["sql"].lstrip().startswith("SELECT")]
        self.assertEqual(len(post_reads), 2)
        self.assertIn('"guid_digest" IN', post_reads[0])
        self.assertIn('"guid" IN', post_reads[1])
        enclosure_reads = [q["sql"] for q in queries if
                           'FROM "feeds_enclosure"' in q["sql"] and '"post_id" IN' in q["sql"]
                           and q["sql"].lstrip().startswith("SELECT")]
        self.assertEqual(len(enclosure_reads), 1)
        self.assertIn(f'"post_id" IN ({matched.pk})', enclosure_reads[0])
        self.assertEqual(src.posts.count(), 43)

    def test_simple_json(self, mock):

        self._populate_mock(
            mock,
            status=200,
            test_file="json_simple_two_entry.json",
            content_type="application/json",
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

        self._populate_mock(
            mock,
            status=200,
            test_file="json_simple_two_entry.json",
            content_type="application/json",
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()
        self.assertEqual(src.json["title"], src.name)

        post = src.posts.all()[0]
        self.assertEqual(post.json["url"], post.link)

    def test_sanitize_1(self, mock):

        self._populate_mock(
            mock,
            status=200,
            test_file="json_simple_two_entry.json",
            content_type="application/json",
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
            test_file="sanitizer_bad_comment.json",
            content_type="application/json",
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        # read the feed to update the name
        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 200)
        self.assertEqual(src.name, "safe")

    def test_podcast(self, mock):

        self._populate_mock(
            mock, status=200, test_file="podcast.json", content_type="application/json"
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        # read the feed to update the name
        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.status_code, 200)

        post = src.posts.all()[0]

        self.assertEqual(post.enclosures.count(), 1)

    def test_keep_old_attachment_reactivates_returning_url(self, mock):

        settings.FEEDS_KEEP_OLD_ENCLOSURES = True

        # to pick up the settings change
        reload(utils)
        reload(utils_internal)

        self._populate_mock(
            mock,
            status=200,
            test_file="json_attachment_a.json",
            content_type="application/json",
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())

        self._populate_mock(
            mock,
            status=200,
            test_file="json_attachment_b.json",
            content_type="application/json",
        )

        read_feed(src, output=NullOutput())

        self._populate_mock(
            mock,
            status=200,
            test_file="json_attachment_a.json",
            content_type="application/json",
        )

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        post = src.posts.get()
        self.assertEqual(post.enclosures.count(), 2)
        self.assertEqual(post.current_enclosures.count(), 1)
        self.assertEqual(post.old_enclosures.count(), 1)
        self.assertEqual(
            post.current_enclosures.get().href,
            "https://example.org/attachment-a.mp3",
        )

    def test_expired_json_feed(self, mock):
        """parse_feed_json must return a 2-tuple for expired feeds."""

        self._populate_mock(
            mock,
            status=200,
            test_file="json_expired.json",
            content_type="application/json",
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.last_result, "This feed has expired")
        self.assertEqual(src.interval, 60 * 24)  # capped to max by read_feed

    def test_json_feed_saves_name_and_icon(self, mock):
        """parse_feed_json must use correct field names in update_fields."""

        self._populate_mock(
            mock,
            status=200,
            test_file="json_simple_two_entry.json",
            content_type="application/json",
        )

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.name, "My Example Feed")
        self.assertEqual(src.site_url, "https://example.org/")
        self.assertEqual(src.image_url, "https://example.org/feed.png")
