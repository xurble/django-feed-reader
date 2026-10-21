import logging
from unittest.mock import patch

import requests_mock

from feeds import utils, utils_internal
from feeds.models import Post, Source

from .base import BASE_URL, BaseTest, NullOutput


class NormalizedPersistenceTests(BaseTest):
    def test_xml_enclosure_normalization_keeps_raw_json_unchanged(self):
        entry = utils_internal.parser.FeedParserDict(
            {
                "guid": "raw-xml",
                "summary": "body",
                "content": [{"type": "text/html", "value": "original alt"}],
                "media_content": [{"url": "https://example.com/media.jpg"}],
            }
        )
        feed = utils_internal.parser.FeedParserDict(
            {
                "feed": utils_internal.parser.FeedParserDict({"title": "Feed"}),
                "entries": [entry],
            }
        )
        source = Source.objects.create(feed_url=BASE_URL)

        with patch.object(utils_internal, "SAVE_JSON", True):
            normalized = utils_internal._normalize_xml_feed(feed, source)
            self.assertNotIn("description", entry["media_content"][0])
            utils_internal._persist_normalized_feed(source, normalized, NullOutput())

        post = Post.objects.get(source=source)
        self.assertNotIn("description", post.json["media_content"][0])
        self.assertEqual(post.enclosures.get().description, "original alt")

    def test_format_adapters_produce_shared_entries_without_mutating_input(self):
        xml = utils_internal.parser.parse(
            b"<rss><channel><title>XML feed</title><link>https://example.com/</link>"
            b"<item><guid>xml-1</guid><title>XML post</title>"
            b"<description>XML body</description></item></channel></rss>"
        )
        json_feed = {
            "version": "https://jsonfeed.org/version/1.1",
            "title": "JSON feed",
            "home_page_url": "https://example.com/",
            "items": [
                {
                    "id": "json-1",
                    "title": "<b>JSON post</b>",
                    "content_html": "<p>JSON body</p>",
                }
            ],
        }
        xml_source = Source.objects.create(feed_url=BASE_URL)
        json_source = Source.objects.create(feed_url=BASE_URL + "json")

        xml_values = utils_internal._normalize_xml_feed(xml, xml_source)
        json_values = utils_internal._normalize_json_feed(json_feed, json_source)

        self.assertIsInstance(xml_values, utils_internal.NormalizedFeed)
        self.assertIsInstance(json_values, utils_internal.NormalizedFeed)
        self.assertIsInstance(xml_values.entries[0], utils_internal.NormalizedEntry)
        self.assertEqual(xml_values.entries[0].guid, "xml-1")
        self.assertEqual(json_values.entries[0].guid, "json-1")
        self.assertEqual(json_feed["items"][0]["title"], "<b>JSON post</b>")
        self.assertEqual(len(xml["entries"]), 1)

        self.assertTrue(
            utils_internal._persist_normalized_feed(
                xml_source, xml_values, NullOutput()
            )
        )
        self.assertTrue(
            utils_internal._persist_normalized_feed(
                json_source, json_values, NullOutput()
            )
        )
        self.assertEqual(Post.objects.get(source=xml_source).title, "XML post")
        self.assertEqual(Post.objects.get(source=json_source).title, "<b>JSON post</b>")

    @requests_mock.Mocker()
    def test_unexpected_persistence_failure_rolls_back_and_logs_safe_context(
        self, mock
    ):
        source = Source.objects.create(feed_url=BASE_URL)
        mock.get(
            BASE_URL,
            content=b"<rss><channel><title>secret title</title>"
            b"<item><guid>secret-guid</guid><title>post</title>"
            b"<description>body</description></item></channel></rss>",
            headers={"Content-Type": "application/rss+xml"},
        )
        with patch.object(
            utils_internal.Post, "save", side_effect=RuntimeError("secret payload")
        ):
            with self.assertLogs(level=logging.ERROR) as captured:
                with self.assertRaisesRegex(RuntimeError, "secret payload"):
                    utils.read_feed(source, output=NullOutput())

        self.assertFalse(source.posts.exists())
        self.assertIsNone(Source.objects.get(pk=source.pk).last_result)
        self.assertEqual(
            source.last_result, "Unexpected feed refresh error (RuntimeError)"
        )
        self.assertLessEqual(len(source.last_result), 255)
        messages = " ".join(captured.output)
        self.assertIn(f"source {source.pk} entry 0 (RuntimeError)", messages)
        for secret in ("secret title", "secret-guid", "secret payload"):
            self.assertNotIn(secret, messages)
