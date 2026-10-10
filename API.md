# Prototype API Open Data

This local, read-only prototype serves prepared page text and the PDF index. It
does not expose SQLite files or local file paths. Every response marks reuse
rights as `not_assessed`; the prototype is not an official open-data catalogue.

## Run locally

Install dependencies, then start the server bound to localhost:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe api.py
```

Open <http://127.0.0.1:8000/> for the dashboard or
<http://127.0.0.1:8000/docs> for interactive API documentation. The service
defaults to `crawl-clean.db` and `pdf-index.db` in the project directory.
Override those paths with `OPENDATA_PAGES_DB` and `OPENDATA_PDF_DB`.
`OPENDATA_HOST` defaults to `127.0.0.1`; do not expose this prototype directly
to the public internet.

The pages database is prepared by `cleaner.py`; pages whose cleaned text is
shorter than 500 characters are excluded there. The original crawl database is
left unchanged. The API also hides pages and documents whose URL path ends
with `pricing` or `token`, including those already present in older databases.

## Endpoints

- `GET /api/v1/health`
- `GET /api/v1/sites`
- `GET /api/v1/pages?q=budget&site=primature.gov.bf&limit=20&offset=0`
- `GET /api/v1/pages/{id}`
- `GET /api/v1/documents?q=recrutement&site=www.defense.gov.bf`
- `GET /api/v1/documents/{id}`

Search terms are combined with AND. Page searches cover `title` and `text`;
document searches cover `title`, `author`, `site`, and extracted PDF `text`.
List responses expose this scope as `search_fields`, the parsed terms as
`matched_terms`, and the source title, URL, and a text excerpt for each result.
Page excerpts are centered on the first query term when it occurs in the text;
PDF excerpts come from the full-text index. The dashboard highlights query
terms in titles, excerpts, and full text. Select **Lire le texte intégral** on
a result to fetch its complete text from the local SQLite-backed API; the
detail endpoints return all text stored in the database without truncation.
No request is made to the institution's website when opening a result. The
source URL is displayed as provenance, not as a link. PDF files themselves
are not served; only extracted text stored in the local index is displayed.
Page and document lists are paginated.

Install `requirements-dev.txt` to run the tests that use FastAPI's test client.
