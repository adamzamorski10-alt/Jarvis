"""
Lightweight JSON Schema validation for tool arguments and results.

No external dependencies — supports a safe subset of JSON Schema needed
for Stage 5.5.2 Tool Execution Security.
"""
from __future__ import annotations

import json
from typing import Any


class ValidationError(Exception):
    """Raised when arguments or result do not match the expected schema."""


def _size_bytes(data: Any) -> int:
    return len(json.dumps(data, default=str, ensure_ascii=False).encode("utf-8"))


def validate_arguments(
    arguments: dict[str, Any],
    schema: dict[str, Any],
    *,
    max_size_bytes: int = 64 * 1024,
) -> None:
    """
    Validate tool arguments against a JSON Schema subset.

    Supported keywords:
    - type: object | string | integer | number | boolean | array | null
    - properties: dict[str, schema]
    - required: list[str]
    - additionalProperties: bool | schema
    - items: schema (for array type)
    - maxLength: integer (for string type)
    - maximum: integer (for integer/number type)

    Raises:
        ValidationError: if arguments do not conform to schema.
        ValueError: if arguments exceed max_size_bytes.
    """
    if not isinstance(arguments, dict):
        raise ValidationError("Arguments must be a JSON object (dict).")

    size = _size_bytes(arguments)
    if size > max_size_bytes:
        raise ValueError(
            f"Arguments size {size} bytes exceeds limit {max_size_bytes} bytes."
        )

    _validate_value(arguments, schema, path="arguments")


def validate_result(
    result: Any,
    schema: dict[str, Any],
    *,
    max_size_bytes: int = 256 * 1024,
) -> None:
    """
    Validate tool execution result against a JSON Schema subset.

    Same supported keywords as validate_arguments.

    Raises:
        ValidationError: if result does not conform to schema.
        ValueError: if result exceeds max_size_bytes.
    """
    size = _size_bytes(result)
    if size > max_size_bytes:
        raise ValueError(
            f"Result size {size} bytes exceeds limit {max_size_bytes} bytes."
        )

    _validate_value(result, schema, path="result")


def _validate_value(value: Any, schema: dict[str, Any], path: str) -> None:
    if not schema or not isinstance(schema, dict):
        return

    expected_type = schema.get("type")
    if expected_type is None:
        return

    if not _check_type(value, expected_type):
        raise ValidationError(
            f"Field '{path}' expected type '{expected_type}', got '{_type_name(value)}'."
        )

    if expected_type == "object" and isinstance(value, dict):
        properties = schema.get("properties", {})
        required_fields = schema.get("required", [])
        additional_properties = schema.get("additionalProperties", True)

        for field in required_fields:
            if field not in value:
                raise ValidationError(
                    f"Missing required field '{path}.{field}'."
                )

        for key, val in value.items():
            if key in properties:
                _validate_value(val, properties[key], path=f"{path}.{key}")
            elif additional_properties is False:
                raise ValidationError(
                    f"Unknown field '{path}.{key}' is not allowed."
                )
            elif isinstance(additional_properties, dict):
                _validate_value(val, additional_properties, path=f"{path}.{key}")

    if expected_type == "integer" and isinstance(value, int) and not isinstance(value, bool):
        maximum = schema.get("maximum")
        minimum = schema.get("minimum")
        if maximum is not None and value > maximum:
            raise ValidationError(
                f"Field '{path}' exceeds maximum value of {maximum}."
            )
        if minimum is not None and value < minimum:
            raise ValidationError(
                f"Field '{path}' is below minimum value of {minimum}."
            )

    if expected_type == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
        maximum = schema.get("maximum")
        minimum = schema.get("minimum")
        if maximum is not None and value > maximum:
            raise ValidationError(
                f"Field '{path}' exceeds maximum value of {maximum}."
            )
        if minimum is not None and value < minimum:
            raise ValidationError(
                f"Field '{path}' is below minimum value of {minimum}."
            )

    if expected_type == "string" and isinstance(value, str):
        max_length = schema.get("maxLength")
        if max_length is not None and len(value) > max_length:
            raise ValidationError(
                f"Field '{path}' exceeds maximum length of {max_length}."
            )

    if expected_type == "array" and isinstance(value, (list, tuple)):
        items_schema = schema.get("items")
        if items_schema:
            for idx, item in enumerate(value):
                _validate_value(item, items_schema, path=f"{path}[{idx}]")


def _check_type(value: Any, expected_type: str) -> bool:
    if expected_type == "null":
        return value is None
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        # bool is subclass of int in Python, but JSON integer != boolean
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "array":
        return isinstance(value, (list, tuple))
    if expected_type == "object":
        return isinstance(value, dict)
    return True


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__
