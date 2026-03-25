from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.app_context import AppContext


def register_mock_routes(
    app: FastAPI,
    get_context: callable,
) -> None:
    @app.api_route(
        "/mock/{section}/{version}/{subpath:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    )
    async def dynamic_mock(section: str, version: str, subpath: str, req: Request) -> JSONResponse:
        ctx: AppContext = get_context()
        matchers = ctx.compiled_operations.get((section, version), [])
        if not matchers:
            raise HTTPException(status_code=404, detail="Unknown section/version")

        target_method = req.method.lower()
        target_path = "/" + subpath

        for item in matchers:
            if item.method != target_method:
                continue
            m = item.regex.match(target_path)
            if not m:
                continue

            body_json: Any = None
            if req.method in {"POST", "PUT", "PATCH"}:
                try:
                    body_json = await req.json()
                except Exception:
                    body_json = None

            status, example = ctx.pick_example(item.responses, item.components)
            if example is not None:
                return JSONResponse(example, status_code=status)

            return JSONResponse(
                {
                    "mock": True,
                    "section": section,
                    "version": version,
                    "operationId": item.operation_id,
                    "method": req.method,
                    "path": target_path,
                    "pathParams": m.groupdict(),
                    "query": dict(req.query_params),
                    "body": body_json,
                },
                status_code=status,
            )

        raise HTTPException(status_code=404, detail="Operation not found in section/version spec")
