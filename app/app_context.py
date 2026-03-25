from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from markdown import markdown

from app.services import auth_service, content_service
from app.services.file_store import read_json
from app.services.mock_engine import OperationMatcher, pick_example
from app.services.presentation_service import html_page
from app.services.source_client import rewrite_guide_links


@dataclass
class AppContext:
    root_dir: Path
    sections_dir: Path
    guides_dir: Path
    source_base_url: str
    source_password: str
    docs_password: str
    auth_cookie: str
    import_specs: dict[str, str]
    compiled_operations: dict[tuple[str, str], list[OperationMatcher]]

    def is_authenticated(self, req: Request) -> bool:
        return auth_service.is_authenticated(req, self.auth_cookie)

    def require_auth(self, req: Request) -> None:
        auth_service.require_auth(req, self.auth_cookie)

    def html_page(self, title: str, body: str) -> HTMLResponse:
        return html_page(title, body)

    def admin_redirect(self, message: str, level: str = "success") -> RedirectResponse:
        return auth_service.admin_redirect(message, level)

    def openapi_path(self, section: str, version: str) -> Path:
        return content_service.openapi_path(self.sections_dir, section, version)

    def guide_version_dir(self, version: str) -> Path:
        return content_service.guide_version_dir(self.guides_dir, version)

    def guide_index_path(self, version: str) -> Path:
        return content_service.guide_index_path(self.guides_dir, version)

    def list_sections(self) -> dict[str, list[str]]:
        return content_service.list_sections(self.sections_dir)

    def list_guide_versions(self) -> list[str]:
        return content_service.list_guide_versions(self.guides_dir)

    def compile_section_version(self, section: str, version: str) -> None:
        content_service.compile_section_version(self.sections_dir, section, version, self.compiled_operations)

    def compile_all(self) -> None:
        content_service.compile_all(self.sections_dir, self.compiled_operations)

    def load_guide_index(self, version: str) -> list[dict[str, Any]]:
        return content_service.load_guide_index(self.guides_dir, version)

    def save_guide_index(self, version: str, data: list[dict[str, Any]]) -> None:
        content_service.save_guide_index(self.guides_dir, version, data)

    def save_openapi_spec(self, section: str, version: str, raw: bytes) -> dict[str, Any]:
        return content_service.save_openapi_spec(
            self.sections_dir,
            self.root_dir,
            self.compiled_operations,
            section,
            version,
            raw,
        )

    def save_guide_markdown(
        self,
        section: str,
        version: str,
        slug: str,
        raw: bytes,
        title: str,
        category: str,
    ) -> dict[str, Any]:
        return content_service.save_guide_markdown(
            self.guides_dir,
            self.root_dir,
            section,
            version,
            slug,
            raw,
            title,
            category,
        )

    def seed_initial_openapi(self, version: str = "1.0") -> None:
        content_service.seed_initial_openapi(
            self.sections_dir,
            self.source_base_url,
            self.source_password,
            self.import_specs,
            version,
        )

    def seed_initial_guides(self, version: str = "1.0") -> None:
        content_service.seed_initial_guides(
            self.guides_dir,
            self.source_base_url,
            self.source_password,
            version,
        )

    def seed_all(self) -> None:
        content_service.seed_all(
            self.sections_dir,
            self.guides_dir,
            self.compiled_operations,
            self.source_base_url,
            self.source_password,
            self.import_specs,
        )

    def read_json(self, path: Path) -> Any:
        return read_json(path)

    def rewrite_guide_links(self, body: str, version: str) -> str:
        return rewrite_guide_links(body, version)

    def render_markdown(self, md: str) -> str:
        return markdown(md, extensions=["extra", "tables", "fenced_code"])

    def pick_example(self, responses: dict[str, Any], components: dict[str, Any]) -> tuple[int, Any | None]:
        return pick_example(responses, components)
