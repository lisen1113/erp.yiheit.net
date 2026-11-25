"""Bounded category attribute definitions and validation shared by all entry points."""

import json
import math
import re
from typing import Any, Literal

Schema = dict[str, Any]
Compatibility = Literal["BACKWARD_COMPATIBLE", "REQUIRES_DATA_REVALIDATION", "BREAKING"]
KEY = re.compile(r"[a-z][a-z0-9_]{0,63}")
# One optional bounded repetition, no groups, lookarounds, alternation or backreferences.
PATTERN = re.compile(
    r"\^?(?:[a-zA-Z0-9_. -]{1,40}|\[[a-zA-Z0-9_ -]{1,40}\])(?:\{[0-9]{1,2}(?:,[0-9]{1,2})?\})?\$?"
)
TYPES = {"string", "integer", "number", "boolean", "array"}
METADATA = {"title", "description", "default"}
LIMITS = {
    "string": {"minLength", "maxLength", "pattern"},
    "integer": {"minimum", "maximum"},
    "number": {"minimum", "maximum"},
    "boolean": set(),
    "array": {"items"},
}


def matches(definition: Schema, value: Any) -> bool:
    kind = definition["type"]
    if kind == "string":
        valid = (
            isinstance(value, str)
            and definition.get("minLength", 0) <= len(value) <= definition.get("maxLength", 1024)
            and ("pattern" not in definition or re.search(definition["pattern"], value) is not None)
        )
    elif kind == "boolean":
        valid = type(value) is bool
    elif kind in {"integer", "number"}:
        valid = (
            type(value) is int
            or (kind == "number" and type(value) is float and math.isfinite(value))
        ) and definition.get("minimum", -(10**30)) <= value <= definition.get("maximum", 10**30)
    else:
        valid = (
            isinstance(value, list)
            and len(value) <= 64
            and all(matches(definition["items"], item) for item in value)
        )
    return bool(
        valid
        and (
            "enum" not in definition
            or any(type(value) is type(option) and value == option for option in definition["enum"])
        )
    )


def validate_property(definition: Any, *, array_item: bool = False) -> None:
    if (
        not isinstance(definition, dict)
        or not isinstance(definition.get("type"), str)
        or definition["type"] not in TYPES
    ):
        raise ValueError("属性必须声明受支持的数据类型")
    kind = definition["type"]
    if (array_item and kind == "array") or definition.keys() - (
        METADATA | LIMITS[kind] | {"type", "enum"}
    ):
        raise ValueError("属性使用了不支持的关键字或嵌套类型")
    for key in ("title", "description"):
        if key in definition and (
            not isinstance(definition[key], str) or len(definition[key]) > 500
        ):
            raise ValueError(f"{key} 必须是最多 500 字的文本")
    for key in ("minLength", "maxLength"):
        if key in definition and (
            type(definition[key]) is not int or not 0 <= definition[key] <= 1024
        ):
            raise ValueError(f"{key} 必须是 0 到 1024 的整数")
    for key in ("minimum", "maximum"):
        if key in definition and (
            type(definition[key]) not in {int, float} or not -(10**30) <= definition[key] <= 10**30
        ):
            raise ValueError(f"{key} 必须是受限的有限数字")
    if definition.get("minLength", 0) > definition.get("maxLength", 1024) or (
        definition.get("minimum", -(10**30)) > definition.get("maximum", 10**30)
    ):
        raise ValueError("最小值不能超过最大值")
    if "pattern" in definition:
        pattern = definition["pattern"]
        if not isinstance(pattern, str) or not PATTERN.fullmatch(pattern):
            raise ValueError("正则只支持简单字符或字符类及单个有限量词")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError("无效正则") from exc
    if kind == "array":
        validate_property(definition.get("items"), array_item=True)
    if "enum" in definition:
        options = definition["enum"]
        if not isinstance(options, list) or not 1 <= len(options) <= 32:
            raise ValueError("enum 必须包含 1 到 32 个选项")
        without_enum = {k: v for k, v in definition.items() if k != "enum"}
        if not all(matches(without_enum, value) for value in options):
            raise ValueError("enum 选项不符合属性约束")
    if "default" in definition and not matches(definition, definition["default"]):
        raise ValueError("default 不符合属性约束")


