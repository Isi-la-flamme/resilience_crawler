import logging
from pathlib import Path
from urllib.parse import urlsplit

from .file_store import FileStore, full_url_hash


logger = logging.getLogger(__name__)


SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".doc", ".docx", ".docm", ".dot", ".dotx", ".dotm",
    ".xls", ".xlsx", ".xlsm", ".xlsb", ".xlt", ".xltx", ".xltm",
    ".ppt", ".pptx", ".pptm", ".pps", ".ppsx", ".ppsm", ".pot", ".potx", ".potm",
    ".xla", ".xlam", ".ppa", ".ppam", ".vsdm", ".wps",
    ".odt", ".ods", ".odp", ".odg",
    ".rtf", ".csv", ".pub", ".vsd", ".vsdx", ".one", ".mdb", ".accdb",
    ".pages", ".numbers", ".key",
}
MIME_EXTENSIONS = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/vnd.oasis.opendocument.presentation": ".odp",
    "application/rtf": ".rtf",
}


def document_extension(url, content_type):
    """Détermine l'extension d'un document pris en charge.

    L'extension d'URL est prioritaire lorsqu'elle figure dans
    `SUPPORTED_EXTENSIONS`; sinon, le type MIME est consulté dans
    `MIME_EXTENSIONS`.

    Returns:
        Extension commençant par un point, ou `None` si le format est inconnu.
    """
    extension = Path(urlsplit(url).path).suffix.lower()
    if extension in SUPPORTED_EXTENSIONS:
        return extension
    return MIME_EXTENSIONS.get(content_type.lower())


class DocumentDownloader:
    """Enregistre les documents téléchargés dans un sous-dossier par site."""

    def __init__(self, output_dir):
        """Configure le dossier racine des documents, sans le créer."""
        self.output_dir = Path(output_dir)

    def save(self, url, content_type, body, site_name):
        """Écrit le document et retourne ses métadonnées de stockage.

        Le nom de fichier est déterministe à partir de l'URL, et les octets
        sont écrits atomiquement par `FileStore`.

        Args:
            url: URL source du document.
            content_type: Type MIME reçu par HTTP.
            body: Contenu binaire complet du document.
            site_name: Hôte utilisé pour isoler les fichiers par site.

        Returns:
            Dictionnaire avec empreinte, chemin, nom, type MIME et taille.

        Raises:
            ValueError: Si aucune extension de document n'est reconnue.
            OSError: Si le fichier ne peut pas être créé ou écrit.
        """
        extension = document_extension(url, content_type)
        if extension is None:
            raise ValueError(f"Format de document non pris en charge : {content_type} ({url})")

        site_store = FileStore(self.output_dir / site_name)
        filename = site_store.build_name(url, extension)
        file_path = site_store.write(filename, body)
        logger.info("DOCUMENT saved %s -> %s (%d bytes)", url, file_path, len(body))
        return {
            "url_hash": full_url_hash(url),
            "site_name": site_name,
            "local_path": str(file_path),
            "filename": file_path.name,
            "content_type": content_type,
            "size_bytes": len(body),
        }