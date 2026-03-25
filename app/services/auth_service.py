from __future__ import annotations

from urllib import parse

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse


def is_authenticated(req: Request, auth_cookie: str) -> bool:
    return req.cookies.get(auth_cookie) == "1"


def require_auth(req: Request, auth_cookie: str) -> None:
    if not is_authenticated(req, auth_cookie):
        raise HTTPException(status_code=401, detail="Authentication required")


def admin_redirect(message: str, level: str = "success") -> RedirectResponse:
    query = parse.urlencode({"message": message, "level": level})
    return RedirectResponse(url=f"/portal/admin?{query}", status_code=303)
