"""
Unit tests for Stage 5.2 — Tool Registry Implementation.
Tests thread-safe registration, lookup, duplicate protection, enable/disable runtime state,
isolation, immutability, unregistration, and exception handling.
"""
import concurrent.futures
import pytest
from pydantic import ValidationError

from app.tools.exceptions import (
    InvalidToolDefinitionError,
    ToolAlreadyRegisteredError,
    ToolDisabledInDefinitionError,
    ToolNotFoundError,
)
from app.tools.models import RiskLevel, ToolCategory, ToolDefinition
from app.tools.registry import ToolRegistry


def _create_sample_tool(
    tool_id: str = "files.read_file",
    name: str = "Read File",
    category: ToolCategory = ToolCategory.FILES,
    risk_level: RiskLevel = RiskLevel.LOW,
    enabled: bool = True,
    action: str | None = None,
) -> ToolDefinition:
    derived_action = action or (tool_id.split('.')[-1] if '.' in tool_id else tool_id)
    return ToolDefinition(
        tool_id=tool_id,
        action=derived_action,
        name=name,
        description=f"Sample tool {tool_id}",
        category=category,
        risk_level=risk_level,
        enabled=enabled,
    )


# --- 1. REGISTRATION & LOOKUP TESTS ---

def test_register_valid_tool():
    """Test registering a valid ToolDefinition and looking it up."""
    reg = ToolRegistry()
    tool = _create_sample_tool("files.read_file")

    registered = reg.register(tool)
    assert registered.tool_id == "files.read_file"
    assert reg.exists("files.read_file") is True
    assert reg.get("files.read_file") == tool
    assert reg.require("files.read_file") == tool


def test_register_invalid_object():
    """Test that register() rejects non-ToolDefinition objects."""
    reg = ToolRegistry()
    with pytest.raises(InvalidToolDefinitionError):
        reg.register({"tool_id": "files.read_file", "name": "Fake Tool"})

    with pytest.raises(InvalidToolDefinitionError):
        reg.register("files.read_file")


def test_exists():
    """Test exists() method returns True for registered tool, False otherwise."""
    reg = ToolRegistry()
    assert reg.exists("system.get_status") is False

    reg.register(_create_sample_tool("system.get_status"))
    assert reg.exists("system.get_status") is True
    assert reg.exists("system.other_tool") is False


def test_list_tools_filtering():
    """Test list_tools filtering by category and enabled_only."""
    reg = ToolRegistry()
    t1 = _create_sample_tool("files.read", category=ToolCategory.FILES)
    t2 = _create_sample_tool("files.write", category=ToolCategory.FILES, enabled=False)
    t3 = _create_sample_tool("system.info", category=ToolCategory.SYSTEM)

    reg.register(t1)
    reg.register(t2)
    reg.register(t3)

    all_tools = reg.list_tools()
    assert len(all_tools) == 3

    file_tools = reg.list_tools(category=ToolCategory.FILES)
    assert len(file_tools) == 2
    assert [t.tool_id for t in file_tools] == ["files.read", "files.write"]

    enabled_tools = reg.list_tools(enabled_only=True)
    assert len(enabled_tools) == 2
    assert set(t.tool_id for t in enabled_tools) == {"files.read", "system.info"}


def test_list_tools_deterministic_order():
    """Test list_tools returns tools in deterministic ascending order of tool_id."""
    reg = ToolRegistry()
    reg.register(_create_sample_tool("system.cpu"))
    reg.register(_create_sample_tool("automation.cron"))
    reg.register(_create_sample_tool("browser.open"))
    reg.register(_create_sample_tool("files.delete"))

    tools = reg.list_tools()
    tool_ids = [t.tool_id for t in tools]
    assert tool_ids == ["automation.cron", "browser.open", "files.delete", "system.cpu"]


# --- 2. DUPLICATE PROTECTION TESTS ---

def test_register_duplicate_blocked():
    """Test that registering a duplicate tool_id raises ToolAlreadyRegisteredError."""
    reg = ToolRegistry()
    t1 = _create_sample_tool("files.read_file", name="Original Name")
    reg.register(t1)

    t2 = _create_sample_tool("files.read_file", name="New Name")
    with pytest.raises(ToolAlreadyRegisteredError) as exc_info:
        reg.register(t2)
    assert "files.read_file" in str(exc_info.value)


def test_register_duplicate_does_not_overwrite():
    """Test that a failed duplicate registration does not overwrite the existing tool."""
    reg = ToolRegistry()
    t1 = _create_sample_tool("files.read_file", name="Original Name")
    reg.register(t1)

    t2 = _create_sample_tool("files.read_file", name="Attempted Overwrite")
    with pytest.raises(ToolAlreadyRegisteredError):
        reg.register(t2)

    stored = reg.require("files.read_file")
    assert stored.name == "Original Name"


# --- 3. MISSING TOOLS TESTS ---

def test_get_missing_returns_none():
    """Test get() on a missing tool returns None."""
    reg = ToolRegistry()
    assert reg.get("non.existent") is None
    assert reg.get(123) is None  # Non-string argument returns None safely


def test_require_missing_raises_tool_not_found():
    """Test require() on a missing tool raises ToolNotFoundError."""
    reg = ToolRegistry()
    with pytest.raises(ToolNotFoundError) as exc_info:
        reg.require("non.existent")
    assert "non.existent" in str(exc_info.value)


