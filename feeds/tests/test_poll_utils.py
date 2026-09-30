"""Tests for update_feeds and test_feed."""

import socket
import uuid
from datetime import timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from feeds import utils as feeds_utils
from feeds.models import Source
from feeds.utils import update_feeds


class FeedWritesToOtherRouter:
    def db_for_write(self, model, **hints):
        if model._meta.app_label == "feeds":
            return "other"
        return None


class UpdateFeedsTests(TransactionTestCase):
    databases = {"default", "other"}

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
        src.refresh_from_db()
        self.assertIsNone(src.poll_claim_token)
        self.assertIsNone(src.poll_claim_expires)

    @patch("feeds.utils.read_feed")
    def test_active_claim_is_skipped_and_other_due_source_is_processed(
        self, mock_read_feed
    ):
        active = Source.objects.create(
            name="active",
            feed_url="http://example.com/active.xml",
            poll_claim_token=uuid.uuid4(),
            poll_claim_expires=timezone.now() + timedelta(minutes=5),
        )
        available = Source.objects.create(
            name="available", feed_url="http://example.com/available.xml"
        )

        update_feeds(max_feeds=1, output=StringIO())

        self.assertEqual(mock_read_feed.call_args.args[0].pk, available.pk)
        active.refresh_from_db()
        self.assertIsNotNone(active.poll_claim_token)

    @patch("feeds.utils.read_feed")
    def test_expired_claim_is_recovered(self, mock_read_feed):
        old_token = uuid.uuid4()
        source = Source.objects.create(
            feed_url="http://example.com/expired.xml",
            poll_claim_token=old_token,
            poll_claim_expires=timezone.now() - timedelta(seconds=1),
        )

        update_feeds(max_feeds=1, output=StringIO())

        claimed_source = mock_read_feed.call_args.args[0]
        self.assertEqual(claimed_source.pk, source.pk)
        self.assertNotEqual(claimed_source._poll_claim_token, old_token)

    def test_stale_owner_cannot_release_newer_claim(self):
        stale_token = uuid.uuid4()
        current_token = uuid.uuid4()
        source = Source.objects.create(
            feed_url="http://example.com/stale.xml",
            poll_claim_token=current_token,
            poll_claim_expires=timezone.now() + timedelta(minutes=5),
        )
        source._poll_claim_token = stale_token

        feeds_utils._release_poll_claim(source)

        source.refresh_from_db()
        self.assertEqual(source.poll_claim_token, current_token)

    @patch("feeds.utils.validate_feed_request_target", return_value=(True, ""))
    @patch("feeds.utils.requests.get")
    def test_stale_owner_cannot_fetch_or_persist(self, mock_get, _mock_validate):
        current_token = uuid.uuid4()
        stale = Source.objects.create(
            name="current",
            feed_url="http://example.com/ownership.xml",
            poll_claim_token=current_token,
            poll_claim_expires=timezone.now() + timedelta(minutes=5),
        )
        stale._poll_claim_token = uuid.uuid4()

        with self.assertRaises(feeds_utils.PollClaimLost):
            feeds_utils.read_feed(stale, output=StringIO())

        mock_get.assert_not_called()
        stale.refresh_from_db()
        self.assertEqual(stale.name, "current")
        self.assertEqual(stale.poll_claim_token, current_token)

    @patch("feeds.utils._read_feed_initial_get")
    @patch("feeds.utils.validate_feed_request_target", return_value=(True, ""))
    def test_owner_replaced_after_fetch_cannot_persist(
        self, _mock_validate, mock_initial_get
    ):
        owned_token = uuid.uuid4()
        replacement_token = uuid.uuid4()
        source = Source.objects.create(
            feed_url="http://example.com/replaced.xml",
            interval=60,
            poll_claim_token=owned_token,
            poll_claim_expires=timezone.now() + timedelta(minutes=5),
        )
        source._poll_claim_token = owned_token
        response = MagicMock(status_code=304, headers={})

        def replace_owner(*args, **kwargs):
            Source.objects.filter(pk=source.pk).update(
                poll_claim_token=replacement_token,
                poll_claim_expires=timezone.now() + timedelta(minutes=5),
            )
            return response

        mock_initial_get.side_effect = replace_owner

        with self.assertRaises(feeds_utils.PollClaimLost):
            feeds_utils.read_feed(source, output=StringIO())

        source.refresh_from_db()
        self.assertEqual(source.poll_claim_token, replacement_token)
        self.assertEqual(source.interval, 60)

    @patch("feeds.utils.validate_feed_request_target", return_value=(True, ""))
    @patch("feeds.utils.requests.get", side_effect=OSError("network down"))
    def test_handled_fetch_failure_reschedules_and_clears_claim(
        self, _mock_get, _mock_validate
    ):
        source = Source.objects.create(
            feed_url="http://example.com/handled-failure.xml", interval=60
        )
        before = timezone.now()

        update_feeds(max_feeds=1, output=StringIO())

        source.refresh_from_db()
        self.assertIsNone(source.poll_claim_token)
        self.assertIsNone(source.poll_claim_expires)
        self.assertGreater(source.due_poll, before)
        self.assertIn("Fetch error", source.last_result)

    @override_settings(FEEDS_POLL_LEASE_SECONDS=30)
    def test_owned_claim_can_be_renewed(self):
        Source.objects.create(feed_url="http://example.com/renew.xml")
        source = feeds_utils._claim_next_due_source("default", timezone.now())
        old_expiry = source.poll_claim_expires

        feeds_utils._renew_poll_claim(source)

        source.refresh_from_db()
        self.assertGreaterEqual(source.poll_claim_expires, old_expiry)

    @patch("feeds.utils.read_feed", side_effect=RuntimeError("handled by scheduler"))
    def test_exception_releases_only_owned_claim(self, mock_read_feed):
        source = Source.objects.create(feed_url="http://example.com/failure.xml")

        with self.assertRaisesRegex(RuntimeError, "handled by scheduler"):
            update_feeds(max_feeds=1, output=StringIO())

        source.refresh_from_db()
        self.assertIsNone(source.poll_claim_token)
        self.assertIsNone(source.poll_claim_expires)

    @override_settings(DATABASE_ROUTERS=[FeedWritesToOtherRouter()])
    @patch("feeds.utils.read_feed")
    def test_claiming_honors_write_router(self, mock_read_feed):
        source = Source.objects.using("other").create(
            feed_url="http://example.com/routed.xml"
        )

        update_feeds(max_feeds=1, output=StringIO())

        self.assertEqual(mock_read_feed.call_args.args[0].pk, source.pk)
        self.assertEqual(mock_read_feed.call_args.args[0]._state.db, "other")
        self.assertFalse(Source.objects.using("default").exists())


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
