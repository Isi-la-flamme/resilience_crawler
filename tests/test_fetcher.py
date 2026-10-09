import unittest
from email.message import Message
from unittest.mock import Mock, patch

from crawler.fetcher import Fetcher, ResponseTooLargeError


class FetcherTests(unittest.TestCase):
    @patch("crawler.fetcher.urlopen")
    def test_rejects_oversized_content_length_without_retry(self, urlopen):
        response = Mock()
        response.headers = Message()
        response.headers["Content-Length"] = "100"
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        urlopen.return_value = response

        with self.assertRaises(ResponseTooLargeError):
            Fetcher(max_response_bytes=10).fetch(
                "https://example.org/large.pdf",
                retries=2,
            )

        urlopen.assert_called_once()
        response.read.assert_not_called()

    @patch("crawler.fetcher.urlopen")
    def test_rejects_oversized_stream_without_retry(self, urlopen):
        response = Mock()
        response.headers = Message()
        response.read.return_value = b"12345"
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        urlopen.return_value = response

        with self.assertRaises(ResponseTooLargeError):
            Fetcher(max_response_bytes=4).fetch(
                "https://example.org/stream.pdf",
                retries=2,
            )

        urlopen.assert_called_once()
        response.read.assert_called_once_with(5)


if __name__ == "__main__":
    unittest.main()
