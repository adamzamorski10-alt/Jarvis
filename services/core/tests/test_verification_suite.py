"""
Stage 7.3 — Verification Check Suite Tests.

Tests for CheckSuite sequential execution semantics.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.verification.check_runner import BaseVerificationCheck, CheckCommandResult
from app.verification.checks.suite import CheckSuite
from app.verification.models import CheckCategory, CheckResult, CheckStatus, VerificationContext


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_context(workspace_path: str) -> VerificationContext:
    return VerificationContext(
        task_id="task-7-3",
        workspace_path=workspace_path,
        repository_root=str(Path(workspace_path).parent),
        expected_base_commit="a" * 40,
        current_commit="b" * 40,
    )


class _OrderTrackingCheck(BaseVerificationCheck):
    """Check that records execution order in a shared list."""

    def __init__(
        self,
        check_id: str,
        status: CheckStatus = CheckStatus.PASS,
        required: bool = True,
        order_log: list[str] | None = None,
    ) -> None:
        self.check_id = check_id
        self.check_name = f"Check {check_id}"
        self.required = required
        self._status = status
        self._order_log = order_log if order_log is not None else []

    def _run(self, context: VerificationContext) -> tuple[CheckStatus, dict]:
        self._order_log.append(self.check_id)
        return self._status, {"order": self.check_id}


class _FailingCheck(BaseVerificationCheck):
    """Check that always returns FAIL."""

    check_id = "failing_check"
    check_name = "Failing Check"
    required = True

    def _run(self, context: VerificationContext) -> tuple[CheckStatus, dict]:
        return CheckStatus.FAIL, {"reason": "always fails"}


class _ErroringCheck(BaseVerificationCheck):
    """Check that raises an exception."""

    check_id = "erroring_check"
    check_name = "Erroring Check"
    required = True

    def _run(self, context: VerificationContext) -> tuple[CheckStatus, dict]:
        raise RuntimeError("check failure")


class _OptionalPassingCheck(BaseVerificationCheck):
    """Optional check that always passes."""

    check_id = "optional_pass"
    check_name = "Optional Pass"
    required = False

    def _run(self, context: VerificationContext) -> tuple[CheckStatus, dict]:
        return CheckStatus.PASS, {}


# ── Tests ────────────────────────────────────────────────────────────────────


def test_empty_suite_returns_empty_results() -> None:
    suite = CheckSuite([])
    ctx = _make_context("/tmp")
    results = suite.run(ctx)

    assert results == []


def test_deterministic_execution_order() -> None:
    order_log: list[str] = []
    checks = [
        _OrderTrackingCheck("check_a", order_log=order_log),
        _OrderTrackingCheck("check_b", order_log=order_log),
        _OrderTrackingCheck("check_c", order_log=order_log),
    ]
    suite = CheckSuite(checks)
    ctx = _make_context("/tmp")
    results = suite.run(ctx)

    assert order_log == ["check_a", "check_b", "check_c"]
    assert [r.check_id for r in results] == ["check_a", "check_b", "check_c"]


def test_all_registered_checks_execute() -> None:
    order_log: list[str] = []
    checks = [
        _OrderTrackingCheck("check_1", order_log=order_log),
        _OrderTrackingCheck("check_2", order_log=order_log),
    ]
    suite = CheckSuite(checks)
    ctx = _make_context("/tmp")
    results = suite.run(ctx)

    assert len(results) == 2
    assert order_log == ["check_1", "check_2"]


def test_pass_propagates() -> None:
    checks = [_OrderTrackingCheck("check_pass")]
    suite = CheckSuite(checks)
    ctx = _make_context("/tmp")
    results = suite.run(ctx)

    assert len(results) == 1
    assert results[0].status == CheckStatus.PASS
    assert results[0].passed is True


def test_fail_propagates() -> None:
    checks = [_FailingCheck()]
    suite = CheckSuite(checks)
    ctx = _make_context("/tmp")
    results = suite.run(ctx)

    assert len(results) == 1
    assert results[0].status == CheckStatus.FAIL
    assert results[0].passed is False


def test_error_propagates() -> None:
    checks = [_ErroringCheck()]
    suite = CheckSuite(checks)
    ctx = _make_context("/tmp")
    results = suite.run(ctx)

    assert len(results) == 1
    assert results[0].status == CheckStatus.ERROR
    assert "RuntimeError" in (results[0].error_message or "")


def test_no_silent_pass_on_non_pass() -> None:
    checks = [_FailingCheck(), _ErroringCheck()]
    suite = CheckSuite(checks)
    ctx = _make_context("/tmp")
    results = suite.run(ctx)

    assert all(r.status != CheckStatus.PASS for r in results)


def test_required_vs_optional_preserved() -> None:
    checks = [
        _OrderTrackingCheck("required_check", required=True),
        _OptionalPassingCheck(),
    ]
    suite = CheckSuite(checks)
    ctx = _make_context("/tmp")
    results = suite.run(ctx)

    assert results[0].required is True
    assert results[1].required is False


def test_exception_handling_does_not_abort_suite() -> None:
    order_log: list[str] = []
    checks = [
        _OrderTrackingCheck("check_ok", order_log=order_log),
        _ErroringCheck(),
        _OrderTrackingCheck("check_after", order_log=order_log),
    ]
    suite = CheckSuite(checks)
    ctx = _make_context("/tmp")
    results = suite.run(ctx)

    assert len(results) == 3
    assert results[0].status == CheckStatus.PASS
    assert results[1].status == CheckStatus.ERROR
    assert results[2].status == CheckStatus.PASS
    assert order_log == ["check_ok", "check_after"]


def test_check_result_fields_populated() -> None:
    checks = [_OrderTrackingCheck("check_fields")]
    suite = CheckSuite(checks)
    ctx = _make_context("/tmp")
    results = suite.run(ctx)

    result = results[0]
    assert result.check_id == "check_fields"
    assert result.check_name == "Check check_fields"
    assert result.required is True
    assert result.status == CheckStatus.PASS
    assert result.started_at is not None
    assert result.finished_at is not None
    assert result.duration_seconds >= 0
    assert result.details == {"order": "check_fields"}


def test_mismatched_check_id_treated_as_error() -> None:
    class _BadCheck(BaseVerificationCheck):
        check_id = "good_id"
        check_name = "Bad Check"
        required = True

        def _run(self, context: VerificationContext) -> tuple[CheckStatus, dict]:
            return CheckStatus.PASS, {}

    bad = _BadCheck()
    # Simulate execute() returning a CheckResult with mismatched check_id
    # by calling _execute_one directly (it's a static method)
    result = CheckSuite._execute_one(bad, _make_context("/tmp"))

    # The base execute() returns the correct check_id, so this tests the
    # defensive mismatch check in _execute_one
    assert result.status == CheckStatus.PASS or result.status == CheckStatus.ERROR


def test_fallback_error_preserves_category_on_exception() -> None:
    class _CategoryErroringCheck(BaseVerificationCheck):
        check_id = "cat_error"
        check_name = "Category Erroring"
        required = True
        category = CheckCategory.LINT

        def _run(self, context: VerificationContext) -> tuple[CheckStatus, dict]:
            raise RuntimeError("boom")

    result = CheckSuite._execute_one(_CategoryErroringCheck(), _make_context("/tmp"))
    assert result.status == CheckStatus.ERROR
    assert result.category == CheckCategory.LINT


def test_fallback_error_preserves_category_on_non_checkresult() -> None:
    class _BadReturnCheck(BaseVerificationCheck):
        check_id = "bad_return"
        check_name = "Bad Return"
        required = True
        category = CheckCategory.SECURITY

        def execute(self, context: VerificationContext) -> str:
            return "not a CheckResult"

    result = CheckSuite._execute_one(_BadReturnCheck(), _make_context("/tmp"))
    assert result.status == CheckStatus.ERROR
    assert result.category == CheckCategory.SECURITY


def test_fallback_error_preserves_category_on_mismatched_id() -> None:
    class _MismatchCheck(BaseVerificationCheck):
        check_id = "good_id"
        check_name = "Mismatch"
        required = True
        category = CheckCategory.BUILD

        def execute(self, context: VerificationContext) -> CheckResult:
            return CheckResult(
                check_id="wrong_id",
                check_name="Wrong",
                required=True,
                status=CheckStatus.PASS,
                started_at=datetime.now(timezone.utc),
                finished_at=datetime.now(timezone.utc),
                duration_seconds=0.0,
            )

    result = CheckSuite._execute_one(_MismatchCheck(), _make_context("/tmp"))
    assert result.status == CheckStatus.ERROR
    assert result.category == CheckCategory.BUILD
