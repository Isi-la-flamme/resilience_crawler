import hashlib
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


HASH_LENGTH = 8
FALLBACK_STEM = "file"


def full_url_hash(url):
    """Calcule le SHA-256 hexadécimal complet d'une URL.

    Cette empreinte sert d'identifiant déterministe lors de l'enregistrement de
    fichiers et dans les bases. L'URL est encodée en UTF-8 sans normalisation.
    """
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def short_url_hash(url):
    """Retourne les huit premiers caractères de l'empreinte SHA-256 de l'URL."""
    return full_url_hash(url)[:HASH_LENGTH]


def original_stem(url):
    """Construit un nom de base de fichier sûr à partir du chemin d'une URL.

    Le dernier segment est décodé depuis l'URL ; seuls les caractères
    alphanumériques ASCII, points, tirets et soulignés sont conservés. Le
    résultat est borné à 100 caractères et utilise `FALLBACK_STEM` si vide.
    """
    name = unquote(Path(urlsplit(url).path).name)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).stem).strip(" ._")
    return (stem or FALLBACK_STEM)[:100]


class FileStore:
    """Produit et écrit des fichiers avec des noms déterministes et sûrs.

    Chaque fichier combine un nom lisible dérivé de l'URL et son empreinte
    courte ; l'écriture passe par un fichier temporaire avant remplacement.
    """

    def __init__(self, output_dir):
        """Initialise le stockage sous `output_dir` sans créer le dossier."""
        self.output_dir = Path(output_dir)

    def build_name(self, url, extension):
        """Retourne `<nom-assaini>_<hash8><extension>` pour `url`.

        L'empreinte évite les collisions entre chemins d'URL ayant le même nom
        de fichier final.
        """
        return f"{original_stem(url)}_{short_url_hash(url)}{extension}"

    def write(self, filename, body):
        """Écrit des octets et publie le fichier par remplacement atomique.

        Le dossier racine est créé au besoin. Un fichier adjacent suffixé
        `.part` est écrit avant `os.replace`, ce qui évite de laisser un fichier
        final partiellement écrit si le processus est interrompu.

        Returns:
            Chemin `Path` du fichier final.
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)
        file_path = self.output_dir / filename
        temporary_path = file_path.with_suffix(file_path.suffix + ".part")
        temporary_path.write_bytes(body)
        os.replace(temporary_path, file_path)
        return file_path