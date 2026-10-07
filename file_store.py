import hashlib
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


HASH_LENGTH = 8
FALLBACK_STEM = "file"


def full_url_hash(url):
    """Hash sha256 complet d'une URL — clé stable pour la base."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def short_url_hash(url):
    """Hash court (8 hex) pour suffixer un nom de fichier lisible."""
    return full_url_hash(url)[:HASH_LENGTH]


def original_stem(url):
    """Nom de fichier d'origine (sans extension), sanitisé. Fallback si vide."""
    name = unquote(Path(urlsplit(url).path).name)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).stem).strip(" ._")
    return (stem or FALLBACK_STEM)[:100]


class FileStore:
    """Écriture atomique de fichiers binaires dans un dossier racine."""

    def __init__(self, output_dir):
        self.output_dir = Path(output_dir)

    def build_name(self, url, extension):
        """Nom final : <stem>_<hash8><ext>."""
        return f"{original_stem(url)}_{short_url_hash(url)}{extension}"

    def write(self, filename, body):
        """Écrit body dans output_dir/filename via .part + os.replace."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        file_path = self.output_dir / filename
        temporary_path = file_path.with_suffix(file_path.suffix + ".part")
        temporary_path.write_bytes(body)
        os.replace(temporary_path, file_path)
        return file_path