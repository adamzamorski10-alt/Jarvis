"""
Stage 7.4 — Verification Reporting Tests.

Tests for deterministic report construction and serialization.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.verification.check_runner import BaseVerificationCheck
from app.verification.checks.suite import CheckSuite
from app.verification.models import CheckCategory, CheckResult, CheckStatus, GateDecision, VerificationContext
from app.verification.reporting import (
    _compute_gate_decision,
    build_verification_report,
    render_json,
    render_markdown,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_result(
    check_id: str,
    status: CheckStatus = CheckStatus.PASS,
    required: bool = True,
    category: CheckCategory | None = CheckCategory.UNIT_TESTS,
    exit_code: int | None = 0,
    stdout: str = "",
    stderr: str = "",
    error_message: str | None = None,
    details: dict[str, object] | None = None,
) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        check_name=f"Check {check_id}",
        required=required,
        status=status,
        started_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 1, 1, 12, 0, 1, tzinfo=timezone.utc),
        duration_seconds=1.0,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        error_message=error_message,
        details=details or {},
        category=category,
    )


# ── _compute_gate_decision ───────────────────────────────────────────────────


def test_compute_gate_decision_empty_results() -> None:
    assert _compute_gate_decision([]) == GateDecision.FAIL


def test_compute_gate_decision_all_pass() -> None:
    results = [_make_result("check_a"), _make_result("check_b")]
    assert _compute_gate_decision(results) == GateDecision.PASS


def test_compute_gate_decision_required_fail() -> None:
    results = [_make_result("check_a", status=CheckStatus.FAIL)]
    assert _compute_gate_decision(results) == GateDecision.FAIL


def test_compute_gate_decision_required_error() -> None:
    results = [_make_result("check_a", status=CheckStatus.ERROR)]
    assert _compute_gate_decision(results) == GateDecision.FAIL


def test_compute_gate_decision_optional_fail_does_not_fail_gate() -> None:
    results = [
        _make_result("check_a", required=True),
        _make_result("check_b", required=False, status=CheckStatus.FAIL),
    ]
    assert _compute_gate_decision(results) == GateDecision.PASS


def test_compute_gate_decision_skipped_required_fails() -> None:
    results = [_make_result("check_a", status=CheckStatus.SKIPPED)]
    assert _compute_gate_decision(results) == GateDecision.FAIL


# ── build_verification_report ────────────────────────────────────────────────


def test_build_report_empty_results() -> None:
    report = build_verification_report("task-1", [])
    assert report.gate_decision == GateDecision.FAIL
    assert report.results == ()
    assert report.task_id == "task-1"


def test_build_report_all_pass() -> None:
    results = [_make_result("check_a"), _make_result("check_b")]
    report = build_verification_report("task-1", results)
    assert report.gate_decision == GateDecision.PASS
    assert len(report.results) == 2
    assert report.results[0].check_id == "check_a"


def test_build_report_preserves_order() -> None:
    results = [
        _make_result("first"),
        _make_result("second"),
        _make_result("third"),
    ]
    report = build_verification_report("task-1", results)
    assert [r.check_id for r in report.results] == ["first", "second", "third"]


def test_build_report_preserves_all_fields() -> None:
    result = _make_result(
        "check_x",
        status=CheckStatus.FAIL,
        exit_code=1,
        stdout="out",
        stderr="err",
        error_message="failed",
        details={"key": "value"},
    )
    report = build_verification_report("task-1", [result])
    stored = report.results[0]
    assert stored.check_id == "check_x"
    assert stored.status == CheckStatus.FAIL
    assert stored.exit_code == 1
    assert stored.stdout == "out"
    assert stored.stderr == "err"
    assert stored.error_message == "failed"
    assert stored.details == {"key": "value"}
    assert stored.category == CheckCategory.UNIT_TESTS


def test_build_report_no_false_pass_on_required_fail() -> None:
    results = [_make_result("check_a", status=CheckStatus.FAIL)]
    report = build_verification_report("task-1", results)
    assert report.gate_decision == GateDecision.FAIL


def test_build_report_optional_fail_allows_pass() -> None:
    results = [
        _make_result("check_a", required=True),
        _make_result("check_b", required=False, status=CheckStatus.FAIL),
    ]
    report = build_verification_report("task-1", results)
    assert report.gate_decision == GateDecision.PASS


def test_build_report_sets_timestamps() -> None:
    started = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    finished = datetime(2026, 1, 1, 12, 0, 5, tzinfo=timezone.utc)
    results = [_make_result("check_a")]
    report = build_verification_report("task-1", results, started_at=started, finished_at=finished)
    assert report.started_at == started
    assert report.finished_at == finished
    assert report.duration_seconds == 5.0


# ── render_json ──────────────────────────────────────────────────────────────


def test_render_json_deterministic() -> None:
    results = [_make_result("check_a"), _make_result("check_b")]
    report = build_verification_report("task-1", results)
    json_str = render_json(report)

    parsed = json.loads(json_str)
    assert parsed["gate_decision"] == "PASS"
    assert len(parsed["results"]) == 2


def test_render_json_preserves_all_fields() -> None:
    result = _make_result(
        "check_x",
        status=CheckStatus.FAIL,
        exit_code=1,
        stdout="out",
        stderr="err",
        error_message="failed",
        details={"key": "value"},
        category=CheckCategory.LINT,
    )
    report = build_verification_report("task-1", [result])
    json_str = render_json(report)
    parsed = json.loads(json_str)

    r = parsed["results"][0]
    assert r["check_id"] == "check_x"
    assert r["status"] == "FAIL"
    assert r["category"] == "LINT"
    assert r["exit_code"] == 1
    assert r["stdout"] == "out"
    assert r["stderr"] == "err"
    assert r["error_message"] == "failed"
    assert r["details"] == {"key": "value"}


def test_render_json_empty_results() -> None:
    report = build_verification_report("task-1", [])
    json_str = render_json(report)
    parsed = json.loads(json_str)

    assert parsed["gate_decision"] == "FAIL"
    assert parsed["results"] == []


def test_render_json_sorted_keys() -> None:
    report = build_verification_report("task-1", [])
    json_str = render_json(report)

    parsed = json.loads(json_str)
    keys = list(parsed.keys())
    assert keys == sorted(keys), "JSON keys must be sorted for determinism"


# ── render_markdown ──────────────────────────────────────────────────────────


def test_render_markdown_empty_results() -> None:
    report = build_verification_report("task-1", [])
    md = render_markdown(report)

    assert "# Verification Report" in md
    assert "**Decision:** FAIL" in md
    assert "_No checks were executed._" in md


def test_render_markdown_all_pass() -> None:
    results = [_make_result("check_a"), _make_result("check_b")]
    report = build_verification_report("task-1", results)
    md = render_markdown(report)

    assert "# Verification Report" in md
    assert "**Decision:** PASS" in md
    assert "| check_a |" in md
    assert "| check_b |" in md


def test_render_markdown_shows_non_pass_details() -> None:
    results = [
        _make_result("check_a"),
        _make_result("check_b", status=CheckStatus.FAIL, error_message="boom"),
    ]
    report = build_verification_report("task-1", results)
    md = render_markdown(report)

    assert "## Details" in md
    assert "**Error:** boom" in md
    assert "check_b" in md


def test_render_markdown_deterministic() -> None:
    results = [_make_result("check_a")]
    report = build_verification_report("task-1", results)
    md1 = render_markdown(report)
    md2 = render_markdown(report)

    assert md1 == md2


def test_render_markdown_no_details_for_pass() -> None:
    results = [_make_result("check_a")]
    report = build_verification_report("task-1", results)
    md = render_markdown(report)

    assert "## Details" not in md
