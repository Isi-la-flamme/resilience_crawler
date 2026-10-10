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

## Endpoints

- `GET /api/v1/health`
- `GET /api/v1/sites`
- `GET /api/v1/pages?q=budget&site=primature.gov.bf&limit=20&offset=0`
- `GET /api/v1/pages/{id}`
- `GET /api/v1/documents?q=recrutement&site=www.defense.gov.bf`
- `GET /api/v1/documents/{id}`

Search terms are combined with AND. Page and document lists are paginated;
full-text details are capped at 100,000 characters. PDF files themselves are
not served: results link to the institution's original source.

Install `requirements-dev.txt` to run the tests that use FastAPI's test client.
