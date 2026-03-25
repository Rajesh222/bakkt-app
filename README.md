# Bakkt Docker Documentation Platform

This project is a Dockerized Bakkt documentation + API mock platform with:

- All existing `1.0` API sections imported from Bakkt OpenAPI JSON files.
- All existing `1.0` guides imported and rendered with preserved cross-links.
- Per-section API versioning (`section + version + openapi.json`) instead of one global API version.
- Guide versioning using markdown (`.md`) uploads while preserving previous versions for preview.
- Password protection for docs portal and admin management endpoints.

## What is included

### 1) Full 1.0 endpoint coverage (callable)
On startup, the service imports these section specs from `https://docs.bakkt.com/openapi/`:

- `onboarding_api.json`
- `accounts-api.json`
- `stablecoin_api.json`
- `zaira_api.json`
- `bakktx_api.json`

Each section is stored at:

- `data/sections/{section}/{version}/openapi.json`

Each section/version OpenAPI has its `servers` rewritten to:

- `/mock/{section}/{version}`

So **every path in each imported 1.0 OpenAPI file is callable locally** through the dynamic mock router.

### 2) Existing guides visible unchanged + cross-links preserved
The system imports guide pages from `docs.bakkt.com/docs/*`, stores their rendered body HTML, and serves them as versioned guides:

- `data/guides/1.0/{slug}.html`
- `data/guides/1.0/index.json`

Internal guide links such as `/docs/{slug}` are rewritten to local routes:

- `/portal/guides/1.0/{slug}`

### 3) Management system for new versions
The browser-based Admin UI is available at:

- `/portal/admin`

It lets you:

- upload a new section/version OpenAPI JSON
- upload a new versioned guide as markdown
- inspect current section and guide versions
- re-seed the 1.0 dataset

Raw admin APIs are also available if you want to automate uploads:

- `POST /admin/sections/{section}/versions/{version}/openapi`
  - upload OpenAPI `.json` file
- `POST /admin/sections/{section}/versions/{version}/guides/{slug}`
  - upload markdown `.md` guide
- `GET /admin/catalog`
  - view current section/version and guide inventory

Old versions remain untouched and available for preview.

### 4) Password protection
Docs and admin routes require login:

- `GET /login`
- `POST /login`
- `POST /logout`

Password is controlled by env var `DOCS_PASSWORD`.

## Run with Docker

1. Ensure env file exists:

```powershell
Copy-Item .env.example .env
```

2. Build and run:

```powershell
docker compose up --build -d
```

If Docker BuildKit fails with `invalid file request app/main.py` in a OneDrive-backed folder on Windows, build with classic mode:

```powershell
$env:DOCKER_BUILDKIT='0'
docker compose build
docker compose up -d
```

3. Open portal:

- http://localhost:8010/login
- then http://localhost:8010/portal
- admin UI: http://localhost:8010/portal/admin

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
