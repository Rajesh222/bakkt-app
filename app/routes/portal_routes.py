from __future__ import annotations

import html
import json
import re
from typing import Any
from urllib import error, parse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.app_context import AppContext
from app.services.mock_engine import schema_to_example


HTTP_METHODS = ("get", "post", "put", "patch", "delete", "options", "head")
METHOD_COLORS = {
    "get": "#2563eb",
    "post": "#0284c7",
    "put": "#7c3aed",
    "patch": "#d97706",
    "delete": "#dc2626",
    "options": "#475569",
    "head": "#475569",
}


def _operation_key(method: str, path_name: str, operation: dict[str, Any]) -> str:
    explicit = operation.get("operationId")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    slug = re.sub(r"[^a-z0-9]+", "-", f"{method}-{path_name}".lower()).strip("-")
    return slug or "operation"


def _resolve_schema(schema: dict[str, Any] | None, components: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    if not isinstance(schema, dict) or depth > 8:
        return {}

    ref = schema.get("$ref")
    if ref:
        parts = ref.lstrip("#/").split("/")
        resolved: Any = {"components": components}
        for part in parts:
            if isinstance(resolved, dict):
                resolved = resolved.get(part, {})
        if isinstance(resolved, dict):
            return _resolve_schema(resolved, components, depth + 1)
        return {}

    if isinstance(schema.get("allOf"), list):
        merged: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
        for item in schema["allOf"]:
            resolved = _resolve_schema(item, components, depth + 1)
            merged["properties"].update(resolved.get("properties", {}))
            merged["required"] = sorted(set(merged["required"]) | set(resolved.get("required", [])))
        return merged

    if isinstance(schema.get("oneOf"), list) and schema["oneOf"]:
        return _resolve_schema(schema["oneOf"][0], components, depth + 1)

    if isinstance(schema.get("anyOf"), list) and schema["anyOf"]:
        return _resolve_schema(schema["anyOf"][0], components, depth + 1)

    return schema


def _schema_label(schema: dict[str, Any] | None, components: dict[str, Any]) -> str:
    resolved = _resolve_schema(schema, components)
    schema_type = resolved.get("type", "object")
    if schema_type == "array":
        return f"array<{_schema_label(resolved.get('items', {}), components)}>"
    if schema_type == "object" and resolved.get("properties"):
        return "object"
    if resolved.get("enum"):
        return "enum"
    if resolved.get("format"):
        return f"{schema_type} ({resolved['format']})"
    return str(schema_type)


def _collect_schema_fields(schema: dict[str, Any] | None, components: dict[str, Any]) -> list[dict[str, Any]]:
    resolved = _resolve_schema(schema, components)
    properties = resolved.get("properties", {}) if isinstance(resolved, dict) else {}
    required = set(resolved.get("required", [])) if isinstance(resolved, dict) else set()

    fields: list[dict[str, Any]] = []
    for name, prop in properties.items():
        prop_resolved = _resolve_schema(prop, components)
        description = prop_resolved.get("description", "")
        enum_values = prop_resolved.get("enum")
        if isinstance(enum_values, list) and enum_values:
            description = f"{description} Allowed values: {', '.join(map(str, enum_values))}".strip()
        fields.append(
            {
                "name": name,
                "required": name in required,
                "type": _schema_label(prop_resolved, components),
                "description": description,
            }
        )
    return fields


def _pick_json_media(content: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(content, dict):
        return {}
    for media_type in ("application/json", "application/*+json"):
        candidate = content.get(media_type)
        if isinstance(candidate, dict):
            return candidate
    for candidate in content.values():
        if isinstance(candidate, dict):
            return candidate
    return {}


def _media_example(media: dict[str, Any], components: dict[str, Any]) -> Any:
    if not isinstance(media, dict):
        return None
    if "example" in media:
        return media.get("example")
    if isinstance(media.get("examples"), dict) and media["examples"]:
        first = next(iter(media["examples"].values()), {})
        if isinstance(first, dict):
            return first.get("value")
    schema = media.get("schema")
    if isinstance(schema, dict):
        return schema_to_example(schema, components)
    return None


def _join_url(base_url: str, path_name: str) -> str:
    if not base_url:
        return path_name
    return f"{base_url.rstrip('/')}/{path_name.lstrip('/')}"


def _build_curl(method: str, url: str, body_example: Any, auth_headers: list[str]) -> str:
    lines = [f"curl --request {method.upper()} \\", f"  --url {url} \\"]
    for header_name in auth_headers:
        lines.append(f"  --header '{header_name}: <value>' \\")
    lines.append("  --header 'accept: application/json' \\")
    if body_example is not None:
        lines.append("  --header 'content-type: application/json' \\")
        lines.append(f"  --data '{json.dumps(body_example, separators=(",", ":"))}'")
    else:
        lines[-1] = lines[-1].removesuffix(" \\")
    return "\n".join(lines)


def _preferred_operation(operations: list[dict[str, Any]], requested_key: str | None) -> dict[str, Any] | None:
    if not operations:
        return None
    if requested_key:
        for item in operations:
            if item["key"] == requested_key:
                return item
    for item in operations:
        haystack = f"{item['summary']} {item['path']} {item['tag']}".lower()
        if "login" in haystack or item["tag"].lower() == "authentication":
            return item
    return operations[0]


def _version_sort_key(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", value)
    if not parts:
        return (0,)
    return tuple(int(part) for part in parts)


def _choose_portal_version(sections: dict[str, list[str]], guide_versions: list[str], requested: str | None) -> str:
    available = {version for versions in sections.values() for version in versions}
    available.update(guide_versions)
    if requested and requested in available:
        return requested
    if "1.0" in available:
        return "1.0"
    ordered = sorted(available, key=_version_sort_key, reverse=True)
    return ordered[0] if ordered else "1.0"


def _collect_portal_guides(ctx: AppContext, version: str, query: str) -> list[dict[str, str]]:
    items = ctx.load_guide_index(version)
    results = []
    for item in items:
        title = str(item.get("title") or item.get("slug") or "Guide")
        slug = str(item.get("slug") or "")
        if not slug:
            continue
        haystack = f"{title} {item.get('category', '')}".lower()
        if query and query not in haystack:
            continue
        results.append(
            {
                "label": title,
                "href": f"/portal/guides/{version}/{slug}",
            }
        )
    return results[:4]


def _collect_portal_section_links(ctx: AppContext, section: str, version: str, query: str) -> list[dict[str, str]]:
    spec_path = ctx.openapi_path(section, version)
    if not spec_path.exists():
        return []

    spec = ctx.read_json(spec_path)
    tags = [item for item in spec.get("tags", []) if isinstance(item, dict)]
    operations: list[dict[str, str]] = []
    seen_tags: set[str] = set()
    for path_name, path_item in spec.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method in HTTP_METHODS:
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            tag_name = str((operation.get("tags") or [section.title()])[0])
            if tag_name in seen_tags:
                continue
            summary = str(operation.get("summary") or tag_name)
            haystack = f"{summary} {tag_name} {path_name}".lower()
            if query and query not in haystack:
                continue
            seen_tags.add(tag_name)
            operations.append(
                {
                    "label": tag_name,
                    "href": f"/portal/reference/{section}/{version}?op={parse.quote(_operation_key(method, path_name, operation))}",
                }
            )

    if operations:
        return operations[:4]

    fallback = []
    for tag in tags:
        tag_name = str(tag.get("name") or section.title())
        if query and query not in tag_name.lower():
            continue
        fallback.append(
            {
                "label": tag_name,
                "href": f"/portal/reference/{section}/{version}",
            }
        )
    return fallback[:4]


def _render_portal_home_page(title: str, body: str) -> HTMLResponse:
    page = f"""
<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      --brand:#ff6a00;
      --brand-deep:#f45a00;
      --surface:#ffffff;
      --surface-soft:#f7f7f5;
      --text:#2f3a48;
      --muted:#64748b;
      --line:#e5e7eb;
    }}
    * {{ box-sizing:border-box; }}
    html, body {{ margin:0; padding:0; font-family:"Segoe UI", Tahoma, Geneva, Verdana, sans-serif; font-size:14px; color:var(--text); background:var(--surface-soft); }}
    a {{ color:inherit; text-decoration:none; }}
    button, input, select {{ font:inherit; }}
    .hero-wrap {{ background:linear-gradient(180deg, var(--brand) 0%, #ff6e00 100%); color:#fff; min-height:470px; }}
    .topbar {{ display:grid; grid-template-columns:220px minmax(240px, 410px) 1fr auto; gap:18px; align-items:center; padding:18px 24px 10px; }}
    .brand {{ font-size:2rem; font-weight:800; letter-spacing:-0.05em; }}
    .home-chip {{ justify-self:start; padding:10px 14px; border-radius:8px; background:rgba(255,255,255,0.12); font-weight:700; }}
    .search-shell {{ position:relative; }}
    .search-shell input {{ width:100%; border:0; border-radius:8px; padding:11px 88px 11px 38px; background:#fff; color:#334155; }}
    .search-icon {{ position:absolute; left:12px; top:50%; transform:translateY(-50%); color:#94a3b8; }}
    .search-kbd {{ position:absolute; right:12px; top:50%; transform:translateY(-50%); font-size:.76rem; color:#64748b; border:1px solid #e5e7eb; background:#fff; border-radius:6px; padding:2px 6px; }}
    .top-actions {{ display:flex; align-items:center; gap:16px; justify-self:end; font-weight:700; }}
    .top-actions form {{ margin:0; }}
    .top-actions button {{ border:0; background:transparent; color:#fff; cursor:pointer; font-weight:700; }}
    .subnav {{ display:flex; align-items:center; gap:12px; padding:0 24px; }}
    .version-switcher {{ position:relative; display:inline-flex; align-items:center; }}
    .version-select {{ appearance:none; -webkit-appearance:none; border:0; border-radius:8px; padding:10px 34px 10px 10px; background:transparent; color:#fff; font-weight:800; cursor:pointer; }}
    .version-select option {{ color:#111827; }}
    .version-chevron {{ position:absolute; right:10px; pointer-events:none; font-size:.8rem; }}
    .subnav a {{ display:inline-flex; align-items:center; gap:8px; padding:10px 12px; border-radius:8px; font-weight:700; }}
    .subnav a.active {{ background:rgba(255,255,255,0.13); }}
    .hero-inner {{ max-width:820px; margin:86px auto 0; padding:0 24px 84px; }}
    .hero-inner h1 {{ margin:0 0 18px; font-size:3.5rem; line-height:1.06; letter-spacing:-0.05em; }}
    .hero-inner p {{ margin:0; font-size:1.15rem; line-height:1.55; color:rgba(255,255,255,0.94); }}
    .hero-actions {{ display:flex; align-items:center; gap:18px; margin-top:26px; }}
    .hero-btn {{ display:inline-flex; align-items:center; gap:10px; padding:14px 18px; border-radius:8px; font-weight:800; }}
    .hero-btn.primary {{ background:#fff; color:var(--brand); }}
    .hero-btn.secondary {{ color:#fff; }}
    .content {{ max-width:1040px; margin:0 auto; padding:42px 24px 54px; }}
    .card-grid {{ display:grid; grid-template-columns:repeat(3, minmax(0, 1fr)); gap:46px; }}
    .column-title {{ margin:0 0 16px; color:var(--brand); font-size:1.1rem; font-weight:800; }}
    .column-list {{ border-top:1px solid var(--line); padding-top:14px; display:grid; gap:12px; }}
    .column-list a {{ color:#4b5563; font-size:1rem; }}
    .column-list a:hover {{ color:var(--brand); }}
    .muted-line {{ color:var(--muted); font-size:.94rem; margin-top:10px; }}
    @media (max-width: 960px) {{
      .topbar {{ grid-template-columns:1fr; }}
      .top-actions {{ justify-self:start; }}
      .subnav {{ overflow:auto; white-space:nowrap; padding-bottom:8px; }}
      .hero-inner {{ margin-top:54px; }}
      .hero-inner h1 {{ font-size:2.7rem; }}
      .card-grid {{ grid-template-columns:1fr; gap:28px; }}
    }}
  </style>
</head>
<body>
  {body}
</body>
</html>
"""
    return HTMLResponse(page)


def _login_redirect(req: Request) -> RedirectResponse:
    next_path = req.url.path
    if req.url.query:
        next_path = f"{next_path}?{req.url.query}"
    query = parse.urlencode({"next": next_path})
    return RedirectResponse(url=f"/login?{query}", status_code=302)


def _render_reference_page(title: str, body: str) -> HTMLResponse:
    page = f"""
<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      --brand:#ff6a00;
      --brand-dark:#ec5f00;
      --surface:#ffffff;
      --surface-alt:#f8fafc;
      --surface-muted:#f1f5f9;
      --text:#1f2937;
      --muted:#64748b;
      --line:#e2e8f0;
      --shadow:0 18px 48px rgba(15, 23, 42, 0.08);
      --radius:16px;
    }}
    * {{ box-sizing:border-box; }}
    html, body {{ margin:0; padding:0; background:#fff; color:var(--text); font-family:"Segoe UI", Tahoma, Geneva, Verdana, sans-serif; font-size:14px; }}
    a {{ color:inherit; text-decoration:none; }}
    button, input {{ font:inherit; }}
    .topbar {{ position:sticky; top:0; z-index:40; background:linear-gradient(90deg, var(--brand) 0%, #ff7b00 55%, #ff6a00 100%); color:#fff; box-shadow:0 8px 24px rgba(255, 106, 0, 0.24); }}
    .topbar-row {{ height:58px; display:grid; grid-template-columns:220px minmax(260px, 430px) 1fr auto; align-items:center; gap:18px; padding:0 22px; }}
    .brand {{ font-size:2rem; font-weight:800; letter-spacing:-0.05em; }}
    .top-link {{ font-size:1.05rem; font-weight:600; }}
    .search-shell {{ position:relative; }}
    .search-shell input {{ width:100%; border:0; border-radius:10px; padding:12px 92px 12px 42px; background:rgba(255,255,255,0.96); color:#334155; }}
    .search-shell .search-icon {{ position:absolute; left:14px; top:50%; transform:translateY(-50%); color:#94a3b8; font-size:1rem; }}
    .search-shell .shortcut {{ position:absolute; right:12px; top:50%; transform:translateY(-50%); border:1px solid #dbeafe; border-radius:8px; padding:3px 7px; font-size:.76rem; color:#64748b; background:#fff; }}
    .top-actions {{ display:flex; align-items:center; gap:18px; }}
    .top-actions form {{ margin:0; }}
    .top-actions button {{ border:0; background:transparent; color:#fff; cursor:pointer; font-weight:700; }}
    .subnav {{ height:48px; display:flex; align-items:center; gap:18px; padding:0 22px; border-top:1px solid rgba(255,255,255,0.18); }}
    .version-switcher {{ position:relative; display:inline-flex; align-items:center; }}
    .version-select {{ appearance:none; -webkit-appearance:none; border:0; border-radius:10px; padding:10px 36px 10px 12px; background:rgba(255,255,255,0.12); color:#fff; font-weight:700; cursor:pointer; }}
    .version-select option {{ color:#111827; }}
    .version-chevron {{ position:absolute; right:12px; pointer-events:none; font-size:.8rem; opacity:.9; }}
    .subnav a {{ display:inline-flex; align-items:center; gap:8px; padding:10px 12px; border-radius:10px; font-weight:600; }}
    .subnav a.active {{ background:rgba(255,255,255,0.18); }}
    .shell {{ display:grid; grid-template-columns:290px minmax(0, 1fr) 430px; min-height:calc(100vh - 106px); }}
    .sidebar {{ border-right:1px solid var(--line); background:#fbfcfe; padding:16px 12px 24px; max-height:calc(100vh - 106px); overflow-y:auto; overscroll-behavior:contain; }}
    .sidebar-search {{ display:flex; align-items:center; justify-content:space-between; gap:12px; border:2px solid #93c5fd; border-radius:14px; padding:10px 12px; background:#fff; box-shadow:0 6px 20px rgba(37, 99, 235, 0.08); color:#475569; font-weight:700; margin-bottom:18px; }}
    .sidebar-search .kbd {{ border:1px solid #cbd5e1; border-radius:8px; padding:2px 6px; font-size:.74rem; color:#64748b; background:#f8fafc; }}
    .sidebar-section {{ margin-top:18px; }}
    .sidebar-section h3 {{ margin:0 0 10px; font-size:.86rem; letter-spacing:.03em; text-transform:uppercase; color:#475569; }}
    .sidebar-section p {{ margin:0 0 10px; color:var(--muted); font-size:.88rem; line-height:1.45; }}
    .nav-list {{ display:grid; gap:6px; }}
    .nav-item {{ display:flex; align-items:flex-start; justify-content:space-between; gap:10px; padding:10px 10px; border-radius:12px; color:#475569; }}
    .nav-item.active {{ background:#eef2f7; color:#111827; font-weight:600; }}
    .nav-item:hover {{ background:#f3f6fa; }}
    .nav-text {{ min-width:0; flex:1; }}
    .nav-label {{ display:block; white-space:normal; overflow-wrap:anywhere; line-height:1.35; }}
    .method-pill {{ display:inline-flex; align-items:center; justify-content:center; min-width:52px; padding:4px 8px; border-radius:999px; font-size:.76rem; font-weight:800; color:#fff; text-transform:uppercase; }}
    .content {{ padding:28px 34px 40px; background:#fff; }}
    .breadcrumbs {{ display:flex; flex-wrap:wrap; align-items:center; gap:10px; color:#64748b; font-size:.88rem; font-weight:700; text-transform:uppercase; letter-spacing:.02em; }}
    .breadcrumbs .sep {{ color:#cbd5e1; }}
    .page-title {{ margin:14px 0 8px; font-size:2.4rem; font-weight:600; letter-spacing:-0.03em; line-height:1.08; }}
    .endpoint-line {{ display:flex; flex-wrap:wrap; align-items:center; gap:12px; margin-bottom:24px; }}
    .endpoint-url {{ color:#64748b; font-size:1.1rem; }}
    .lede {{ max-width:760px; color:#374151; font-size:1.08rem; line-height:1.6; margin:0 0 28px; }}
    .section-block {{ border-top:1px solid var(--line); padding:28px 0; }}
    .section-block h2 {{ margin:0 0 14px; font-size:1.55rem; font-weight:600; letter-spacing:-0.02em; }}
    .ghost-table {{ width:100%; border:1px solid var(--line); border-radius:14px; overflow:hidden; background:#fff; box-shadow:var(--shadow); }}
    .ghost-table table {{ width:100%; border-collapse:collapse; }}
    .ghost-table th, .ghost-table td {{ padding:14px 16px; text-align:left; border-bottom:1px solid var(--line); }}
    .ghost-table th {{ background:#f8fafc; color:#64748b; font-size:.78rem; text-transform:uppercase; letter-spacing:.03em; }}
    .ghost-table tr:last-child td {{ border-bottom:0; }}
    .empty-row {{ color:#64748b; text-align:center; }}
    .param-note {{ color:#475569; line-height:1.6; margin-bottom:18px; }}
    .param-note p {{ margin:0 0 10px; }}
    .param-list {{ display:grid; gap:12px; }}
    .param-item {{ display:grid; grid-template-columns:minmax(0, 1fr) 160px; gap:18px; padding:16px 18px; border:1px solid var(--line); border-radius:14px; background:#fff; }}
    .param-item code {{ padding:3px 7px; border-radius:8px; background:#eef2ff; color:#3730a3; font-weight:700; }}
    .param-item p {{ margin:8px 0 0; color:#64748b; line-height:1.5; }}
    .required-badge {{ display:inline-flex; margin-left:8px; padding:2px 6px; border-radius:999px; background:#fff7ed; color:#c2410c; font-size:.72rem; font-weight:800; text-transform:uppercase; }}
    .param-type {{ align-self:start; justify-self:end; padding:7px 10px; border-radius:999px; background:#f8fafc; border:1px solid var(--line); color:#475569; font-size:.84rem; font-weight:700; }}
    .response-grid {{ display:grid; gap:12px; }}
    .response-card {{ display:grid; grid-template-columns:90px 1fr; gap:18px; padding:16px 18px; border:1px solid var(--line); border-radius:14px; background:#fff; }}
    .status-pill {{ display:inline-flex; align-items:center; justify-content:center; width:72px; padding:10px 0; border-radius:999px; background:#eff6ff; color:#1d4ed8; font-weight:800; }}
    .response-card p {{ margin:0; color:#64748b; line-height:1.5; }}
    .code-pane {{ border-left:1px solid var(--line); background:#fbfcff; padding:28px 24px 32px; }}
    .sticky-pane {{ position:sticky; top:126px; display:grid; gap:16px; }}
    .mini-label {{ margin:0 0 10px; color:#475569; font-size:.84rem; font-weight:800; text-transform:uppercase; }}
    .credential-card, .url-card, .code-card, .response-preview {{ border:1px solid var(--line); border-radius:16px; background:#fff; box-shadow:0 10px 24px rgba(15, 23, 42, 0.05); }}
    .credential-tabs {{ display:flex; gap:0; border-bottom:1px solid var(--line); }}
    .credential-tab {{ flex:1; padding:12px 14px; text-align:center; font-weight:700; color:#94a3b8; background:#fff; }}
    .credential-tab.active {{ color:#0f172a; background:#f8fafc; }}
    .credential-body {{ padding:14px 16px; color:#64748b; min-height:64px; display:flex; align-items:center; justify-content:space-between; gap:14px; }}
    .credential-name {{ color:#111827; font-weight:700; }}
    .lock {{ font-size:1.1rem; color:#94a3b8; }}
    .url-row {{ display:grid; grid-template-columns:100px minmax(0, 1fr); }}
    .url-row strong {{ padding:14px 16px; border-right:1px solid var(--line); color:#475569; background:#f8fafc; }}
    .url-row span {{ padding:14px 16px; color:#64748b; overflow-wrap:anywhere; }}
    .code-card {{ overflow:hidden; background:#111827; color:#e5e7eb; }}
    .code-head {{ display:flex; align-items:center; justify-content:space-between; padding:14px 16px; background:#1f2937; font-weight:800; }}
    pre {{ margin:0; white-space:pre-wrap; word-break:break-word; }}
    .code-body {{ padding:18px 16px 18px; font-size:.92rem; line-height:1.6; }}
    .try-row {{ display:flex; justify-content:flex-end; padding:0 16px 16px; }}
    .try-btn {{ border:0; border-radius:12px; padding:10px 16px; background:#ff8b1f; color:#fff; font-weight:800; box-shadow:0 8px 18px rgba(255, 139, 31, 0.35); cursor:not-allowed; }}
    .response-preview pre {{ padding:16px; background:#f8fafc; color:#0f172a; border-radius:0 0 16px 16px; }}
    .empty-state {{ padding:28px; border:1px dashed var(--line); border-radius:18px; color:#64748b; background:#f8fafc; }}
    @media (max-width: 1180px) {{
      .shell {{ grid-template-columns:260px minmax(0, 1fr); }}
      .code-pane {{ grid-column:1 / -1; border-left:0; border-top:1px solid var(--line); }}
      .sticky-pane {{ position:static; }}
    }}
    @media (max-width: 900px) {{
      .topbar-row {{ grid-template-columns:1fr; height:auto; padding:14px 16px; }}
      .subnav {{ overflow:auto; white-space:nowrap; padding:0 16px; }}
      .shell {{ grid-template-columns:1fr; }}
      .sidebar {{ border-right:0; border-bottom:1px solid var(--line); }}
      .content {{ padding:22px 18px 28px; }}
    .page-title {{ font-size:1.9rem; }}
      .param-item, .response-card, .url-row {{ grid-template-columns:1fr; }}
      .param-type {{ justify-self:start; }}
    }}
  </style>
</head>
<body>
  {body}
</body>
</html>
"""
    return HTMLResponse(page)


def _load_guide_content(ctx: AppContext, version: str, slug: str) -> str:
        vdir = ctx.guide_version_dir(version)
        html_file = vdir / f"{slug}.html"
        md_file = vdir / f"{slug}.md"

        if html_file.exists():
                body = html_file.read_text(encoding="utf-8")
                return ctx.rewrite_guide_links(body, version)
        if md_file.exists():
                md = md_file.read_text(encoding="utf-8")
                return ctx.rewrite_guide_links(ctx.render_markdown(md), version)
        raise HTTPException(status_code=404, detail="Guide not found")


def _strip_primary_heading(rendered_html: str) -> str:
        return re.sub(r"^\s*<h1[^>]*>.*?</h1>\s*", "", rendered_html, count=1, flags=re.I | re.S)


def _render_guides_doc_page(
        ctx: AppContext,
        version: str,
        items: list[dict[str, Any]],
        active_slug: str,
        rendered: str,
        heading: str,
        category: str,
        q: str,
) -> HTMLResponse:
        ordered = sorted(items, key=lambda x: int(x.get("order", 0)))
        grouped: dict[str, list[dict[str, Any]]] = {}
        query = q.strip().lower()
        for item in ordered:
                title = str(item.get("title") or item.get("slug") or "Guide")
                slug = str(item.get("slug") or "")
                cat = str(item.get("category") or "Guides")
                if not slug:
                        continue
                haystack = f"{title} {slug} {cat}".lower()
                if query and query not in haystack:
                        continue
                grouped.setdefault(cat, []).append(item)

        versions = sorted(ctx.list_guide_versions(), key=_version_sort_key)
        version_options = []
        for item_version in versions:
                href = f"/portal/guides/{item_version}"
                if item_version == version:
                        href = f"/portal/guides/{item_version}/{active_slug}"
                        if q:
                                href = f"{href}?q={parse.quote(q)}"
                selected_attr = " selected" if item_version == version else ""
                version_options.append(f"<option value='{html.escape(href)}'{selected_attr}>{html.escape(item_version)}</option>")

        nav_sections = []
        for cat_name, cat_items in grouped.items():
                links = []
                for item in cat_items:
                        slug = str(item.get("slug") or "")
                        title = str(item.get("title") or slug)
                        href = f"/portal/guides/{version}/{slug}"
                        if q:
                                href = f"{href}?q={parse.quote(q)}"
                        active_class = "active" if slug == active_slug else ""
                        links.append(f"<a class='guide-link {active_class}' href='{html.escape(href)}'>{html.escape(title)}</a>")
                nav_sections.append(
                        "<section class='guide-group'>"
                        f"<h3>{html.escape(cat_name.upper())}</h3>"
                        f"<div class='guide-links'>{''.join(links)}</div>"
                        "</section>"
                )

        next_link_html = ""
        current_index = next((idx for idx, item in enumerate(ordered) if str(item.get("slug")) == active_slug), -1)
        if current_index >= 0 and current_index + 1 < len(ordered):
                nxt = ordered[current_index + 1]
                nxt_slug = str(nxt.get("slug") or "")
                nxt_title = str(nxt.get("title") or nxt_slug)
                next_link_html = (
                        "<div class='next-row'>"
                        f"<a href='/portal/guides/{html.escape(version)}/{html.escape(nxt_slug)}'>"
                        f"{html.escape(nxt_title)} <span>→</span></a></div>"
                )

        body = f"""
<header class="guide-topbar">
    <div class="top-row">
        <div class="brand">Bakkt</div>
        <a class="home-chip" href="/portal">Home</a>
        <form class="search-shell" method="get" action="/portal/guides/{html.escape(version)}/{html.escape(active_slug)}">
            <span class="search-icon">⌕</span>
            <input type="text" name="q" value="{html.escape(q)}" placeholder="Search" />
            <span class="search-kbd">CTRL-K</span>
        </form>
        <div class="top-actions">
            <form method="post" action="/logout"><button type="submit">Log Out</button></form>
        </div>
    </div>
    <nav class="guide-nav">
        <div class="version-switcher">
            <select class="version-select" aria-label="Select guide version" onchange="if (this.value) window.location.href=this.value;">
                {''.join(version_options) or f'<option selected>{html.escape(version)}</option>'}
            </select>
            <span class="version-chevron">▼</span>
        </div>
        <a href="/portal">Home</a>
        <a class="active" href="/portal/guides/{html.escape(version)}">Guides</a>
        <a href="/portal/reference/onboarding/{html.escape(version)}">API Reference</a>
    </nav>
</header>
<div class="guide-shell">
    <aside class="guide-sidebar">
        {''.join(nav_sections) if nav_sections else '<div class="empty">No guides match your search.</div>'}
    </aside>
    <main class="guide-content">
        <div class="crumb">{html.escape(category.upper())}</div>
        <h1>{html.escape(heading)}</h1>
        <hr />
        <article class="doc-body">{_strip_primary_heading(rendered)}</article>
        {next_link_html}
    </main>
</div>
"""

        page = f"""
<!doctype html>
<html>
<head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>{html.escape(heading)}</title>
    <style>
        :root {{ --brand:#ff6a00; --text:#343b45; --muted:#677585; --line:#d9dde2; --bg:#f4f5f7; }}
        * {{ box-sizing:border-box; }}
        html, body {{ margin:0; padding:0; font-family:"Segoe UI", Tahoma, Geneva, Verdana, sans-serif; font-size:14px; background:var(--bg); color:var(--text); }}
        a {{ color:inherit; text-decoration:none; }}
        button, input, select {{ font:inherit; }}
        .guide-topbar {{ background:var(--brand); color:#fff; box-shadow:0 8px 22px rgba(255,106,0,.24); }}
        .top-row {{ display:grid; grid-template-columns:190px 90px minmax(250px, 400px) 1fr; align-items:center; gap:14px; padding:12px 16px 8px; }}
        .brand {{ font-size:2rem; font-weight:800; letter-spacing:-0.05em; }}
        .home-chip {{ font-weight:700; }}
        .search-shell {{ position:relative; }}
        .search-shell input {{ width:100%; border:0; border-radius:8px; padding:10px 84px 10px 36px; }}
        .search-icon {{ position:absolute; left:11px; top:50%; transform:translateY(-50%); color:#94a3b8; }}
        .search-kbd {{ position:absolute; right:10px; top:50%; transform:translateY(-50%); color:#64748b; border:1px solid #e5e7eb; border-radius:6px; padding:2px 6px; font-size:.76rem; background:#fff; }}
        .top-actions {{ justify-self:end; display:flex; align-items:center; gap:12px; font-weight:700; }}
        .top-actions form {{ margin:0; }}
        .top-actions button {{ border:0; background:transparent; color:#fff; cursor:pointer; font-weight:700; }}
        .guide-nav {{ display:flex; align-items:center; gap:10px; padding:0 16px 8px; }}
        .guide-nav a {{ padding:8px 10px; border-radius:7px; font-weight:700; }}
        .guide-nav a.active {{ background:rgba(255,255,255,.16); }}
        .version-switcher {{ position:relative; display:inline-flex; align-items:center; }}
        .version-select {{ appearance:none; border:0; border-radius:8px; padding:8px 30px 8px 8px; background:transparent; color:#fff; font-weight:700; }}
        .version-select option {{ color:#111827; }}
        .version-chevron {{ position:absolute; right:8px; pointer-events:none; font-size:.8rem; }}
        .guide-shell {{ display:grid; grid-template-columns:280px minmax(0, 1fr); min-height:calc(100vh - 96px); }}
        .guide-sidebar {{ background:#f0f2f5; border-right:1px solid #d7dce3; padding:16px 10px 20px; max-height:calc(100vh - 96px); overflow:auto; }}
        .guide-group {{ margin-bottom:18px; }}
        .guide-group h3 {{ margin:0 0 8px; font-size:1.02rem; color:#5f6f81; letter-spacing:.02em; }}
        .guide-links {{ display:grid; gap:6px; }}
        .guide-link {{ padding:8px 10px; border-radius:8px; color:#485567; line-height:1.35; }}
        .guide-link:hover {{ background:#e6ebf2; }}
        .guide-link.active {{ background:#dfe3e8; font-weight:700; }}
        .guide-content {{ padding:26px 30px 44px; }}
        .crumb {{ color:#647181; font-size:1.02rem; font-weight:700; margin-bottom:8px; }}
        .guide-content h1 {{ margin:0 0 16px; font-size:2.1rem; font-weight:600; color:#303843; }}
        .guide-content hr {{ border:0; border-top:1px solid var(--line); margin:0 0 24px; }}
        .doc-body {{ color:#414b57; line-height:1.65; max-width:920px; }}
        .doc-body h1, .doc-body h2, .doc-body h3 {{ font-weight:600; color:#2f3741; }}
        .doc-body pre {{ background:#1f2937; color:#e5e7eb; padding:12px; border-radius:8px; overflow:auto; }}
        .doc-body code {{ background:#eef2f7; padding:2px 5px; border-radius:5px; }}
        .next-row {{ margin-top:28px; border-top:1px solid var(--line); padding-top:18px; max-width:920px; display:flex; justify-content:flex-end; }}
        .next-row a {{ color:#4a5565; font-size:1.8rem; font-weight:600; display:inline-flex; align-items:center; gap:10px; }}
        .next-row span {{ font-size:2rem; line-height:1; }}
        .empty {{ padding:14px; color:#6b7280; }}
        @media (max-width: 980px) {{
            .top-row {{ grid-template-columns:1fr; }}
            .top-actions {{ justify-self:start; }}
            .guide-nav {{ overflow:auto; white-space:nowrap; }}
            .guide-shell {{ grid-template-columns:1fr; }}
            .guide-sidebar {{ max-height:none; border-right:0; border-bottom:1px solid #d7dce3; }}
            .guide-content {{ padding:20px 16px 30px; }}
            .guide-content h1 {{ font-size:1.7rem; }}
            .next-row a {{ font-size:1.2rem; }}
        }}
    </style>
</head>
<body>{body}</body>
</html>
"""
        return HTMLResponse(page)


def _build_reference_response(ctx: AppContext, section: str, version: str, op: str | None, q: str) -> HTMLResponse:
        spec_path = ctx.openapi_path(section, version)
        if not spec_path.exists():
                raise HTTPException(status_code=404, detail="Spec not found")

        spec = ctx.read_json(spec_path)
        components = spec.get("components", {}) if isinstance(spec, dict) else {}
        server_url = ""
        servers = spec.get("servers", []) if isinstance(spec, dict) else []
        if isinstance(servers, list) and servers:
                first_server = servers[0]
                if isinstance(first_server, dict):
                        server_url = str(first_server.get("url", ""))

        tag_descriptions = {
                item.get("name", "General"): item.get("description", "")
                for item in spec.get("tags", [])
                if isinstance(item, dict) and item.get("name")
        }

        operations: list[dict[str, Any]] = []
        for path_name, path_item in spec.get("paths", {}).items():
                if not isinstance(path_item, dict):
                        continue
                for method in HTTP_METHODS:
                        operation = path_item.get(method)
                        if not isinstance(operation, dict):
                                continue

                        media = _pick_json_media((operation.get("requestBody") or {}).get("content"))
                        request_schema = media.get("schema") if isinstance(media.get("schema"), dict) else {}
                        response_status, response_example = ctx.pick_example(operation.get("responses", {}), components)
                        security_requirements = operation.get("security") or spec.get("security") or []
                        auth_headers: list[str] = []
                        for requirement in security_requirements:
                                if not isinstance(requirement, dict):
                                        continue
                                for scheme_name in requirement:
                                        scheme = components.get("securitySchemes", {}).get(scheme_name, {})
                                        if isinstance(scheme, dict) and scheme.get("in") == "header":
                                                auth_headers.append(str(scheme.get("name", scheme_name)))

                        tag_name = (operation.get("tags") or ["General"])[0]
                        operations.append(
                                {
                                        "key": _operation_key(method, path_name, operation),
                                        "method": method,
                                        "path": path_name,
                                        "tag": tag_name,
                                        "summary": operation.get("summary") or operation.get("operationId") or path_name,
                                        "description": operation.get("description") or "No description provided.",
                                        "request_description": (operation.get("requestBody") or {}).get("description", ""),
                                        "request_fields": _collect_schema_fields(request_schema, components),
                                        "request_example": _media_example(media, components),
                                        "responses": operation.get("responses", {}),
                                        "response_status": response_status,
                                        "response_example": response_example,
                                        "auth_headers": auth_headers,
                                        "url": _join_url(server_url, path_name),
                                }
                        )

        query = q.strip().lower()
        filtered_operations = [
                item
                for item in operations
                if not query
                or query in item["summary"].lower()
                or query in item["path"].lower()
                or query in item["tag"].lower()
        ]

        selected = _preferred_operation(filtered_operations or operations, op)
        if not selected:
                raise HTTPException(status_code=404, detail="No operations found in spec")

        selected_request_html = ""
        if selected["request_description"]:
                selected_request_html = ctx.render_markdown(selected["request_description"])

        request_fields_html = "".join(
                (
                        "<div class='param-item'>"
                        f"<div><div><code>{html.escape(field['name'])}</code>"
                        f"{'<span class=\'required-badge\'>required</span>' if field['required'] else ''}</div>"
                        f"<p>{html.escape(field['description'] or 'No description provided.')}</p></div>"
                        f"<div class='param-type'>{html.escape(field['type'])}</div>"
                        "</div>"
                )
                for field in selected["request_fields"]
        )
        if not request_fields_html:
                request_fields_html = "<div class='empty-state'>This operation does not define request body parameters.</div>"

        response_cards = []
        for code, response in selected["responses"].items():
                description = response.get("description", "No description provided.") if isinstance(response, dict) else "No description provided."
                response_cards.append(
                        "<div class='response-card'>"
                        f"<div><span class='status-pill'>{html.escape(str(code))}</span></div>"
                        f"<div><strong>{html.escape(description)}</strong><p>Response code {html.escape(str(code))} for this endpoint.</p></div>"
                        "</div>"
                )
        responses_html = "".join(response_cards) or "<div class='empty-state'>No responses defined.</div>"

        request_preview = json.dumps(selected["request_example"], indent=2) if selected["request_example"] is not None else "{}"
        response_preview = json.dumps(selected["response_example"], indent=2) if selected["response_example"] is not None else "{}"
        curl_request = _build_curl(selected["method"], selected["url"], selected["request_example"], selected["auth_headers"])

        grouped_operations: dict[str, list[dict[str, Any]]] = {}
        for item in filtered_operations or operations:
                grouped_operations.setdefault(item["tag"], []).append(item)

        ordered_tags = [item.get("name") for item in spec.get("tags", []) if isinstance(item, dict) and item.get("name")]
        for tag_name in grouped_operations:
                if tag_name not in ordered_tags:
                        ordered_tags.append(tag_name)

        nav_sections = []
        for tag_name in ordered_tags:
                items = grouped_operations.get(tag_name)
                if not items:
                        continue

                nav_items = []
                for item in items:
                        query_string = parse.urlencode({key: value for key, value in {"op": item["key"], "q": q}.items() if value})
                        href = f"/portal/reference/{section}/{version}"
                        if query_string:
                                href = f"{href}?{query_string}"
                        nav_items.append(
                                f"<a class='nav-item {'active' if item['key'] == selected['key'] else ''}' href='{html.escape(href)}'>"
                                f"<span class='nav-text'><span class='nav-label'>{html.escape(item['summary'])}</span></span>"
                                f"<span class='method-pill' style='background:{METHOD_COLORS.get(item['method'], '#475569')};'>{html.escape(item['method'])}</span>"
                                "</a>"
                        )

                nav_sections.append(
                        "<section class='sidebar-section'>"
                        f"<h3>{html.escape(tag_name)}</h3>"
                        f"<p>{html.escape(tag_descriptions.get(tag_name, ''))}</p>"
                        f"<div class='nav-list'>{''.join(nav_items)}</div>"
                        "</section>"
                )

        security_header = selected["auth_headers"][0] if selected["auth_headers"] else "No auth header required"
        guides_href = f"/portal/guides/{version}"
        version_links = ctx.list_sections().get(section, [])
        version_badge = f"v{html.escape(version)}"
        version_options = []
        version_query = parse.urlencode({key: value for key, value in {"q": q}.items() if value})
        for item_version in version_links:
            href = f"/portal/reference/{section}/{item_version}"
            if version_query:
                href = f"{href}?{version_query}"
            selected_attr = " selected" if item_version == version else ""
            version_options.append(
                f"<option value='{html.escape(href)}'{selected_attr}>{html.escape(item_version)}</option>"
            )
        version_switcher = " · ".join(
                f"<a href='/portal/reference/{html.escape(section)}/{html.escape(item_version)}'>{html.escape(item_version)}</a>"
                for item_version in version_links
        )

        body = f"""
<header class="topbar">
    <div class="topbar-row">
        <div class="brand">Bakkt</div>
        <a class="top-link" href="/portal">Home</a>
        <form class="search-shell" method="get" action="/portal/reference/{html.escape(section)}/{html.escape(version)}">
            <span class="search-icon">⌕</span>
            <input type="text" name="q" value="{html.escape(q)}" placeholder="Search" />
            <span class="shortcut">CTRL-K</span>
            <input type="hidden" name="op" value="{html.escape(selected['key'])}" />
        </form>
        <div class="top-actions">
            <form method="post" action="/logout"><button type="submit">Log Out</button></form>
        </div>
    </div>
    <nav class="subnav">
        <form class="version-switcher" onsubmit="return false;">
            <select class="version-select" aria-label="Select version" onchange="if (this.value) window.location.href=this.value;">
                {''.join(version_options) or f'<option selected>{html.escape(version)}</option>'}
            </select>
            <span class="version-chevron">▼</span>
        </form>
        <a href="/portal">Home</a>
        <a href="{html.escape(guides_href)}">Guides</a>
        <a class="active" href="/portal/reference/{html.escape(section)}/{html.escape(version)}">API Reference</a>
    </nav>
</header>
<div class="shell">
    <aside class="sidebar">
        <div class="sidebar-search"><span>JUMP TO</span><span class="kbd">CTRL-/</span></div>
        {''.join(nav_sections) if nav_sections else '<div class="empty-state">No operations match your search.</div>'}
    </aside>
    <main class="content">
        <div class="breadcrumbs">
            <span>{html.escape(section)} API</span>
            <span class="sep">›</span>
            <span>{html.escape(selected['tag'])}</span>
            <span class="sep">⌂</span>
            <span>{version_badge}</span>
        </div>
        <h1 class="page-title">{html.escape(selected['summary'])}</h1>
        <div class="endpoint-line">
            <span class="method-pill" style="background:{METHOD_COLORS.get(selected['method'], '#475569')};">{html.escape(selected['method'])}</span>
            <span class="endpoint-url">{html.escape(selected['url'])}</span>
        </div>
        <p class="lede">{html.escape(selected['description'])}</p>
        <div class="section-block">
            <h2>Recent Requests</h2>
            <div class="ghost-table">
                <table>
                    <thead>
                        <tr><th>Time</th><th>Status</th><th>User Agent</th></tr>
                    </thead>
                    <tbody>
                        <tr><td colspan="3" class="empty-row">Make a request to see history.</td></tr>
                    </tbody>
                </table>
            </div>
        </div>
        <div class="section-block">
            <h2>Body Params</h2>
            <div class="param-note">{selected_request_html or '<p>No request body description provided.</p>'}</div>
            <div class="param-list">{request_fields_html}</div>
        </div>
        <div class="section-block">
            <h2>Responses</h2>
            <div class="response-grid">{responses_html}</div>
        </div>
        <div class="section-block">
            <h2>Section Versions</h2>
            <p class="lede">Available versions for this section: {version_switcher or html.escape(version)}</p>
        </div>
    </main>
    <aside class="code-pane">
        <div class="sticky-pane">
            <section class="credential-card">
                <div class="credential-tabs">
                    <div class="credential-tab active">Header</div>
                    <div class="credential-tab">Authorization</div>
                </div>
                <div class="credential-body">
                    <div>
                        <div class="mini-label" style="margin-bottom:6px;">Credentials</div>
                        <div class="credential-name">{html.escape(security_header)}</div>
                    </div>
                    <span class="lock">🔒</span>
                </div>
            </section>
            <section class="url-card">
                <div class="url-row">
                    <strong>Base URL</strong>
                    <span>{html.escape(selected['url'])}</span>
                </div>
            </section>
            <section class="code-card">
                <div class="code-head"><span>cURL Request</span><span>Examples</span></div>
                <pre class="code-body">{html.escape(curl_request)}</pre>
                <div class="try-row"><button class="try-btn" type="button">Try It!</button></div>
            </section>
            <section class="response-preview">
                <div class="code-head" style="background:#f8fafc; color:#111827;"><span>Response</span><span>{html.escape(str(selected['response_status']))}</span></div>
                <pre>{html.escape(response_preview)}</pre>
            </section>
            <section class="response-preview">
                <div class="code-head" style="background:#f8fafc; color:#111827;"><span>Request Example</span><span>JSON</span></div>
                <pre>{html.escape(request_preview)}</pre>
            </section>
        </div>
    </aside>
</div>
"""
        return _render_reference_page(f"Reference {section} {version}", body)


def register_portal_routes(
    app: FastAPI,
    get_context: callable,
) -> None:
    @app.get("/portal", response_class=HTMLResponse)
    def portal(req: Request, version: str | None = None, q: str = "") -> Response:
        ctx: AppContext = get_context()
        if not ctx.is_authenticated(req):
            return _login_redirect(req)

        sections = ctx.list_sections()
        guide_versions = ctx.list_guide_versions()
        selected_version = _choose_portal_version(sections, guide_versions, version)
        query = q.strip().lower()

        versions_for_select = sorted({item for values in sections.values() for item in values} | set(guide_versions), key=_version_sort_key)
        version_options = []
        for item_version in versions_for_select:
            href = f"/portal?version={parse.quote(item_version)}"
            if q:
                href = f"{href}&q={parse.quote(q)}"
            selected_attr = " selected" if item_version == selected_version else ""
            version_options.append(f"<option value='{html.escape(href)}'{selected_attr}>{html.escape(item_version)}</option>")

        guide_links = _collect_portal_guides(ctx, selected_version, query)
        guide_items = "".join(f"<a href='{html.escape(item['href'])}'>{html.escape(item['label'])}</a>" for item in guide_links)
        guide_items = guide_items or f"<a href='/portal/guides/{html.escape(selected_version)}'>Welcome to Bakkt API</a>"

        preferred_sections = [name for name in ("accounts", "onboarding") if name in sections]
        remaining_sections = [name for name in sorted(sections) if name not in preferred_sections]
        section_columns = []
        for section_name in (preferred_sections + remaining_sections)[:2]:
            versions = sorted(sections.get(section_name, []), key=_version_sort_key)
            section_version = selected_version if selected_version in versions else (versions[-1] if versions else selected_version)
            links = _collect_portal_section_links(ctx, section_name, section_version, query)
            link_html = "".join(f"<a href='{html.escape(item['href'])}'>{html.escape(item['label'])}</a>" for item in links)
            if versions:
                more_href = f"/portal/reference/{section_name}/{section_version}"
            else:
                more_href = "/portal"
            link_html += f"<a href='{html.escape(more_href)}'>View More...</a>"
            section_columns.append(
                f"<section><h2 class='column-title'>{html.escape(section_name.title())}</h2><div class='column-list'>{link_html}</div></section>"
            )

        primary_section = preferred_sections[0] if preferred_sections else (sorted(sections)[0] if sections else "onboarding")
        primary_versions = sorted(sections.get(primary_section, []), key=_version_sort_key)
        primary_version = selected_version if selected_version in primary_versions else (primary_versions[-1] if primary_versions else selected_version)
        get_started_href = f"/portal/reference/{primary_section}/{primary_version}"
        guides_href = f"/portal/guides/{selected_version}"

        body = f"""
<div class="hero-wrap">
    <header class="topbar">
        <div class="brand">Bakkt</div>
        <a class="home-chip" href="/portal">Home</a>
        <form class="search-shell" method="get" action="/portal">
            <span class="search-icon">⌕</span>
            <input type="text" name="q" value="{html.escape(q)}" placeholder="Search" />
            <input type="hidden" name="version" value="{html.escape(selected_version)}" />
            <span class="search-kbd">CTRL-K</span>
        </form>
        <div class="top-actions">
            <form method="post" action="/logout"><button type="submit">Log Out</button></form>
        </div>
    </header>
    <nav class="subnav">
        <div class="version-switcher">
            <select class="version-select" aria-label="Select portal version" onchange="if (this.value) window.location.href=this.value;">
                {''.join(version_options) or f'<option selected>{html.escape(selected_version)}</option>'}
            </select>
            <span class="version-chevron">▼</span>
        </div>
        <a class="active" href="/portal">Home</a>
        <a href="{html.escape(guides_href)}">Guides</a>
        <a href="{html.escape(get_started_href)}">API Reference</a>
    </nav>
    <section class="hero-inner">
        <h1>The Bakkt API Developer Hub</h1>
        <p>Welcome to the Bakkt API developer hub. You'll find comprehensive guides and documentation to help you start working with Bakkt API as quickly as possible, as well as support if you get stuck. Let's jump right in!</p>
        <div class="hero-actions">
            <a class="hero-btn primary" href="{html.escape(get_started_href)}">Get Started</a>
            <a class="hero-btn secondary" href="{html.escape(guides_href)}">Read Guides</a>
        </div>
    </section>
</div>
<main class="content">
    <div class="card-grid">
        <section>
            <h2 class="column-title">Getting Started</h2>
            <div class="column-list">{guide_items}</div>
            <div class="muted-line">Version {html.escape(selected_version)} guide highlights</div>
        </section>
        {''.join(section_columns) if section_columns else '<section><h2 class="column-title">API Reference</h2><div class="column-list"><a href="/portal">No section content available</a></div></section>'}
    </div>
</main>
"""
        return _render_portal_home_page("Portal", body)

    @app.get("/portal/reference/{section}/{version}", response_class=HTMLResponse)
    def reference_ui(section: str, version: str, req: Request, op: str | None = None, q: str = "") -> HTMLResponse:
        ctx: AppContext = get_context()
        ctx.require_auth(req)
        return _build_reference_response(ctx, section, version, op, q)

    @app.get("/specs/{section}/{version}/openapi.json")
    def get_spec(section: str, version: str, req: Request) -> JSONResponse:
        ctx: AppContext = get_context()
        ctx.require_auth(req)
        path = ctx.openapi_path(section, version)
        if not path.exists():
            raise HTTPException(status_code=404, detail="Spec not found")
        return JSONResponse(ctx.read_json(path))

    @app.get("/portal/guides/{version}", response_class=HTMLResponse)
    def guides_index(version: str, req: Request, q: str = "") -> HTMLResponse:
        ctx: AppContext = get_context()
        ctx.require_auth(req)
        items = ctx.load_guide_index(version)
        if not items:
            raise HTTPException(status_code=404, detail="Guide version not found")

        ordered = sorted(items, key=lambda x: int(x.get("order", 0)))
        active = ordered[0]
        active_slug = str(active.get("slug") or "")
        if not active_slug:
            raise HTTPException(status_code=404, detail="Guide not found")
        rendered = _load_guide_content(ctx, version, active_slug)
        heading = str(active.get("title") or active_slug)
        category = str(active.get("category") or "Guides")
        return _render_guides_doc_page(ctx, version, items, active_slug, rendered, heading, category, q)

    @app.get("/portal/guides/{version}/{slug:path}", response_class=HTMLResponse)
    def guide_page(version: str, slug: str, req: Request, q: str = "") -> HTMLResponse:
        ctx: AppContext = get_context()
        ctx.require_auth(req)

        items = ctx.load_guide_index(version)
        if not items:
            raise HTTPException(status_code=404, detail="Guide version not found")
        active_item = next((item for item in items if str(item.get("slug") or "") == slug), None)
        if not active_item:
            raise HTTPException(status_code=404, detail="Guide not found")

        rendered = _load_guide_content(ctx, version, slug)
        heading = str(active_item.get("title") or slug)
        category = str(active_item.get("category") or "Guides")
        return _render_guides_doc_page(ctx, version, items, slug, rendered, heading, category, q)
