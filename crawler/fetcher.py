import logging
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
from .constants import USER_AGENT
from .document_downloader import document_extension
from .metrics import metrics

logger = logging.getLogger(__name__)
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024


class ResponseTooLargeError(ValueError):
    """Indique qu'une réponse HTTP dépasse la limite autorisée."""


class DocumentTooLargeError(ResponseTooLargeError):
    """Indique qu'un document dépasse la limite dédiée aux documents."""


class OutOfScopeRedirectError(ValueError):
    """Signale une redirection HTTP vers un hôte non autorisé."""


class _ScopeRedirectHandler(HTTPRedirectHandler):
    """Bloque les redirections dont la destination échoue au prédicat fourni."""

    def __init__(self, is_allowed):
        """Stocke un prédicat prenant l'URL de destination et renvoyant un booléen."""
        super().__init__()
        self.is_allowed = is_allowed

    def redirect_request(self, request, response, code, message, headers, new_url):
        """Valide la destination avant de laisser urllib construire la requête."""
        if not self.is_allowed(new_url):
            raise OutOfScopeRedirectError(
                f"Redirection hors du domaine autorisé refusée : {new_url}"
            )
        return super().redirect_request(
            request, response, code, message, headers, new_url
        )


class Fetcher:
    """Effectue des requêtes HTTP avec retries, limites de taille et cache HTTP."""

    def __init__(self, timeout=10, max_response_bytes=5 * 1024 * 1024, user_agent=USER_AGENT):
        """Configure le délai réseau, la taille maximale et l'identité HTTP.

        `max_response_bytes` s'applique aux réponses ordinaires. Les documents
        reconnus sont en plus plafonnés à `MAX_DOCUMENT_BYTES`.
        """
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.user_agent = user_agent

    def fetch(self, url, retries=2, headers=None, redirect_validator=None):
        """Récupère une URL et retourne son corps, son type et ses validateurs.

        Un 304 renvoie `(None, None, None, validators)` sans retry. Les erreurs
        transitoires HTTP configurées et les erreurs réseau sont réessayées avec
        attente exponentielle ; les corps trop volumineux et redirections hors
        portée échouent immédiatement.

        Args:
            url: URL HTTP(S) à télécharger.
            retries: Nombre de tentatives supplémentaires après l'essai initial.
            headers: En-têtes additionnels, notamment les validateurs conditionnels.
            redirect_validator: Prédicat facultatif validant chaque URL de
                redirection avant de la suivre.

        Returns:
            Tuple `(body, content_type, charset, validators)`. `body` est en
            octets, et vaut `None` pour HTTP 304 ; `validators` contient `etag`
            et `last_modified`.

        Raises:
            HTTPError: Pour les statuts non récupérables ou après épuisement des retries.
            DocumentTooLargeError: Si un document dépasse sa limite.
            ResponseTooLargeError: Si une réponse ordinaire dépasse sa limite.
            OutOfScopeRedirectError: Si le validateur refuse une redirection.
            URLError, TimeoutError, OSError: En cas d'échec réseau final.
        """
        with metrics.measure("fetch"):
            for attempt in range(retries + 1):
                logger.info("FETCH start %s (attempt %d/%d)", url, attempt + 1, retries + 1)
                request_headers = {"User-Agent": self.user_agent}
                if headers:
                    request_headers.update(headers)
                request = Request(url, headers=request_headers)
                try:
                    if redirect_validator is None:
                        response_context = urlopen(request, timeout=self.timeout)
                    else:
                        opener = build_opener(
                            _ScopeRedirectHandler(redirect_validator)
                        )
                        response_context = opener.open(request, timeout=self.timeout)
                    with response_context as response:
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
                except OutOfScopeRedirectError:
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
        """Extrait les validateurs HTTP utilisés par le mode `--refresh`."""
        return {
            "etag": headers.get("ETag"),
            "last_modified": headers.get("Last-Modified"),
        }
