"""
Domain exceptions for Tool Registry operations.
"""

class ToolRegistryError(ValueError):
    """Base exception for all Tool Registry domain errors."""
    def __init__(self, message: str, code: str = "tool_registry_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class ToolAlreadyRegisteredError(ToolRegistryError):
    """Raised when attempting to register a tool_id that already exists in the registry."""
    def __init__(self, tool_id: str) -> None:
        super().__init__(
            f"Tool '{tool_id}' is already registered in the registry.",
            code="tool_already_registered",
        )
        self.tool_id = tool_id


class ToolNotFoundError(ToolRegistryError):
    """Raised when looking up or requiring a tool_id that does not exist in the registry."""
    def __init__(self, tool_id: str) -> None:
        super().__init__(
            f"Tool '{tool_id}' was not found in the registry.",
            code="tool_not_found",
        )
        self.tool_id = tool_id


class ToolDisabledError(ToolRegistryError):
    """Base error for disabled tool operations."""
    def __init__(self, tool_id: str, reason: str = "Tool is disabled.") -> None:
        super().__init__(
            f"Tool '{tool_id}' is disabled: {reason}",
            code="tool_disabled",
        )
        self.tool_id = tool_id
        self.reason = reason


class ToolDisabledInDefinitionError(ToolDisabledError):
    """Raised when trying to enable a tool whose static definition sets enabled=False."""
    def __init__(self, tool_id: str) -> None:
        super().__init__(
            tool_id,
            reason="Tool definition has static enabled=False constraint.",
        )
        self.code = "tool_disabled_in_definition"


class InvalidToolDefinitionError(ToolRegistryError):
    """Raised when an object passed to register() is not a valid ToolDefinition instance."""
    def __init__(self, details: str) -> None:
        super().__init__(
            f"Invalid ToolDefinition object: {details}",
            code="invalid_tool_definition",
        )
