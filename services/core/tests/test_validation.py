"""
Tests for Stage 5.5.2 — lightweight JSON Schema validation.
"""
from __future__ import annotations

import pytest

from app.tools.validation import (
    ValidationError as ArgsValidationError,
    validate_arguments,
    validate_result,
)


# ===========================================================================
# validate_arguments
# ===========================================================================

def test_valid_arguments_passes():
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"},
        },
        "required": ["name"],
    }
    validate_arguments({"name": "Adam", "age": 30}, schema)


def test_missing_required_field_raises():
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
        },
        "required": ["name", "age"],
    }
    with pytest.raises(ArgsValidationError, match="Missing required field 'arguments.age'"):
        validate_arguments({"name": "Adam"}, schema)


def test_wrong_type_raises():
    schema = {
        "type": "object",
        "properties": {
            "age": {"type": "integer"},
        },
    }
    with pytest.raises(ArgsValidationError, match="expected type 'integer', got 'string'"):
        validate_arguments({"age": "thirty"}, schema)


def test_bool_not_accepted_as_integer():
    schema = {
        "type": "object",
        "properties": {
            "age": {"type": "integer"},
        },
    }
    with pytest.raises(ArgsValidationError, match="expected type 'integer', got 'boolean'"):
        validate_arguments({"age": True}, schema)


def test_nested_object_validated():
    schema = {
        "type": "object",
        "properties": {
            "user": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                },
                "required": ["name"],
            },
        },
    }
    validate_arguments({"user": {"name": "Adam"}}, schema)


def test_nested_object_missing_field_raises():
    schema = {
        "type": "object",
        "properties": {
            "user": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                },
                "required": ["name"],
            },
        },
    }
    with pytest.raises(ArgsValidationError, match="Missing required field 'arguments.user.name'"):
        validate_arguments({"user": {}}, schema)


def test_array_with_items_validated():
    schema = {
        "type": "object",
        "properties": {
            "ids": {
                "type": "array",
                "items": {"type": "integer"},
            },
        },
    }
    validate_arguments({"ids": [1, 2, 3]}, schema)


def test_array_with_wrong_item_type_raises():
    schema = {
        "type": "object",
        "properties": {
            "ids": {
                "type": "array",
                "items": {"type": "integer"},
            },
        },
    }
    with pytest.raises(ArgsValidationError, match="expected type 'integer', got 'string'"):
        validate_arguments({"ids": [1, "two"]}, schema)


def test_unknown_field_with_additional_properties_false_raises():
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
        },
        "required": ["name"],
        "additionalProperties": False,
    }
    with pytest.raises(ArgsValidationError, match="Unknown field 'arguments.age' is not allowed"):
        validate_arguments({"name": "Adam", "age": 30}, schema)


def test_unknown_field_with_additional_properties_schema_raises():
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
        },
        "additionalProperties": {"type": "integer"},
    }
    with pytest.raises(ArgsValidationError, match="expected type 'integer', got 'string'"):
        validate_arguments({"name": "Adam", "age": "thirty"}, schema)


def test_empty_schema_accepts_any():
    validate_arguments({"anything": 1, "goes": True}, {})


def test_oversized_arguments_raises():
    huge = {"data": "x" * (64 * 1024 + 1)}
    with pytest.raises(ValueError, match="exceeds limit"):
        validate_arguments(huge, {})


def test_null_type_accepted():
    schema = {
        "type": "object",
        "properties": {
            "value": {"type": "null"},
        },
    }
    validate_arguments({"value": None}, schema)


def test_number_type_accepts_int_and_float():
    schema = {
        "type": "object",
        "properties": {
            "value": {"type": "number"},
        },
    }
    validate_arguments({"value": 3.14}, schema)
    validate_arguments({"value": 42}, schema)


# ===========================================================================
# validate_result
# ===========================================================================

def test_valid_result_passes():
    schema = {
        "type": "object",
        "properties": {
            "timestamp": {"type": "string"},
        },
        "required": ["timestamp"],
    }
    validate_result({"timestamp": "2024-01-01T00:00:00Z"}, schema)


def test_result_oversized_raises():
    huge = {"data": "x" * (256 * 1024 + 1)}
    with pytest.raises(ValueError, match="exceeds limit"):
        validate_result(huge, {})
