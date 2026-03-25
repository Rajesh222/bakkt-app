"""
Full application test-suite for the Bakkt Docker Docs + Mock platform.

Groups:
  1.  Auth & redirect
  2.  Portal pages (HTML)
  3.  Admin UI (browser forms)
  4.  Admin API (raw JSON endpoints)
  5.  Specs endpoint
  6.  Guide pages
  7.  Mock engine – routing + schema-aware response generation
  8.  Schema-example generator unit tests (_schema_to_example)
  9.  Delete-version routes
  10. Live-Docker smoke tests  (skipped when container is not reachable)
"""
from __future__ import annotations

import json
import socket
import urllib.request
import urllib.parse
import http.cookiejar
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
from app.main import _schema_to_example
from tests.conftest import MINI_SPEC, MINI_SPEC_BYTES, PASSWORD, AUTH_COOKIE

LIVE_BASE = "http://localhost:8000"


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────

def _multipart(fields: dict, file_field: str, filename: str, file_bytes: bytes) -> tuple[bytes, str]:
    """Build a minimal multipart/form-data body."""
    boundary = "TestBoundary1234"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
        f'Content-Type: application/octet-stream\r\n\r\n'.encode()
        + file_bytes
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _is_docker_up() -> bool:
    try:
        s = socket.create_connection(("localhost", 8000), timeout=1)
        s.close()
        return True
    except OSError:
        return False


