from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.services.file_store import read_json, write_json
from app.services.mock_engine import OperationMatcher, compile_path
from app.services.source_client import extract_ssr_props, make_source_opener, url_get

GUIDE_INDEX_FILE = "index.json"


def ensure_dirs(sections_dir: Path, guides_dir: Path) -> None:
    sections_dir.mkdir(parents=True, exist_ok=True)
    guides_dir.mkdir(parents=True, exist_ok=True)


def openapi_path(sections_dir: Path, section: str, version: str) -> Path:
    return sections_dir / section / version / "openapi.json"


def guide_version_dir(guides_dir: Path, version: str) -> Path:
    return guides_dir / version


def guide_index_path(guides_dir: Path, version: str) -> Path:
    return guide_version_dir(guides_dir, version) / GUIDE_INDEX_FILE


def list_sections(sections_dir: Path) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    if not sections_dir.exists():
        return result

    for section_dir in sorted(path for path in sections_dir.iterdir() if path.is_dir()):
        versions: list[str] = []
        for version_dir in sorted(path for path in section_dir.iterdir() if path.is_dir()):
            if (version_dir / "openapi.json").exists():
                versions.append(version_dir.name)
        if versions:
            result[section_dir.name] = versions
    return result


def list_guide_versions(guides_dir: Path) -> list[str]:
    if not guides_dir.exists():
        return []
    versions = [path.name for path in guides_dir.iterdir() if path.is_dir() and (path / GUIDE_INDEX_FILE).exists()]
    return sorted(versions)


def compile_section_version(
    sections_dir: Path,
    section: str,
    version: str,
    compiled_operations: dict[tuple[str, str], list[OperationMatcher]],
) -> None:
    openapi_file = openapi_path(sections_dir, section, version)
    if not openapi_file.exists():
        compiled_operations.pop((section, version), None)
        return

    spec = read_json(openapi_file)
    paths = spec.get("paths", {})
    components = spec.get("components", {})
    items: list[OperationMatcher] = []

    for path_template, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, operation in methods.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                continue
            if not isinstance(operation, dict):
                continue
            op_id = operation.get("operationId") or f"{method}_{path_template}"
            items.append(
                OperationMatcher(
                    section=section,
                    version=version,
                    method=method.lower(),
                    path_template=path_template,
                    regex=compile_path(path_template),
                    operation_id=op_id,
                    responses=operation.get("responses", {}),
                    components=components,
                )
            )

    compiled_operations[(section, version)] = items


def compile_all(sections_dir: Path, compiled_operations: dict[tuple[str, str], list[OperationMatcher]]) -> None:
    for section, versions in list_sections(sections_dir).items():
        for version in versions:
            compile_section_version(sections_dir, section, version, compiled_operations)


def load_guide_index(guides_dir: Path, version: str) -> list[dict[str, Any]]:
    index_path = guide_index_path(guides_dir, version)
    if not index_path.exists():
        return []
    data = read_json(index_path)
    if isinstance(data, list):
        return data
    return []


def save_guide_index(guides_dir: Path, version: str, data: list[dict[str, Any]]) -> None:
    write_json(guide_index_path(guides_dir, version), data)


def save_openapi_spec(
    sections_dir: Path,
    root_dir: Path,
    compiled_operations: dict[tuple[str, str], list[OperationMatcher]],
    section: str,
    version: str,
    raw: bytes,
) -> dict[str, Any]:
    try:
        spec = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc

    if "openapi" not in spec or "paths" not in spec:
        raise HTTPException(status_code=400, detail="File is not a valid OpenAPI document")

    spec["servers"] = [{"url": f"/mock/{section}/{version}"}]
    dst = openapi_path(sections_dir, section, version)
    write_json(dst, spec)
    compile_section_version(sections_dir, section, version, compiled_operations)

    return {
        "saved": str(dst.relative_to(root_dir)) if dst.is_relative_to(root_dir) else str(dst),
        "section": section,
        "version": version,
        "operations": len(compiled_operations.get((section, version), [])),
    }


