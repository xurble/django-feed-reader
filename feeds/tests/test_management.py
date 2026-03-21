"""Management commands."""

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TransactionTestCase


class RefreshFeedsCommandTests(TransactionTestCase):
    @patch("feeds.management.commands.refreshfeeds.update_feeds")
    def test_refreshfeeds_calls_update_feeds_with_batch_size(self, mock_update):
        out = StringIO()
        call_command("refreshfeeds", stdout=out)
        mock_update.assert_called_once_with(30)
        self.assertIn("Finished", out.getvalue())
