"""
Unit tests for Stage 5.1 — Core Domain Models & Schemas.
Tests validation, immutability, defaults, enums, and error handling for tool models.
"""
from datetime import datetime, timedelta, timezone
import pytest
from pydantic import ValidationError

from app.tools.models import (
    ApprovalRequest,
    ApprovalStatus,
    AutonomyMode,
    GrantType,
    PermissionDecision,
    PermissionDecisionType,
    PermissionEvaluationRequest,
    PermissionGrant,
    RiskLevel,
    ToolCall,
    ToolCategory,
    ToolDefinition,
    ToolExecutionResult,
    ToolExecutionStatus,
)


# --- 1. TOOL DEFINITION TESTS & IMMUTABILITY ---

def test_tool_definition_valid():
    """Test creating a valid ToolDefinition."""
    tool = ToolDefinition(
        action="read_file",
        tool_id="files.read_file",
        name="Read File",
        description="Reads contents of a local file safely.",
        version="1.0.0",
        category=ToolCategory.FILES,
        risk_level=RiskLevel.LOW,
        requires_confirmation=False,
        enabled=True,
        parameters_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        result_schema={"type": "object", "properties": {"content": {"type": "string"}}},
    )
    assert tool.tool_id == "files.read_file"
    assert tool.name == "Read File"
    assert tool.category == ToolCategory.FILES
    assert tool.risk_level == RiskLevel.LOW
    assert tool.requires_confirmation is False
    assert tool.enabled is True


def test_tool_definition_immutability():
    """Test that ToolDefinition is strictly immutable (frozen)."""
    tool = ToolDefinition(
        action="get_status",
        tool_id="system.get_status",
        name="Get System Status",
        description="Returns system metrics.",
        category=ToolCategory.SYSTEM,
        risk_level=RiskLevel.SAFE,
    )
    with pytest.raises((ValidationError, TypeError)):
        tool.enabled = False  # Mutating frozen attribute must fail

    with pytest.raises((ValidationError, TypeError)):
        tool.risk_level = RiskLevel.CRITICAL  # Mutating frozen attribute must fail

    with pytest.raises((ValidationError, TypeError)):
        tool.requires_confirmation = True  # Mutating frozen attribute must fail


def test_tool_definition_invalid_tool_id():
    """Test validation errors for invalid tool_id formats."""
    invalid_ids = ["", "   ", "read_file", "files.", ".read_file", "files..read", "Files.Read", "files/read"]
    for invalid_id in invalid_ids:
        with pytest.raises(ValidationError) as exc_info:
            ToolDefinition(
                tool_id=invalid_id,
                name="Test Tool",
                description="Test Description",
                category=ToolCategory.FILES,
                risk_level=RiskLevel.SAFE,
            )
        assert "tool_id" in str(exc_info.value)


def test_tool_definition_invalid_version():
    """Test validation errors for non-semver version strings."""
    invalid_versions = ["1", "1.0", "v1.0.0", "1.0.0-beta", "abc"]
    for version in invalid_versions:
        with pytest.raises(ValidationError) as exc_info:
            ToolDefinition(
                action="read_file",
                tool_id="files.read_file",
                name="Read File",
                description="Test Description",
                version=version,
                category=ToolCategory.FILES,
                risk_level=RiskLevel.SAFE,
            )
        assert "version" in str(exc_info.value)


def test_tool_definition_invalid_category_or_risk():
    """Test invalid category or risk level enum values."""
    with pytest.raises(ValidationError):
        ToolDefinition(
            action="read_file",
            tool_id="files.read_file",
            name="Read File",
            description="Test Description",
            category="invalid_category",
            risk_level=RiskLevel.SAFE,
        )

    with pytest.raises(ValidationError):
        ToolDefinition(
            action="read_file",
            tool_id="files.read_file",
            name="Read File",
            description="Test Description",
            category=ToolCategory.FILES,
            risk_level="INVALID_RISK",
        )


def test_tool_definition_empty_name_or_description():
    """Test that empty or whitespace-only name/description are rejected."""
    with pytest.raises(ValidationError):
        ToolDefinition(
            action="read_file",
            tool_id="files.read_file",
            name="   ",
            description="Valid Description",
            category=ToolCategory.FILES,
            risk_level=RiskLevel.SAFE,
        )

    with pytest.raises(ValidationError):
        ToolDefinition(
            action="read_file",
            tool_id="files.read_file",
            name="Valid Name",
            description="",
            category=ToolCategory.FILES,
            risk_level=RiskLevel.SAFE,
        )


# --- 2. TOOL CALL & EXECUTION RESULT TESTS ---

def test_tool_call_valid():
    """Test valid ToolCall creation."""
    call = ToolCall(
        tool_id="files.read_file",
        arguments={"path": "/tmp/test.txt"},
        request_id="req-12345",
    )
    assert call.tool_id == "files.read_file"
    assert call.arguments == {"path": "/tmp/test.txt"}
    assert call.request_id == "req-12345"


