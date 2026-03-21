import hashlib

import requests_mock

from django.test import SimpleTestCase, TransactionTestCase
from django.db import IntegrityError, transaction
from django.contrib.auth import get_user_model
from django.utils import timezone

from feeds.models import Source, Post, Enclosure, Subscription, default_due_poll
from feeds.utils import read_feed

from .base import BaseTest, NullOutput, BASE_URL


User = get_user_model()


class SourceDefaultDuePollTests(SimpleTestCase):

    def test_default_due_poll_is_timezone_aware(self):
        d = default_due_poll()
        self.assertTrue(timezone.is_aware(d))


class SourceDisplayPropertiesTests(TransactionTestCase):

    def test_best_link_uses_site_url_when_set(self):
        s = Source(
            name="n",
            feed_url="http://example.com/feed.xml",
            site_url="https://example.com/",
            interval=0,
        )
        self.assertEqual(s.best_link, "https://example.com/")

    def test_best_link_falls_back_to_feed_url(self):
        s = Source(name="n", feed_url="http://example.com/feed.xml", interval=0)
        self.assertEqual(s.best_link, "http://example.com/feed.xml")

    def test_display_name_uses_name_when_set(self):
        s = Source(name="My Feed", feed_url="http://example.com/f.xml", interval=0)
        self.assertEqual(s.display_name, "My Feed")

    def test_display_name_falls_back_to_best_link(self):
        s = Source(name="", feed_url="http://example.com/f.xml", interval=0)
        self.assertEqual(s.display_name, "http://example.com/f.xml")

    def test_garden_style_dead_feed(self):
        s = Source(name="n", feed_url="http://x.com/f.xml", interval=0, live=False)
        self.assertIn("#ccc", s.garden_style.lower())

    def test_health_box_dead_feed(self):
        s = Source(name="n", feed_url="http://x.com/f.xml", interval=0, live=False)
        self.assertIn("#ccc", s.health_box.lower())

    def test_garden_style_live_with_recent_change(self):
        now = timezone.now()
        s = Source(
            name="n",
            feed_url="http://x.com/f.xml",
            interval=0,
            live=True,
            last_change=now,
            last_success=now,
        )
        self.assertIn("background-color", s.garden_style.lower())

    def test_health_box_live_with_recent_change(self):
        now = timezone.now()
        s = Source(
            name="n",
            feed_url="http://x.com/f.xml",
            interval=0,
            live=True,
            last_change=now,
            last_success=now,
        )
        self.assertTrue(s.health_box.startswith("#"))
        self.assertIn("00", s.health_box)


class SourceIntegrityConstraintTests(TransactionTestCase):

    def test_feed_url_must_be_unique(self):
        Source.objects.create(name="one", feed_url="http://example.com/feed.xml", interval=0)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Source.objects.create(name="two", feed_url="http://example.com/feed.xml", interval=0)


