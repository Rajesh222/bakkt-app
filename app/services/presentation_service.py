from __future__ import annotations

import html

from fastapi.responses import HTMLResponse


def html_page(title: str, body: str) -> HTMLResponse:
    page = f"""
<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>{html.escape(title)}</title>
  <style>
    :root {{ --bg:#f8fafc; --card:#ffffff; --text:#102a43; --accent:#0f766e; --line:#d9e2ec; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family: \"Segoe UI\", Tahoma, Geneva, Verdana, sans-serif; font-size:14px; background:linear-gradient(180deg,#f0f9ff,#f8fafc 30%); color:var(--text); }}
    .wrap {{ max-width:1100px; margin:24px auto; padding:0 16px; }}
    .card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; margin-bottom:16px; box-shadow:0 8px 24px rgba(16,42,67,.06); }}
    a {{ color:#0f766e; text-decoration:none; }} a:hover {{ text-decoration:underline; }}
    h1,h2,h3 {{ margin:0 0 12px; }}
    ul {{ margin:0; padding-left:18px; }}
    code {{ background:#e6fffa; padding:2px 6px; border-radius:6px; }}
    input,button,select {{ padding:10px; border-radius:8px; border:1px solid #9fb3c8; }}
    button {{ background:#0f766e; color:#fff; border:none; cursor:pointer; }}
    button:hover {{ filter:brightness(.95); }}
  </style>
</head>
<body>
  <div class=\"wrap\">{body}</div>
</body>
</html>
"""
    return HTMLResponse(page)
