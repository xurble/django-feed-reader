from unittest.mock import patch

import requests_mock
from django.db import connections
from django.test import override_settings

from feeds import utils
from feeds.models import Enclosure, Post, Source

from .base import BASE_URL, BaseTest, NullOutput


class FeedWriteRouter:
    def db_for_read(self, model, **hints):
        return "default"

    def db_for_write(self, model, **hints):
        if model._meta.app_label == "feeds":
            return "other"


@requests_mock.Mocker()
class RefreshTransactionTests(BaseTest):
    databases = {"default", "other"}

    def test_secondary_database_refresh_and_rollback(self, mock):
        for routers in ([], [FeedWriteRouter()]):
            for fixture, content_type in (
                ("podcast.xml", "application/rss+xml"),
                ("podcast.json", "application/json"),
            ):
                with self.subTest(routers=routers, fixture=fixture):
                    with override_settings(DATABASE_ROUTERS=routers):
                        Source.objects.using("other").all().delete()
                        src = Source.objects.using("other").create(
                            name="original", feed_url=BASE_URL
                        )
                        self._populate_mock(mock, fixture, 200, content_type)
                        before = Source.objects.using("other").values().get(pk=src.pk)
                        finalize = utils._read_feed_finalize_interval_and_save

                        def fail_after_save(source, interval, output):
                            finalize(source, interval, output)
                            self.assertTrue(connections["other"].in_atomic_block)
                            self.assertFalse(connections["default"].in_atomic_block)
                            self.assertTrue(Post.objects.using("other").exists())
                            self.assertTrue(Enclosure.objects.using("other").exists())
                            raise RuntimeError("late failure")

                        with patch.object(
                            utils,
                            "_read_feed_finalize_interval_and_save",
                            side_effect=fail_after_save,
                        ):
                            with self.assertRaisesRegex(RuntimeError, "late failure"):
                                utils.read_feed(src, output=NullOutput())
                        self.assertEqual(
                            Source.objects.using("other").values().get(pk=src.pk),
                            before,
                        )
                        self.assertFalse(Post.objects.using("other").exists())
                        self.assertFalse(Enclosure.objects.using("other").exists())

                        src.refresh_from_db(using="other")
                        utils.read_feed(src, output=NullOutput())
                        src.refresh_from_db(using="other")
                        indexes = list(
                            Post.objects.using("other")
                            .order_by("index")
                            .values_list("index", flat=True)
                        )
                        self.assertEqual(indexes, list(range(1, src.max_index + 1)))
                        self.assertTrue(Enclosure.objects.using("other").exists())
                        utils.read_feed(src, output=NullOutput())
                        self.assertEqual(
                            Post.objects.using("other").count(), len(indexes)
                        )
                        self.assertFalse(Post.objects.using("default").exists())
                        self.assertFalse(Enclosure.objects.using("default").exists())

    def test_stale_refresh_preserves_another_refresh_redirect(self, mock):
        for status in (200, 304):
            with self.subTest(status=status):
                Source.objects.all().delete()
                src = Source.objects.create(name="original", feed_url=BASE_URL)
                stale = Source.objects.get(pk=src.pk)
                moved_url = BASE_URL + "moved.xml"
                mock.get(BASE_URL, status_code=301, headers={"Location": moved_url})
                utils.read_feed(src, output=NullOutput())
                self._populate_mock(mock, "podcast.xml", status, "application/rss+xml")
                utils.read_feed(stale, output=NullOutput())
                src.refresh_from_db()
                self.assertEqual(src.feed_url, moved_url)
