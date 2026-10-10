import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from crawler.public_api import (
    get_document,
    get_page,
    list_documents,
    list_pages,
    list_sites,
)


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_PAGES_DATABASE = PROJECT_ROOT / "crawl-clean.db"
DEFAULT_PDF_DATABASE = PROJECT_ROOT / "pdf-index.db"

DASHBOARD = """<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#075b4c">
  <title>Portail prototype — données publiques</title>
  <style>
    :root { color-scheme: light; font-family: system-ui, sans-serif; background: #f3f6f4; color: #15231f; }
    * { box-sizing: border-box; }
    body { margin: 0; }
    header { background: #075b4c; color: white; padding: 1.2rem max(1rem, calc((100% - 960px) / 2)); }
    main { max-width: 960px; margin: auto; padding: 1rem; }
    h1 { margin: 0; font-size: 1.45rem; }
    header p { margin: .45rem 0 0; opacity: .9; }
    .notice { border-left: 4px solid #b87500; background: #fff7e5; padding: .8rem 1rem; margin: 1rem 0; }
    form { display: grid; grid-template-columns: minmax(0, 1fr) minmax(150px, .45fr) auto; gap: .6rem; }
    input, select, button { min-height: 44px; padding: .6rem .75rem; border: 1px solid #aab9b3; border-radius: .45rem; font: inherit; }
    button { background: #075b4c; color: white; border: 0; cursor: pointer; font-weight: 650; }
    button:focus-visible, input:focus-visible, select:focus-visible { outline: 3px solid #e4a52a; outline-offset: 2px; }
    #summary { margin: 1rem 0; color: #40534c; }
    article { background: white; border: 1px solid #dce5e0; border-radius: .55rem; padding: 1rem; margin: .7rem 0; }
    article h2 { margin: 0 0 .35rem; font-size: 1.08rem; }
    article p { margin: .45rem 0; line-height: 1.55; white-space: pre-wrap; overflow-wrap: anywhere; }
    .source-url { overflow-wrap: anywhere; }
    .read-text { margin-top: .8rem; }
    .full-text { max-height: 70vh; overflow: auto; border-top: 1px solid #dce5e0; margin-top: 1rem; padding-top: .7rem; }
    mark { background: #ffe08a; color: inherit; border-radius: .15rem; padding: 0 .08rem; }
    .meta { color: #52645e; font-size: .9rem; }
    .tabs { display: flex; gap: .5rem; margin: 1rem 0; }
    .tabs button { background: #dce8e2; color: #17372e; }
    .tabs button[aria-pressed="true"] { background: #075b4c; color: white; }
    #more { display: block; margin: 1rem auto; }
    [hidden] { display: none !important; }
    @media (max-width: 650px) { form { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <header>
    <h1>Portail de recherche — prototype</h1>
    <p>Pages et documents des sites institutionnels burkinabè collectés</p>
  </header>
  <main>
    <aside class="notice">
      Prototype de recherche, pas un catalogue officiel. Les conditions de
      réutilisation des contenus n’ont pas encore été vérifiées. Les textes
      affichés proviennent des bases locales ; l’URL source est fournie comme
      référence et n’est pas ouverte par ce portail.
    </aside>
    <section aria-labelledby="sites-heading">
      <h2 id="sites-heading">Sources disponibles</h2>
      <div id="sites" aria-live="polite">Chargement des sources…</div>
    </section>
    <div class="tabs" role="group" aria-label="Type de contenu">
      <button type="button" data-kind="pages" aria-pressed="true">Pages</button>
      <button type="button" data-kind="documents" aria-pressed="false">PDF</button>
    </div>
    <form id="search">
      <input id="query" name="q" type="search" maxlength="200"
             placeholder="Rechercher un sujet, un titre ou un mot-clé"
             aria-label="Mots à rechercher">
      <select id="site" aria-label="Filtrer par institution">
        <option value="">Toutes les institutions</option>
      </select>
      <button type="submit">Rechercher</button>
    </form>
    <p id="summary" aria-live="polite"></p>
    <section id="results" aria-label="Résultats de recherche"></section>
    <button id="more" type="button" hidden>Afficher plus</button>
  </main>
  <script>
    const results = document.querySelector("#results");
    const summary = document.querySelector("#summary");
    const siteSelect = document.querySelector("#site");
    const queryInput = document.querySelector("#query");
    const moreButton = document.querySelector("#more");
    let kind = "pages";
    let offset = 0;
    let total = 0;

    function element(tag, text, className) {
      const node = document.createElement(tag);
      if (text !== undefined && text !== null) node.textContent = text;
      if (className) node.className = className;
      return node;
    }

    function appendHighlighted(container, text) {
      const terms = queryInput.value.match(/[\\p{L}\\p{N}]+/gu) || [];
      if (!terms.length) {
        container.textContent = text;
        return;
      }
      const alternatives = [...new Set(terms)]
        .sort((left, right) => right.length - left.length)
        .map(term => term.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\\\$&"));
      const matcher = new RegExp(`(${alternatives.join("|")})`, "giu");
      let cursor = 0;
      for (const match of text.matchAll(matcher)) {
        container.append(document.createTextNode(text.slice(cursor, match.index)));
        container.append(element("mark", match[0]));
        cursor = match.index + match[0].length;
      }
      container.append(document.createTextNode(text.slice(cursor)));
    }

    async function loadSites() {
      const response = await fetch("/api/v1/sites");
      if (!response.ok) throw new Error("Chargement des sources impossible");
      const data = await response.json();
      const list = element("ul");
      for (const site of data.items) {
        const label = `${site.name} — ${site.pages} pages, ${site.documents} PDF`;
        list.append(element("li", label));
        const option = element("option", site.name);
        option.value = site.name;
        siteSelect.append(option);
      }
      const container = document.querySelector("#sites");
      container.replaceChildren(list);
    }

    function renderItem(item) {
      const card = element("article");
      const title = element("h2");
      appendHighlighted(title, item.title || item.filename || item.url);
      card.append(title);
      card.append(element("div", `${item.site} · ${item.rights_status}`, "meta"));
      if (item.excerpt) {
        card.append(element("div", "Extrait du texte", "meta"));
        const excerpt = element("p");
        appendHighlighted(excerpt, item.excerpt);
        card.append(excerpt);
      }
      if (item.status) card.append(element("p", `État du texte : ${item.status}`, "meta"));
      card.append(element("p", `Source : ${item.source_url || item.url}`, "meta source-url"));
      const detailKind = kind;
      const detail = element("section");
      detail.hidden = true;
      detail.className = "full-text";
      detail.setAttribute("aria-label", "Texte intégral");
      const readButton = element("button", "Lire le texte intégral");
      readButton.type = "button";
      readButton.className = "read-text";
      readButton.setAttribute("aria-expanded", "false");
      readButton.addEventListener("click", async () => {
        if (!detail.hidden) {
          detail.hidden = true;
          readButton.textContent = "Lire le texte intégral";
          readButton.setAttribute("aria-expanded", "false");
          return;
        }
        if (!detail.dataset.loaded) {
          readButton.disabled = true;
          readButton.textContent = "Chargement depuis la base locale…";
          try {
            const response = await fetch(`/api/v1/${detailKind}/${item.id}`);
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || "Lecture du texte impossible");
            const heading = element("h3", data.title || data.filename || item.title);
            detail.append(heading);
            if (data.text) {
              const fullText = element("p");
              appendHighlighted(fullText, data.text);
              detail.append(fullText);
            } else {
              detail.append(element(
                "p",
                data.status === "no_text"
                  ? "Aucun texte n’a pu être extrait de ce PDF."
                  : "Aucun texte disponible dans la base locale."
              ));
            }
            detail.dataset.loaded = "true";
          } catch (error) {
            detail.replaceChildren(element("p", error.message, "meta"));
            detail.dataset.loaded = "";
          } finally {
            readButton.disabled = false;
          }
        }
        detail.hidden = false;
        readButton.textContent = "Masquer le texte intégral";
        readButton.setAttribute("aria-expanded", "true");
      });
      card.append(readButton, detail);
      return card;
    }

    async function search(reset = true) {
      if (reset) {
        offset = 0;
        results.replaceChildren();
      }
      summary.textContent = "Recherche en cours…";
      moreButton.hidden = true;
      const params = new URLSearchParams({limit: "20", offset: String(offset)});
      if (queryInput.value.trim()) params.set("q", queryInput.value.trim());
      if (siteSelect.value) params.set("site", siteSelect.value);
      try {
        const response = await fetch(`/api/v1/${kind}?${params}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Recherche impossible");
        total = data.total;
        for (const item of data.items) results.append(renderItem(item));
        offset += data.items.length;
        const fieldLabels = {
          title: "titre",
          author: "auteur",
          site: "site",
          text: kind === "documents" ? "texte du PDF" : "texte",
        };
        const searchScope = data.search_fields
          .map(field => fieldLabels[field] || field)
          .join(", ");
        summary.textContent = `${total} résultat(s) · recherche dans : ${searchScope} · réutilisation non évaluée`;
        moreButton.hidden = offset >= total;
      } catch (error) {
        summary.textContent = error.message;
      }
    }

    document.querySelector("#search").addEventListener("submit", event => {
      event.preventDefault();
      search();
    });
    siteSelect.addEventListener("change", () => search());
    moreButton.addEventListener("click", () => search(false));
    document.querySelectorAll("[data-kind]").forEach(button => {
      button.addEventListener("click", () => {
        kind = button.dataset.kind;
        document.querySelectorAll("[data-kind]").forEach(tab =>
          tab.setAttribute("aria-pressed", String(tab === button)));
        search();
      });
    });
    Promise.all([loadSites(), search()]).catch(error => {
      document.querySelector("#sites").textContent = error.message;
    });
  </script>
</body>
</html>"""


