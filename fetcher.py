import logging
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class Fetcher:
    def __init__(self, timeout=10, max_response_bytes=5 * 1024 * 1024, user_agent="YennengaCrawl/1.0"):
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.user_agent = user_agent

    def fetch(self, url, retries=2):
        for attempt in range(retries + 1):
            logger.info("FETCH start %s (attempt %d/%d)", url, attempt + 1, retries + 1)
            request = Request(url, headers={"User-Agent": self.user_agent})
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    content_type = response.headers.get_content_type()
                    charset = response.headers.get_content_charset() or "utf-8"

                    body = bytearray()
                    while True:
                        remaining = self.max_response_bytes - len(body)
                        if remaining <= 0:
                            raise ValueError(f"Réponse supérieure à la limite de {self.max_response_bytes} octets.")
                        chunk = response.read(min(65536, remaining))
                        if not chunk:
                            break
                        body.extend(chunk)
                        if len(body) > self.max_response_bytes:
                            raise ValueError(f"Réponse supérieure à la limite de {self.max_response_bytes} octets.")

                    logger.info("FETCH OK %s (%d bytes, %s)", url, len(body), content_type)
                    return bytes(body), content_type, charset
            except HTTPError as error:
                if error.code in {404, 410}:
                    logger.info("HTTP %s for %s", error.code, url)
                else:
                    logger.warning("HTTP %s for %s", error.code, url)
                error.close()
                if error.code not in RETRYABLE_STATUS_CODES or attempt == retries:
                    raise
            except (URLError, TimeoutError, OSError, ValueError) as error:
                logger.warning("FETCH failed for %s: %s", url, error)
                if attempt == retries:
                    raise
            time.sleep(2 ** attempt)

        raise RuntimeError(f"Impossible de récupérer {url}")
