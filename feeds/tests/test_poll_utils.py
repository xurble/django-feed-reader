"""Tests for update_feeds and test_feed."""

import socket
from io import StringIO
from unittest.mock import MagicMock, patch

from django.test import TransactionTestCase

from feeds import utils as feeds_utils
from feeds.models import Source
from feeds.utils import update_feeds


class UpdateFeedsTests(TransactionTestCase):
    @patch("feeds.utils.read_feed")
    def test_update_feeds_invokes_read_feed_for_due_sources(self, mock_read_feed):
        src = Source(
            name="due", feed_url="http://example.com/feed.xml", interval=60, live=True
        )
        src.save()

        update_feeds(max_feeds=10, output=StringIO())

        mock_read_feed.assert_called()
        self.assertEqual(mock_read_feed.call_count, 1)
        args, _kw = mock_read_feed.call_args
        self.assertEqual(args[0].pk, src.pk)


class TestFeedTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        dns_patcher = patch("feeds.url_safety.socket.getaddrinfo")
        self.mock_getaddrinfo = dns_patcher.start()
        self.addCleanup(dns_patcher.stop)
        self.mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))
        ]

    @patch("feeds.utils.requests.get")
    def test_test_feed_returns_true_on_ok_response(self, mock_get):
        response = MagicMock()
        response.ok = True
        response.text = "<xml/>"
        mock_get.return_value = response

        src = Source(name="t", feed_url="http://example.com/feed.xml", interval=0)
        src.save()

        out = StringIO()
        self.assertTrue(feeds_utils.test_feed(src, cache=False, output=out))
        mock_get.assert_called_once()

    @patch("feeds.utils.requests.get")
    def test_test_feed_returns_false_on_error(self, mock_get):
        mock_get.side_effect = OSError("network down")

        src = Source(name="t", feed_url="http://example.com/feed.xml", interval=0)
        src.save()

        self.assertFalse(feeds_utils.test_feed(src, output=StringIO()))

    @patch("feeds.utils.requests.get")
    def test_test_feed_rejects_private_url_before_request(self, mock_get):
        src = Source(name="t", feed_url="http://10.0.0.1/feed.xml", interval=0)
        src.save()

        self.assertFalse(feeds_utils.test_feed(src, output=StringIO()))
        mock_get.assert_not_called()
