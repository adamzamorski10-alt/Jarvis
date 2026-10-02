"""
Operator Capability — Module.

Provides capability resolution and execution for the Unified Operator Pipeline.
"""
from app.operator_capability.models import (
    CapabilityCategory,
    CapabilityExecutionError,
    CapabilityExecutionResult,
    CapabilityKind,
    CapabilityResolution,
    UnsupportedCapabilityError,
)
from app.operator_capability.resolver import CapabilityResolver
from app.operator_capability.executor import OperatorCapabilityExecutor

__all__ = [
    "CapabilityKind",
    "CapabilityCategory",
    "CapabilityResolution",
    "CapabilityExecutionResult",
    "UnsupportedCapabilityError",
    "CapabilityExecutionError",
    "CapabilityResolver",
    "OperatorCapabilityExecutor",
]