def create_app(pages_database=DEFAULT_PAGES_DATABASE, pdf_database=DEFAULT_PDF_DATABASE):
    pages_database = Path(pages_database).resolve()
    pdf_database = Path(pdf_database).resolve()
    app = FastAPI(
        title="Prototype Open Data — Burkina Faso",
        version="0.1.0",
        description=(
            "API de consultation en lecture seule des pages préparées et PDF indexés. "
            "La réutilisation juridique des contenus n’a pas été évaluée."
        ),
    )

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def dashboard():
        return DASHBOARD

    @app.get("/api/v1/health")
    def health():
        return {
            "status": "ok" if pages_database.is_file() and pdf_database.is_file() else "degraded",
            "pages_database_available": pages_database.is_file(),
            "pdf_database_available": pdf_database.is_file(),
            "rights_status": "not_assessed",
        }

    @app.get("/api/v1/sites")
    def sites():
        try:
            return {"items": list_sites(pages_database, pdf_database)}
        except FileNotFoundError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @app.get("/api/v1/pages")
    def pages(
        q: str | None = Query(default=None, max_length=200),
        site: str | None = Query(default=None, max_length=255),
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=1_000_000),
    ):
        try:
            return list_pages(pages_database, q, site, limit, offset)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except FileNotFoundError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @app.get("/api/v1/pages/{page_id}")
    def page(page_id: int):
        try:
            result = get_page(pages_database, page_id)
        except FileNotFoundError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        if result is None:
            raise HTTPException(status_code=404, detail="Page introuvable.")
        return result

    @app.get("/api/v1/documents")
    def documents(
        q: str | None = Query(default=None, max_length=200),
        site: str | None = Query(default=None, max_length=255),
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=1_000_000),
    ):
        try:
            return list_documents(pdf_database, q, site, limit, offset)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except FileNotFoundError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @app.get("/api/v1/documents/{document_id}")
    def document(document_id: int):
        try:
            result = get_document(pdf_database, document_id)
        except FileNotFoundError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        if result is None:
            raise HTTPException(status_code=404, detail="Document introuvable.")
        return result

    return app


app = create_app(
    Path(os.environ.get("OPENDATA_PAGES_DB", DEFAULT_PAGES_DATABASE)),
    Path(os.environ.get("OPENDATA_PDF_DB", DEFAULT_PDF_DATABASE)),
)


if __name__ == "__main__":
    uvicorn.run(
        "api:app",
        host=os.environ.get("OPENDATA_HOST", "127.0.0.1"),
        port=int(os.environ.get("OPENDATA_PORT", "8000")),
        reload=False,
    )
