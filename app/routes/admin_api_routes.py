from __future__ import annotations

import shutil
from typing import Any
from urllib import error

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse

from app.app_context import AppContext


def register_admin_api_routes(
    app: FastAPI,
    get_context: callable,
) -> None:
    @app.post("/portal/admin/delete-version")
    def admin_delete_version(
        req: Request,
        section: str = Form(...),
        version: str = Form(...),
    ) -> RedirectResponse:
        ctx: AppContext = get_context()
        ctx.require_auth(req)
        section_local = section.strip().lower()
        version_local = version.strip()
        dst = ctx.openapi_path(section_local, version_local)
        if dst.exists():
            shutil.rmtree(dst.parent)
            ctx.compiled_operations.pop((section_local, version_local), None)
            return ctx.admin_redirect(f"Deleted {section_local} v{version_local}", "success")
        return ctx.admin_redirect(f"{section_local} v{version_local} not found", "error")

    @app.post("/portal/admin/delete-guide-version")
    def admin_delete_guide_version(
        req: Request,
        version: str = Form(...),
    ) -> RedirectResponse:
        ctx: AppContext = get_context()
        ctx.require_auth(req)
        version_local = version.strip()
        vdir = ctx.guide_version_dir(version_local)
        if vdir.exists():
            shutil.rmtree(vdir)
            return ctx.admin_redirect(f"Deleted all guides for v{version_local}", "success")
        return ctx.admin_redirect(f"Guide version v{version_local} not found", "error")

    @app.get("/admin/catalog")
    def admin_catalog(req: Request) -> JSONResponse:
        ctx: AppContext = get_context()
        ctx.require_auth(req)
        guides = {version: ctx.load_guide_index(version) for version in ctx.list_guide_versions()}
        return JSONResponse({"sections": ctx.list_sections(), "guides": guides})

    @app.post("/admin/sections/{section}/versions/{version}/openapi")
    async def upload_openapi(
        section: str,
        version: str,
        req: Request,
        openapi_file: UploadFile = File(...),
    ) -> JSONResponse:
        ctx: AppContext = get_context()
        ctx.require_auth(req)
        raw = await openapi_file.read()
        return JSONResponse(ctx.save_openapi_spec(section, version, raw))

    @app.post("/admin/sections/{section}/versions/{version}/guides/{slug}")
    async def upload_guide(
        section: str,
        version: str,
        slug: str,
        req: Request,
        guide_file: UploadFile = File(...),
        title: str = Form(""),
        category: str = Form("Custom"),
    ) -> JSONResponse:
        ctx: AppContext = get_context()
        ctx.require_auth(req)

        if not guide_file.filename.lower().endswith(".md"):
            raise HTTPException(status_code=400, detail="Guide must be a .md file")

        raw = await guide_file.read()
        return JSONResponse(ctx.save_guide_markdown(section, version, slug, raw, title, category))

    @app.get("/admin/reseed-1-0")
    def reseed(req: Request) -> JSONResponse:
        ctx: AppContext = get_context()
        ctx.require_auth(req)
        try:
            ctx.seed_initial_openapi("1.0")
            ctx.seed_initial_guides("1.0")
            ctx.compile_all()
        except error.URLError as exc:
            raise HTTPException(status_code=502, detail=f"Unable to reach source docs: {exc}") from exc
        return JSONResponse({"status": "reseeded"})
