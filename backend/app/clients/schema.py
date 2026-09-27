# Checks a model's JSON against the schema we asked for. Covers the parts of JSON Schema the prompts use:
# type (or a list of types), properties, required, items, maxItems, enum, minimum, maximum.

from typing import Any

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: (isinstance(v, int) and not isinstance(v, bool)) or (isinstance(v, float) and v.is_integer()),
}


def problems(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_TYPES.get(x, lambda v: True)(value) for x in types):
            return [f"{path}: expected {' or '.join(types)}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{path}: not one of the allowed values"]
    out: list[str] = []
    if isinstance(value, dict):
        out += [f"{path}.{k}: missing" for k in schema.get("required", []) if k not in value]
        for k, sub in schema.get("properties", {}).items():
            if k in value:
                out += problems(value[k], sub, f"{path}.{k}")
    elif isinstance(value, list):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            out.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, v in enumerate(value):
                out += problems(v, schema["items"], f"{path}[{i}]")
    elif _TYPES["number"](value):
        if "minimum" in schema and value < schema["minimum"]:
            out.append(f"{path}: below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            out.append(f"{path}: above {schema['maximum']}")
    return out


def unexpected(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    # Keys the schema doesn't declare, anywhere in the value: a model reply that goes off-schema is rejected, even
    # where the provider's schema support would have let extra keys through.
    out: list[str] = []
    if isinstance(value, dict) and "properties" in schema:
        props = schema["properties"]
        for k, v in value.items():
            if k not in props:
                out.append(f"{path}.{k}")
            else:
                out += unexpected(v, props[k], f"{path}.{k}")
    elif isinstance(value, list) and "items" in schema:
        for i, v in enumerate(value):
            out += unexpected(v, schema["items"], f"{path}[{i}]")
    return out


# Keywords the Claude and OpenAI structured-output modes reject or ignore. They're left out of what those providers
# are sent; problems() still checks them on the reply.
_CONSTRAINTS = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minLength", "maxLength",
                "pattern", "format", "minItems", "maxItems", "uniqueItems"}


def closed(schema: dict[str, Any], all_required: bool = False) -> dict[str, Any]:
    # The schema as strict structured output needs it: every object closed (additionalProperties: false), a list of
    # types as anyOf, no constraint keywords. all_required (OpenAI strict mode): every property is required, and one
    # that wasn't may be null instead; without_added_nulls() takes those nulls out of the reply again.
    s = {k: v for k, v in schema.items() if k not in _CONSTRAINTS}
    t = s.get("type")
    if isinstance(t, list):
        common = {k: v for k, v in s.items() if k in ("description", "enum")}
        by_type = {"object": ("properties", "required"), "array": ("items",)}
        return {"anyOf": [{"type": "null"} if x == "null" else
                          closed({**common, "type": x, **{k: s[k] for k in by_type.get(x, ()) if k in s}}, all_required)
                          for x in t]}
    if "properties" in s:
        props = {k: closed(v, all_required) for k, v in s["properties"].items()}
        required = list(s.get("required", []))
        if all_required:
            props = {k: v if k in required else _or_null(v) for k, v in props.items()}
            required = list(props)
        s = {**s, "properties": props, "required": required, "additionalProperties": False}
    if "items" in s:
        s["items"] = closed(s["items"], all_required)
    return s


def _or_null(s: dict[str, Any]) -> dict[str, Any]:
    if s.get("type") == "null" or {"type": "null"} in s.get("anyOf", []):
        return s
    if "anyOf" in s:
        return {**s, "anyOf": [*s["anyOf"], {"type": "null"}]}
    return {"anyOf": [s, {"type": "null"}]}


def _allows_null(s: dict[str, Any]) -> bool:
    t = s.get("type")
    return t == "null" or (isinstance(t, list) and "null" in t)


def without_added_nulls(value: Any, schema: dict[str, Any]) -> Any:
    # Undoes closed(all_required=True): a property the original schema made optional, and that can't be null there,
    # is dropped when the reply set it to null.
    if isinstance(value, dict) and "properties" in schema:
        props, required = schema["properties"], set(schema.get("required", []))
        return {k: without_added_nulls(v, props[k]) if k in props else v for k, v in value.items()
                if not (v is None and k in props and k not in required and not _allows_null(props[k]))}
    if isinstance(value, list) and "items" in schema:
        return [without_added_nulls(v, schema["items"]) for v in value]
    return value
