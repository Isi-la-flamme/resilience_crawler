import logging
from html.parser import HTMLParser

logger = logging.getLogger(__name__)


class PageParser(HTMLParser):
    """Extrait du HTML les métadonnées et le contenu textuel utile d'une page.

    Le parser ignore le texte des zones non éditoriales listées dans
    `_excluded_tags`, tout en collectant les liens et images rencontrés dans le
    document.
    """

    _excluded_tags = {"head", "script", "style", "noscript", "svg", "nav", "header", "footer", "aside", "form"}

    def __init__(self):
        """Initialise l'état de collecte pour un unique document HTML."""
        super().__init__()
        self.title_parts = []
        self.links = []
        self.images = []
        self.published_at = None
        self.body_parts = []
        self.main_parts = []
        self.article_parts = []
        self._in_title = False
        self._excluded_depth = 0
        self._main_depth = 0
        self._article_depth = 0

    def handle_starttag(self, tag, attrs):
        """Traite une balise ouvrante et collecte les attributs pertinents.

        Les dates sont reconnues via les métadonnées usuelles (`article:*`,
        Open Graph, `datePublished`) et les attributs de `<time>`. Les liens
        `a[href]` et images `img[src]` sont collectés sans résolution d'URL.
        """
        attrs = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (
                attrs.get("property")
                or attrs.get("name")
                or attrs.get("itemprop")
                or ""
            ).lower()
            if self.published_at is None and key in {
                "article:published_time",
                "og:published_time",
                "datepublished",
                "date",
                "published_time",
                "parsely-pub-date",
            }:
                self.published_at = attrs.get("content")
        elif tag == "time":
            if self.published_at is None and (
                attrs.get("itemprop", "").lower() == "datepublished"
                or "pubdate" in attrs
            ):
                self.published_at = attrs.get("datetime")
        if tag == "main":
            self._main_depth += 1
        elif tag == "article":
            self._article_depth += 1
        if tag in self._excluded_tags:
            self._excluded_depth += 1
        elif tag == "a":
            href = attrs.get("href")
            if href:
                self.links.append(href)
        elif tag == "img":
            src = attrs.get("src")
            if src:
                self.images.append(src)

    def handle_endtag(self, tag):
        """Met à jour les profondeurs de titre, de zones exclues et éditoriales."""
        if tag == "title":
            self._in_title = False
        if tag in self._excluded_tags and self._excluded_depth:
            self._excluded_depth -= 1
        if tag == "main" and self._main_depth:
            self._main_depth -= 1
        elif tag == "article" and self._article_depth:
            self._article_depth -= 1

    def handle_data(self, data):
        """Collecte le texte courant et l'affecte aux zones article/main/body."""
        if self._in_title:
            self.title_parts.append(data)
        if self._excluded_depth or self._in_title:
            return
        text = " ".join(data.split())
        if text:
            self.body_parts.append(text)
            if self._article_depth:
                self.article_parts.append(text)
            elif self._main_depth:
                self.main_parts.append(text)

    @classmethod
    def parse_html(cls, html_text):
        """Analyse `html_text` et retourne le contenu extrait.

        Returns:
            Tuple `(title, links, images, published_at, content)`. Le texte
            privilégie les balises `<article>`, puis `<main>`, puis le corps
            complet filtré. Les URL sont encore relatives à ce stade.
        """
        parser = cls()
        parser.feed(html_text)
        title = " ".join("".join(parser.title_parts).split())
        content_parts = parser.article_parts or parser.main_parts or parser.body_parts
        content = " ".join(content_parts)
        logger.debug("Parsed title=%s links=%d images=%d", title, len(parser.links), len(parser.images))
        return title, parser.links, parser.images, parser.published_at, content
