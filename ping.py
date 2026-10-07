import logging
from urllib.error import HTTPError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)
HEAD_UNSUPPORTED_STATUS_CODES = {405, 501}
MISSING_STATUS_CODES = {404, 410}


class PingChecker:
    def __init__(self, timeout=5, user_agent="YennengaCrawl/1.0", before_request=None):
        self.timeout = timeout
        self.user_agent = user_agent
        self.before_request = before_request or (lambda: None)

    def ping(self, url):
        self.before_request()
        request = Request(url, headers={"User-Agent": self.user_agent}, method="HEAD")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                status = response.status
        except HTTPError as error:
            status = error.code
            error.close()
            if status in HEAD_UNSUPPORTED_STATUS_CODES:
                status = self._range_get_status(url)

        if status in MISSING_STATUS_CODES:
            logger.info("PING missing HTTP %d %s", status, url)
        else:
            logger.debug("PING HTTP %d %s", status, url)
        return status

    def _range_get_status(self, url):
        self.before_request()
        request = Request(
            url,
            headers={"User-Agent": self.user_agent, "Range": "bytes=0-0"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return response.status
        except HTTPError as error:
            status = error.code
            error.close()
            return status
