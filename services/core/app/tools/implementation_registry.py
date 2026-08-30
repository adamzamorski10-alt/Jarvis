"""
Thread-safe in-memory registry for tool implementations.

Maps tool_id -> BaseTool instance.
Separate from ToolRegistry, which stores ToolDefinitions only.
"""
import threading
from typing import Any

from app.tools.base import BaseTool


class ToolImplementationRegistry:
    """
    Central registry for BaseTool implementations.

    - Thread-safe registration and lookup.
    - Rejects duplicate registrations.
    - Does not perform dynamic imports.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._impls: dict[str, BaseTool] = {}

    def register(self, impl: BaseTool) -> None:
        """
        Register a tool implementation.

        Raises:
            TypeError: if impl is not a BaseTool instance.
            ValueError: if a tool with the same tool_id is already registered.
        """
        if not isinstance(impl, BaseTool):
            raise TypeError(f"Expected BaseTool instance, got {type(impl).__name__}.")

        tool_id = impl.tool_id

        with self._lock:
            if tool_id in self._impls:
                raise ValueError(f"Tool implementation '{tool_id}' is already registered.")

            self._impls[tool_id] = impl

    def get(self, tool_id: str) -> BaseTool | None:
        """Return the registered implementation for tool_id, or None."""
        if not isinstance(tool_id, str):
            return None
        with self._lock:
            return self._impls.get(tool_id.strip())

    def get_implementation(self, tool_id: str) -> BaseTool:
        """
        Return the registered implementation for tool_id.

        Raises:
            KeyError: if no implementation is registered for tool_id.
        """
        impl = self.get(tool_id)
        if impl is None:
            raise KeyError(f"No implementation registered for tool '{tool_id}'.")
        return impl

    def clear(self) -> None:
        """Remove all registered implementations."""
        with self._lock:
            self._impls.clear()


# Module-level singleton
tool_implementation_registry = ToolImplementationRegistry()
