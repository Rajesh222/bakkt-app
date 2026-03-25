from __future__ import annotations

import html
from collections.abc import Callable
from urllib import parse

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse


def register_auth_routes(
    app: FastAPI,
    docs_password: str,
    auth_cookie: str,
    html_page: Callable[[str, str], HTMLResponse],
) -> None:
    @app.get("/login", response_class=HTMLResponse)
    def login_form(next: str = "/portal", error: str = "") -> HTMLResponse:
                page = f"""
<!doctype html>
<html>
<head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Password Protected</title>
    <style>
        :root {{ --text:#0f172a; --muted:#6b7280; --line:#e5e7eb; --bg:#f8fafc; --brand:#0f766e; }}
        * {{ box-sizing: border-box; }}
        body {{
            margin: 0;
            min-height: 100vh;
            display: grid;
            place-items: center;
            background:
                radial-gradient(1200px 500px at 50% -20%, #e2e8f0 0%, transparent 60%),
                linear-gradient(180deg, #ffffff 0%, var(--bg) 65%);
            color: var(--text);
            font-family: \"Segoe UI\", Tahoma, Geneva, Verdana, sans-serif; font-size:14px;
            padding: 20px;
        }}
        .panel {{
            width: 100%;
            max-width: 420px;
            background: #fff;
            border: 1px solid var(--line);
            border-radius: 14px;
            padding: 28px 24px;
            text-align: center;
            box-shadow: 0 16px 32px rgba(2, 6, 23, 0.08);
        }}
        .brand {{
            width: 56px;
            height: 56px;
            border-radius: 999px;
            margin: 0 auto 14px;
            display: grid;
            place-items: center;
            font-weight: 700;
            color: #fff;
            background: linear-gradient(135deg, #0f766e 0%, #0ea5e9 100%);
            letter-spacing: 0.5px;
        }}
        h1 {{ margin: 0; font-size: 1.45rem; font-weight: 650; }}
        p {{ margin: 10px 0 0; color: var(--muted); }}
        form {{ margin-top: 18px; }}
        input[type=\"password\"] {{
            width: 100%;
            border: 1px solid #cbd5e1;
            border-radius: 10px;
            padding: 12px;
            font-size: 1rem;
            outline: none;
        }}
        input[type=\"password\"]:focus {{
            border-color: #0ea5e9;
            box-shadow: 0 0 0 3px rgba(14, 165, 233, 0.15);
        }}
        button {{
            width: 100%;
            margin-top: 10px;
            border: 0;
            border-radius: 10px;
            padding: 12px;
            font-size: 0.98rem;
            font-weight: 600;
            color: #fff;
            background: var(--brand);
            cursor: pointer;
        }}
        button:hover {{ filter: brightness(0.95); }}
        .meta {{ margin-top: 14px; font-size: 0.92rem; }}
        .meta a {{ color: #0284c7; text-decoration: none; }}
        .meta a:hover {{ text-decoration: underline; }}
        .error {{
            margin-top: 12px;
            border: 1px solid #fecaca;
            background: #fef2f2;
            color: #991b1b;
            border-radius: 10px;
            padding: 10px 12px;
            font-size: 0.92rem;
            text-align: left;
        }}
    </style>
</head>
<body>
    <main class=\"panel\">
        <div class=\"brand\">B</div>
        <h1>Password Protected</h1>
        <p>Enter the site's password to view it.</p>
        <form method=\"post\" action=\"/login\">
            <input type=\"hidden\" name=\"next\" value=\"{html.escape(next)}\" />
            <input type=\"password\" name=\"password\" placeholder=\"Password\" required />
            <button type=\"submit\">Submit</button>
        </form>
        {f'<div class="error">{html.escape(error)}</div>' if error else ''}
        <div class=\"meta\">
            <a href=\"https://docs.bakkt.com/login?redirect=/\" target=\"_blank\" rel=\"noreferrer\">Admin and Editor Login</a>
        </div>
    </main>
</body>
</html>
"""
                return HTMLResponse(page)

    @app.post("/login")
    async def login(password: str = Form(...), next: str = Form("/portal")) -> RedirectResponse:
        safe_next = next if next.startswith("/") else "/portal"
        if password != docs_password:
            query = parse.urlencode({"next": safe_next, "error": "Invalid password"})
            res = RedirectResponse(url=f"/login?{query}", status_code=302)
            return res

        res = RedirectResponse(url=safe_next, status_code=302)
        res.set_cookie(auth_cookie, "1", httponly=True, samesite="lax")
        return res

    @app.post("/logout")
    def logout() -> RedirectResponse:
        res = RedirectResponse(url="/login", status_code=302)
        res.delete_cookie(auth_cookie)
        return res
