"""
Verification Reporting — deterministic report construction and serialization.

Reuses existing CheckResult, CheckStatus, GateDecision, and VerificationReport
models.  Does NOT persist reports or expose REST endpoints.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Sequence

from app.verification.models import (
    CheckCategory,
    CheckResult,
    CheckStatus,
    GateDecision,
    VerificationReport,
    _utc_now,
)


# ── Report Construction ──────────────────────────────────────────────────────


def build_verification_report(
    task_id: str,
    results: Sequence[CheckResult],
    *,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> VerificationReport:
    """
    Build a VerificationReport from check results.

    Args:
        task_id: The DevelopmentTask identifier.
        results: Ordered sequence of CheckResult instances.
        started_at: When the verification run began. Defaults to now.
        finished_at: When the verification run completed. Defaults to now.

    Returns:
        VerificationReport with fail-closed GateDecision.
    """
    now = _utc_now()
    started = started_at or now
    finished = finished_at or now
    duration = (finished - started).total_seconds()

    gate_decision = _compute_gate_decision(results)

    return VerificationReport(
        task_id=task_id,
        gate_decision=gate_decision,
        started_at=started,
        finished_at=finished,
        duration_seconds=duration,
        results=tuple(results),
    )


def _compute_gate_decision(results: Sequence[CheckResult]) -> GateDecision:
    """
    Compute GateDecision from check results.

    PASS only if every required check returned PASS.
    Empty results → FAIL (fail-closed).
    """
    if not results:
        return GateDecision.FAIL

    for result in results:
        if result.required and result.status != CheckStatus.PASS:
            return GateDecision.FAIL
    return GateDecision.PASS


# ── Serialization ────────────────────────────────────────────────────────────


def render_json(report: VerificationReport) -> str:
    """
    Render a VerificationReport as deterministic JSON.

    Returns a sorted, indented JSON string.
    """
    data = {
        "task_id": report.task_id,
        "gate_decision": report.gate_decision.value,
        "started_at": report.started_at.isoformat(),
        "finished_at": report.finished_at.isoformat(),
        "duration_seconds": round(report.duration_seconds, 6),
        "results": [
            _serialize_result(r) for r in report.results
        ],
    }
    return json.dumps(data, indent=2, sort_keys=True, default=str)


def _serialize_result(result: CheckResult) -> dict[str, object]:
    """Serialize a single CheckResult to a plain dict."""
    data: dict[str, object] = {
        "check_id": result.check_id,
        "check_name": result.check_name,
        "category": result.category.value if isinstance(result.category, CheckCategory) else None,
        "required": result.required,
        "status": result.status.value,
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "error_message": result.error_message,
        "details": dict(result.details),
        "started_at": result.started_at.isoformat(),
        "finished_at": result.finished_at.isoformat(),
        "duration_seconds": round(result.duration_seconds, 6),
    }
    return data


def render_markdown(report: VerificationReport) -> str:
    """
    Render a VerificationReport as deterministic Markdown.
    """
    lines: list[str] = []
    lines.append("# Verification Report")
    lines.append("")
    lines.append(f"**Task ID:** {report.task_id}")
    lines.append(f"**Decision:** {report.gate_decision.value}")
    lines.append(f"**Duration:** {report.duration_seconds:.3f}s")
    lines.append("")
    lines.append("## Results")
    lines.append("")

    if not report.results:
        lines.append("_No checks were executed._")
        lines.append("")
        return "\n".join(lines)

    lines.append("| Check | Category | Required | Status | Exit | Duration |")
    lines.append("|-------|----------|----------|--------|------|----------|")

    for result in report.results:
        category = result.category.value if isinstance(result.category, CheckCategory) else ""
        exit_code = str(result.exit_code) if result.exit_code is not None else ""
        lines.append(
            f"| {result.check_id} | {category} | {result.required} | "
            f"{result.status.value} | {exit_code} | "
            f"{result.duration_seconds:.3f}s |"
        )

    lines.append("")

    # Detail sections for non-PASS results
    non_pass = [r for r in report.results if r.status != CheckStatus.PASS]
    if non_pass:
        lines.append("## Details")
        lines.append("")
        for result in non_pass:
            lines.append(f"### {result.check_id}: {result.check_name}")
            lines.append("")
            if result.error_message:
                lines.append(f"**Error:** {result.error_message}")
                lines.append("")
            if result.stdout:
                lines.append("**Stdout:**")
                lines.append("```")
                lines.append(result.stdout)
                lines.append("```")
                lines.append("")
            if result.stderr:
                lines.append("**Stderr:**")
                lines.append("```")
                lines.append(result.stderr)
                lines.append("```")
                lines.append("")

    return "\n".join(lines)
