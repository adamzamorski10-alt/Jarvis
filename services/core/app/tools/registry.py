"""
Thread-safe in-memory Tool Registry for registering, inspecting,
enabling, disabling, and retrieving ToolDefinitions.
"""
import threading
from typing import Sequence

from app.tools.exceptions import (
    InvalidToolDefinitionError,
    ToolAlreadyRegisteredError,
    ToolDisabledInDefinitionError,
    ToolNotFoundError,
)
from app.tools.models import ToolCategory, ToolDefinition


class ToolRegistry:
    """
    Central in-memory registry for ToolDefinitions.
    - Preserves immutability of ToolDefinition objects.
    - Manages isolated runtime enabled/disabled state without mutating ToolDefinition objects.
    - Thread-safe operations using threading.RLock.
    """
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tools: dict[str, ToolDefinition] = {}
        self._runtime_enabled: dict[str, bool] = {}

    def register(self, tool_definition: ToolDefinition) -> ToolDefinition:
        """
        Registers a new ToolDefinition.
        - Accepts ONLY valid ToolDefinition instances.
        - Rejects duplicates with ToolAlreadyRegisteredError.
        - Preserves immutability of tool_definition.
        - Runtime enabled state defaults to tool_definition.enabled.
        """
        if not isinstance(tool_definition, ToolDefinition):
            raise InvalidToolDefinitionError(
                f"Expected a ToolDefinition instance, got {type(tool_definition).__name__}."
            )

        tool_id = tool_definition.tool_id

        with self._lock:
            if tool_id in self._tools:
                raise ToolAlreadyRegisteredError(tool_id)

            self._tools[tool_id] = tool_definition
            self._runtime_enabled[tool_id] = tool_definition.enabled
            return tool_definition

    def unregister(self, tool_id: str) -> None:
        """
        Unregisters a tool by tool_id.
        - Removes the stored ToolDefinition and runtime enabled state.
        - Raises ToolNotFoundError if tool_id is not registered.
        """
        tool_id_clean = tool_id.strip() if isinstance(tool_id, str) else ""
        with self._lock:
            if tool_id_clean not in self._tools:
                raise ToolNotFoundError(tool_id_clean)

            del self._tools[tool_id_clean]
            self._runtime_enabled.pop(tool_id_clean, None)

    def get(self, tool_id: str) -> ToolDefinition | None:
        """
        Returns the registered ToolDefinition for tool_id, or None if not found.
        """
        if not isinstance(tool_id, str):
            return None
        with self._lock:
            return self._tools.get(tool_id.strip())

    def require(self, tool_id: str) -> ToolDefinition:
        """
        Returns the registered ToolDefinition for tool_id, or raises ToolNotFoundError.
        """
        tool = self.get(tool_id)
        if tool is None:
            tool_id_clean = tool_id.strip() if isinstance(tool_id, str) else str(tool_id)
            raise ToolNotFoundError(tool_id_clean)
        return tool

    def exists(self, tool_id: str) -> bool:
        """
        Returns True if tool_id is registered.
        """
        return self.get(tool_id) is not None

    def is_enabled(self, tool_id: str) -> bool:
        """
        Returns True if:
        1) Tool exists in registry, AND
        2) Definition has enabled == True, AND
        3) Runtime enabled state is True.
        Returns False if tool does not exist or if either state is False.
        """
        with self._lock:
            tool = self.get(tool_id)
            if tool is None:
                return False
            if not tool.enabled:
                return False
            return self._runtime_enabled.get(tool.tool_id, False)

    def enable(self, tool_id: str) -> bool:
        """
        Enables runtime state for tool_id.
        - Raises ToolNotFoundError if tool_id is not registered.
        - Raises ToolDisabledInDefinitionError if static definition.enabled is False.
        - Returns True on success.
        """
        with self._lock:
            tool = self.require(tool_id)
            if not tool.enabled:
                raise ToolDisabledInDefinitionError(tool.tool_id)

            self._runtime_enabled[tool.tool_id] = True
            return True

    def disable(self, tool_id: str) -> bool:
        """
        Disables runtime state for tool_id.
        - Raises ToolNotFoundError if tool_id is not registered.
        - Returns True on success.
        """
        with self._lock:
            tool = self.require(tool_id)
            self._runtime_enabled[tool.tool_id] = False
            return True

    def list_tools(
        self,
        category: ToolCategory | None = None,
        enabled_only: bool = False,
    ) -> tuple[ToolDefinition, ...]:
        """
        Returns registered ToolDefinitions in deterministic ascending order of tool_id.
        - Optionally filters by category.
        - Optionally filters to enabled_only tools.
        - Returns an immutable tuple to prevent caller mutation of internal collection.
        """
        with self._lock:
            sorted_keys = sorted(self._tools.keys())
            result: list[ToolDefinition] = []
            for k in sorted_keys:
                tool = self._tools[k]
                if category is not None and tool.category != category:
                    continue
                if enabled_only and not self.is_enabled(k):
                    continue
                result.append(tool)
            return tuple(result)

    def clear(self) -> None:
        """Clears all registered tools and runtime states."""
        with self._lock:
            self._tools.clear()
            self._runtime_enabled.clear()


# Module-level singleton
tool_registry = ToolRegistry()
