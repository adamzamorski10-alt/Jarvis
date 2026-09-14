"""
AI Manager — Capability Registry.

Application-owned capability definitions. The model may propose that a
capability is required, but the application decides whether it is known,
enabled, and authorized.

The registry is NOT an execution registry. It does not contain callable
functions, shell commands, Python import paths, or arbitrary executable code.
It is metadata used for authorization decisions only.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.ai_manager.models import CapabilityIdentifier, RiskLevel


class CapabilityDefinition(BaseModel):
    """
    Application-owned capability definition.

    Contains only what is required to make an authorization decision.
    The model cannot create or modify these definitions.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    capability_id: CapabilityIdentifier
    risk: RiskLevel
    enabled: bool = True
    approval_required: bool = False
    description: str | None = Field(default=None, max_length=1024)


class CapabilityRegistry:
    """
    Immutable application-owned capability registry.

    Answers:
      1. Does capability exist?
      2. What is its application-defined risk?
      3. Is it enabled?
      4. Does it require approval?
    """

    def __init__(self, capabilities: list[CapabilityDefinition] | None = None) -> None:
        self._capabilities: dict[str, CapabilityDefinition] = {}
        for cap in (capabilities or []):
            key = str(cap.capability_id)
            if key in self._capabilities:
                raise ValueError(f"Duplicate capability definition: {key}")
            self._capabilities[key] = cap

    def get(self, capability_id: CapabilityIdentifier) -> CapabilityDefinition | None:
        """Return the capability definition, or None if unknown."""
        return self._capabilities.get(str(capability_id))

    def exists(self, capability_id: CapabilityIdentifier) -> bool:
        """Return True if the capability is registered."""
        return str(capability_id) in self._capabilities

    def is_enabled(self, capability_id: CapabilityIdentifier) -> bool:
        """Return True if the capability exists and is enabled."""
        cap = self._capabilities.get(str(capability_id))
        return cap.enabled if cap else False

    def requires_approval(self, capability_id: CapabilityIdentifier) -> bool:
        """Return True if the capability requires application approval."""
        cap = self._capabilities.get(str(capability_id))
        return cap.approval_required if cap else False

    def get_risk(self, capability_id: CapabilityIdentifier) -> RiskLevel | None:
        """Return the application-defined risk level, or None if unknown."""
        cap = self._capabilities.get(str(capability_id))
        return cap.risk if cap else None


def create_default_registry() -> CapabilityRegistry:
    """
    Create the default application capability registry.

    All definitions are application-controlled. Unknown capabilities fail closed.
    """
    return CapabilityRegistry([
        CapabilityDefinition(
            capability_id=CapabilityIdentifier(namespace="development", name="plan"),
            risk=RiskLevel.LOW,
            enabled=True,
            approval_required=False,
            description="Plan development tasks",
        ),
        CapabilityDefinition(
            capability_id=CapabilityIdentifier(namespace="development", name="code_review"),
            risk=RiskLevel.LOW,
            enabled=True,
            approval_required=False,
            description="Review code changes",
        ),
        CapabilityDefinition(
            capability_id=CapabilityIdentifier(namespace="development", name="test"),
            risk=RiskLevel.LOW,
            enabled=True,
            approval_required=False,
            description="Run test suites",
        ),
        CapabilityDefinition(
            capability_id=CapabilityIdentifier(namespace="development", name="execute"),
            risk=RiskLevel.HIGH,
            enabled=True,
            approval_required=True,
            description="Execute development actions",
        ),
        CapabilityDefinition(
            capability_id=CapabilityIdentifier(namespace="tool_execution", name="run"),
            risk=RiskLevel.CRITICAL,
            enabled=False,
            approval_required=True,
            description="Execute arbitrary tools (disabled by policy)",
        ),
        CapabilityDefinition(
            capability_id=CapabilityIdentifier(namespace="git", name="commit"),
            risk=RiskLevel.MEDIUM,
            enabled=True,
            approval_required=True,
            description="Commit to Git repository",
        ),
        CapabilityDefinition(
            capability_id=CapabilityIdentifier(namespace="workspace", name="access"),
            risk=RiskLevel.HIGH,
            enabled=True,
            approval_required=True,
            description="Access workspace filesystem",
        ),
        CapabilityDefinition(
            capability_id=CapabilityIdentifier(namespace="shell", name="execute"),
            risk=RiskLevel.CRITICAL,
            enabled=False,
            approval_required=True,
            description="Execute shell commands (disabled by policy)",
        ),
        CapabilityDefinition(
            capability_id=CapabilityIdentifier(namespace="verification_gate", name="check"),
            risk=RiskLevel.MEDIUM,
            enabled=True,
            approval_required=False,
            description="Run verification checks",
        ),
        CapabilityDefinition(
            capability_id=CapabilityIdentifier(namespace="permission_engine", name="evaluate"),
            risk=RiskLevel.MEDIUM,
            enabled=True,
            approval_required=False,
            description="Evaluate permissions",
        ),
    ])