def validate_schema(schema: Schema) -> Schema:
    if schema == {}:
        return {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    if schema.get("type") != "object" or schema.keys() - {
        "type",
        "properties",
        "required",
        "title",
        "description",
        "additionalProperties",
    }:
        raise ValueError("根 Schema 必须是受控 object")
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    additional = schema.get("additionalProperties", False)
    if type(additional) is not bool or not isinstance(properties, dict) or len(properties) > 64:
        raise ValueError("最多支持 64 个属性，additionalProperties 必须是布尔值")
    if (
        not isinstance(required, list)
        or any(not isinstance(key, str) or key not in properties for key in required)
        or len(set(required)) != len(required)
    ):
        raise ValueError("required 必须是已定义且不重复的属性名")
    for name, definition in properties.items():
        if not KEY.fullmatch(name):
            raise ValueError("属性键必须是小写英文字母开头的技术标识")
        validate_property(definition)
    for key in ("title", "description"):
        if key in schema and (not isinstance(schema[key], str) or len(schema[key]) > 500):
            raise ValueError(f"{key} 必须是最多 500 字的文本")
    if len(json.dumps(schema, ensure_ascii=False, allow_nan=False).encode()) > 16384:
        raise ValueError("Schema 最大 16 KiB")
    return {
        **schema,
        "properties": properties,
        "required": required,
        "additionalProperties": additional,
    }


def compatibility(before: Schema, after: Schema) -> Compatibility:
    old, new = validate_schema(before), validate_schema(after)
    old_props, new_props = old["properties"], new["properties"]
    if not old_props.keys() <= new_props.keys() or any(
        definition["type"] != new_props[key]["type"] for key, definition in old_props.items()
    ):
        return "BREAKING"
    if set(new["required"]) - set(old["required"]) or (
        old["additionalProperties"] and not new["additionalProperties"]
    ):
        return "REQUIRES_DATA_REVALIDATION"
    if any(
        {k: v for k, v in definition.items() if k not in METADATA}
        != {k: v for k, v in new_props[key].items() if k not in METADATA}
        for key, definition in old_props.items()
    ) or (old["additionalProperties"] and old_props.keys() != new_props.keys()):
        return "REQUIRES_DATA_REVALIDATION"
    return "BACKWARD_COMPATIBLE"


def tighten(parent: Schema, child: Schema, key: str) -> Schema:
    for name, value in child.items():
        if name in METADATA or value == parent.get(name):
            continue
        if name in {"minimum", "minLength"} and value >= parent.get(name, -(10**30)):
            continue
        if name in {"maximum", "maxLength"} and value <= parent.get(name, 10**30):
            continue
        if name == "enum" and all(matches(parent, option) for option in value):
            continue
        if name == "pattern" and name not in parent:
            continue
        if name == "items" and name in parent:
            child = {**child, "items": tighten(parent["items"], value, key)}
            continue
        raise ValueError(f"子分类不能放宽或替换继承规则：{key}")
    return {**parent, **child}


def effective_schema(parent: Schema, local: Schema) -> Schema:
    base, child = validate_schema(parent), validate_schema(local)
    properties = dict(base["properties"])
    for key, rule in child["properties"].items():
        if key in properties:
            properties[key] = tighten(properties[key], rule, key)
        else:
            properties[key] = rule
    return validate_schema(
        {
            **child,
            "properties": properties,
            "required": sorted(set(base["required"]) | set(child["required"])),
            "additionalProperties": base["additionalProperties"] and child["additionalProperties"],
        }
    )


def validate_attributes(schema: Schema, values: dict[str, Any]) -> dict[str, str]:
    rules = validate_schema(schema)
    props = rules["properties"]
    errors = {key: "缺少必填属性" for key in rules["required"] if key not in values}
    for key, value in values.items():
        if key in props and not matches(props[key], value):
            errors[key] = "属性值不符合约束"
        elif key not in props and not rules["additionalProperties"]:
            errors[key] = "未定义的属性"
    return errors
