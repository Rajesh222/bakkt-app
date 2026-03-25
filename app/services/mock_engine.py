from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class OperationMatcher:
    section: str
    version: str
    method: str
    path_template: str
    regex: re.Pattern[str]
    operation_id: str
    responses: dict[str, Any]
    components: dict[str, Any]


def compile_path(path_template: str) -> re.Pattern[str]:
    escaped = re.escape(path_template)
    escaped = re.sub(r"\\\{([a-zA-Z0-9_]+)\\\}", r"(?P<\1>[^/]+)", escaped)
    return re.compile(f"^{escaped}$")


def schema_to_example(schema: dict[str, Any], components: dict[str, Any], _depth: int = 0) -> Any:
    """Recursively generate a sample value from an OpenAPI schema node."""
    if _depth > 8 or not isinstance(schema, dict):
        return None

    ref = schema.get("$ref")
    if ref:
        parts = ref.lstrip("#/").split("/")
        resolved: Any = {"components": components}
        for part in parts:
            if isinstance(resolved, dict):
                resolved = resolved.get(part, {})
        return schema_to_example(resolved, components, _depth + 1)

    if "example" in schema:
        return schema["example"]
    if "default" in schema:
        return schema["default"]

    for combo_key in ("allOf", "oneOf", "anyOf"):
        combo = schema.get(combo_key)
        if isinstance(combo, list) and combo:
            if combo_key == "allOf":
                merged: dict[str, Any] = {}
                for sub in combo:
                    val = schema_to_example(sub, components, _depth + 1)
                    if isinstance(val, dict):
                        merged.update(val)
                return merged or None
            return schema_to_example(combo[0], components, _depth + 1)

    schema_type = schema.get("type")
    fmt = schema.get("format", "")

    if schema_type == "object" or "properties" in schema:
        props = schema.get("properties", {})
        result: dict[str, Any] = {key: schema_to_example(v, components, _depth + 1) for key, v in props.items()}
        if not props:
            add = schema.get("additionalProperties")
            if isinstance(add, dict):
                result["key"] = schema_to_example(add, components, _depth + 1)
        return result

    if schema_type == "array":
        items_schema = schema.get("items", {})
        item_val = schema_to_example(items_schema, components, _depth + 1)
        return [item_val] if item_val is not None else []

    if schema_type in ("integer", "number"):
        if "enum" in schema:
            return schema["enum"][0]
        return schema.get("minimum", 0 if schema_type == "integer" else 0.0)

    if schema_type == "boolean":
        return True

    if "enum" in schema:
        return schema["enum"][0]

    format_defaults: dict[str, str] = {
        "date": "2024-01-01",
        "date-time": "2024-01-01T00:00:00Z",
        "time": "00:00:00",
        "email": "user@example.com",
        "uri": "https://example.com",
        "uuid": "00000000-0000-0000-0000-000000000000",
        "hostname": "example.com",
        "ipv4": "127.0.0.1",
        "ipv6": "::1",
        "byte": "dGVzdA==",
        "binary": "data",
        "password": "********",
    }
    if fmt in format_defaults:
        return format_defaults[fmt]

    title = schema.get("title", "")
    return title.lower().replace(" ", "-") if title else "string"


def pick_example(responses: dict[str, Any], components: dict[str, Any]) -> tuple[int, Any | None]:
    status_code = 200
    payload = None

    for code, response in responses.items():
        if isinstance(code, str) and code.startswith("2"):
            try:
                status_code = int(code)
            except ValueError:
                status_code = 200
            if isinstance(response, dict):
                content = response.get("content", {})
                app_json = content.get("application/json", {}) if isinstance(content, dict) else {}
                if isinstance(app_json, dict):
                    if "example" in app_json:
                        payload = app_json.get("example")
                    elif "examples" in app_json and isinstance(app_json["examples"], dict):
                        first = next(iter(app_json["examples"].values()), {})
                        if isinstance(first, dict):
                            payload = first.get("value")
                    elif "schema" in app_json:
                        payload = schema_to_example(app_json["schema"], components)
            break

    return status_code, payload
