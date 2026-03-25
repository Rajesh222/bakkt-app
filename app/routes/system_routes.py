from __future__ import annotations

from urllib import parse

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse


def register_system_routes(app: FastAPI, app_title: str, app_version: str, auth_cookie: str) -> None:
    @app.get("/healthcheck")
    def healthcheck() -> dict[str, str]:
        return {"status": "ok", "service": app_title, "version": app_version}

    @app.get("/")
    def root(req: Request) -> RedirectResponse:
        if req.cookies.get(auth_cookie) != "1":
            query = parse.urlencode({"next": "/portal"})
            return RedirectResponse(url=f"/login?{query}", status_code=302)
        return RedirectResponse(url="/portal", status_code=302)
