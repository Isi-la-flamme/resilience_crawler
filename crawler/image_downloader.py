import logging
import mimetypes
from pathlib import Path
from urllib.parse import urlsplit

from .file_store import FileStore


logger = logging.getLogger(__name__)


class ImageDownloader:
    """Télécharge et stocke des images via un fetcher partagé."""

    def __init__(
        self,
        fetcher,
        output_dir,
        retries=2,
        before_request=None,
        redirect_validator=None,
    ):
        """Configure les dépendances, la politique de retry et les callbacks.

        Args:
            fetcher: Objet fournissant `fetch(url, ...)`.
            output_dir: Dossier où stocker les images.
            retries: Nombre de tentatives supplémentaires du fetcher.
            before_request: Callback appelé avec l'URL avant chaque téléchargement.
            redirect_validator: Prédicat qui autorise ou refuse chaque cible de
                redirection HTTP ; `None` conserve le comportement du fetcher.
        """
        self.fetcher = fetcher
        self.store = FileStore(output_dir)
        self.retries = retries
        self.before_request = before_request or (lambda url: None)
        self.redirect_validator = redirect_validator

    def download(self, url):
        """Télécharge une URL et retourne le chemin de l'image enregistrée.

        Le callback de temporisation est exécuté avant la requête. Le type MIME
        doit commencer par `image/`; sinon la fonction lève `ValueError`.
        """
        self.before_request(url)
        body, content_type, _, _ = self.fetcher.fetch(
            url,
            retries=self.retries,
            redirect_validator=self.redirect_validator,
        )
        if not content_type.startswith("image/"):
            raise ValueError(f"Ressource non image ({content_type}) : {url}")

        extension = mimetypes.guess_extension(content_type) or Path(urlsplit(url).path).suffix
        filename = self.store.build_name(url, extension)
        image_path = self.store.write(filename, body)
        logger.info("IMAGE saved %s -> %s (%d bytes)", url, image_path, len(body))
        return image_path