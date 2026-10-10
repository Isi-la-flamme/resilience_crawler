import logging
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from .constants import USER_AGENT
from .document_downloader import document_extension
from .metrics import metrics

logger = logging.getLogger(__name__)
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024


class ResponseTooLargeError(ValueError):
    pass


class DocumentTooLargeError(ResponseTooLargeError):
    pass


class Fetcher:
    def __init__(self, timeout=10, max_response_bytes=5 * 1024 * 1024, user_agent=USER_AGENT):
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.user_agent = user_agent

    def fetch(self, url, retries=2, headers=None):
        with metrics.measure("fetch"):
                
            for attempt in range(retries + 1):
                logger.info("FETCH start %s (attempt %d/%d)", url, attempt + 1, retries + 1)
                request_headers = {"User-Agent": self.user_agent}
                if headers:
                    request_headers.update(headers)
                request = Request(url, headers=request_headers)
                try:
                    with urlopen(request, timeout=self.timeout) as response:
                        content_type = response.headers.get_content_type()
                        is_document = document_extension(url, content_type) is not None
                        response_limit = (
                            min(self.max_response_bytes, MAX_DOCUMENT_BYTES)
                            if is_document
                            else self.max_response_bytes
                        )
                        content_length = response.headers.get("Content-Length")
                        if content_length is not None and int(content_length) > response_limit:
                            if is_document:
                                raise DocumentTooLargeError(
                                    f"Document supérieur à la limite de "
                                    f"{response_limit} octets."
                                )
                            raise ResponseTooLargeError(
                                f"Réponse supérieure à la limite de "
                                f"{response_limit} octets."
                            )

                        charset = response.headers.get_content_charset() or "utf-8"

                        body = bytearray()
                        while True:
                            remaining = response_limit + 1 - len(body)
                            chunk = response.read(min(65536, remaining))
                            if not chunk:
                                break
                            body.extend(chunk)
                            if len(body) > response_limit:
                                if is_document:
                                    raise DocumentTooLargeError(
                                        f"Document supérieur à la limite de "
                                        f"{response_limit} octets."
                                    )
                                raise ResponseTooLargeError(
                                    f"Réponse supérieure à la limite de "
                                    f"{response_limit} octets."
                                )

                        logger.info("FETCH OK %s (%d bytes, %s)", url, len(body), content_type)
                        return bytes(body), content_type, charset, self._validators(response.headers)
                except HTTPError as error:
                    if error.code == 304:
                        validators = self._validators(error.headers)
                        error.close()
                        logger.info("FETCH unchanged %s (HTTP 304)", url)
                        return None, None, None, validators
                    if error.code in {404, 410}:
                        logger.info("HTTP %s for %s", error.code, url)
                    else:
                        logger.warning("HTTP %s for %s", error.code, url)
                    error.close()
                    if error.code not in RETRYABLE_STATUS_CODES or attempt == retries:
                        raise
                except ResponseTooLargeError as error:
                    logger.warning("FETCH skipped for %s: %s", url, error)
                    raise
                except (URLError, TimeoutError, OSError, ValueError) as error:
                    logger.warning("FETCH failed for %s: %s", url, error)
                    if attempt == retries:
                        raise
                time.sleep(2 ** attempt)

            raise RuntimeError(f"Impossible de récupérer {url}")

    @staticmethod
    def _validators(headers):
        return {
            "etag": headers.get("ETag"),
            "last_modified": headers.get("Last-Modified"),
        }
