# Bakkt Docker Documentation Platform

A Dockerized Bakkt API documentation and mock server. Serves a custom developer portal with versioned API reference pages, guides, and a live mock endpoint for every imported OpenAPI operation.

## Features

- **Custom developer portal** — Bakkt-styled UI with orange top bar, version dropdown, scrollable sidebar, and article view for guides.
- **Per-section versioning** — each API section (`onboarding`, `accounts`, `stablecoin`, `zaira`, `bakktx`) stores specs at `data/sections/{section}/{version}/openapi.json`.
- **Guide versioning** — guides are stored at `data/guides/{version}/{slug}.html` and indexed via `index.json`. Internal cross-links are rewritten to local routes.
- **Dynamic mock server** — every path in every imported OpenAPI file is callable through `/mock/{section}/{version}/{path}`, with schema-aware example responses.
- **Password protection** — all portal and API routes require login. Unauthenticated requests are redirected to `/login` with the original URL preserved as a `next` query parameter. Wrong passwords show an inline error on the login page.
- **Persistent data** — spec and guide data is mounted via a Docker volume so it survives container rebuilds.

## Project structure

```
app/
  main.py                  # FastAPI app entry point + context wiring
  app_context.py           # Shared AppContext dataclass
  routes/
    auth_routes.py         # Login / logout
    portal_routes.py       # Portal home, guides, API reference
    mock_routes.py         # Dynamic mock endpoint
    admin_api_routes.py    # Raw admin JSON API (upload specs/guides, catalog)
    system_routes.py       # /healthcheck, root redirect
  services/
    auth_service.py
    content_service.py
    mock_engine.py
    presentation_service.py
    source_client.py
    file_store.py
data/
  sections/{section}/{version}/openapi.json
  guides/{version}/{slug}.html
  guides/{version}/index.json
tests/
  test_app.py
```

## Pages and routes

| Route | Description |
|---|---|
| `GET /` | Redirects to `/portal` (or `/login` if not authenticated) |
| `GET /login` | Login page |
| `POST /login` | Authenticate; redirects to `next` on success |
| `POST /logout` | Clear session cookie |
| `GET /portal` | Developer hub home |
| `GET /portal/guides/{version}` | Guides index (first guide) |
| `GET /portal/guides/{version}/{slug}` | Individual guide article |
| `GET /portal/reference/{section}/{version}` | API reference for a section |
| `GET /mock/{section}/{version}/{path}` | Live mock endpoint |
| `GET /healthcheck` | Health status (no auth required) |
| `GET /admin/catalog` | JSON catalog of all sections and guide versions |
| `POST /admin/sections/{section}/versions/{version}/openapi` | Upload an OpenAPI spec |
| `POST /admin/sections/{section}/versions/{version}/guides/{slug}` | Upload a guide markdown file |

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `DOCS_PASSWORD` | `Rajesh123` | Password for the documentation portal |
| `BAKKT_SOURCE_BASE_URL` | `https://docs.bakkt.com` | Source URL for seeding specs and guides on startup |
| `BAKKT_SOURCE_PASSWORD` | *(empty)* | Password for the source docs site (if required) |

Copy the example file and customise before running:

```powershell
Copy-Item .env.example .env
```

## Run with Docker

```powershell
docker compose up --build -d
```

> **OneDrive / Windows BuildKit issue:** If the build fails with `invalid file request app/main.py`, disable BuildKit:
> ```powershell
> $env:DOCKER_BUILDKIT='0'
> docker compose build
> docker compose up -d
> ```

Once running, open:

- http://localhost:8000 — redirects to login then portal
- http://localhost:8000/portal — developer hub home
- http://localhost:8000/healthcheck — health status

## Run tests

```powershell
.venv\Scripts\pytest tests/ -v
```

The test suite covers auth, portal pages, API reference, guide rendering, mock engine behaviour, schema-example generation, and version-delete routes. Live Docker smoke tests are automatically skipped when the container is not reachable.

## Tech stack

- **Python 3.12**
- **FastAPI 0.116** + **Uvicorn**
- **Pydantic 2**
- **python-markdown**
- **Docker / Docker Compose**

## Environment variables

- `DOCS_PASSWORD`: password for docs/admin login
- `BAKKT_SOURCE_BASE_URL`: source docs host (default `https://docs.bakkt.com`)
- `BAKKT_SOURCE_PASSWORD`: optional source docs password used during import

## Useful routes

- `GET /healthcheck`
- `GET /portal`
- `GET /portal/reference/{section}/{version}`
- `GET /portal/guides/{version}`
- `GET /portal/guides/{version}/{slug}`
- `GET /specs/{section}/{version}/openapi.json`
- `ANY /mock/{section}/{version}/{path}`

## Notes

- This is a local Dockerized docs + mock platform, not an official Bakkt backend.
- Data is persisted in local project files under `data/`.
- Re-import seed for 1.0 (if needed): `GET /admin/reseed-1-0` (requires login cookie).