def test_unregister_missing_raises_tool_not_found():
    """Test unregister() on a missing tool raises ToolNotFoundError."""
    reg = ToolRegistry()
    with pytest.raises(ToolNotFoundError):
        reg.unregister("non.existent")


# --- 4. ENABLE / DISABLE RUNTIME STATE TESTS ---

def test_is_enabled_default():
    """Test that registered tools default to is_enabled == True if definition.enabled is True."""
    reg = ToolRegistry()
    t = _create_sample_tool("files.read_file", enabled=True)
    reg.register(t)
    assert reg.is_enabled("files.read_file") is True


def test_disable_tool():
    """Test disabling runtime state of a registered tool."""
    reg = ToolRegistry()
    t = _create_sample_tool("files.read_file", enabled=True)
    reg.register(t)

    assert reg.disable("files.read_file") is True
    assert reg.is_enabled("files.read_file") is False


def test_enable_tool():
    """Test enabling runtime state after disabling a tool."""
    reg = ToolRegistry()
    t = _create_sample_tool("files.read_file", enabled=True)
    reg.register(t)

    reg.disable("files.read_file")
    assert reg.is_enabled("files.read_file") is False

    assert reg.enable("files.read_file") is True
    assert reg.is_enabled("files.read_file") is True


def test_enable_tool_disabled_in_definition_fails():
    """Test that enabling a tool with static definition.enabled=False fails."""
    reg = ToolRegistry()
    t = _create_sample_tool("system.shutdown", enabled=False)
    reg.register(t)

    assert reg.is_enabled("system.shutdown") is False

    with pytest.raises(ToolDisabledInDefinitionError):
        reg.enable("system.shutdown")

    assert reg.is_enabled("system.shutdown") is False


def test_runtime_state_isolation():
    """Test that runtime disable/enable does not mutate the static ToolDefinition object."""
    reg = ToolRegistry()
    t = _create_sample_tool("files.read_file", enabled=True)
    reg.register(t)

    reg.disable("files.read_file")
    stored = reg.require("files.read_file")

    # Stored definition must still have enabled=True
    assert stored.enabled is True
    # But runtime is_enabled must be False
    assert reg.is_enabled("files.read_file") is False


# --- 5. UNREGISTER TESTS ---

def test_unregister_success():
    """Test unregistering a registered tool."""
    reg = ToolRegistry()
    t = _create_sample_tool("files.read_file")
    reg.register(t)

    assert reg.exists("files.read_file") is True
    reg.unregister("files.read_file")

    assert reg.exists("files.read_file") is False
    assert reg.get("files.read_file") is None
    assert len(reg.list_tools()) == 0


def test_unregister_removes_runtime_state():
    """Test that unregistering cleans up runtime state completely."""
    reg = ToolRegistry()
    t = _create_sample_tool("files.read_file")
    reg.register(t)
    reg.disable("files.read_file")

    reg.unregister("files.read_file")
    assert reg.is_enabled("files.read_file") is False


def test_re_register_after_unregister():
    """Test that a tool can be re-registered after being unregistered."""
    reg = ToolRegistry()
    t1 = _create_sample_tool("files.read_file", name="V1")
    reg.register(t1)
    reg.unregister("files.read_file")

    t2 = _create_sample_tool("files.read_file", name="V2")
    reg.register(t2)
    assert reg.require("files.read_file").name == "V2"


# --- 6. ISOLATION & IMMUTABILITY TESTS ---

def test_isolation_stored_definition():
    """Test that ToolDefinition remains immutable and frozen."""
    reg = ToolRegistry()
    t = _create_sample_tool("files.read_file")
    reg.register(t)

    stored = reg.require("files.read_file")
    with pytest.raises((ValidationError, AttributeError, TypeError)):
        stored.enabled = False


def test_list_tools_returns_immutable_tuple():
    """Test list_tools returns a tuple that caller cannot mutate."""
    reg = ToolRegistry()
    reg.register(_create_sample_tool("files.read"))

    tools = reg.list_tools()
    assert isinstance(tools, tuple)


def test_clear():
    """Test clear() removes all tools and runtime states."""
    reg = ToolRegistry()
    reg.register(_create_sample_tool("files.read"))
    reg.register(_create_sample_tool("system.info"))

    assert len(reg.list_tools()) == 2
    reg.clear()

    assert len(reg.list_tools()) == 0
    assert reg.exists("files.read") is False


# --- 7. THREAD SAFETY TEST ---

def test_thread_safety():
    """Test multi-threaded concurrent registration, lookup, enable, and disable."""
    reg = ToolRegistry()
    num_threads = 10
    num_tools_per_thread = 20

    def worker(thread_idx: int):
        for i in range(num_tools_per_thread):
            tool_id = f"files.tool_{thread_idx}_{i}"
            t = _create_sample_tool(tool_id)
            reg.register(t)
            assert reg.exists(tool_id) is True
            reg.disable(tool_id)
            assert reg.is_enabled(tool_id) is False
            reg.enable(tool_id)
            assert reg.is_enabled(tool_id) is True

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, idx) for idx in range(num_threads)]
        for future in concurrent.futures.as_completed(futures):
            future.result()

    assert len(reg.list_tools()) == num_threads * num_tools_per_thread
