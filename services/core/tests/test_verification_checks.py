"""
Stage 7.2 — Core Verification Checks Tests.

Unit tests for concrete workspace checks with mocked CheckExecutor.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.verification.check_runner import CheckCommandResult
from app.verification.checks.frontend_build_check import FrontendBuildCheck
from app.verification.checks.lint_check import LintCheck
from app.verification.checks.pytest_check import PytestCheck
from app.verification.checks.typescript_check import TypeScriptCheck
from app.verification.exceptions import CheckExecutionError
from app.verification.models import CheckCategory, CheckResult, CheckStatus


def _make_context(workspace_path: str) -> "VerificationContext":
    from app.verification.models import VerificationContext

    return VerificationContext(
        task_id="task-7-2",
        workspace_path=workspace_path,
        repository_root=str(Path(workspace_path).parent),
        expected_base_commit="a" * 40,
        current_commit="b" * 40,
    )


# ── PytestCheck ─────────────────────────────────────────────────────────────


def test_pytest_missing_tests_dir_fail(tmp_path: Path) -> None:
    check = PytestCheck()
    ctx = _make_context(str(tmp_path))
    result = check.execute(ctx)

    assert result.status == CheckStatus.FAIL
    assert "tests directory not found" in (result.details.get("reason") or "").lower()


def test_pytest_pass(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    check = PytestCheck()
    ctx = _make_context(str(tmp_path))
    cmd_result = CheckCommandResult(exit_code=0, stdout="passed", stderr="", timed_out=False)

    with patch.object(check, "_executor") as mock_exec:
        mock_exec.return_value.run_command.return_value = cmd_result
        result = check.execute(ctx)

    assert result.status == CheckStatus.PASS
    assert result.details.get("exit_code") == 0


def test_pytest_fail_on_nonzero_exit(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    check = PytestCheck()
    ctx = _make_context(str(tmp_path))
    cmd_result = CheckCommandResult(exit_code=1, stdout="1 failed", stderr="", timed_out=False)

    with patch.object(check, "_executor") as mock_exec:
        mock_exec.return_value.run_command.return_value = cmd_result
        result = check.execute(ctx)

    assert result.status == CheckStatus.FAIL
    assert result.details.get("exit_code") == 1


def test_pytest_error_on_timeout(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    check = PytestCheck()
    ctx = _make_context(str(tmp_path))
    cmd_result = CheckCommandResult(exit_code=-1, stdout="", stderr="timed out", timed_out=True)

    with patch.object(check, "_executor") as mock_exec:
        mock_exec.return_value.run_command.return_value = cmd_result
        result = check.execute(ctx)

    assert result.status == CheckStatus.ERROR
    assert "timed out" in (result.details.get("reason") or "").lower()


def test_pytest_error_on_exception(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    check = PytestCheck()
    ctx = _make_context(str(tmp_path))

    with patch.object(check, "_executor", side_effect=RuntimeError("boom")):
        result = check.execute(ctx)

    assert result.status == CheckStatus.ERROR
    assert "RuntimeError" in (result.error_message or "")


# ── TypeScriptCheck ─────────────────────────────────────────────────────────


def test_typescript_missing_tsconfig_fail(tmp_path: Path) -> None:
    check = TypeScriptCheck()
    ctx = _make_context(str(tmp_path))
    result = check.execute(ctx)

    assert result.status == CheckStatus.FAIL
    assert "tsconfig.json not found" in (result.details.get("reason") or "").lower()


def test_typescript_pass(tmp_path: Path) -> None:
    (tmp_path / "tsconfig.json").write_text("{}")
    check = TypeScriptCheck()
    ctx = _make_context(str(tmp_path))
    cmd_result = CheckCommandResult(exit_code=0, stdout="", stderr="", timed_out=False)

    with patch.object(check, "_executor") as mock_exec:
        mock_exec.return_value.run_command.return_value = cmd_result
        result = check.execute(ctx)

    assert result.status == CheckStatus.PASS


def test_typescript_fail_on_nonzero_exit(tmp_path: Path) -> None:
    (tmp_path / "tsconfig.json").write_text("{}")
    check = TypeScriptCheck()
    ctx = _make_context(str(tmp_path))
    cmd_result = CheckCommandResult(exit_code=1, stdout="error", stderr="", timed_out=False)

    with patch.object(check, "_executor") as mock_exec:
        mock_exec.return_value.run_command.return_value = cmd_result
        result = check.execute(ctx)

    assert result.status == CheckStatus.FAIL


# ── FrontendBuildCheck ──────────────────────────────────────────────────────


def test_frontend_build_missing_package_json_fail(tmp_path: Path) -> None:
    check = FrontendBuildCheck()
    ctx = _make_context(str(tmp_path))
    result = check.execute(ctx)

    assert result.status == CheckStatus.FAIL
    assert "package.json not found" in (result.details.get("reason") or "").lower()


def test_frontend_build_pass(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}")
    check = FrontendBuildCheck()
    ctx = _make_context(str(tmp_path))
    cmd_result = CheckCommandResult(exit_code=0, stdout="built", stderr="", timed_out=False)

    with patch.object(check, "_executor") as mock_exec:
        mock_exec.return_value.run_command.return_value = cmd_result
        result = check.execute(ctx)

    assert result.status == CheckStatus.PASS


def test_frontend_build_error_on_timeout(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}")
    check = FrontendBuildCheck()
    ctx = _make_context(str(tmp_path))
    cmd_result = CheckCommandResult(exit_code=-1, stdout="", stderr="timed out", timed_out=True)

    with patch.object(check, "_executor") as mock_exec:
        mock_exec.return_value.run_command.return_value = cmd_result
        result = check.execute(ctx)

    assert result.status == CheckStatus.ERROR


# ── LintCheck ───────────────────────────────────────────────────────────────


def test_lint_pass(tmp_path: Path) -> None:
    check = LintCheck()
    ctx = _make_context(str(tmp_path))
    cmd_result = CheckCommandResult(exit_code=0, stdout="All passed", stderr="", timed_out=False)

    with patch.object(check, "_executor") as mock_exec:
        mock_exec.return_value.run_command.return_value = cmd_result
        result = check.execute(ctx)

    assert result.status == CheckStatus.PASS


def test_lint_fail_on_nonzero_exit(tmp_path: Path) -> None:
    check = LintCheck()
    ctx = _make_context(str(tmp_path))
    cmd_result = CheckCommandResult(exit_code=1, stdout="E123 error", stderr="", timed_out=False)

    with patch.object(check, "_executor") as mock_exec:
        mock_exec.return_value.run_command.return_value = cmd_result
        result = check.execute(ctx)

    assert result.status == CheckStatus.FAIL


def test_lint_error_on_exception(tmp_path: Path) -> None:
    check = LintCheck()
    ctx = _make_context(str(tmp_path))

    with patch.object(check, "_executor", side_effect=OSError("ruff not found")):
        result = check.execute(ctx)

    assert result.status == CheckStatus.ERROR


# ── Category metadata ───────────────────────────────────────────────────────


def test_checks_have_required_category() -> None:
    for cls in (PytestCheck, TypeScriptCheck, FrontendBuildCheck, LintCheck):
        check = cls()
        assert check.category in CheckCategory
        assert check.required is True


# ── Missing executable -> FAIL ───────────────────────────────────────────────


def test_pytest_fail_on_missing_executable(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    check = PytestCheck()
    ctx = _make_context(str(tmp_path))

    with patch.object(check, "_executor") as mock_exec:
        mock_exec.return_value.run_command.side_effect = CheckExecutionError("Executable 'pytest' not found")
        result = check.execute(ctx)

    assert result.status == CheckStatus.FAIL
    assert "tool unavailable" in (result.details.get("reason") or "").lower()


def test_typescript_fail_on_missing_executable(tmp_path: Path) -> None:
    (tmp_path / "tsconfig.json").write_text("{}")
    check = TypeScriptCheck()
    ctx = _make_context(str(tmp_path))

    with patch.object(check, "_executor") as mock_exec:
        mock_exec.return_value.run_command.side_effect = CheckExecutionError("Executable 'npx' not found")
        result = check.execute(ctx)

    assert result.status == CheckStatus.FAIL
    assert "tool unavailable" in (result.details.get("reason") or "").lower()


def test_frontend_build_fail_on_missing_executable(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}")
    check = FrontendBuildCheck()
    ctx = _make_context(str(tmp_path))

    with patch.object(check, "_executor") as mock_exec:
        mock_exec.return_value.run_command.side_effect = CheckExecutionError("Executable 'npm' not found")
        result = check.execute(ctx)

    assert result.status == CheckStatus.FAIL
    assert "tool unavailable" in (result.details.get("reason") or "").lower()


def test_lint_fail_on_missing_executable(tmp_path: Path) -> None:
    check = LintCheck()
    ctx = _make_context(str(tmp_path))

    with patch.object(check, "_executor") as mock_exec:
        mock_exec.return_value.run_command.side_effect = CheckExecutionError("Executable 'ruff' not found")
        result = check.execute(ctx)

    assert result.status == CheckStatus.FAIL
    assert "tool unavailable" in (result.details.get("reason") or "").lower()
