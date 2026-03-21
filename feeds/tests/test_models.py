import requests_mock

from django.test import SimpleTestCase, TransactionTestCase
from django.utils import timezone

from feeds.models import Source, Post, Enclosure, default_due_poll
from feeds.utils import read_feed

from .base import BaseTest, NullOutput, BASE_URL


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