def test_tool_call_invalid_tool_id():
    """Test ToolCall invalid tool_id format rejection."""
    with pytest.raises(ValidationError):
        ToolCall(tool_id="invalid_id")


def test_tool_execution_result_valid():
    """Test valid ToolExecutionResult creation."""
    res = ToolExecutionResult(
        success=True,
        tool_id="system.get_status",
        request_id="req-999",
        status=ToolExecutionStatus.SUCCESS,
        result={"cpu_usage": 15.2},
        execution_time_ms=45.6,
    )
    assert res.success is True
    assert res.status == ToolExecutionStatus.SUCCESS
    assert res.execution_time_ms == 45.6


def test_tool_execution_result_error():
    """Test ToolExecutionResult failure representation."""
    res = ToolExecutionResult(
        success=False,
        tool_id="network.fetch_url",
        status=ToolExecutionStatus.ERROR,
        error_code="connection_refused",
        error_message="Target server refused connection.",
    )
    assert res.success is False
    assert res.error_code == "connection_refused"


# --- 3. PERMISSION & AUTONOMY MODELS TESTS ---

def test_autonomy_mode_defaults():
    """Test AutonomyMode enum and default value 'manual'."""
    assert AutonomyMode.MANUAL.value == "manual"
    assert AutonomyMode.SEMI_AUTONOMOUS.value == "semi_autonomous"
    assert AutonomyMode.AUTONOMOUS.value == "autonomous"

    req = PermissionEvaluationRequest(
        tool_id="files.read_file",
        risk_level=RiskLevel.LOW,
        action="read",
    )
    assert req.autonomy_mode == AutonomyMode.MANUAL


def test_permission_decision_valid():
    """Test PermissionDecision model."""
    dec = PermissionDecision(
        decision=PermissionDecisionType.ALLOW,
        reason="Rule matches SAFE risk level in manual mode.",
        rule_id="rule-safe-allow",
    )
    assert dec.decision == PermissionDecisionType.ALLOW
    assert dec.reason == "Rule matches SAFE risk level in manual mode."


def test_permission_decision_invalid():
    """Test PermissionDecision validation errors."""
    with pytest.raises(ValidationError):
        PermissionDecision(
            decision="INVALID_DECISION",
            reason="Some reason",
        )

    with pytest.raises(ValidationError):
        PermissionDecision(
            decision=PermissionDecisionType.DENY,
            reason="   ",
        )


# --- 4. CONSENT & PERMISSION GRANT TESTS ---

def test_permission_grant_one_time():
    """Test One-time PermissionGrant creation."""
    grant = PermissionGrant(
        id="grant-001",
        tool_id="files.read_file",
        action_pattern="read",
        grant_type=GrantType.ONE_TIME,
    )
    assert grant.grant_type == GrantType.ONE_TIME
    assert grant.expires_at is None
    assert grant.is_expired() is False


def test_permission_grant_time_bound_valid():
    """Test Time-bound PermissionGrant creation and expiration check."""
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=15)
    grant = PermissionGrant(
        id="grant-002",
        tool_id="files.*",
        grant_type=GrantType.TIME_BOUND,
        created_at=now,
        expires_at=expires,
    )
    assert grant.grant_type == GrantType.TIME_BOUND
    assert grant.is_expired(at=now + timedelta(minutes=5)) is False
    assert grant.is_expired(at=now + timedelta(minutes=16)) is True


def test_permission_grant_time_bound_missing_expires():
    """Test that time-bound grants require 'expires_at'."""
    with pytest.raises(ValidationError) as exc_info:
        PermissionGrant(
            tool_id="files.read_file",
            grant_type=GrantType.TIME_BOUND,
        )
    assert "expires_at" in str(exc_info.value)


def test_permission_grant_time_bound_invalid_expiration():
    """Test that 'expires_at' must be after 'created_at'."""
    now = datetime.now(timezone.utc)
    past = now - timedelta(minutes=5)
    with pytest.raises(ValidationError) as exc_info:
        PermissionGrant(
            tool_id="files.read_file",
            grant_type=GrantType.TIME_BOUND,
            created_at=now,
            expires_at=past,
        )
    assert "expires_at" in str(exc_info.value)


# --- 5. APPROVAL REQUEST TESTS ---

def test_approval_request_valid():
    """Test ApprovalRequest creation and expiration check."""
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=5)
    appr = ApprovalRequest(
        approval_id="appr-100",
        tool_id="system.restart_service",
        action="restart",
        risk_level=RiskLevel.HIGH,
        created_at=now,
        expires_at=expires,
        status=ApprovalStatus.PENDING,
    )
    assert appr.status == ApprovalStatus.PENDING
    assert appr.is_expired(at=now + timedelta(minutes=2)) is False
    assert appr.is_expired(at=now + timedelta(minutes=6)) is True


def test_approval_request_invalid_expiration():
    """Test that ApprovalRequest rejects expires_at <= created_at."""
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        ApprovalRequest(
            approval_id="appr-101",
            tool_id="system.restart_service",
            action="restart",
            risk_level=RiskLevel.CRITICAL,
            created_at=now,
            expires_at=now,
        )
