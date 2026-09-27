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
