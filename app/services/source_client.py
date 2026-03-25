from __future__ import annotations

import json
import re
from typing import Any
from urllib import parse, request


def url_get(url: str, opener: request.OpenerDirector | None = None) -> str:
    req = request.Request(url, method="GET")
    req.add_header(
        "User-Agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    )
    req.add_header("Accept", "text/html,application/json,*/*;q=0.8")
    with (opener.open(req, timeout=30) if opener else request.urlopen(req, timeout=30)) as resp:
        return resp.read().decode("utf-8", errors="replace")


def make_source_opener(source_base_url: str, source_password: str) -> request.OpenerDirector:
    cj = request.HTTPCookieProcessor()
    opener = request.build_opener(cj)
    if source_password:
        payload = parse.urlencode({"redirect": "/", "password": source_password}).encode("utf-8")
        req = request.Request(f"{source_base_url}/password", data=payload, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            opener.open(req, timeout=30)
        except Exception:
            pass
    return opener


def extract_ssr_props(html_text: str) -> dict[str, Any]:
    m = re.search(r'<script id="ssr-props" type="application/json">(.*?)</script>', html_text, flags=re.S)
    if not m:
        raise ValueError("Unable to locate ssr-props JSON")
    return json.loads(m.group(1))


def rewrite_guide_links(html_body: str, version: str) -> str:
    html_body = re.sub(
        r'href="/docs/([^"#?]+)([^"]*)"',
        lambda m: f'href="/portal/guides/{version}/{m.group(1)}{m.group(2)}"',
        html_body,
    )
    html_body = re.sub(
        r"href='/docs/([^'#?]+)([^']*)'",
        lambda m: f"href='/portal/guides/{version}/{m.group(1)}{m.group(2)}'",
        html_body,
    )
    html_body = html_body.replace('href="/reference"', 'href="/portal"')
    return html_body
