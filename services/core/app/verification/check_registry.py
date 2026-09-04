"""
Verification Gate Foundation — Check Registry.

The CheckRegistry is an ordered, duplicate-rejecting, lockable container of
VerificationCheck instances.

DESIGN INVARIANTS:
  1. Duplicate check_id → DuplicateCheckError immediately (no silent overwrite).
  2. Retrieval order is deterministic: insertion order is preserved.
  3. Locked during gate execution — no new checks can be registered while
     the gate is running.
  4. Thread-safe via threading.Lock for concurrent registration scenarios.
  5. Registry is NOT a module-level singleton — callers create instances,
     preventing hidden global state from affecting gate results.
  6. No dynamic module imports; no plugin loader; no arbitrary code loading.
"""
from __future__ import annotations

import threading
from typing import Sequence

from app.verification.check_runner import VerificationCheck
from app.verification.exceptions import (
    CheckNotFoundError,
    DuplicateCheckError,
    RegistryLockedError,
)


class CheckRegistry:
    """
    Ordered, duplicate-rejecting, lockable registry for VerificationChecks.

    Usage::

        registry = CheckRegistry()
        registry.register(MyRequiredCheck())
        registry.register(MyOptionalCheck())

        # Gate locks the registry before running:
        checks = registry.get_ordered()
        registry.lock()
        # Now registry.register(...) raises RegistryLockedError

    Thread Safety:
        register() and get_ordered() are protected by an internal Lock.
        lock() is idempotent and thread-safe.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._checks: dict[str, VerificationCheck] = {}
        self._order: list[str] = []      # insertion-order list of check_ids
        self._locked: bool = False

    # ── Public API ────────────────────────────────────────────────────────────

    def register(self, check: VerificationCheck) -> None:
        """
        Register a verification check.

        Args:
            check: A VerificationCheck (Protocol-compatible) instance.
                   Must have non-empty check_id, check_name, and required fields.

        Raises:
            RegistryLockedError: if the registry is locked for execution.
            DuplicateCheckError: if check_id is already registered.
            TypeError: if check does not satisfy the VerificationCheck protocol.
        """
        if not isinstance(check, VerificationCheck):
            raise TypeError(
                f"Expected a VerificationCheck-compatible object, "
                f"got {type(check).__name__}. "
                "Ensure check_id, check_name, required, and execute() are defined."
            )

        check_id = check.check_id
        if not check_id or not isinstance(check_id, str):
            raise ValueError(
                "VerificationCheck.check_id must be a non-empty string."
            )

        with self._lock:
            if self._locked:
                raise RegistryLockedError(check_id)
            if check_id in self._checks:
                raise DuplicateCheckError(check_id)
            self._checks[check_id] = check
            self._order.append(check_id)

    def lock(self) -> None:
        """
        Lock the registry against further registration.

        Called by VerificationGate before executing checks.
        Idempotent: calling lock() on an already-locked registry is a no-op.
        """
        with self._lock:
            self._locked = True

    def is_locked(self) -> bool:
        """Return True if the registry is locked."""
        with self._lock:
            return self._locked

    def get_ordered(self) -> list[VerificationCheck]:
        """
        Return registered checks in deterministic insertion order.

        Returns:
            A new list of VerificationCheck instances.
            Order matches the sequence of register() calls.
        """
        with self._lock:
            return [self._checks[cid] for cid in self._order]

    def require(self, check_id: str) -> VerificationCheck:
        """
        Return the check with the given ID, or raise CheckNotFoundError.

        Args:
            check_id: The unique identifier of the check to retrieve.

        Raises:
            CheckNotFoundError: if no check with check_id is registered.
        """
        with self._lock:
            if check_id not in self._checks:
                raise CheckNotFoundError(check_id)
            return self._checks[check_id]

    def get(self, check_id: str) -> VerificationCheck | None:
        """
        Return the check with the given ID, or None if not found.
        """
        with self._lock:
            return self._checks.get(check_id)

    def exists(self, check_id: str) -> bool:
        """Return True if check_id is registered."""
        with self._lock:
            return check_id in self._checks

    def count(self) -> int:
        """Return the number of registered checks."""
        with self._lock:
            return len(self._checks)

    def check_ids(self) -> tuple[str, ...]:
        """
        Return registered check IDs in insertion order as an immutable tuple.
        """
        with self._lock:
            return tuple(self._order)
