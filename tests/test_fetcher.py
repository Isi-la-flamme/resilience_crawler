import unittest
from email.message import Message
from unittest.mock import Mock, patch

from crawler.fetcher import (
    MAX_DOCUMENT_BYTES,
    DocumentDownloadDisabledError,
    DocumentTooLargeError,
    Fetcher,
    ResponseTooLargeError,
)


class FetcherTests(unittest.TestCase):
    @patch("crawler.fetcher.urlopen")
    def test_skips_document_before_reading_body_when_download_is_disabled(self, urlopen):
        response = Mock()
        response.headers = Message()
        response.headers["Content-Type"] = "application/pdf"
        response.headers["Content-Length"] = "100"
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        urlopen.return_value = response

        with self.assertRaises(DocumentDownloadDisabledError):
            Fetcher().fetch(
                "https://example.org/download",
                retries=2,
                skip_documents=True,
            )

        urlopen.assert_called_once()
        response.read.assert_not_called()

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

    @patch("crawler.fetcher.urlopen")
    def test_skips_oversized_pdf_without_retry(self, urlopen):
        response = Mock()
        response.headers = Message()
        response.headers["Content-Type"] = "application/pdf"
        response.headers["Content-Length"] = str(MAX_DOCUMENT_BYTES + 1)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        urlopen.return_value = response

        with self.assertRaisesRegex(DocumentTooLargeError, str(MAX_DOCUMENT_BYTES)):
            Fetcher(max_response_bytes=50 * 1024 * 1024).fetch(
                "https://example.org/rapport.pdf",
                retries=2,
            )

        urlopen.assert_called_once()
        response.read.assert_not_called()

    @patch("crawler.fetcher.urlopen")
    def test_detects_oversized_extensionless_pdf_by_content_type(self, urlopen):
        response = Mock()
        response.headers = Message()
        response.headers["Content-Type"] = "application/pdf"
        response.headers["Content-Length"] = str(MAX_DOCUMENT_BYTES + 1)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        urlopen.return_value = response

        with self.assertRaises(DocumentTooLargeError):
            Fetcher(max_response_bytes=50 * 1024 * 1024).fetch(
                "https://example.org/download?id=42",
                retries=2,
            )

        urlopen.assert_called_once()
        response.read.assert_not_called()

    @patch("crawler.fetcher.urlopen")
    def test_skips_streamed_oversized_document_without_content_length(self, urlopen):
        response = Mock()
        response.headers = Message()
        response.headers["Content-Type"] = "application/pdf"
        response.read.side_effect = [b"x" * 65536] * 320 + [b"xx"]
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        urlopen.return_value = response

        with self.assertRaises(DocumentTooLargeError):
            Fetcher(max_response_bytes=50 * 1024 * 1024).fetch(
                "https://example.org/download",
                retries=2,
            )

        urlopen.assert_called_once()
        self.assertEqual(response.read.call_count, 321)


if __name__ == "__main__":
    unittest.main()