class PostIntegrityConstraintTests(TransactionTestCase):

    def _source(self, suffix="1"):
        return Source.objects.create(name=f"s{suffix}", feed_url=f"http://example.com/{suffix}.xml", interval=0)

    def test_guid_must_be_unique_per_source_when_present(self):
        src = self._source()
        Post.objects.create(
            source=src,
            title="one",
            index=1,
            guid="same-guid",
            body="body",
            created=timezone.now(),
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Post.objects.create(
                    source=src,
                    title="two",
                    index=2,
                    guid="same-guid",
                    body="body",
                    created=timezone.now(),
                )

    def test_same_guid_can_exist_on_different_sources(self):
        src1 = self._source("a")
        src2 = self._source("b")
        guid = "same-guid"

        Post.objects.create(source=src1, title="one", index=1, guid=guid, body="body", created=timezone.now())
        Post.objects.create(source=src2, title="two", index=1, guid=guid, body="body", created=timezone.now())

        self.assertEqual(Post.objects.filter(guid=guid).count(), 2)

    def test_multiple_null_guids_are_allowed(self):
        src = self._source()
        Post.objects.create(source=src, title="one", index=1, guid=None, body="body", created=timezone.now())
        Post.objects.create(source=src, title="two", index=2, guid=None, body="body", created=timezone.now())

        self.assertEqual(Post.objects.filter(source=src, guid__isnull=True).count(), 2)

    def test_guid_digest_matches_sha256_of_guid(self):
        src = self._source()
        guid = "some-feed-guid"
        p = Post.objects.create(
            source=src,
            title="t",
            index=1,
            guid=guid,
            body="body",
            created=timezone.now(),
        )
        expected = hashlib.sha256(guid.encode("utf-8")).hexdigest()
        self.assertEqual(p.guid_digest, expected)
        p.refresh_from_db()
        self.assertEqual(p.guid_digest, expected)

    def test_partial_save_persists_guid_digest(self):
        src = self._source()
        p = Post.objects.create(
            source=src,
            title="t",
            index=1,
            guid="g1",
            body="body",
            created=timezone.now(),
        )
        p.title = "updated"
        p.save(update_fields=["title"])
        p.refresh_from_db()
        self.assertEqual(p.guid_digest, hashlib.sha256(b"g1").hexdigest())


class EnclosureMediaTypeTests(TransactionTestCase):

    def _post(self):
        src = Source(name="s", feed_url="http://x.com/f.xml", interval=0)
        src.save()
        p = Post(
            source=src,
            title="t",
            index=1,
            guid="g-enclosure",
            body="b",
            created=timezone.now(),
        )
        p.save()
        return p

    def test_is_image_from_mime(self):
        e = Enclosure(post=self._post(), href="http://x.com/a.png", type="image/png")
        self.assertTrue(e.is_image)
        self.assertFalse(e.is_audio)
        self.assertFalse(e.is_video)

    def test_is_audio_from_mime(self):
        e = Enclosure(post=self._post(), href="http://x.com/a.mp3", type="audio/mpeg")
        self.assertTrue(e.is_audio)
        self.assertFalse(e.is_image)

    def test_is_video_from_mime(self):
        e = Enclosure(post=self._post(), href="http://x.com/v.mp4", type="video/mp4")
        self.assertTrue(e.is_video)

    def test_medium_field_overrides_mime_prefix(self):
        e = Enclosure(post=self._post(), href="http://x.com/x", type="application/octet-stream", medium="image")
        self.assertTrue(e.is_image)


class SubscriptionIntegrityConstraintTests(TransactionTestCase):

    def _user(self, suffix="1"):
        return User.objects.create(username=f"user-{suffix}")

    def _source(self, suffix="1"):
        return Source.objects.create(name=f"s{suffix}", feed_url=f"http://example.com/feed-{suffix}.xml", interval=0)

    def test_user_cannot_subscribe_to_same_source_twice(self):
        user = self._user()
        src = self._source()
        Subscription.objects.create(user=user, source=src, name="First")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Subscription.objects.create(user=user, source=src, name="Second")

    def test_same_user_can_have_multiple_folder_subscriptions(self):
        user = self._user()
        Subscription.objects.create(user=user, source=None, name="Folder A")
        Subscription.objects.create(user=user, source=None, name="Folder B")

        self.assertEqual(Subscription.objects.filter(user=user, source__isnull=True).count(), 2)

    def test_different_users_can_subscribe_to_same_source(self):
        src = self._source()
        Subscription.objects.create(user=self._user("a"), source=src, name="A")
        Subscription.objects.create(user=self._user("b"), source=src, name="B")

        self.assertEqual(Subscription.objects.filter(source=src).count(), 2)


@requests_mock.Mocker()
class PostModelTest(BaseTest):

    def test_title_url_encoded_returns_value(self, mock):
        """Post.title_url_encoded must return the encoded title string."""

        self._populate_mock(mock, status=200, test_file="rss_xhtml_body.xml", content_type="application/rss+xml")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        post = src.posts.first()
        result = post.title_url_encoded
        self.assertIsNotNone(result)
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)