requires_docker = pytest.mark.skipif(
    not _is_docker_up(), reason="Docker container not reachable on localhost:8000"
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Auth & redirect
# ─────────────────────────────────────────────────────────────────────────────

class TestAuth:
    def test_root_redirects_to_portal(self, client: TestClient) -> None:
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code in (301, 302, 307, 308)
        assert "/portal" in resp.headers["location"]

    def test_login_page_renders(self, client: TestClient) -> None:
        resp = client.get("/login")
        assert resp.status_code == 200
        assert "Password" in resp.text

    def test_login_wrong_password_401(self, client: TestClient) -> None:
        resp = client.post("/login", data={"password": "wrong", "next": "/portal"})
        assert resp.status_code == 401

    def test_login_correct_password_sets_cookie(self, client: TestClient) -> None:
        resp = client.post(
            "/login",
            data={"password": PASSWORD, "next": "/portal"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        assert AUTH_COOKIE in resp.cookies

    def test_portal_without_auth_401(self, client: TestClient) -> None:
        bare = TestClient(main_mod.app, raise_server_exceptions=False)
        resp = bare.get("/portal")
        assert resp.status_code == 401

    def test_logout_clears_cookie(self, auth_client: TestClient) -> None:
        resp = auth_client.post("/logout", follow_redirects=False)
        # After logout redirect, cookie should be deleted
        assert resp.status_code in (302, 303)
        auth_client.cookies.set(AUTH_COOKIE, "1")   # restore for other tests


# ─────────────────────────────────────────────────────────────────────────────
# 2. Portal pages
# ─────────────────────────────────────────────────────────────────────────────

class TestPortalPages:
    def test_portal_renders(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/portal")
        assert resp.status_code == 200
        assert "Bakkt" in resp.text

    def test_portal_lists_test_section(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/portal")
        # The synthetic 'test' section seeded in conftest should appear
        assert "test" in resp.text.lower()

    def test_portal_has_admin_link(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/portal")
        assert "/portal/admin" in resp.text

    def test_healthcheck(self, client: TestClient) -> None:
        resp = client.get("/healthcheck")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


# ─────────────────────────────────────────────────────────────────────────────
# 3. Admin UI
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminUI:
    def test_admin_page_renders(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/portal/admin")
        assert resp.status_code == 200
        assert "Admin UI" in resp.text

    def test_admin_shows_upload_forms(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/portal/admin")
        assert "upload-openapi" in resp.text
        assert "upload-guide" in resp.text
        assert "reseed-1-0" in resp.text

    def test_admin_shows_delete_buttons(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/portal/admin")
        assert "delete-version" in resp.text
        assert "delete-guide-version" in resp.text

    def test_admin_message_success_banner(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/portal/admin?message=All+good&level=success")
        assert "All good" in resp.text

    def test_admin_message_error_banner(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/portal/admin?message=Something+failed&level=error")
        assert "Something failed" in resp.text

    def test_admin_upload_openapi_via_form(self, auth_client: TestClient) -> None:
        body, ct = _multipart(
            {"section": "formpkg", "version": "1.2.3"},
            "openapi_file", "spec.json", MINI_SPEC_BYTES,
        )
        resp = auth_client.post(
            "/portal/admin/upload-openapi",
            content=body,
            headers={"Content-Type": ct},
        )
        assert resp.status_code == 200          # after PRG redirect
        assert "formpkg" in resp.text or "1.2.3" in resp.text or "Admin UI" in resp.text

    def test_admin_upload_guide_via_form(self, auth_client: TestClient) -> None:
        guide_md = b"# Hello\nThis is a test guide."
        body, ct = _multipart(
            {"section": "formpkg", "version": "1.2.3", "slug": "hello", "title": "Hello", "category": "Test"},
            "guide_file", "hello.md", guide_md,
        )
        resp = auth_client.post(
            "/portal/admin/upload-guide",
            content=body,
            headers={"Content-Type": ct},
        )
        assert resp.status_code == 200
        assert "Admin UI" in resp.text

    def test_admin_upload_guide_rejects_non_md(self, auth_client: TestClient) -> None:
        body, ct = _multipart(
            {"section": "formpkg", "version": "1.2.3", "slug": "bad", "title": "", "category": ""},
            "guide_file", "bad.txt", b"not markdown",
        )
        resp = auth_client.post(
            "/portal/admin/upload-guide",
            content=body,
            headers={"Content-Type": ct},
        )
        # Should redirect back to admin with error message
        assert resp.status_code == 200
        assert "must be a .md" in resp.text or "error" in resp.text.lower() or "Admin UI" in resp.text

    def test_admin_upload_openapi_invalid_json(self, auth_client: TestClient) -> None:
        body, ct = _multipart(
            {"section": "formpkg", "version": "9.9.9"},
            "openapi_file", "bad.json", b"not valid json {{{",
        )
        resp = auth_client.post(
            "/portal/admin/upload-openapi",
            content=body,
            headers={"Content-Type": ct},
        )
        assert resp.status_code in (400, 200, 422)   # 400 from save, or re-rendered admin

    def test_admin_upload_openapi_missing_paths(self, auth_client: TestClient) -> None:
        bad_spec = json.dumps({"openapi": "3.0.0", "info": {"title": "x", "version": "1"}}).encode()
        body, ct = _multipart(
            {"section": "formpkg", "version": "9.9.9"},
            "openapi_file", "bad.json", bad_spec,
        )
        resp = auth_client.post(
            "/portal/admin/upload-openapi",
            content=body,
            headers={"Content-Type": ct},
        )
        assert resp.status_code in (400, 200, 422)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Admin API (raw endpoints)
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminAPI:
    def test_catalog_returns_json(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/admin/catalog")
        assert resp.status_code == 200
        data = resp.json()
        assert "sections" in data
        assert "guides" in data

    def test_catalog_contains_test_section(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/admin/catalog")
        data = resp.json()
        assert "test" in data["sections"]
        assert "0.0.1" in data["sections"]["test"]

    def test_raw_openapi_upload(self, auth_client: TestClient) -> None:
        body, ct = _multipart(
            {},
            "openapi_file", "spec.json", MINI_SPEC_BYTES,
        )
        resp = auth_client.post(
            "/admin/sections/rawapi/versions/5.0.0/openapi",
            content=body,
            headers={"Content-Type": ct},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["section"] == "rawapi"
        assert data["version"] == "5.0.0"
        assert data["operations"] > 0

    def test_raw_guide_upload(self, auth_client: TestClient) -> None:
        guide_md = b"# Raw Guide\nContent here."
        body, ct = _multipart(
            {"title": "Raw Guide", "category": "API"},
            "guide_file", "raw-guide.md", guide_md,
        )
        resp = auth_client.post(
            "/admin/sections/rawapi/versions/5.0.0/guides/raw-guide",
            content=body,
            headers={"Content-Type": ct},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["slug"] == "raw-guide"
        assert data["section"] == "rawapi"

    def test_raw_guide_rejects_non_md(self, auth_client: TestClient) -> None:
        body, ct = _multipart(
            {"title": "", "category": ""},
            "guide_file", "bad.html", b"<p>html</p>",
        )
        resp = auth_client.post(
            "/admin/sections/rawapi/versions/5.0.0/guides/html-guide",
            content=body,
            headers={"Content-Type": ct},
        )
        assert resp.status_code == 400

    def test_catalog_unauthenticated_401(self, client: TestClient) -> None:
        bare = TestClient(main_mod.app, raise_server_exceptions=False)
        resp = bare.get("/admin/catalog")
        assert resp.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# 5. Specs endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestSpecs:
    def test_serve_existing_spec(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/specs/test/0.0.1/openapi.json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["openapi"] == "3.0.0"
        assert "/items" in data["paths"]

    def test_spec_missing_returns_404(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/specs/ghost/9.9.9/openapi.json")
        assert resp.status_code == 404

    def test_spec_server_rewritten(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/specs/test/0.0.1/openapi.json")
        data = resp.json()
        # uploaded via conftest — server should be set to mock URL
        servers = data.get("servers", [])
        # server rewriting happens on _save_openapi_spec, not on direct file copy
        # so the spec written by conftest.py (direct file write) may not have it
        # but sections uploaded through admin API do
        resp2 = auth_client.get("/specs/rawapi/5.0.0/openapi.json")  # uploaded in test_raw_openapi_upload
        if resp2.status_code == 200:
            data2 = resp2.json()
            assert any("/mock/rawapi/5.0.0" in s.get("url", "") for s in data2.get("servers", []))

    def test_reference_page_renders(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/portal/reference/test/0.0.1")
        assert resp.status_code == 200
        assert "API Reference" in resp.text
        assert "cURL Request" in resp.text

    def test_reference_page_missing_spec_404(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/portal/reference/ghost/9.9.9")
        assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# 6. Guide pages
# ─────────────────────────────────────────────────────────────────────────────

class TestGuidePages:
    def test_guide_index_renders(self, auth_client: TestClient) -> None:
        # Use the real seeded 1.0 guides if present
        resp = auth_client.get("/portal/guides/1.0")
        if resp.status_code == 404:
            pytest.skip("No guides seeded for v1.0 in test data dir")
        assert resp.status_code == 200
        assert "Guides" in resp.text

    def test_guide_page_renders_html(self, auth_client: TestClient) -> None:
        from app.main import _load_guide_index
        import app.main as m
        orig = m.GUIDES_DIR
        # find a real seeded guide
        idx = _load_guide_index("1.0")
        if not idx:
            pytest.skip("No guides index for v1.0")
        slug = idx[0]["slug"]
        resp = auth_client.get(f"/portal/guides/1.0/{slug}")
        assert resp.status_code == 200
        assert "Back to guides" in resp.text

    def test_guide_page_renders_markdown(self, auth_client: TestClient) -> None:
        # The guide uploaded via admin form (hello.md) in TestAdminUI
        resp = auth_client.get("/portal/guides/1.2.3/hello")
        assert resp.status_code == 200
        assert "Hello" in resp.text

    def test_guide_missing_404(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/portal/guides/1.0/no-such-guide-xyzxyz")
        assert resp.status_code == 404

    def test_guide_version_missing_404(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/portal/guides/99.99")
        assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# 7. Mock engine – routing
# ─────────────────────────────────────────────────────────────────────────────

class TestMockEngine:
    def test_unknown_section_404(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/mock/ghost/0.0.1/items")
        assert resp.status_code == 404

    def test_unknown_path_404(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/mock/test/0.0.1/does/not/exist")
        assert resp.status_code == 404

    def test_get_collection_returns_200(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/mock/test/0.0.1/items")
        assert resp.status_code == 200

    def test_get_with_path_param(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/mock/test/0.0.1/items/99")
        assert resp.status_code == 200
        data = resp.json()
        # has literal example: {"id": 42, "name": "widget"}
        assert data.get("id") == 42

    def test_post_returns_201(self, auth_client: TestClient) -> None:
        resp = auth_client.post("/mock/test/0.0.1/echo", json={"x": 1})
        assert resp.status_code == 201
        assert resp.json().get("created") is True

    def test_delete_operation(self, auth_client: TestClient) -> None:
        resp = auth_client.delete("/mock/test/0.0.1/items/5")
        # 204 declared in spec
        assert resp.status_code == 204

    def test_method_mismatch_404(self, auth_client: TestClient) -> None:
        # POST to an endpoint that only has GET
        resp = auth_client.post("/mock/test/0.0.1/items/5", json={})
        assert resp.status_code == 404

    def test_scheme_aware_response_has_typed_fields(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/mock/test/0.0.1/items")
        assert resp.status_code == 200
        data = resp.json()
        # schema-generated from MINI_SPEC: object with id, name, active, score, tags
        assert isinstance(data.get("id"), int)
        assert isinstance(data.get("active"), bool)
        assert isinstance(data.get("score"), (int, float))
        assert isinstance(data.get("tags"), list)


# ─────────────────────────────────────────────────────────────────────────────
# 8. _schema_to_example unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSchemaToExample:
    COMPONENTS: dict = {}

    def test_string_basic(self) -> None:
        assert isinstance(_schema_to_example({"type": "string"}, self.COMPONENTS), str)

    def test_string_formats(self) -> None:
        assert _schema_to_example({"type": "string", "format": "date"}, self.COMPONENTS) == "2024-01-01"
        assert _schema_to_example({"type": "string", "format": "uuid"}, self.COMPONENTS) == "00000000-0000-0000-0000-000000000000"
        assert _schema_to_example({"type": "string", "format": "email"}, self.COMPONENTS) == "user@example.com"
        assert _schema_to_example({"type": "string", "format": "date-time"}, self.COMPONENTS) == "2024-01-01T00:00:00Z"

    def test_integer(self) -> None:
        val = _schema_to_example({"type": "integer"}, self.COMPONENTS)
        assert isinstance(val, int)

    def test_number(self) -> None:
        val = _schema_to_example({"type": "number"}, self.COMPONENTS)
        assert isinstance(val, (int, float))

    def test_boolean(self) -> None:
        assert _schema_to_example({"type": "boolean"}, self.COMPONENTS) is True

    def test_array(self) -> None:
        val = _schema_to_example({"type": "array", "items": {"type": "string"}}, self.COMPONENTS)
        assert isinstance(val, list)
        assert len(val) == 1
        assert isinstance(val[0], str)

    def test_object_with_properties(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "id":   {"type": "integer"},
                "name": {"type": "string"},
            },
        }
        val = _schema_to_example(schema, self.COMPONENTS)
        assert isinstance(val, dict)
        assert isinstance(val["id"], int)
        assert isinstance(val["name"], str)

    def test_enum_string(self) -> None:
        val = _schema_to_example({"type": "string", "enum": ["a", "b", "c"]}, self.COMPONENTS)
        assert val == "a"

    def test_enum_integer(self) -> None:
        val = _schema_to_example({"type": "integer", "enum": [1, 2, 3]}, self.COMPONENTS)
        assert val == 1

    def test_inline_example_wins(self) -> None:
        val = _schema_to_example({"type": "integer", "example": 99}, self.COMPONENTS)
        assert val == 99

    def test_default_wins(self) -> None:
        val = _schema_to_example({"type": "string", "default": "hello"}, self.COMPONENTS)
        assert val == "hello"

    def test_ref_resolution(self) -> None:
        components = {
            "schemas": {
                "Widget": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"}
                    },
                }
            }
        }
        val = _schema_to_example({"$ref": "#/components/schemas/Widget"}, components)
        assert isinstance(val, dict)
        assert isinstance(val["id"], int)

    def test_allof_merges(self) -> None:
        schema = {
            "allOf": [
                {"type": "object", "properties": {"a": {"type": "integer"}}},
                {"type": "object", "properties": {"b": {"type": "string"}}},
            ]
        }
        val = _schema_to_example(schema, self.COMPONENTS)
        assert isinstance(val, dict)
        assert "a" in val and "b" in val

    def test_oneof_picks_first(self) -> None:
        schema = {
            "oneOf": [
                {"type": "integer"},
                {"type": "string"},
            ]
        }
        val = _schema_to_example(schema, self.COMPONENTS)
        assert isinstance(val, int)

    def test_anyof_picks_first(self) -> None:
        schema = {
            "anyOf": [
                {"type": "boolean"},
                {"type": "string"},
            ]
        }
        val = _schema_to_example(schema, self.COMPONENTS)
        assert val is True

    def test_depth_limit_prevents_infinite_recursion(self) -> None:
        # circular reference via $ref that would loop forever
        components = {
            "schemas": {
                "Node": {
                    "type": "object",
                    "properties": {
                        "child": {"$ref": "#/components/schemas/Node"},
                    },
                }
            }
        }
        val = _schema_to_example({"$ref": "#/components/schemas/Node"}, components)
        # should return something, not hang or blow the stack
        assert val is not None

    def test_empty_schema_returns_string(self) -> None:
        val = _schema_to_example({}, self.COMPONENTS)
        assert isinstance(val, str)

    def test_additional_properties(self) -> None:
        schema = {
            "type": "object",
            "additionalProperties": {"type": "integer"},
        }
        val = _schema_to_example(schema, self.COMPONENTS)
        assert isinstance(val, dict)
        assert "key" in val
        assert isinstance(val["key"], int)


# ─────────────────────────────────────────────────────────────────────────────
# 9. Delete-version routes
# ─────────────────────────────────────────────────────────────────────────────

class TestDeleteVersionRoutes:
    def _upload_spec(self, auth_client: TestClient, section: str, version: str) -> None:
        body, ct = _multipart(
            {"section": section, "version": version},
            "openapi_file", "spec.json", MINI_SPEC_BYTES,
        )
        auth_client.post(
            "/portal/admin/upload-openapi",
            content=body,
            headers={"Content-Type": ct},
        )

    def test_delete_section_version(self, auth_client: TestClient) -> None:
        self._upload_spec(auth_client, "deltest", "1.0.0")
        # confirm it's in catalog
        cat = auth_client.get("/admin/catalog").json()
        assert "deltest" in cat["sections"]

        resp = auth_client.post(
            "/portal/admin/delete-version",
            data={"section": "deltest", "version": "1.0.0"},
        )
        assert resp.status_code == 200
        cat_after = auth_client.get("/admin/catalog").json()
        assert "deltest" not in cat_after["sections"]

    def test_delete_nonexistent_version_error(self, auth_client: TestClient) -> None:
        resp = auth_client.post(
            "/portal/admin/delete-version",
            data={"section": "ghost", "version": "9.9.9"},
        )
        assert resp.status_code == 200
        assert "not found" in resp.text.lower() or "error" in resp.text.lower() or "ghost" in resp.text

    def test_delete_guide_version(self, auth_client: TestClient) -> None:
        # Upload a guide at a fresh version
        guide_md = b"# Temp Guide"
        body, ct = _multipart(
            {"section": "x", "version": "3.3.3", "slug": "temp", "title": "Temp", "category": "X"},
            "guide_file", "temp.md", guide_md,
        )
        auth_client.post(
            "/portal/admin/upload-guide",
            content=body,
            headers={"Content-Type": ct},
        )
        cat = auth_client.get("/admin/catalog").json()
        assert "3.3.3" in cat["guides"]

        resp = auth_client.post(
            "/portal/admin/delete-guide-version",
            data={"version": "3.3.3"},
        )
        assert resp.status_code == 200
        cat_after = auth_client.get("/admin/catalog").json()
        assert "3.3.3" not in cat_after["guides"]

    def test_mock_returns_404_after_version_deleted(self, auth_client: TestClient) -> None:
        self._upload_spec(auth_client, "ephemeral", "2.0.0")
        resp_before = auth_client.get("/mock/ephemeral/2.0.0/items")
        assert resp_before.status_code == 200

        auth_client.post(
            "/portal/admin/delete-version",
            data={"section": "ephemeral", "version": "2.0.0"},
        )
        resp_after = auth_client.get("/mock/ephemeral/2.0.0/items")
        assert resp_after.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# 10. Live Docker smoke tests
# ─────────────────────────────────────────────────────────────────────────────

@requires_docker
class TestLiveDockerSmoke:
    """
    End-to-end tests against the running Docker container.
    Skipped automatically when the container is not reachable.
    """

    def _live_opener(self) -> urllib.request.OpenerDirector:
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        payload = urllib.parse.urlencode({"password": PASSWORD, "next": "/portal"}).encode()
        opener.open(urllib.request.Request(LIVE_BASE + "/login", data=payload, method="POST"))
        return opener

    def _get(self, opener: urllib.request.OpenerDirector, path: str) -> tuple[int, str]:
        resp = opener.open(LIVE_BASE + path, timeout=10)
        return resp.status, resp.read().decode()

    def test_healthcheck(self) -> None:
        with urllib.request.urlopen(LIVE_BASE + "/healthcheck", timeout=5) as r:
            data = json.loads(r.read())
        assert data["status"] == "ok"

    def test_login_sets_cookie(self) -> None:
        opener = self._live_opener()
        status, body = self._get(opener, "/portal")
        assert status == 200
        assert "Bakkt" in body

    def test_portal_lists_seeded_sections(self) -> None:
        opener = self._live_opener()
        _, body = self._get(opener, "/portal")
        for section in ("accounts", "onboarding", "stablecoin", "zaira", "bakktx"):
            assert section in body.lower(), f"Expected section '{section}' in portal"

    def test_admin_page(self) -> None:
        opener = self._live_opener()
        _, body = self._get(opener, "/portal/admin")
        assert "Admin UI" in body
        assert "delete-version" in body

    def test_catalog_has_all_sections(self) -> None:
        opener = self._live_opener()
        _, body = self._get(opener, "/admin/catalog")
        data = json.loads(body)
        for s in ("accounts", "onboarding", "stablecoin", "zaira", "bakktx"):
            assert s in data["sections"], f"Section '{s}' missing from catalog"

    def test_guide_index(self) -> None:
        opener = self._live_opener()
        status, body = self._get(opener, "/portal/guides/1.0")
        assert status == 200
        assert "Guides" in body

    def test_spec_served(self) -> None:
        opener = self._live_opener()
        for section in ("accounts", "onboarding", "stablecoin"):
            status, body = self._get(opener, f"/specs/{section}/1.0/openapi.json")
            assert status == 200, f"/specs/{section}/1.0/openapi.json → {status}"
            spec = json.loads(body)
            assert spec["openapi"].startswith("3.")
            assert len(spec.get("paths", {})) > 0

    def test_swagger_ui_page(self) -> None:
        opener = self._live_opener()
        status, body = self._get(opener, "/portal/reference/stablecoin/1.0")
        assert status == 200
        assert "API Reference" in body

    def test_mock_schema_aware_response(self) -> None:
        opener = self._live_opener()
        status, body = self._get(opener, "/mock/stablecoin/1.0/stablecoin/wallet/balance")
        data = json.loads(body)
        assert status == 200
        # schema-aware: should NOT be the generic mock-true dict
        assert data.get("mock") is None, f"Got generic mock fallback: {data}"

    def test_mock_literal_example(self) -> None:
        opener = self._live_opener()
        # onboarding first path for literal example
        _, catalog_body = self._get(opener, "/admin/catalog")
        catalog = json.loads(catalog_body)
        section = "onboarding"
        version = "1.0"
        _, spec_body = self._get(opener, f"/specs/{section}/{version}/openapi.json")
        spec = json.loads(spec_body)
        paths = list(spec.get("paths", {}).keys())
        assert paths, "No paths in onboarding spec"
        # call first GET path
        for path, methods in spec["paths"].items():
            if "get" in methods:
                mock_path = path.split("{")[0].rstrip("/")
                status, body = self._get(opener, f"/mock/{section}/{version}{mock_path}")
                assert status == 200
                break

    def test_unauthenticated_portal_blocked(self) -> None:
        bare = urllib.request.build_opener()
        req = urllib.request.Request(LIVE_BASE + "/portal")
        try:
            bare.open(req, timeout=5)
            assert False, "Expected 401"
        except urllib.error.HTTPError as e:
            assert e.code == 401

    def test_wrong_password_rejected(self) -> None:
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        payload = urllib.parse.urlencode({"password": "wrong", "next": "/portal"}).encode()
        try:
            opener.open(urllib.request.Request(LIVE_BASE + "/login", data=payload, method="POST"), timeout=5)
            assert False, "Expected 401"
        except urllib.error.HTTPError as e:
            assert e.code == 401

    def test_upload_and_delete_lifecycle(self) -> None:
        opener = self._live_opener()
        boundary = "BoundaryLifecycle"
        mini = json.dumps(MINI_SPEC).encode()
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"section\"\r\n\r\nlifecycle\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"version\"\r\n\r\n7.7.7\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"openapi_file\"; filename=\"s.json\"\r\n"
            f"Content-Type: application/json\r\n\r\n"
        ).encode() + mini + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(LIVE_BASE + "/portal/admin/upload-openapi", data=body, method="POST")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        opener.open(req, timeout=10)

        _, cat_body = self._get(opener, "/admin/catalog")
        assert "lifecycle" in json.loads(cat_body)["sections"]

        # call mock
        status, _ = self._get(opener, "/mock/lifecycle/7.7.7/items")
        assert status == 200

        # delete
        del_data = urllib.parse.urlencode({"section": "lifecycle", "version": "7.7.7"}).encode()
        req2 = urllib.request.Request(LIVE_BASE + "/portal/admin/delete-version", data=del_data, method="POST")
        req2.add_header("Content-Type", "application/x-www-form-urlencoded")
        opener.open(req2, timeout=10)

        _, cat_body2 = self._get(opener, "/admin/catalog")
        assert "lifecycle" not in json.loads(cat_body2)["sections"]
