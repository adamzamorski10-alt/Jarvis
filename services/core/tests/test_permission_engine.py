"""
Unit tests for Stage 5.3 — Permission Engine & Grant Persistence.
Tests decision rules, autonomy modes, grant matching, time-bound expiration,
atomic one-time grant consumption (with concurrency test), and fail-closed security guarantees.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import pytest

from app.db.database import AsyncSessionLocal, init_db
from app.db.models.permission_grant import PermissionGrantModel
from app.repositories.grant_repository import GrantRepository, match_action, match_tool_id
from app.tools.models import (
    AutonomyMode,
    GrantType,
    PermissionDecisionType,
    PermissionEvaluationRequest,
    PermissionGrant,
    RiskLevel,
    ToolCategory,
    ToolDefinition,
)
from app.tools.permission_engine import PermissionEngine
from app.tools.registry import ToolRegistry


def _create_test_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(
        ToolDefinition(
            tool_id="system.get_time",
            name="Get Time",
            description="Returns current system timestamp.",
            category=ToolCategory.SYSTEM,
            risk_level=RiskLevel.SAFE,
        )
    )
    reg.register(
        ToolDefinition(
            tool_id="files.read_file",
            name="Read File",
            description="Reads contents of a file.",
            category=ToolCategory.FILES,
            risk_level=RiskLevel.LOW,
        )
    )
    reg.register(
        ToolDefinition(
            tool_id="files.write_file",
            name="Write File",
            description="Writes data to a file.",
            category=ToolCategory.FILES,
            risk_level=RiskLevel.MEDIUM,
        )
    )
    reg.register(
        ToolDefinition(
            tool_id="system.restart_service",
            name="Restart Service",
            description="Restarts a background service.",
            category=ToolCategory.SYSTEM,
            risk_level=RiskLevel.HIGH,
        )
    )
    reg.register(
        ToolDefinition(
            tool_id="system.format_drive",
            name="Format Drive",
            description="Formats a disk drive.",
            category=ToolCategory.SYSTEM,
            risk_level=RiskLevel.CRITICAL,
        )
    )
    reg.register(
        ToolDefinition(
            tool_id="files.confirm_required",
            name="Confirm Required Tool",
            description="Tool requiring explicit confirmation.",
            category=ToolCategory.FILES,
            risk_level=RiskLevel.LOW,
            requires_confirmation=True,
        )
    )
    reg.register(
        ToolDefinition(
            tool_id="files.static_disabled",
            name="Static Disabled Tool",
            description="Disabled in definition.",
            category=ToolCategory.FILES,
            risk_level=RiskLevel.SAFE,
            enabled=False,
        )
    )
    return reg


# --- 1. ACTION & TOOL ID PATTERN MATCHING TESTS ---

def test_pattern_matching_tool_id():
    """Test exact, domain wildcard, and non-matching tool_id rules."""
    assert match_tool_id("files.read_file", "files.read_file") is True
    assert match_tool_id("files.read_file", "FILES.READ_FILE") is True
    assert match_tool_id("files.*", "files.read_file") is True
    assert match_tool_id("files.*", "files.write_file") is True
    assert match_tool_id("files.*", "system.restart") is False
    assert match_tool_id("files.*", "file_other") is False
    assert match_tool_id("*", "any.tool") is True


def test_pattern_matching_action():
    """Test exact and wildcard action matching rules."""
    assert match_action("read", "read") is True
    assert match_action("READ", "read") is True
    assert match_action("*", "read") is True
    assert match_action("*", "delete") is True
    assert match_action("read", "write") is False


# --- 2. PERMISSION DECISION RULE TESTS ---

@pytest.mark.asyncio
async def test_permission_unknown_tool_denied():
    """Test Rule 1: Unknown tool is DENIED."""
    reg = _create_test_registry()
    engine = PermissionEngine(registry=reg)
    req = PermissionEvaluationRequest(
        tool_id="unknown.tool_id",
        risk_level=RiskLevel.SAFE,
        action="read",
    )
    decision = await engine.evaluate(req)
    assert decision.decision == PermissionDecisionType.DENY
    assert decision.rule_id == "RULE_01_TOOL_NOT_FOUND"


@pytest.mark.asyncio
async def test_permission_runtime_disabled_tool_denied():
    """Test Rule 2: Runtime disabled tool is DENIED."""
    reg = _create_test_registry()
    reg.disable("files.read_file")

    engine = PermissionEngine(registry=reg)
    req = PermissionEvaluationRequest(
        tool_id="files.read_file",
        risk_level=RiskLevel.LOW,
        action="read",
    )
    decision = await engine.evaluate(req)
    assert decision.decision == PermissionDecisionType.DENY
    assert decision.rule_id == "RULE_02_TOOL_DISABLED"


@pytest.mark.asyncio
async def test_permission_statically_disabled_tool_denied():
    """Test Rule 2: Statically disabled tool (enabled=False in definition) is DENIED."""
    reg = _create_test_registry()
    engine = PermissionEngine(registry=reg)
    req = PermissionEvaluationRequest(
        tool_id="files.static_disabled",
        risk_level=RiskLevel.SAFE,
        action="read",
    )
    decision = await engine.evaluate(req)
    assert decision.decision == PermissionDecisionType.DENY
    assert decision.rule_id == "RULE_02_TOOL_DISABLED"


@pytest.mark.asyncio
async def test_permission_manual_autonomy_mode():
    """Test Rule 7: Manual autonomy mode rules."""
    reg = _create_test_registry()
    engine = PermissionEngine(registry=reg)

    # SAFE risk in MANUAL -> ALLOW
    req_safe = PermissionEvaluationRequest(
        tool_id="system.get_time",
        risk_level=RiskLevel.SAFE,
        action="read",
        autonomy_mode=AutonomyMode.MANUAL,
    )
    dec_safe = await engine.evaluate(req_safe)
    assert dec_safe.decision == PermissionDecisionType.ALLOW

    # LOW risk in MANUAL -> ASK_USER
    req_low = PermissionEvaluationRequest(
        tool_id="files.read_file",
        risk_level=RiskLevel.LOW,
        action="read",
        autonomy_mode=AutonomyMode.MANUAL,
    )
    dec_low = await engine.evaluate(req_low)
    assert dec_low.decision == PermissionDecisionType.ASK_USER
    assert dec_low.rule_id == "RULE_07_MANUAL_ASK_USER"


@pytest.mark.asyncio
async def test_permission_semi_autonomous_mode():
    """Test Rule 8: Semi-autonomous mode rules."""
    reg = _create_test_registry()
    engine = PermissionEngine(registry=reg)

    # LOW risk -> ALLOW
    req_low = PermissionEvaluationRequest(
        tool_id="files.read_file",
        risk_level=RiskLevel.LOW,
        action="read",
        autonomy_mode=AutonomyMode.SEMI_AUTONOMOUS,
    )
    assert (await engine.evaluate(req_low)).decision == PermissionDecisionType.ALLOW

    # MEDIUM risk -> ASK_USER
    req_med = PermissionEvaluationRequest(
        tool_id="files.write_file",
        risk_level=RiskLevel.MEDIUM,
        action="write",
        autonomy_mode=AutonomyMode.SEMI_AUTONOMOUS,
    )
    assert (await engine.evaluate(req_med)).decision == PermissionDecisionType.ASK_USER

    # CRITICAL risk -> DENY
    req_crit = PermissionEvaluationRequest(
        tool_id="system.format_drive",
        risk_level=RiskLevel.CRITICAL,
        action="format",
        autonomy_mode=AutonomyMode.SEMI_AUTONOMOUS,
    )
    dec_crit = await engine.evaluate(req_crit)
    assert dec_crit.decision == PermissionDecisionType.DENY
    assert dec_crit.rule_id == "RULE_08_SEMI_AUTONOMOUS_CRITICAL_DENY"


@pytest.mark.asyncio
async def test_permission_autonomous_mode():
    """Test Rule 9: Autonomous mode rules (CRITICAL is DENIED, HIGH is ASK_USER)."""
    reg = _create_test_registry()
    engine = PermissionEngine(registry=reg)

    # MEDIUM risk -> ALLOW
    req_med = PermissionEvaluationRequest(
        tool_id="files.write_file",
        risk_level=RiskLevel.MEDIUM,
        action="write",
        autonomy_mode=AutonomyMode.AUTONOMOUS,
    )
    assert (await engine.evaluate(req_med)).decision == PermissionDecisionType.ALLOW

    # HIGH risk -> ASK_USER
    req_high = PermissionEvaluationRequest(
        tool_id="system.restart_service",
        risk_level=RiskLevel.HIGH,
        action="restart",
        autonomy_mode=AutonomyMode.AUTONOMOUS,
    )
    assert (await engine.evaluate(req_high)).decision == PermissionDecisionType.ASK_USER

    # CRITICAL risk -> DENY
    req_crit = PermissionEvaluationRequest(
        tool_id="system.format_drive",
        risk_level=RiskLevel.CRITICAL,
        action="format",
        autonomy_mode=AutonomyMode.AUTONOMOUS,
    )
    assert (await engine.evaluate(req_crit)).decision == PermissionDecisionType.DENY


@pytest.mark.asyncio
async def test_permission_requires_confirmation_constraint():
    """Test Rule 6: Tool with requires_confirmation=True forces ASK_USER."""
    reg = _create_test_registry()
    engine = PermissionEngine(registry=reg)

    req = PermissionEvaluationRequest(
        tool_id="files.confirm_required",
        risk_level=RiskLevel.LOW,
        action="read",
        autonomy_mode=AutonomyMode.AUTONOMOUS,
    )
    decision = await engine.evaluate(req)
    assert decision.decision == PermissionDecisionType.ASK_USER
    assert decision.rule_id == "RULE_06_REQUIRES_CONFIRMATION"


# --- 3. GRANT LIFECYCLE & PERSISTENCE TESTS ---

@pytest.mark.asyncio
async def test_grant_repository_crud():
    """Test creating, fetching, listing, and revoking grants in SQLite."""
    await init_db()
    async with AsyncSessionLocal() as session:
        repo = GrantRepository(session)

        # Create persistent grant
        g1 = await repo.create_grant(
            tool_id="files.write_file",
            action_pattern="write",
            grant_type=GrantType.PERSISTENT,
        )
        assert g1.id is not None
        assert g1.tool_id == "files.write_file"

        # List grants
        grants = await repo.list_grants(tool_id="files.write_file")
        assert len(grants) == 1
        assert grants[0].id == g1.id

        # Revoke grant
        revoked = await repo.revoke_grant(g1.id)
        assert revoked is True

        # Verify revoked
        grants_after = await repo.list_grants(tool_id="files.write_file")
        assert len(grants_after) == 0


@pytest.mark.asyncio
async def test_permission_engine_with_active_grant():
    """Test PermissionEngine evaluates ALLOW when a matching valid grant exists."""
    await init_db()
    reg = _create_test_registry()

    async with AsyncSessionLocal() as session:
        repo = GrantRepository(session)
        g = await repo.create_grant(
            tool_id="files.write_file",
            action_pattern="write",
            grant_type=GrantType.PERSISTENT,
        )

        engine = PermissionEngine(registry=reg, grant_repo=repo)
        req = PermissionEvaluationRequest(
            tool_id="files.write_file",
            risk_level=RiskLevel.MEDIUM,
            action="write",
            autonomy_mode=AutonomyMode.MANUAL,  # Would normally be ASK_USER in MANUAL
        )
        decision = await engine.evaluate(req)

        assert decision.decision == PermissionDecisionType.ALLOW
        assert decision.grant_id == g.id


@pytest.mark.asyncio
async def test_expired_time_bound_grant_ignored():
    """Test that expired time-bound grants do NOT grant permission."""
    await init_db()
    reg = _create_test_registry()
    now = datetime.now(timezone.utc)
    past = now - timedelta(minutes=10)

    async with AsyncSessionLocal() as session:
        repo = GrantRepository(session)
        # Insert an already-expired grant directly via ORM (bypasses domain validation,
        # which correctly rejects expires_at < created_at for new grants from user input).
        import uuid
        expired_model = PermissionGrantModel(
            id=f"grant_{uuid.uuid4().hex[:12]}",
            tool_id="files.write_file",
            action_pattern="write",
            grant_type=GrantType.TIME_BOUND.value,
            created_at=past - timedelta(minutes=30),
            expires_at=past,
            consumed=False,
        )
        session.add(expired_model)
        await session.flush()

        engine = PermissionEngine(registry=reg, grant_repo=repo)
        req = PermissionEvaluationRequest(
            tool_id="files.write_file",
            risk_level=RiskLevel.MEDIUM,
            action="write",
            autonomy_mode=AutonomyMode.MANUAL,
        )
        decision = await engine.evaluate(req)

        # Expired grant ignored -> falls back to MANUAL mode rule (ASK_USER)
        assert decision.decision == PermissionDecisionType.ASK_USER
        assert decision.grant_id is None


@pytest.mark.asyncio
async def test_grant_cleanup_expired():
    """Test cleaning up expired time-bound grants."""
    await init_db()
    now = datetime.now(timezone.utc)
    past = now - timedelta(minutes=10)
    future = now + timedelta(minutes=10)

    async with AsyncSessionLocal() as session:
        repo = GrantRepository(session)
        import uuid
        # Insert pre-expired grant directly via ORM to bypass domain validation
        expired_model = PermissionGrantModel(
            id=f"grant_{uuid.uuid4().hex[:12]}",
            tool_id="cleanup.tool1",
            action_pattern="*",
            grant_type=GrantType.TIME_BOUND.value,
            created_at=past - timedelta(minutes=30),
            expires_at=past,
            consumed=False,
        )
        session.add(expired_model)
        await session.flush()

        # Normal (non-expired) grant created via repository
        await repo.create_grant(
            tool_id="cleanup.tool2",
            grant_type=GrantType.TIME_BOUND,
            expires_at=future,
        )

        cleaned_count = await repo.cleanup_expired_grants()
        assert cleaned_count == 1

        active = await repo.list_grants()
        assert len(active) == 1
        assert active[0].tool_id == "cleanup.tool2"


# --- 4. ATOMIC ONE-TIME GRANT & CONCURRENCY TESTS ---

@pytest.mark.asyncio
async def test_one_time_grant_atomic_consumption():
    """Test that a one-time grant is consumed upon first execution and cannot be reused."""
    await init_db()
    reg = _create_test_registry()

    async with AsyncSessionLocal() as session:
        repo = GrantRepository(session)
        g = await repo.create_grant(
            tool_id="files.write_file",
            action_pattern="write",
            grant_type=GrantType.ONE_TIME,
        )

        engine = PermissionEngine(registry=reg, grant_repo=repo)
        req = PermissionEvaluationRequest(
            tool_id="files.write_file",
            risk_level=RiskLevel.MEDIUM,
            action="write",
            autonomy_mode=AutonomyMode.MANUAL,
        )

        # 1st attempt -> ALLOW (one-time grant consumed)
        dec1 = await engine.evaluate(req)
        assert dec1.decision == PermissionDecisionType.ALLOW
        assert dec1.grant_id == g.id

        # 2nd attempt -> ASK_USER (grant already consumed)
        dec2 = await engine.evaluate(req)
        assert dec2.decision == PermissionDecisionType.ASK_USER
        assert dec2.grant_id is None


@pytest.mark.asyncio
async def test_parallel_one_time_grant_race_condition():
    """
    Test 20 parallel async requests attempting to consume the exact same one-time grant.
    Guarantees that EXACTLY ONE request succeeds (ALLOW) and 19 fail (ASK_USER).
    """
    await init_db()
    reg = _create_test_registry()

    async with AsyncSessionLocal() as session:
        repo = GrantRepository(session)
        g = await repo.create_grant(
            tool_id="files.write_file",
            action_pattern="write",
            grant_type=GrantType.ONE_TIME,
        )
        await session.commit()

    # Parallel requests with independent DB sessions
    async def try_consume():
        async with AsyncSessionLocal() as session:
            repo = GrantRepository(session)
            engine = PermissionEngine(registry=reg, grant_repo=repo)
            req = PermissionEvaluationRequest(
                tool_id="files.write_file",
                risk_level=RiskLevel.MEDIUM,
                action="write",
                autonomy_mode=AutonomyMode.MANUAL,
            )
            res = await engine.evaluate(req)
            await session.commit()
            return res.decision

    tasks = [try_consume() for _ in range(20)]
    results = await asyncio.gather(*tasks)

    allows = [r for r in results if r == PermissionDecisionType.ALLOW]
    ask_users = [r for r in results if r == PermissionDecisionType.ASK_USER]

    assert len(allows) == 1, "Exactly one parallel request must consume the one-time grant"
    assert len(ask_users) == 19


# --- 5. SECURITY & IMMUTABILITY GUARANTEES ---

@pytest.mark.asyncio
async def test_permission_engine_does_not_mutate_registry_or_definitions():
    """Test that PermissionEngine evaluation never mutates ToolRegistry or ToolDefinition objects."""
    reg = _create_test_registry()
    engine = PermissionEngine(registry=reg)
    tool_before = reg.require("files.read_file")

    req = PermissionEvaluationRequest(
        tool_id="files.read_file",
        risk_level=RiskLevel.LOW,
        action="read",
        autonomy_mode=AutonomyMode.MANUAL,
    )
    await engine.evaluate(req)

    tool_after = reg.require("files.read_file")
    assert tool_after.enabled == tool_before.enabled
    assert tool_after.risk_level == tool_before.risk_level
    assert reg.is_enabled("files.read_file") is True
