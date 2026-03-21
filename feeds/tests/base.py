import os

from django.test import TransactionTestCase

TEST_FILES_FOLDER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "testdata"
)
BASE_URL = "http://feed.com/"


class NullOutput(object):
    def write(self, strin: str):
        pass


class BaseTest(TransactionTestCase):
    def _populate_mock(
        self,
        mock,
        test_file,
        status,
        content_type,
        etag=None,
        headers=None,
        url=BASE_URL,
        is_cloudflare=False,
    ):

        content = open(os.path.join(TEST_FILES_FOLDER, test_file), "rb").read()

        ret_headers = {"Content-Type": content_type, "etag": "an-etag"}
        if headers is not None:
            ret_headers = {**ret_headers, **headers}

        if is_cloudflare:
            ret_headers["Server"] = "Some cloudflare thing"
            mock.register_uri(
                "GET", url, status_code=status, content=content, headers=ret_headers
            )
        else:
            if etag is None:
                mock.register_uri(
                    "GET", url, status_code=status, content=content, headers=ret_headers
                )
            else:
                mock.register_uri(
                    "GET",
                    url,
                    request_headers={"If-None-Match": etag},
                    status_code=status,
                    content=content,
                    headers=ret_headers,
                )
