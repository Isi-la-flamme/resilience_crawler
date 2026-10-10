# Prototype API Open Data

This local, read-only prototype serves prepared page text, the PDF index, and
downloaded images from the crawler's local image directory. It does not expose
SQLite files or local file paths. Every response marks reuse rights as
`not_assessed`; the prototype is not an official open-data catalogue.

## Run locally

Install dependencies, then start the server bound to localhost:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe api.py
```

Open <http://127.0.0.1:8000/> for the dashboard or
<http://127.0.0.1:8000/docs> for interactive API documentation. The service
defaults to `crawl-clean.db`, `pdf-index.db`, `crawl.db`, and `img/` in the
project directory. Override those paths with `OPENDATA_PAGES_DB`,
`OPENDATA_PDF_DB`, `OPENDATA_CRAWL_DB`, and `OPENDATA_IMAGES_DIR`.
`OPENDATA_HOST` defaults to `127.0.0.1`; do not expose this prototype directly
to the public internet.

The pages database is prepared by `cleaner.py`; pages whose cleaned text is
shorter than 500 characters are excluded there. The original crawl database is
left unchanged. The API also hides pages and documents whose URL path ends
with `pricing` or `token`, including those already present in older databases.

## Endpoints

- `GET /api/v1/health`
- `GET /api/v1/sites`
- `GET /api/v1/pages?q=budget&site=primature.gov.bf&match_mode=whole`
- `GET /api/v1/pages/{id}`
- `GET /api/v1/documents?q=recrutement&site=www.defense.gov.bf&match_mode=contains`
- `GET /api/v1/documents/{id}`
- `GET /api/v1/images?q=logo&site=primature.gov.bf&match_mode=whole`
- `GET /api/v1/images/file?url={encoded-image-url}`

Search terms are combined with AND. Page searches cover `title` and `text`;
document searches cover `title`, `author`, `site`, and extracted PDF `text`.
The `match_mode` parameter is available for pages, documents, and images:
`contains` (default) finds a term inside a word (for example, `char` matches
`charge`), while `whole` matches only complete words. The dashboard exposes
the same choice beside the search box.
List responses expose this scope as `search_fields`, the parsed terms as
`matched_terms`, and the source title, URL, and a text excerpt for each result.
Page excerpts are centered on the first query term when it occurs in the text;
PDF excerpts come from the local text or full-text index. The dashboard
highlights query terms in titles, excerpts, and full text. Select
**Lire le texte intégral** on a result to fetch its complete text from the local SQLite-backed API; the
detail endpoints return all text stored in the database without truncation.
No request is made to the institution's website when opening a result. The
source URL is displayed as provenance, not as a link. PDF files themselves
are not served; only extracted text stored in the local index is displayed.
Page and document lists are paginated.

The dashboard's **Images** tab lists only image records marked downloaded whose
local file exists under the configured image directory. The site filter applies
to the page's crawl site. Clicking a thumbnail opens a larger local preview.
Image files are never fetched from source websites, and the file endpoint
accepts an image URL only as a lookup key; it does not accept filesystem paths
or proxy remote URLs. Missing or out-of-directory files are omitted from the
gallery and return `404`.

Install `requirements-dev.txt` to run the tests that use FastAPI's test client.
