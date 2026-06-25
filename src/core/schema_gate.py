"""Structural schema-conformance gate for structured-output responses.

When a turn requests structured output (a JSON schema) and a *weak* provider returns
valid-JSON-but-WRONG-SHAPE (a missing required field, or an array-of-objects that came
back as scalars), the parse succeeds but the caller's downstream model/pydantic rejects
it — silently failing the task. This gate catches that shape mismatch so the caller can
treat it as a provider failure and fail over to a provider that gets the shape right,
instead of passing garbage downstream.

NOT a full JSON-Schema validator (no $ref resolution / enums / deep nesting) — a
structural gate, **fail-OPEN by construction**: anything it doesn't understand passes,
so it can never reject a valid response. Provider-agnostic (ported from the live gateway).

Typical use in a failover loop:
    text = await provider.invoke(...)          # asked for response_format=json_schema
    try:
        obj = json.loads(text)
        if not schema_conforms(obj, requested_schema):
            raise ValueError("schema_shape")   # → classified/handled as a failover trigger
    except Exception:
        ...advance to next provider...
"""
from __future__ import annotations

_JSON_PY = {
    "object": dict, "array": list, "string": str,
    "integer": int, "number": (int, float), "boolean": bool, "null": type(None),
}


def schema_of(response_format) -> dict | None:
    """Extract the JSON schema dict from an OpenAI-style response_format, or None."""
    if not response_format or response_format.get("type") != "json_schema":
        return None
    js = response_format.get("json_schema", {})
    return js.get("schema", js)


def _is_object_items(items) -> bool:
    return isinstance(items, dict) and (
        items.get("type") == "object" or "properties" in items or "$ref" in items
    )


def _value_ok(val, prop) -> bool:
    if not isinstance(prop, dict):
        return True
    t = prop.get("type")
    if isinstance(t, list):                          # union type — ok if any member fits
        return any(_value_ok(val, {**prop, "type": tt}) for tt in t)
    if t is None:
        return True
    if t == "integer" and isinstance(val, bool):     # bool is not an integer here
        return False
    py = _JSON_PY.get(t)
    if py is not None and not isinstance(val, py):
        return False
    if t == "array" and _is_object_items(prop.get("items", {})):
        return all(isinstance(e, dict) for e in val)  # array-of-objects: reject scalars
    return True


def schema_conforms(obj, schema) -> bool:
    """True if `obj` structurally conforms to `schema` (required fields present, basic
    type match, array-of-objects-not-scalars). Fail-open on anything unrecognized."""
    if not isinstance(schema, dict):
        return True
    if schema.get("type") == "array":
        if not isinstance(obj, list):
            return False
        return all(isinstance(e, dict) for e in obj) if _is_object_items(schema.get("items", {})) else True
    if schema.get("type") == "object" or "properties" in schema or "required" in schema:
        if not isinstance(obj, dict):
            return False
        for req in schema.get("required", []):
            if req not in obj:
                return False
        for k, pv in schema.get("properties", {}).items():
            if k in obj and not _value_ok(obj[k], pv):
                return False
    return True
