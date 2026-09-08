"""
Verification Check Suite — Sequential execution of ordered checks.

The CheckSuite is a thin orchestration layer over VerificationCheck instances.
It runs checks sequentially in deterministic registration order and returns
ordered CheckResult tuples.

This module does NOT implement parallel execution, reporting, or registry setup.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from app.verification.check_runner import VerificationCheck
from app.verification.models import (
    CheckCategory,
    CheckResult,
    CheckStatus,
    VerificationContext,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CheckSuite:
    """
    Sequential verification check suite.

    Executes registered checks in deterministic insertion order.
    Each check is executed via its own execute() method; exceptions are
    caught and converted to ERROR results (fail-closed).
    """

    def __init__(self, checks: Sequence[VerificationCheck]) -> None:
        self._checks = list(checks)

    @property
    def checks(self) -> list[VerificationCheck]:
        return list(self._checks)

    def run(self, context: VerificationContext) -> list[CheckResult]:
        """
        Execute all registered checks sequentially.

        Returns:
            Ordered list of CheckResult instances matching the registration order.
        """
        results: list[CheckResult] = []
        for check in self._checks:
            result = self._execute_one(check, context)
            results.append(result)
        return results

    @staticmethod
    def _execute_one(check: VerificationCheck, context: VerificationContext) -> CheckResult:
        """
        Execute a single check, catching all exceptions → ERROR result.

        Mirrors VerificationGate._execute_one() fail-closed semantics.
        """
        started_at = _utc_now()
        try:
            result = check.execute(context)

            if not isinstance(result, CheckResult):
                finished_at = _utc_now()
                return CheckResult(
                    check_id=check.check_id,
                    check_name=check.check_name,
                    required=check.required,
                    status=CheckStatus.ERROR,
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_seconds=(finished_at - started_at).total_seconds(),
                    error_message=(
                        f"Check {check.check_id!r} returned {type(result).__name__!r} "
                        "instead of CheckResult — treated as ERROR."
                    ),
                    category=getattr(check, 'category', None),
                )

            if result.check_id != check.check_id:
                finished_at = _utc_now()
                return CheckResult(
                    check_id=check.check_id,
                    check_name=check.check_name,
                    required=check.required,
                    status=CheckStatus.ERROR,
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_seconds=(finished_at - started_at).total_seconds(),
                    error_message=(
                        f"Check {check.check_id!r} returned result with "
                        f"mismatched check_id {result.check_id!r} — treated as ERROR."
                    ),
                    category=getattr(check, 'category', None),
                )

            return result

        except Exception as exc:
            finished_at = _utc_now()
            return CheckResult(
                check_id=check.check_id,
                check_name=check.check_name,
                required=check.required,
                status=CheckStatus.ERROR,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=(finished_at - started_at).total_seconds(),
                error_message=f"{type(exc).__name__}: {exc}",
                category=getattr(check, 'category', None),
            )
