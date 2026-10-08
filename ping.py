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

        status = self._request_status(url, method="HEAD")

        if status in HEAD_UNSUPPORTED_STATUS_CODES:

            status = self._request_status(url, range_header="bytes=0-0")



        if status in MISSING_STATUS_CODES:

            logger.info("PING missing HTTP %d %s", status, url)

        else:

            logger.debug("PING HTTP %d %s", status, url)

        return status



    def _request_status(self, url, method="GET", range_header=None):

        self.before_request()

        headers = {"User-Agent": self.user_agent}

        if range_header:

            headers["Range"] = range_header

        request = Request(url, headers=headers, method=method)

        try:

            with urlopen(request, timeout=self.timeout) as response:

                return response.status

        except HTTPError as error:

            status = error.code

            error.close()

            return status