def save_guide_markdown(
    guides_dir: Path,
    root_dir: Path,
    section: str,
    version: str,
    slug: str,
    raw: bytes,
    title: str,
    category: str,
) -> dict[str, Any]:
    text = raw.decode("utf-8")

    version_dir = guide_version_dir(guides_dir, version)
    version_dir.mkdir(parents=True, exist_ok=True)
    target = version_dir / f"{slug}.md"
    target.write_text(text, encoding="utf-8")

    index = load_guide_index(guides_dir, version)
    existing = next((item for item in index if item.get("slug") == slug), None)
    if existing:
        existing["title"] = title or existing.get("title", slug)
        existing["category"] = category or existing.get("category", "Custom")
        existing["source"] = "uploaded-markdown"
        existing["section"] = section
    else:
        index.append(
            {
                "slug": slug,
                "title": title or slug,
                "category": category or "Custom",
                "source": "uploaded-markdown",
                "order": len(index) + 1,
                "section": section,
            }
        )

    save_guide_index(guides_dir, version, index)
    return {
        "saved": str(target.relative_to(root_dir)) if target.is_relative_to(root_dir) else str(target),
        "section": section,
        "version": version,
        "slug": slug,
    }


def seed_initial_openapi(
    sections_dir: Path,
    source_base_url: str,
    source_password: str,
    import_specs: dict[str, str],
    version: str = "1.0",
) -> None:
    opener = make_source_opener(source_base_url, source_password)
    for section, filename in import_specs.items():
        dst = openapi_path(sections_dir, section, version)
        if dst.exists():
            continue

        url = f"{source_base_url}/openapi/{filename}"
        try:
            content = url_get(url, opener)
            spec = json.loads(content)
        except Exception as exc:
            spec = {
                "openapi": "3.0.0",
                "info": {
                    "title": f"{section} {version} placeholder",
                    "version": version,
                    "description": f"Automatic import failed for {url}: {exc}",
                },
                "paths": {},
            }

        spec["servers"] = [{"url": f"/mock/{section}/{version}"}]
        write_json(dst, spec)


def seed_initial_guides(
    guides_dir: Path,
    source_base_url: str,
    source_password: str,
    version: str = "1.0",
) -> None:
    version_dir = guide_version_dir(guides_dir, version)
    version_dir.mkdir(parents=True, exist_ok=True)
    if guide_index_path(guides_dir, version).exists():
        return

    opener = make_source_opener(source_base_url, source_password)
    try:
        root_html = url_get(f"{source_base_url}/docs/getting-started", opener)
        root_props = extract_ssr_props(root_html)
    except Exception:
        save_guide_index(guides_dir, version, [])
        return

    sidebar = root_props.get("sidebar", [])
    guide_entries: list[dict[str, Any]] = []

    order = 0
    for category in sidebar:
        category_title = category.get("title", "Guides")
        pages = category.get("pages", [])
        for page in pages:
            slug = page.get("slug")
            title = page.get("title") or slug
            if not slug:
                continue
            order += 1
            guide_entries.append(
                {
                    "slug": slug,
                    "title": title,
                    "category": category_title,
                    "source": "remote-html",
                    "order": order,
                }
            )

    for item in guide_entries:
        slug = item["slug"]
        try:
            guide_html = url_get(f"{source_base_url}/docs/{slug}", opener)
            props = extract_ssr_props(guide_html)
            body = props.get("rdmd", {}).get("dehydrated", {}).get("body", "")
        except Exception as exc:
            body = f"<p>Unable to import guide {html.escape(slug)}: {html.escape(str(exc))}</p>"
        (version_dir / f"{slug}.html").write_text(body, encoding="utf-8")

    save_guide_index(guides_dir, version, guide_entries)


def seed_all(
    sections_dir: Path,
    guides_dir: Path,
    compiled_operations: dict[tuple[str, str], list[OperationMatcher]],
    source_base_url: str,
    source_password: str,
    import_specs: dict[str, str],
) -> None:
    ensure_dirs(sections_dir, guides_dir)
    seed_initial_openapi(sections_dir, source_base_url, source_password, import_specs, "1.0")
    seed_initial_guides(guides_dir, source_base_url, source_password, "1.0")
    compile_all(sections_dir, compiled_operations)
