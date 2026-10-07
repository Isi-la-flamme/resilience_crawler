import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from ping import PingChecker


class PingCheckerTests(unittest.TestCase):
    @patch("ping.urlopen")
    def test_reports_not_found_from_head_without_fetching_body(self, urlopen):
        urlopen.side_effect = HTTPError(
            "https://example.org/missing",
            404,
            "Not Found",
            {},
            None,
        )

        status = PingChecker().ping("https://example.org/missing")

        self.assertEqual(status, 404)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "HEAD")
        self.assertEqual(request.get_header("User-agent"), "YennengaCrawl/1.0")
        urlopen.assert_called_once()

    @patch("ping.urlopen")
    def test_falls_back_to_range_get_when_head_is_unsupported(self, urlopen):
        urlopen.side_effect = [
            HTTPError(
                "https://example.org/file.pdf",
                405,
                "Method Not Allowed",
                {},
                None,
            ),
            Mock(status=206, __enter__=Mock(return_value=Mock(status=206)), __exit__=Mock()),
        ]
        wait_before_request = Mock()

        status = PingChecker(before_request=wait_before_request).ping(
            "https://example.org/file.pdf"
        )

        self.assertEqual(status, 206)
        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(urlopen.call_args.args[0].get_method(), "GET")
        self.assertEqual(urlopen.call_args.args[0].get_header("Range"), "bytes=0-0")
        self.assertEqual(wait_before_request.call_count, 2)


if __name__ == "__main__":
    unittest.main()
