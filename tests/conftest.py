"""
Pytest configuration and shared fixtures.

Two test modes:
  - TestClient (in-process)  → uses an isolated tmp data dir, no Docker needed
  - Live Docker (localhost:8010)  → smoke-tests the real container
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient

# ── locate repo root ─────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_DATA  = REPO_ROOT / "data"

PASSWORD   = "Rajesh123"
AUTH_COOKIE = "bakkt_docs_auth"

# ── minimal OpenAPI spec used by multiple tests ───────────────────────────────
MINI_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Test", "version": "0.0.1"},
    "paths": {
        "/items": {
            "get": {
                "operationId": "listItems",
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "id":    {"type": "integer"},
                                        "name": {"type": "string", "format": ""},
                                        "active": {"type": "boolean"},
                                        "score": {"type": "number"},
                                        "tags": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                    },
                                }
                            }
                        },
                    }
                },
            }
        },
        "/items/{item_id}": {
            "get": {
                "operationId": "getItem",
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {
                            "application/json": {
                                "example": {"id": 42, "name": "widget"},
                            }
                        },
                    }
                },
            },
            "delete": {
                "operationId": "deleteItem",
                "responses": {"204": {"description": "deleted"}},
            },
        },
        "/echo": {
            "post": {
                "operationId": "echoItem",
                "responses": {
                    "201": {
                        "description": "created",
                        "content": {"application/json": {"example": {"created": True}}},
                    }
                },
            }
        },
    },
}

MINI_SPEC_BYTES = json.dumps(MINI_SPEC).encode()


@pytest.fixture(scope="session")
def tmp_data_dir() -> Generator[Path, None, None]:
    """
    An isolated data directory populated with the real seeded 1.0 specs/guides
    plus a synthetic 'test' section at version '0.0.1'.
    Cleaned up after the entire test session.
    """
    tmp = Path(tempfile.mkdtemp(prefix="bakkt_test_"))
    sections_dir = tmp / "sections"
    guides_dir   = tmp / "guides"

    # copy real seeded data so catalog / guide tests work
    if REAL_DATA.exists():
        shutil.copytree(REAL_DATA / "sections", sections_dir)
        shutil.copytree(REAL_DATA / "guides",   guides_dir)
    else:
        sections_dir.mkdir(parents=True)
        guides_dir.mkdir(parents=True)

    # inject synthetic test section
    test_spec_dir = sections_dir / "test" / "0.0.1"
    test_spec_dir.mkdir(parents=True, exist_ok=True)
    (test_spec_dir / "openapi.json").write_text(
        json.dumps(MINI_SPEC), encoding="utf-8"
    )

    yield tmp

    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(scope="session")
def client(tmp_data_dir: Path) -> Generator[TestClient, None, None]:
    """
    Patched TestClient: DATA_DIR points at the isolated tmp directory so no
    real seeding network calls are made.
    """
    import app.main as main_mod

    original_data    = main_mod.DATA_DIR
    original_sections = main_mod.SECTIONS_DIR
    original_guides  = main_mod.GUIDES_DIR

    main_mod.DATA_DIR     = tmp_data_dir
    main_mod.SECTIONS_DIR = tmp_data_dir / "sections"
    main_mod.GUIDES_DIR   = tmp_data_dir / "guides"

    # re-compile matchers against tmp data
    main_mod._compile_all()

    with TestClient(main_mod.app, raise_server_exceptions=True, follow_redirects=True) as c:
        yield c

    main_mod.DATA_DIR     = original_data
    main_mod.SECTIONS_DIR = original_sections
    main_mod.GUIDES_DIR   = original_guides
    main_mod._compile_all()


@pytest.fixture(scope="session")
def auth_client(client: TestClient) -> TestClient:
    """TestClient with the auth cookie pre-set."""
    client.cookies.set(AUTH_COOKIE, "1")
    return client
