from __future__ import annotations

from contextlib import asynccontextmanager
import html
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any
from urllib import error, parse, request

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from markdown import markdown

from app.app_context import AppContext
from app.services import auth_service, content_service
from app.services.auth_service import admin_redirect as service_admin_redirect
from app.services.auth_service import is_authenticated as service_is_authenticated
from app.services.auth_service import require_auth as service_require_auth
from app.services.presentation_service import html_page as service_html_page
from app.services import content_service
from app.services.file_store import read_json, write_json
from app.services.mock_engine import (
    OperationMatcher,
    compile_path,
    pick_example,
    schema_to_example,
)
from app.services.source_client import (
    extract_ssr_props,
    make_source_opener,
    rewrite_guide_links,
    url_get,
)
from app.routes.admin_api_routes import register_admin_api_routes
from app.routes.auth_routes import register_auth_routes
from app.routes.mock_routes import register_mock_routes
from app.routes.portal_routes import register_portal_routes
from app.routes.system_routes import register_system_routes


APP_TITLE = "Bakkt Docker Docs + Mock"
APP_VERSION = "2.0.0"

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
SECTIONS_DIR = DATA_DIR / "sections"
GUIDES_DIR = DATA_DIR / "guides"

SOURCE_BASE_URL = os.getenv("BAKKT_SOURCE_BASE_URL", "https://docs.bakkt.com")
SOURCE_PASSWORD = os.getenv("BAKKT_SOURCE_PASSWORD", "")

DOCS_PASSWORD = os.getenv("DOCS_PASSWORD", "Rajesh123")
AUTH_COOKIE = "bakkt_docs_auth"

IMPORT_SPECS: dict[str, str] = {
    "onboarding": "onboarding_api.json",
    "accounts": "accounts-api.json",
    "stablecoin": "stablecoin_api.json",
    "zaira": "zaira_api.json",
    "bakktx": "bakktx_api.json",
}

GUIDE_INDEX_FILE = "index.json"


compiled_operations: dict[tuple[str, str], list[OperationMatcher]] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    _seed_all()
    yield


app = FastAPI(title=APP_TITLE, version=APP_VERSION, lifespan=lifespan)


def _ensure_dirs() -> None:
    content_service.ensure_dirs(SECTIONS_DIR, GUIDES_DIR)


def _is_authenticated(req: Request) -> bool:
    return service_is_authenticated(req, AUTH_COOKIE)


def _require_auth(req: Request) -> None:
    service_require_auth(req, AUTH_COOKIE)


def _html_page(title: str, body: str) -> HTMLResponse:
        return service_html_page(title, body)


def _read_json(path: Path) -> Any:
    return read_json(path)


def _write_json(path: Path, payload: Any) -> None:
    write_json(path, payload)


def _openapi_path(section: str, version: str) -> Path:
    return content_service.openapi_path(SECTIONS_DIR, section, version)


def _guide_version_dir(version: str) -> Path:
    return content_service.guide_version_dir(GUIDES_DIR, version)


def _guide_index_path(version: str) -> Path:
    return content_service.guide_index_path(GUIDES_DIR, version)


def _list_sections() -> dict[str, list[str]]:
    return content_service.list_sections(SECTIONS_DIR)


def _list_guide_versions() -> list[str]:
    return content_service.list_guide_versions(GUIDES_DIR)


def _compile_path(path_template: str) -> re.Pattern[str]:
    return compile_path(path_template)


def _compile_section_version(section: str, version: str) -> None:
    content_service.compile_section_version(SECTIONS_DIR, section, version, compiled_operations)


def _compile_all() -> None:
    content_service.compile_all(SECTIONS_DIR, compiled_operations)


def _load_guide_index(version: str) -> list[dict[str, Any]]:
    return content_service.load_guide_index(GUIDES_DIR, version)


def _save_guide_index(version: str, data: list[dict[str, Any]]) -> None:
    content_service.save_guide_index(GUIDES_DIR, version, data)


def _save_openapi_spec(section: str, version: str, raw: bytes) -> dict[str, Any]:
    return content_service.save_openapi_spec(SECTIONS_DIR, ROOT_DIR, compiled_operations, section, version, raw)


def _save_guide_markdown(
    section: str,
    version: str,
    slug: str,
    raw: bytes,
    title: str,
    category: str,
) -> dict[str, Any]:
    return content_service.save_guide_markdown(GUIDES_DIR, ROOT_DIR, section, version, slug, raw, title, category)


def _admin_redirect(message: str, level: str = "success") -> RedirectResponse:
    return service_admin_redirect(message, level)


def _url_get(url: str, opener: request.OpenerDirector | None = None) -> str:
    return url_get(url, opener)


def _make_source_opener() -> request.OpenerDirector:
    return make_source_opener(SOURCE_BASE_URL, SOURCE_PASSWORD)


def _extract_ssr_props(html_text: str) -> dict[str, Any]:
    return extract_ssr_props(html_text)


def _seed_initial_openapi(version: str = "1.0") -> None:
    content_service.seed_initial_openapi(SECTIONS_DIR, SOURCE_BASE_URL, SOURCE_PASSWORD, IMPORT_SPECS, version)


def _rewrite_guide_links(html_body: str, version: str) -> str:
    return rewrite_guide_links(html_body, version)


def _seed_initial_guides(version: str = "1.0") -> None:
    content_service.seed_initial_guides(GUIDES_DIR, SOURCE_BASE_URL, SOURCE_PASSWORD, version)


def _seed_all() -> None:
    content_service.seed_all(SECTIONS_DIR, GUIDES_DIR, compiled_operations, SOURCE_BASE_URL, SOURCE_PASSWORD, IMPORT_SPECS)


def _get_context() -> AppContext:
    return AppContext(
        root_dir=ROOT_DIR,
        sections_dir=SECTIONS_DIR,
        guides_dir=GUIDES_DIR,
        source_base_url=SOURCE_BASE_URL,
        source_password=SOURCE_PASSWORD,
        docs_password=DOCS_PASSWORD,
        auth_cookie=AUTH_COOKIE,
        import_specs=IMPORT_SPECS,
        compiled_operations=compiled_operations,
    )


register_system_routes(app, APP_TITLE, APP_VERSION, AUTH_COOKIE)
register_auth_routes(app, DOCS_PASSWORD, AUTH_COOKIE, _html_page)
register_portal_routes(app=app, get_context=_get_context)


def _schema_to_example(schema: dict[str, Any], components: dict[str, Any], _depth: int = 0) -> Any:
    return schema_to_example(schema, components, _depth)


def _pick_example(responses: dict[str, Any], components: dict[str, Any]) -> tuple[int, Any | None]:
    return pick_example(responses, components)


register_mock_routes(app, _get_context)


register_admin_api_routes(app=app, get_context=_get_context)
