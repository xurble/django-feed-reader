import requests_mock

from feeds.models import Source
from feeds.utils import read_feed

from .base import BaseTest, NullOutput, BASE_URL


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
